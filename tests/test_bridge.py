import os
import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from proxy._aes import Cipher, algorithms, modes
from proxy.bridge import MsgSplitter, bridge_ws_reencrypt
from proxy.network_debug import WsActivity, log_ws_flow
from proxy.utils import (
    PROTO_ABRIDGED_INT,
    PROTO_INTERMEDIATE_INT,
    PROTO_PADDED_INTERMEDIATE_INT,
)


def _relay_init() -> bytes:
    return os.urandom(64)


def _encryptor(relay_init: bytes):
    enc = Cipher(
        algorithms.AES(relay_init[8:40]), modes.CTR(relay_init[40:56])
    ).encryptor()
    enc.update(b'\x00' * 64)
    return enc


def _abridged(payload: bytes) -> bytes:
    words = len(payload) // 4
    if words < 0x7F:
        return bytes([words]) + payload
    return b'\x7f' + words.to_bytes(3, 'little') + payload


def _intermediate(payload: bytes) -> bytes:
    return len(payload).to_bytes(4, 'little') + payload


class MsgSplitterTest(unittest.TestCase):
    def _split(self, proto_int, packets, chunk_sizes=None):
        relay_init = _relay_init()
        splitter = MsgSplitter(proto_int)
        enc = _encryptor(relay_init)
        plain = b''.join(packets)
        stream = enc.update(plain)

        bounds = []
        if chunk_sizes is None:
            bounds = [(0, len(stream))]
        else:
            offset = 0
            for size in chunk_sizes:
                bounds.append((offset, offset + size))
                offset += size
            if offset < len(stream):
                bounds.append((offset, len(stream)))

        parts = []
        for a, b in bounds:
            parts.extend(splitter.split(stream[a:b], plain[a:b]))
        return splitter, stream, parts

    def test_abridged_stream_splits_into_packets(self):
        packets = [_abridged(b'a' * 4), _abridged(b'b' * 16), _abridged(b'c' * 40)]
        _, stream, parts = self._split(PROTO_ABRIDGED_INT, packets)
        self.assertEqual(len(parts), 3)
        self.assertEqual(b''.join(parts), stream)
        self.assertEqual([len(p) for p in parts], [5, 17, 41])

    def test_intermediate_stream_splits_into_packets(self):
        packets = [_intermediate(b'a' * 8), _intermediate(b'b' * 12)]
        _, stream, parts = self._split(PROTO_INTERMEDIATE_INT, packets)
        self.assertEqual(len(parts), 2)
        self.assertEqual(b''.join(parts), stream)

    def test_padded_intermediate_uses_intermediate_framing(self):
        packets = [_intermediate(b'z' * 20)]
        _, stream, parts = self._split(PROTO_PADDED_INTERMEDIATE_INT, packets)
        self.assertEqual(parts, [stream])

    def test_partial_packet_is_buffered_until_complete(self):
        packets = [_abridged(b'a' * 20)]
        _, stream, parts = self._split(
            PROTO_ABRIDGED_INT, packets, chunk_sizes=[1] * (len(packets[0]) - 1)
        )
        self.assertEqual(parts, [stream])

    def test_split_preserves_stream_across_arbitrary_chunking(self):
        packets = [_intermediate(bytes([i]) * 16) for i in range(8)]
        _, stream, parts = self._split(
            PROTO_INTERMEDIATE_INT, packets, chunk_sizes=[7, 3, 50, 11]
        )
        self.assertEqual(b''.join(parts), stream)
        self.assertEqual(len(parts), 8)

    def test_empty_chunk_yields_nothing(self):
        splitter = MsgSplitter(PROTO_INTERMEDIATE_INT)
        self.assertEqual(splitter.split(b'', b''), [])

    def test_zero_length_packet_disables_splitting(self):
        relay_init = _relay_init()
        splitter = MsgSplitter(PROTO_INTERMEDIATE_INT)
        enc = _encryptor(relay_init)
        plain = (0).to_bytes(4, 'little') + b'tail'
        stream = enc.update(plain)
        parts = splitter.split(stream, plain)
        self.assertEqual(parts, [stream])
        self.assertEqual(splitter.split(b'raw', b'raw'), [b'raw'])

    def test_flush_returns_buffered_tail_once(self):
        relay_init = _relay_init()
        splitter = MsgSplitter(PROTO_INTERMEDIATE_INT)
        enc = _encryptor(relay_init)
        plain = _intermediate(b'x' * 32)[:10]
        partial = enc.update(plain)
        self.assertEqual(splitter.split(partial, plain), [])
        self.assertEqual(splitter.flush(), [partial])
        self.assertEqual(splitter.flush(), [])

    def test_extended_and_quick_ack_headers_can_cross_chunk_boundaries(self):
        for proto, frame in ((PROTO_ABRIDGED_INT, _abridged),
                             (PROTO_INTERMEDIATE_INT, _intermediate),
                             (PROTO_PADDED_INTERMEDIATE_INT, _intermediate)):
            for size in (16, 512, 65536):
                packet = bytearray(frame(b'p' * size))
                packet[0 if proto == PROTO_ABRIDGED_INT else 3] |= 0x80
                packets = [bytes(packet), frame(b't' * 8)]
                for header_split in (1, 2, 3):
                    with self.subTest(proto=proto, size=size, header_split=header_split):
                        _, stream, parts = self._split(
                            proto, packets, chunk_sizes=[header_split, 1, 11, size - 8])
                        self.assertEqual(parts, [stream[:len(packet)], stream[len(packet):]])


class WsReencryptTest(unittest.IsolatedAsyncioTestCase):
    async def test_relay_preserves_encrypted_packets_and_incomplete_tail(self):
        for proto, frame in ((PROTO_ABRIDGED_INT, _abridged),
                             (PROTO_INTERMEDIATE_INT, _intermediate),
                             (PROTO_PADDED_INTERMEDIATE_INT, _intermediate)):
            with self.subTest(proto=proto):
                packets = [frame(b'a' * 8), frame(b'b' * 16),
                           frame(b'c' * 70000), frame(b'd' * 32)[:7]]
                client_init, relay_init = _relay_init(), _relay_init()
                reader = asyncio.StreamReader()
                reader.feed_data(_encryptor(client_init).update(b''.join(packets)))
                reader.feed_eof()
                sent = []

                async def send(data):
                    sent.append(data)

                async def send_batch(parts):
                    sent.extend(parts)

                async def recv():
                    await asyncio.Future()

                ws = SimpleNamespace(send=send, send_batch=send_batch, recv=recv,
                                     close=AsyncMock())
                writer = Mock(wait_closed=AsyncMock())
                ctx = SimpleNamespace(clt_dec=_encryptor(client_init), tg_enc=_encryptor(relay_init))
                await asyncio.wait_for(bridge_ws_reencrypt(
                    reader, writer, ws, 'framing-test', ctx, splitter=MsgSplitter(proto)), 1)
                decoder = _encryptor(relay_init)
                self.assertEqual([decoder.update(part) for part in sent], packets)
                ws.close.assert_awaited_once()


class WsDiagnosticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_flow_distinguishes_upstream_send_from_native_backpressure(self):
        class Cipher:
            def update(self, data):
                return data

        class Socket:
            def __init__(self):
                self.queue = asyncio.Queue()
                self.sending = asyncio.Event()
                self.release = asyncio.Event()
                self.sent = []

            async def send(self, data):
                self.sent.append(data)
                self.sending.set()
                await self.release.wait()

            async def recv(self):
                return await self.queue.get()

            async def close(self):
                pass

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.written = asyncio.Event()
                self.release = asyncio.Event()

            def write(self, data):
                self.data.extend(data)
                self.written.set()

            async def drain(self):
                await self.release.wait()

            def close(self):
                pass

            async def wait_closed(self):
                pass

        ws, writer = Socket(), Writer()
        reader = asyncio.StreamReader()
        ctx = SimpleNamespace(clt_dec=Cipher(), clt_enc=Cipher(), tg_dec=Cipher(), tg_enc=Cipher())
        with self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            bridge = asyncio.create_task(bridge_ws_reencrypt(reader, writer, ws, 'owned-test', ctx, dc=203))
            try:
                reader.feed_data(b'private-native-payload')
                await asyncio.wait_for(ws.sending.wait(), 1)
                activity = next(item for item in WsActivity.active if item.label == 'owned-test')
                self.assertIsNotNone(activity.sending)
                self.assertEqual(activity.ws_up, 0)
                log_ws_flow(time.monotonic())
                # A reply arriving before send drain finishes must not create
                # a spurious wait for another response when drain resumes.
                await ws.queue.put(b'private-upstream-payload')
                await asyncio.wait_for(writer.written.wait(), 1)
                self.assertEqual(activity.ws_down, len(writer.data))
                self.assertEqual(activity.native_down, 0)
                self.assertIsNotNone(activity.writing_native)
                log_ws_flow(time.monotonic())
                ws.release.set()
                writer.release.set()
                for _ in range(100):
                    if activity.native_down and activity.ws_up:
                        break
                    await asyncio.sleep(.001)
                self.assertEqual(activity.native_down, len(writer.data))
                self.assertIsNone(activity.awaiting_rx)
                self.assertEqual(ws.sent, [b'private-native-payload'])
                self.assertEqual(bytes(writer.data), b'private-upstream-payload')
                reader.feed_eof()
                await asyncio.wait_for(bridge, 1)
            finally:
                bridge.cancel()
                await asyncio.gather(bridge, return_exceptions=True)
        self.assertFalse(WsActivity.active)
        output = '\n'.join(captured.output)
        self.assertIn('WS FLOW DC203', output)
        self.assertIn('WS END DC203', output)
        self.assertNotIn('private-', output)


if __name__ == '__main__':
    unittest.main()
