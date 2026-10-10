import asyncio
import inspect
import struct
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from proxy.balancer import _Balancer
from proxy.cf_h2 import (
    CfH2Pool, MAX_CHANNEL_REQUESTS, MAX_PACKET, _HttpChannel, _HttpLane,
    _ReplayPacket, _encode_reply, _read_packet, bridge_h2,
)
from proxy.utils import PROTO_TAG_ABRIDGED, PROTO_TAG_INTERMEDIATE, PROTO_TAG_SECURE

class _IdentityCipher:
    def update(self, value):
        return value


class NativeFramingTest(unittest.IsolatedAsyncioTestCase):
    async def _packet(self, wire, tag):
        reader = asyncio.StreamReader()
        async def feed():
            for byte in wire:
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0)
            reader.feed_eof()
        task = asyncio.create_task(feed())
        try:
            return await _read_packet(reader, _IdentityCipher(), tag)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_fragmented_abridged_extended_and_quick_ack(self):
        body = b'a' * 512
        decoded, quick = await self._packet(b'\xff\x80\x00\x00' + body, PROTO_TAG_ABRIDGED)
        self.assertEqual(decoded, body)
        self.assertTrue(quick)

    async def test_intermediate_quick_ack_does_not_change_body(self):
        body = b'a' * 40
        decoded, quick = await self._packet(struct.pack('<I', len(body) | 0x80000000) + body,
                                            PROTO_TAG_INTERMEDIATE)
        self.assertEqual(decoded, body)
        self.assertTrue(quick)

    async def test_padded_plaintext_removes_all_fifteen_padding_lengths(self):
        body = b'\x00' * 8 + b'm' * 8 + struct.pack('<I', 20) + b'p' * 20
        for padding in range(16):
            wire = body + b'z' * padding
            decoded, quick = await self._packet(struct.pack('<I', len(wire)) + wire, PROTO_TAG_SECURE)
            self.assertEqual(decoded, body, padding)
            self.assertFalse(quick)

    async def test_plaintext_accepts_extended_padding_used_by_apple_and_android(self):
        body = b'\x00' * 8 + b'm' * 8 + struct.pack('<I', 20) + b'p' * 20
        # MTProtoKit appends 0..236 bytes outside message_data_length, then
        # the TCP transport adds 0..15 more. Android uses 0..256 directly.
        for padding in range(257):
            with self.subTest(padding=padding):
                wire = body + b'z' * padding
                reader = asyncio.StreamReader()
                reader.feed_data(struct.pack('<I', len(wire) | 0x80000000) + wire)
                decoded, quick = await _read_packet(reader, _IdentityCipher(), PROTO_TAG_SECURE)
                self.assertEqual(decoded, body)
                self.assertTrue(quick)

    async def test_padded_plaintext_preserves_padding_included_in_message_length(self):
        body = b'\x00' * 8 + b'm' * 8 + struct.pack('<I', 272) + b'p' * 20 + b'd' * 252
        decoded, _ = await self._packet(struct.pack('<I', len(body) + 15) + body + b'z' * 15,
                                       PROTO_TAG_SECURE)
        self.assertEqual(decoded, body)

    async def test_extended_padding_preserves_packet_size_limit(self):
        body = b'\x00' * 8 + b'm' * 8 + struct.pack('<I', 20) + b'p' * 20
        wire = body + b'z' * 256
        with patch('proxy.cf_h2.MAX_PACKET', len(body)):
            decoded, _ = await self._packet(struct.pack('<I', len(wire)) + wire, PROTO_TAG_SECURE)
            self.assertEqual(decoded, body)
            oversized = body[:16] + struct.pack('<I', 24) + b'p' * 24
            with self.assertRaisesRegex(ValueError, 'size'):
                await self._packet(struct.pack('<I', len(oversized)) + oversized, PROTO_TAG_SECURE)
            encrypted = b'k' * 8 + b'm' * 16 + b'c' * 32
            with self.assertRaisesRegex(ValueError, 'size'):
                await self._packet(struct.pack('<I', len(encrypted)) + encrypted, PROTO_TAG_SECURE)

    async def test_invalid_plaintext_lengths_and_excess_padding_are_rejected(self):
        for declared, payload, padding in ((0, 20, 0), (24, 20, 0), (21, 21, 0),
                                           (20, 20, 257), (0xffffffff, 20, 0)):
            with self.subTest(declared=declared, padding=padding):
                body = b'\x00' * 8 + b'm' * 8 + struct.pack('<I', declared) + b'p' * payload
                wire = body + b'z' * padding
                with self.assertRaises(ValueError):
                    await self._packet(struct.pack('<I', len(wire)) + wire, PROTO_TAG_SECURE)

    async def test_padded_ciphertext_removes_full_padding_blocks(self):
        body = b'k' * 8 + b'm' * 16 + b'c' * 32
        for padding in range(16):
            wire = body + b'z' * padding
            decoded, _ = await self._packet(struct.pack('<I', len(wire)) + wire, PROTO_TAG_SECURE)
            self.assertEqual(decoded, body, padding)

    async def test_oversize_header_rejected_before_body_read(self):
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack('<I', MAX_PACKET + 4))
        with self.assertRaisesRegex(ValueError, 'length'):
            await _read_packet(reader, _IdentityCipher(), PROTO_TAG_INTERMEDIATE)

    def test_negative_transport_error_keeps_native_framing(self):
        error = struct.pack('<i', -404)
        self.assertEqual(_encode_reply(error, PROTO_TAG_ABRIDGED), b'\x01' + error)
        self.assertEqual(_encode_reply(error, PROTO_TAG_INTERMEDIATE), b'\x04\x00\x00\x00' + error)


class HttpMultiplexTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lane = _HttpLane('kws4.example.org', 1)
        await self.lane.client.aclose()
        self.channels = []

    async def asyncTearDown(self):
        await self.lane.close()

    def _channel(self):
        channel = _HttpChannel(self.lane, len(self.channels) + 1, 'owned-test')
        self.channels.append(channel)
        return channel

    def _transport(self, handler, *, trace_send=True):
        async def handle(request):
            if trace_send and 'trace' in request.extensions:
                # MockTransport receives the complete body before calling us.
                await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': 3})
            result = handler(request)
            return await result if inspect.isawaitable(result) else result
        self.lane.client = httpx.AsyncClient(http2=True, transport=httpx.MockTransport(handle))

    async def test_same_auth_prefix_out_of_order_replies_stay_with_source_channel(self):
        first_started, release_first = asyncio.Event(), asyncio.Event()
        body_a, body_b = b'samekey!' + b'a' * 32, b'samekey!' + b'b' * 32
        async def handler(request):
            if request.content == body_a:
                first_started.set()
                await release_first.wait()
            return httpx.Response(200, content=request.content, extensions={
                'http_version': b'HTTP/2', 'stream_id': 3 if request.content == body_a else 5})
        self._transport(handler)
        first, second = self._channel(), self._channel()
        await first.send(body_a, False)
        await first_started.wait()
        await second.send(body_b, False)
        self.assertEqual(await asyncio.wait_for(second.receive(), 1), body_b)
        release_first.set()
        self.assertEqual(await asyncio.wait_for(first.receive(), 1), body_a)
        self.assertEqual(self.lane.max_inflight, 2)

    async def test_close_one_channel_does_not_cancel_another(self):
        async def handler(request):
            if request.content[:1] == b'a':
                await asyncio.Event().wait()
            return httpx.Response(200, content=request.content, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        first, second = self._channel(), self._channel()
        await first.send(b'a' * 40, False)
        await first.close()
        await second.send(b'b' * 40, False)
        self.assertEqual(await asyncio.wait_for(second.receive(), 1), b'b' * 40)
        await second.close()
        self.assertEqual(self.lane.inflight, 0)
        self.assertEqual(self.lane.queued_bytes, 0)
        self.assertFalse(self.lane.closed)

    async def test_backpressure_waits_instead_of_dropping_requests(self):
        release = asyncio.Event()
        async def handler(request):
            await release.wait()
            return httpx.Response(200, content=request.content, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        for _ in range(MAX_CHANNEL_REQUESTS):
            await channel.send(b'x' * 40, False)
        extra = asyncio.create_task(channel.send(b'y' * 40, False))
        await asyncio.sleep(0.01)
        self.assertFalse(extra.done())
        self.assertEqual(len(channel.pending), MAX_CHANNEL_REQUESTS)
        release.set()
        await asyncio.wait_for(extra, 1)
        received = [await asyncio.wait_for(channel.receive(), 1) for _ in range(MAX_CHANNEL_REQUESTS + 1)]
        self.assertEqual(received.count(b'y' * 40), 1)

    async def test_http_error_closes_only_its_channel_without_replaying_packet(self):
        calls = []
        async def handler(request):
            calls.append(request.content)
            return httpx.Response(503, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        await channel.send(b'x' * 40, False)
        with self.assertRaisesRegex(ConnectionError, '503'):
            await asyncio.wait_for(channel.receive(), 1)
        self.assertEqual(calls, [b'x' * 40])
        self.assertTrue(channel.closed)
        self.assertFalse(self.lane.closed)

    async def test_transport_error_reaches_client_before_close_without_cooling_domain(self):
        class ErrorBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise AssertionError('HTTP transport error bodies must be ignored')
                yield b''

        for tag in (PROTO_TAG_ABRIDGED, PROTO_TAG_INTERMEDIATE, PROTO_TAG_SECURE):
            for status in (403, 404, 429, 444):
                with self.subTest(tag=tag, status=status):
                    calls = []

                    def handler(request):
                        calls.append(request.content)
                        if request.content == b'h' * 40:
                            return httpx.Response(200, content=b'r' * 40,
                                                  extensions={'http_version': b'HTTP/2'})
                        return httpx.Response(status, stream=ErrorBody(),
                                              headers={'content-type': 'text/html'},
                                              extensions={'http_version': b'HTTP/2'})

                    self._transport(handler)
                    channel = self._channel()
                    output = bytearray()
                    draining, release = asyncio.Event(), asyncio.Event()

                    async def drain():
                        draining.set()
                        await release.wait()

                    writer = SimpleNamespace(write=output.extend, drain=drain)
                    reader = asyncio.StreamReader()
                    packet = b'k' * 40
                    reader.feed_data(_encode_reply(packet, tag))
                    ctx = SimpleNamespace(clt_enc=_IdentityCipher(), clt_dec=_IdentityCipher())
                    task = asyncio.create_task(bridge_h2(reader, writer, channel, ctx, tag))
                    try:
                        await asyncio.wait_for(draining.wait(), 1)
                        self.assertFalse(task.done())
                        self.assertFalse(channel.close_done)
                        if tag == PROTO_TAG_ABRIDGED:
                            self.assertEqual(output, b'\x01' + struct.pack('<i', -status))
                        else:
                            length, = struct.unpack('<I', output[:4])
                            self.assertEqual(length, len(output) - 4)
                            self.assertEqual(output[4:8], struct.pack('<i', -status))
                            self.assertLessEqual(length, 7)
                        self.assertEqual(self.lane.failed_until, 0)
                        healthy = self._channel()
                        await healthy.send(b'h' * 40, False)
                        self.assertEqual(await asyncio.wait_for(healthy.receive(), 1), b'r' * 40)
                        self.assertEqual(calls, [packet, b'h' * 40])
                        self.assertFalse(self.lane.closed)
                    finally:
                        release.set()
                        await asyncio.wait_for(task, 1)
                    self.assertTrue(channel.close_done)
                    self.assertFalse(channel.pending)
                    await healthy.close()

    async def test_long_polls_cannot_block_fresh_request_and_retired_packet_recovers(self):
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(MAX_CHANNEL_REQUESTS)]
        fresh = b'samekey!' + b'n' * 32
        calls = []
        async def handler(request):
            calls.append(request.content)
            await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': len(calls) * 2 + 1})
            if request.content == fresh:
                return httpx.Response(200, content=b'n' * 40, extensions={'http_version': b'HTTP/2'})
            if request.content == bodies[0] and calls.count(bodies[0]) == 2:
                return httpx.Response(200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'})
            await asyncio.Event().wait()
        self._transport(handler)
        channel = self._channel()
        for body in bodies:
            await channel.send(body, False)
        await asyncio.sleep(0)
        original = next(iter(channel.pending))
        with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.wait_for(channel.send(fresh, False), 1)
                received = [await asyncio.wait_for(channel.receive(), 1) for _ in range(2)]
                self.assertCountEqual(received, [b'n' * 40, b'r' * 40])
                self.assertTrue(original.cancelled())
                self.assertTrue(channel.replay_history[0].retired)
                self.assertEqual(calls.count(bodies[0]), 2)
                self.assertEqual(channel.capacity_rotations, 1)
                self.assertEqual(self.lane.max_inflight, MAX_CHANNEL_REQUESTS)
                self.assertEqual(self.lane.errors, 0)
                self.assertFalse(channel.closed)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_shared_lane_long_polls_cannot_block_new_channel(self):
        fresh = b'samekey!' + b'n' * 32
        async def handler(request):
            await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': 3})
            if request.content == fresh:
                return httpx.Response(200, content=b'n' * 40, extensions={'http_version': b'HTTP/2'})
            await asyncio.Event().wait()
        self._transport(handler)
        first, second, waiting = self._channel(), self._channel(), self._channel()
        with patch('proxy.cf_h2.MAX_LANE_REQUESTS', 2), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            await first.send(b'samekey!' + b'a' * 32, False)
            await second.send(b'samekey!' + b'b' * 32, False)
            await asyncio.sleep(0)
            originals = [next(iter(channel.pending)) for channel in (first, second)]
            recovery = asyncio.create_task(waiting._recover())
            try:
                await asyncio.wait_for(waiting.send(fresh, False), 1)
                self.assertEqual(await asyncio.wait_for(waiting.receive(), 1), b'n' * 40)
                self.assertEqual(sum(task.cancelled() for task in originals), 1)
                self.assertEqual(sum(channel.capacity_rotations for channel in (first, second)), 1)
                self.assertEqual(self.lane.capacity_rotations, 1)
                self.assertEqual(self.lane.max_inflight, 2)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_upload_room_accounts_for_waiting_packet_size(self):
        for limit in ('MAX_CHANNEL_BYTES', 'MAX_LANE_BYTES'):
            with self.subTest(limit=limit):
                started = asyncio.Event()
                async def handler(request):
                    await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': 3})
                    if request.content == b'k' * 40:
                        started.set()
                        await asyncio.Event().wait()
                    return httpx.Response(200, content=b'n' * 40, extensions={'http_version': b'HTTP/2'})
                self._transport(handler)
                channel = self._channel()
                with patch('proxy.cf_h2.' + limit, 60), \
                        patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                        patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
                    await channel.send(b'k' * 40, False)
                    await started.wait()
                    recovery = asyncio.create_task(channel._recover())
                    try:
                        # 20 bytes are free, but the pending upload needs 40.
                        await asyncio.wait_for(channel.send(b'n' * 40, False), .5)
                        self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'n' * 40)
                        self.assertEqual(channel.capacity_rotations, 1)
                    finally:
                        recovery.cancel()
                        await asyncio.gather(recovery, return_exceptions=True)
                        await channel.close()

    async def test_close_releases_request_cancelled_before_coroutine_start_once(self):
        self._transport(lambda request: self.fail('cancelled request reached HTTP'))
        channel = self._channel()
        create_task = asyncio.create_task
        def cancelled_task(coroutine):
            task = create_task(coroutine)
            task.cancel()
            return task
        with patch('proxy.cf_h2.asyncio.create_task', cancelled_task):
            await channel.send(b'k' * 40, False)
        packet = channel.replay_history[0]
        await channel.close()
        await channel.close()
        self.assertEqual((self.lane.inflight, self.lane.queued_bytes, channel.pending_bytes), (0, 0, 0))
        self.assertFalse(channel.pending)
        self.assertFalse(channel.pending_since)
        self.assertFalse(channel.sent_since)
        self.assertFalse(packet.originals)

    async def test_capacity_rotation_keeps_upload_and_started_response_intact(self):
        uploading, receiving, waiting = [b'samekey!' + bytes([index]) * 32 for index in range(3)]
        fresh = b'samekey!' + b'n' * 32
        body_started = asyncio.Event()
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'r' * 20
                body_started.set()
                await asyncio.Event().wait()
        async def handler(request):
            if request.content == uploading:
                await asyncio.Event().wait()  # No send_request_body.complete yet.
            await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': 3})
            if request.content == receiving:
                return httpx.Response(200, stream=SlowBody(), extensions={'http_version': b'HTTP/2'})
            if request.content == waiting:
                await asyncio.Event().wait()
            return httpx.Response(200, content=b'n' * 40, extensions={'http_version': b'HTTP/2'})
        self._transport(handler, trace_send=False)
        channel = self._channel()
        with patch('proxy.cf_h2.MAX_CHANNEL_REQUESTS', 3), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            for body in (uploading, receiving, waiting):
                await channel.send(body, False)
            await asyncio.wait_for(body_started.wait(), 1)
            tasks = list(channel.pending)
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.wait_for(channel.send(fresh, False), 1)
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'n' * 40)
                self.assertFalse(tasks[0].done())
                self.assertFalse(tasks[1].done())
                self.assertTrue(tasks[2].cancelled())
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_capacity_rotation_never_retires_unretained_or_plaintext_request(self):
        release = asyncio.Event()
        async def handler(request):
            await request.extensions['trace']('http2.send_request_body.complete', {'stream_id': 3})
            await release.wait()
            return httpx.Response(200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        with patch('proxy.cf_h2.MAX_CHANNEL_REQUESTS', 2), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .002):
            await channel.send(b'\0' * 8 + b'p' * 32, False)
            await channel.send(b'samekey!' + b'u' * (64 * 1024), False)
            extra = asyncio.create_task(channel.send(b'samekey!' + b'n' * 32, False))
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.sleep(.04)
                self.assertFalse(extra.done())
                self.assertEqual(channel.capacity_rotations, 0)
                self.assertTrue(all(not task.done() for task in channel.pending))
                release.set()
                await asyncio.wait_for(extra, 1)
            finally:
                release.set()
                extra.cancel()
                recovery.cancel()
                await asyncio.gather(extra, recovery, return_exceptions=True)

    async def test_http1_rejected_by_preflight(self):
        self._transport(lambda request: httpx.Response(200, extensions={'http_version': b'HTTP/1.1'}))
        with self.assertRaisesRegex(ValueError, 'HTTP/1.1'):
            await self.lane._preflight()

    async def test_telegram_head_501_still_allows_http2_preflight(self):
        self._transport(lambda request: httpx.Response(501, extensions={'http_version': b'HTTP/2'}))
        await self.lane._preflight()

    async def test_missing_http_endpoint_is_rejected_before_native_requests(self):
        self._transport(lambda request: httpx.Response(
            404, extensions={'http_version': b'HTTP/2'}))
        with self.assertRaisesRegex(ConnectionError, 'preflight HTTP 404'):
            await self.lane._preflight()

    async def _packet_bridge(self, *, content_length=True, truncate=False, concurrent_small=False):
        first_chunk_read, release_tail = asyncio.Event(), asyncio.Event()
        body = b'x' * (64 * 1024 + 40)
        first_chunk = body[:16384]
        class ResponseStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield first_chunk
                first_chunk_read.set()
                await release_tail.wait()
                if not truncate:
                    yield body[len(first_chunk):]
        async def handler(request):
            if request.content == b's' * 40:
                return httpx.Response(200, content=b'z' * 40,
                                      extensions={'http_version': b'HTTP/2'})
            headers = {'content-length': str(len(body))} if content_length else {}
            return httpx.Response(200, headers=headers, stream=ResponseStream(),
                                  extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        class Writer:
            def __init__(self):
                self.body = bytearray()
                self.changed = asyncio.Event()
            def write(self, data):
                self.body.extend(data)
                self.changed.set()
            async def drain(self):
                await asyncio.sleep(0)
        reader, writer = asyncio.StreamReader(), Writer()
        channel = self._channel()
        ctx = SimpleNamespace(clt_enc=_IdentityCipher(), clt_dec=_IdentityCipher())
        task = asyncio.create_task(bridge_h2(reader, writer, channel, ctx, PROTO_TAG_INTERMEDIATE))
        reader.feed_data(struct.pack('<I', 40) + b'l' * 40)
        try:
            await asyncio.wait_for(first_chunk_read.wait(), 1)
            self.assertEqual(writer.body, b'')
            if concurrent_small:
                reader.feed_data(struct.pack('<I', 40) + b's' * 40)
                await asyncio.wait_for(writer.changed.wait(), 1)
                self.assertEqual(writer.body, struct.pack('<I', 40) + b'z' * 40)
            release_tail.set()
            if truncate:
                await asyncio.wait_for(task, 1)
                self.assertTrue(channel.closed)
                self.assertEqual(writer.body, b'')
            else:
                expected = struct.pack('<I', len(body)) + body
                if concurrent_small:
                    expected = struct.pack('<I', 40) + b'z' * 40 + expected
                for _ in range(100):
                    if len(writer.body) >= len(expected):
                        break
                    await asyncio.sleep(0)
                self.assertEqual(writer.body, expected)
        finally:
            release_tail.set()
            reader.feed_eof()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_native_frame_starts_only_after_entire_body_is_available(self):
        await self._packet_bridge()

    async def test_ready_reply_bypasses_incomplete_large_response_on_same_channel(self):
        await self._packet_bridge(concurrent_small=True)

    async def test_response_without_length_uses_buffered_framing(self):
        await self._packet_bridge(content_length=False)

    async def test_truncated_stream_closes_native_channel(self):
        await self._packet_bridge(truncate=True)

    async def test_recovery_pauses_during_body_receive_and_resumes_afterwards(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'r' * 20
                started.set()
                await release.wait()
                yield b'r' * 20

        def handler(request):
            calls.append(request.content)
            if len(calls) == 1:
                return httpx.Response(200, stream=SlowBody(), extensions={'http_version': b'HTTP/2'})
            return httpx.Response(200, content=b's' * 40, extensions={'http_version': b'HTTP/2'})

        self._transport(handler)
        channel = self._channel()
        await channel.send(b'x' * 40, False)
        await asyncio.wait_for(started.wait(), 1)
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.sleep(.06)
                self.assertEqual(len(calls), 1)
                self.assertEqual(channel.receiving_http, 1)
                release.set()
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b's' * 40)
                self.assertEqual(calls, [b'x' * 40, b'x' * 40])
                self.assertEqual(channel.receiving_http, 0)
                self.assertEqual(self.lane.reply_buffer_bytes, 0)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_lane_reply_memory_bound_and_cancellation_cleanup(self):
        started = asyncio.Event()

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'r' * 32
                started.set()
                await asyncio.Event().wait()

        def handler(request):
            if request.content == b'a' * 40:
                return httpx.Response(200, stream=SlowBody(), extensions={'http_version': b'HTTP/2'})
            return httpx.Response(200, content=b'r' * 16, extensions={'http_version': b'HTTP/2'})

        self._transport(handler)
        slow = self._channel()
        with patch('proxy.cf_h2.MAX_LANE_REPLY_BYTES', 40):
            await slow.send(b'a' * 40, False)
            await asyncio.wait_for(started.wait(), 1)
            self.assertEqual(self.lane.reply_buffer_bytes, 32)
            with self.assertRaisesRegex(BufferError, 'response buffers'):
                await self.lane._post(b'b' * 40, 2)
            self.assertEqual(self.lane.reply_buffer_bytes, 32)
            self.assertEqual(self.lane.failed_until, 0)
            await slow.close()
            self.assertEqual(slow.receiving_http, 0)
            self.assertEqual(self.lane.reply_buffer_bytes, 0)
            self.assertEqual(await self.lane._post(b'b' * 40, 2), b'r' * 16)

    async def test_native_disconnect_does_not_cool_down_healthy_lane(self):
        self._transport(lambda request: httpx.Response(
            200, content=b'r' * 65536, extensions={'http_version': b'HTTP/2'}))

        class DisconnectedWriter:
            def write(self, data):
                pass

            async def drain(self):
                raise ConnectionResetError('Connection lost')

        channel = self._channel()
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack('<I', 40) + b'x' * 40)
        ctx = SimpleNamespace(clt_enc=_IdentityCipher(), clt_dec=_IdentityCipher())
        await asyncio.wait_for(bridge_h2(reader, DisconnectedWriter(), channel, ctx,
                                         PROTO_TAG_INTERMEDIATE), 1)
        self.assertTrue(channel.closed)
        self.assertEqual(self.lane.failed_until, 0)
        self.assertEqual(self.lane.errors, 0)
        healthy = self._channel()
        await healthy.send(b'h' * 40, False)
        self.assertEqual(await asyncio.wait_for(healthy.receive(), 1), b'r' * 65536)

    async def test_recovery_cancellation_is_not_lost_when_wakeup_completes(self):
        channel = self._channel()
        wait = channel.recovery_wakeup.wait
        calls = 0

        async def wake_and_cancel():
            nonlocal calls
            calls += 1
            if calls == 1:
                recovery.cancel()
                return True
            return await wait()

        with patch.object(channel.recovery_wakeup, 'wait', side_effect=wake_and_cancel):
            recovery = asyncio.create_task(channel._recover())
            try:
                done, _ = await asyncio.wait((recovery,), timeout=.2)
                self.assertIn(recovery, done)
                self.assertTrue(recovery.cancelled())
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_slow_receiver_queue_overflow_does_not_break_other_channels(self):
        async def handler(request):
            return httpx.Response(200, content=request.content, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        slow, healthy = self._channel(), self._channel()
        with patch('proxy.cf_h2.MAX_CHANNEL_BYTES', 80):
            for _ in range(3):
                await slow.send(b's' * 40, False)
                await asyncio.gather(*list(slow.pending))
            with self.assertRaises(BufferError):
                await asyncio.wait_for(slow.receive(), 1)
            await healthy.send(b'h' * 40, False)
            self.assertEqual(await asyncio.wait_for(healthy.receive(), 1), b'h' * 40)
        self.assertFalse(self.lane.closed)

    async def test_simultaneous_pool_opens_preflight_once_and_share_client(self):
        pool = CfH2Pool()
        balancer = _Balancer()
        balancer.update_domains_list(['example.org'])
        started = []
        async def preflight(lane):
            started.append(lane)
            await asyncio.sleep(0)
        try:
            with patch('proxy.cf_h2.balancer', balancer), patch.object(_HttpLane, '_preflight', preflight):
                channels = await asyncio.gather(*(pool.open(4, 'owned-test') for _ in range(6)))
            self.assertEqual(len(started), 1)
            self.assertEqual(len({id(channel.lane.client) for channel in channels}), 1)
            self.assertEqual(len({channel.channel_id for channel in channels}), 6)
        finally:
            await pool.close()

    async def test_recovery_reuses_exact_ciphertext_when_original_http_is_still_pending(self):
        calls = []
        original_waiting = asyncio.Event()
        body = b'opaque-key-and-message-id' + b'x' * 16
        async def handler(request):
            calls.append(request.content)
            if len(calls) == 1:
                original_waiting.set()
                await asyncio.Event().wait()
            return httpx.Response(200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        await channel.send(body, False)
        await original_waiting.wait()
        with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .01), patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            try:
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
                self.assertEqual(calls, [body, body])
                self.assertEqual(channel.replay_requests, 1)
                self.assertEqual(self.lane.max_inflight, 2)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
                await channel.close()
        self.assertFalse(channel.pending)
        self.assertEqual(self.lane.inflight, 0)

    async def test_recovery_after_http_finished_without_native_reply(self):
        calls = []
        async def handler(request):
            calls.append(request.content)
            return httpx.Response(200, content=b'' if len(calls) == 1 else b'r' * 40,
                                  extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        body = b'k' * 40
        await channel.send(body, False)
        await asyncio.gather(*list(channel.pending))
        self.assertTrue(channel.queue.empty())
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            try:
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
                await asyncio.sleep(.03)
                self.assertEqual(calls, [body, body])
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_idle_receiver_preserves_capacity_for_new_native_request(self):
        calls = []
        probes_waiting = asyncio.Event()
        async def handler(request):
            calls.append(request.content)
            if calls.count(request.content) > 1:
                if len(calls) == 4:
                    probes_waiting.set()
                await asyncio.Event().wait()
            return httpx.Response(200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(4)]
        for body in bodies[:3]:
            await channel.send(body, False)
            await asyncio.gather(*list(channel.pending))
            await channel.receive()
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.wait_for(probes_waiting.wait(), 1)
                await asyncio.sleep(.03)
                self.assertEqual(channel.replay_requests, 1)
                self.assertEqual(len(channel.replay_pending), 1)
                await asyncio.wait_for(channel.send(bodies[3], False), 1)
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
                self.assertEqual(calls[3:4], [bodies[2]])
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
                await asyncio.gather(channel.close(), channel.close())
        self.assertEqual(self.lane.inflight, 0)
        self.assertEqual(channel.pending_bytes, 0)
        self.assertFalse(channel.replay_pending)

    async def test_handshake_and_expired_ciphertext_are_never_replayed(self):
        calls = []
        async def handler(request):
            calls.append(request.content)
            return httpx.Response(200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        for body in (b'\x00' * 8 + b'h' * 32, b'k' * 40):
            await channel.send(body, False)
            await asyncio.gather(*list(channel.pending))
            await channel.receive()
        # Windows may give consecutive monotonic() calls the same timestamp;
        # an event wakeup need not advance the clock like the old sleep did.
        channel.replay_history[-1].sent_at -= 1
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_MAX_AGE', 0):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.sleep(.04)
                self.assertEqual(len(calls), 2)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_unhelpful_replies_do_not_cause_unbounded_recovery(self):
        calls = []
        retries_finished = asyncio.Event()
        async def handler(request):
            calls.append((request.content, time.monotonic()))
            if len(calls) == 5:
                retries_finished.set()
            return httpx.Response(200, content=b'', extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(2)]
        for body in bodies:
            await channel.send(body, False)
        await asyncio.gather(*list(channel.pending))
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .002), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .02):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.wait_for(retries_finished.wait(), 1)
                await asyncio.sleep(.06)
                older = [when for body, when in calls if body == bodies[0]]
                latest = [when for body, when in calls if body == bodies[1]]
                # Completed history is not scanned; only the latest is polled.
                self.assertEqual(len(older), 1)
                self.assertEqual(len(latest), 4)
                for first, second in zip(latest[1:], latest[2:]):
                    self.assertGreaterEqual(second - first, .015)
                self.assertFalse(channel.pending)
                self.assertFalse(channel.replay_pending)
                self.assertEqual(self.lane.inflight, 0)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_old_pending_request_has_bounded_retries_after_newer_requests(self):
        body = b'samekey!' + b't' * 32
        original_started = asyncio.Event()
        retries_finished = asyncio.Event()
        attempts = 0
        async def handler(request):
            nonlocal attempts
            if request.content == body:
                attempts += 1
                if attempts == 1:
                    original_started.set()
                    await asyncio.Event().wait()
                if attempts == 4:
                    retries_finished.set()
            return httpx.Response(200, content=b'', extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        await channel.send(body, False)
        await original_started.wait()
        original = next(iter(channel.pending))
        await channel.send(b'samekey!' + b'n' * 32, False)
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .02):
            recovery = asyncio.create_task(channel._recover())
            try:
                await asyncio.wait_for(retries_finished.wait(), 1)
                await asyncio.sleep(.06)
                self.assertEqual(attempts, 4)
                self.assertFalse(original.done())
                self.assertFalse(channel.replay_pending)
            finally:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

    async def test_closed_pool_cannot_open_new_channels(self):
        pool = CfH2Pool()
        await pool.close()
        with self.assertRaisesRegex(ConnectionError, 'pool closed'):
            await pool.open(4, 'owned-test')

    async def test_concurrent_upload_and_recovery_cannot_exceed_channel_request_bound(self):
        release = asyncio.Event()
        async def handler(request):
            await release.wait()
            return httpx.Response(200, content=request.content, extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        for _ in range(MAX_CHANNEL_REQUESTS - 1):
            await channel.send(b'k' * 40, False)
        first = asyncio.create_task(channel.send(b'a' * 40, False))
        second = asyncio.create_task(channel.send(b'b' * 40, False))
        try:
            await asyncio.wait_for(first, 1)
            await asyncio.sleep(.02)
            self.assertEqual(len(channel.pending), MAX_CHANNEL_REQUESTS)
            self.assertFalse(second.done())
            release.set()
            await asyncio.wait_for(second, 1)
        finally:
            release.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_failing_preflight_returns_to_ws_without_consuming_native_data(self):
        pool = CfH2Pool()
        balancer = _Balancer()
        balancer.update_domains_list(['example.org'])
        async def preflight(lane):
            raise OSError('owned connection failure')
        try:
            with patch('proxy.cf_h2.balancer', balancer), patch.object(_HttpLane, '_preflight', preflight):
                self.assertIsNone(await pool.open(4, 'owned-test'))
            self.assertFalse(pool.lanes)
        finally:
            await pool.close()

    async def test_hanging_setup_returns_to_ws_and_cleans_up_lane(self):
        pool = CfH2Pool()
        balancer = _Balancer()
        balancer.update_domains_list(['example.org'])
        lanes = []

        async def preflight(lane):
            lanes.append(lane)
            await asyncio.Event().wait()

        try:
            with patch('proxy.cf_h2.balancer', balancer), \
                    patch('proxy.cf_h2.SETUP_TIMEOUT', .02), \
                    patch.object(_HttpLane, '_preflight', preflight):
                self.assertIsNone(await pool.open(4, 'owned-test'))
                self.assertIsNone(await pool.open(4, 'owned-test'))
            self.assertEqual(len(lanes), 1)
            self.assertTrue(lanes[0].closed)
            self.assertTrue(lanes[0].client.is_closed)
            self.assertFalse(pool.lanes)
        finally:
            await pool.close()

    async def test_recovery_does_not_wait_for_channel_or_lane_capacity(self):
        channel = self._channel()
        body = b'k' * 40
        channel.replay_history.append(_ReplayPacket(0, body, 0))
        with patch('proxy.cf_h2.MAX_CHANNEL_REQUESTS', 0):
            self.assertFalse(await asyncio.wait_for(channel.send(body, False, replay=True), .1))
        with patch('proxy.cf_h2.MAX_LANE_REQUESTS', 0):
            self.assertFalse(await asyncio.wait_for(channel.send(body, False, replay=True), .1))
        self.assertFalse(channel.send_lock.locked())
        self.assertFalse(channel.replay_history[0].replayed)
        self.assertEqual(channel.requests, 0)
        self.assertEqual(self.lane.inflight, 0)

    async def test_recovery_does_not_queue_behind_native_upload(self):
        channel = self._channel()
        async with channel.send_lock:
            self.assertFalse(await asyncio.wait_for(channel.send(b'k' * 40, False, replay=True), .1))
        self.assertEqual(channel.requests, 0)

    async def test_wait_diagnostics_combine_channels_and_are_rate_limited(self):
        async def handler(request):
            await asyncio.Event().wait()
        self._transport(handler)
        now = time.monotonic()
        for _ in range(2):
            channel = self._channel()
            await channel.send(b'k' * 40, False)
            channel.last_progress = now - 4
            channel.pending_since = dict.fromkeys(channel.pending, now - 4)
        with self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            for _ in range(20):
                self.lane.log_wait(now)
        self.assertEqual(len(captured.records), 1)
        self.assertIn('H2 WAIT', captured.output[0])
        self.assertIn('affected=2', captured.output[0])
        self.assertIn('channel=1', captured.output[0])
        self.assertIn('channel=2', captured.output[0])
        self.assertIn('recovery_left=', captured.output[0])

    async def test_new_request_after_idle_does_not_log_a_stall(self):
        async def handler(request):
            await asyncio.Event().wait()
        self._transport(handler)
        channel = self._channel()
        await channel.send(b'k' * 40, False)
        now = time.monotonic()
        channel.last_progress = now - 20
        with patch('proxy.cf_h2.log.isEnabledFor', return_value=True), \
                patch('proxy.cf_h2.log.debug') as debug:
            self.lane.log_wait(now)
        debug.assert_not_called()

    async def test_wait_diagnostics_include_old_request_while_other_replies_progress(self):
        async def handler(request):
            await asyncio.Event().wait()
        self._transport(handler)
        channel = self._channel()
        await channel.send(b'k' * 40, False)
        now = time.monotonic()
        channel.pending_since = dict.fromkeys(channel.pending, now - 4)
        channel.last_progress = now
        with self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            self.lane.log_wait(now)
        self.assertEqual(len(captured.records), 1)
        self.assertIn('waiting_headers=1', captured.output[0])
        self.assertIn('idle_ms=0', captured.output[0])

    async def test_slow_response_diagnostics_are_rate_limited(self):
        self._transport(lambda request: httpx.Response(
            200, content=b'r' * 40, extensions={'http_version': b'HTTP/2'}))
        with patch('proxy.cf_h2.SLOW_RESPONSE_SECONDS', 0), \
                self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            for _ in range(10):
                await self.lane._post(b'k' * 40, 1)
        self.assertEqual(len(captured.records), 1)
        self.assertIn('H2 SLOW', captured.output[0])
        self.assertIn('headers_ms=', captured.output[0])
        self.assertIn('body_ms=', captured.output[0])

    async def test_pending_details_link_replay_to_original_and_bound_history(self):
        calls = 0
        original_waiting = asyncio.Event()
        async def handler(request):
            nonlocal calls
            calls += 1
            if calls == 1:
                original_waiting.set()
                await asyncio.Event().wait()
            return httpx.Response(200, content=b'', extensions={'http_version': b'HTTP/2'})
        self._transport(handler)
        channel = self._channel()
        with self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            await channel.send(b'samekey!' + b'p' * 32, False)
            await original_waiting.wait()
            original = next(iter(channel.pending))
            with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', 0):
                await channel.send(b'samekey!' + b'p' * 32, False, replay=True)
            await asyncio.gather(*(task for task in channel.pending if task is not original))
            trace = channel.request_traces[original]
            replay = channel.recent_traces[-1]
            self.assertEqual(replay.packet, trace.packet)
            self.assertNotEqual(replay.request, trace.request)
            self.assertTrue(replay.replay)
            now = time.monotonic()
            trace.started = now - 4
            channel.pending_since[original] = now - 4
            self.lane.log_wait(now)
            for index in range(24):
                await channel.send(b'samekey!' + bytes([index]) * 32, False)
                await asyncio.gather(*(task for task in channel.pending if task is not original))
            self.assertEqual(len(channel.recent_traces), 16)
            self.assertEqual(len(channel.request_traces), 1)
        output = '\n'.join(captured.output)
        self.assertIn('H2 DETAIL', output)
        self.assertIn('pkt=1', output)
        self.assertIn('replay=1 phase=complete', output)
        self.assertNotIn('samekey!', output)
