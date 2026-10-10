"""Wire-level regressions: real TLS sockets, ALPN and HTTP/2 flow control."""
import asyncio
import datetime
import hashlib
import ipaddress
import os
import ssl
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded, StreamReset
from h2.settings import SettingCodes

from proxy.h2_transport import H2Transport, STREAM_RECEIVE_WINDOW, _Connection
from proxy.cf_h2 import _HttpChannel, _HttpLane, bridge_h2
from proxy import tg_ws_proxy
from proxy._aes import Cipher, algorithms, modes
from proxy.config import proxy_config
from proxy.utils import PROTO_TAG_ABRIDGED, PROTO_TAG_INTERMEDIATE, PROTO_TAG_SECURE


class H2ContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_lanes_reuse_verified_context_without_changing_websocket_contexts(self):
        from proxy.h2_transport import _default_ssl_context
        from proxy.raw_websocket import _ssl_ctx, _ssl_ctx_fronting
        from proxy.utils import create_ssl_context

        _default_ssl_context.cache_clear()
        self.addCleanup(_default_ssl_context.cache_clear)
        lanes = []
        try:
            with patch('proxy.h2_transport.create_ssl_context', wraps=create_ssl_context) as create:
                lanes = [_HttpLane('kws4.example.org', 1), _HttpLane('kws2.example.net', 2)]
                create.assert_called_once_with()
            first, second = (lane.client._transport.ssl_context for lane in lanes)
            self.assertIs(first, second)
            self.assertIsNot(first, _ssl_ctx)
            self.assertIsNot(first, _ssl_ctx_fronting)
            self.assertTrue(first.check_hostname)
            self.assertEqual(first.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(_ssl_ctx.check_hostname)
            self.assertFalse(_ssl_ctx_fronting.check_hostname)
            self.assertEqual(set(first.get_ca_certs(binary_form=True)),
                             set(_ssl_ctx.get_ca_certs(binary_form=True)))

            custom = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            custom.check_hostname = False
            transport = H2Transport(custom)
            self.assertIs(transport.ssl_context, custom)
            self.assertTrue(first.check_hostname)
            await transport.aclose()
        finally:
            await asyncio.gather(*(lane.close() for lane in lanes))


class H2WireTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        cls.folder = tempfile.TemporaryDirectory(prefix='tg-h2-tests-')
        cls.addClassCleanup(cls.folder.cleanup)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([
                    x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        cls.cert_path = Path(cls.folder.name) / 'cert.pem'
        key_path = Path(cls.folder.name) / 'key.pem'
        cls.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                              serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        cls.server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_ssl.load_cert_chain(str(cls.cert_path), str(key_path))
        cls.server_ssl.set_alpn_protocols(['h2'])

    async def asyncSetUp(self):
        self.requests = asyncio.Queue()
        self.resets = asyncio.Queue()
        self.connections = []
        self.handlers = []
        self.tasks = []
        self.server_max_streams = 64
        self.server = await asyncio.start_server(self.serve, '127.0.0.1', 0, ssl=self.server_ssl)
        port = self.server.sockets[0].getsockname()[1]
        self.lane = _HttpLane('127.0.0.1:%d' % port, 1)
        await self.lane.client.aclose()
        self.transport = H2Transport(ssl.create_default_context(cafile=str(self.cert_path)))
        self.lane.client = httpx.AsyncClient(transport=self.transport, trust_env=False,
                                           timeout=httpx.Timeout(2, read=1))

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.lane.close()
        self.server.close()
        await self.server.wait_closed()
        for connection in self.connections:
            connection.writer.close()
        await asyncio.wait_for(asyncio.gather(*self.handlers), 3)

    async def serve(self, reader, writer):
        self.handlers.append(asyncio.current_task())
        conn = SimpleNamespace(h2=H2Connection(config=H2Configuration(client_side=False)),
                               writer=writer, bodies={}, pending={})
        self.connections.append(conn)
        conn.h2.initiate_connection()
        conn.h2.update_settings({SettingCodes.MAX_CONCURRENT_STREAMS: self.server_max_streams})
        writer.write(conn.h2.data_to_send())
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                for event in conn.h2.receive_data(data):
                    if isinstance(event, RequestReceived):
                        conn.bodies[event.stream_id] = bytearray()
                    elif isinstance(event, DataReceived):
                        conn.bodies[event.stream_id].extend(event.data)
                        conn.h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    elif isinstance(event, StreamEnded):
                        await self.requests.put((conn, event.stream_id, bytes(conn.bodies[event.stream_id])))
                    elif isinstance(event, StreamReset):
                        conn.pending.pop(event.stream_id, None)
                        await self.resets.put((event.stream_id, event.error_code))
                self.flush(conn)
                await writer.drain()
        except (ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, ssl.SSLError):
                pass

    def flush(self, conn):
        for sid, (body, end) in list(conn.pending.items()):
            while body:
                count = min(len(body), conn.h2.max_outbound_frame_size,
                            conn.h2.local_flow_control_window(sid))
                if count <= 0:
                    break
                chunk, body = body[:count], body[count:]
                conn.h2.send_data(sid, chunk, end_stream=end and not body)
            if body:
                conn.pending[sid] = (body, end)
            else:
                del conn.pending[sid]
        conn.writer.write(conn.h2.data_to_send())

    def respond(self, request, body=b'r' * 40, *, end=True, length=None, status=200):
        conn, sid, _ = request
        conn.h2.send_headers(sid, [(b':status', str(status).encode()),
                                   (b'content-type', b'application/octet-stream'),
                                   (b'content-length', str(len(body) if length is None else length).encode())],
                             end_stream=end and not body)
        if body:
            conn.pending[sid] = (body, end)
        self.flush(conn)

    async def post(self, body=b'x' * 40):
        task = asyncio.create_task(self.lane._post(body, len(self.tasks) + 1))
        self.tasks.append(task)
        request = await asyncio.wait_for(self.requests.get(), 2)
        self.assertEqual(request[2], body)
        return task, request

    async def test_fast_response_does_not_wait_for_unrelated_long_polls(self):
        slow1, request1 = await self.post()
        slow2, request2 = await self.post()
        fast, request3 = await self.post()
        self.respond(request3)
        self.assertEqual(await asyncio.wait_for(fast, .5), b'r' * 40)
        self.assertFalse(slow1.done())
        self.assertFalse(slow2.done())
        self.respond(request1)
        self.respond(request2)
        await asyncio.gather(slow1, slow2)
        self.assertEqual(len(self.connections), 1)

    async def test_shared_default_context_rejects_untrusted_certificate(self):
        async with httpx.AsyncClient(transport=H2Transport(), trust_env=False) as client:
            with self.assertRaises(httpx.ConnectError):
                await client.get('https://' + self.lane.host + '/api')
        task, request = await self.post()
        self.respond(request)
        self.assertEqual(await asyncio.wait_for(task, 1), b'r' * 40)

    async def test_debug_trace_follows_real_stream_without_logging_payload(self):
        channel = _HttpChannel(self.lane, 1, 'trace-test')
        body = b'private-payload-' + b'x' * 24
        with self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            await channel.send(body, False)
            request = await asyncio.wait_for(self.requests.get(), 1)
            trace = next(iter(channel.request_traces.values()))

            async def phase(expected):
                while trace.phase != expected:
                    await asyncio.sleep(.001)

            await asyncio.wait_for(phase('headers'), .5)
            self.assertEqual(trace.stream, request[1])
            self.assertEqual(trace.packet, 1)
            self.assertEqual(trace.request, 1)
            self.assertIsNotNone(trace.sent_at)
            self.assertIsNone(trace.headers_at)
            self.respond(request, b'r' * 20, end=False, length=40)
            await asyncio.wait_for(phase('body'), .5)
            self.assertIsNotNone(trace.headers_at)
            conn, sid, _ = request
            conn.h2.send_data(sid, b'r' * 20, end_stream=True)
            self.flush(conn)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'r' * 40)
            await asyncio.gather(*list(channel.pending))
            self.assertEqual(trace.received, 40)
            self.assertEqual(trace.phase, 'complete')
            self.assertFalse(channel.request_traces)
            self.assertIs(channel.recent_traces[-1], trace)
            self.lane.log_flow(trace.finished_at)
            await channel.close()
        output = '\n'.join(captured.output)
        self.assertIn('http_down=40', output)
        self.assertIn('small=1 large=0', output)
        self.assertNotIn('private-payload', output)
        self.assertFalse(channel.recent_traces)

    async def test_stale_recovery_slot_replaced_without_resetting_original_request(self):
        channel = _HttpChannel(self.lane, 1, 'recovery-wire-test')
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(3)]
        originals = []
        for body in bodies[:2]:
            await channel.send(body, False)
            originals.append(await asyncio.wait_for(self.requests.get(), 1))

        with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_SLOT_SECONDS', .1):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            old_poll = await asyncio.wait_for(self.requests.get(), 1)
            other_poll = await asyncio.wait_for(self.requests.get(), 1)
            self.assertEqual([old_poll[2], other_poll[2]], bodies[:2])

            # The two duplicate requests remain silent. A new native request
            # also gets no reply until its own duplicate solicits one.
            await channel.send(bodies[2], False)
            original = await asyncio.wait_for(self.requests.get(), 1)
            probe = await asyncio.wait_for(self.requests.get(), 1)
            self.assertEqual(probe[2], bodies[2])
            reset_sid, reset_code = await asyncio.wait_for(self.resets.get(), 1)
            self.assertEqual((reset_sid, reset_code), (old_poll[1], 8))
            self.assertIn(original[1], self.transport.connection.streams)
            for request in originals:
                self.assertIn(request[1], self.transport.connection.streams)
            self.assertIn(other_poll[1], self.transport.connection.streams)
            self.assertEqual(len(channel.replay_pending), 2)
            self.respond(probe, b'r' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
            # The original stream remains usable, even after recovery.
            self.respond(original, b'o' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'o' * 40)
            self.assertEqual(channel.replay_rotations, 1)
            self.assertEqual(self.lane.failed_until, 0)
            self.assertEqual(len(self.connections), 1)

    async def test_normal_long_poll_survives_six_seconds_and_delivers_late_reply(self):
        channel = _HttpChannel(self.lane, 1, 'normal-long-poll-test')
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(3)]
        for body in bodies[:2]:
            await channel.send(body, False)
            self.respond(await asyncio.wait_for(self.requests.get(), 1))
            await asyncio.wait_for(channel.receive(), 1)
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            poll = await asyncio.wait_for(self.requests.get(), 1)
            # Advance request ages, keeping the real TLS/H2 streams open.
            # Six seconds is still inside Telegram's default 25s HTTP wait.
            for task in channel.replay_pending:
                channel.pending_since[task] = time.monotonic() - 6
                channel.sent_since[task] = time.monotonic() - 6
            await asyncio.sleep(.03)
            self.assertTrue(self.resets.empty(), 'normal HTTP wait was reset before its delayed result')
            await channel.send(bodies[2], False)
            fresh = await asyncio.wait_for(self.requests.get(), 1)
            self.assertEqual(fresh[2], bodies[2])
            self.respond(fresh, b'n' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'n' * 40)
            self.respond(poll, b'd' * (128 * 1024))
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'd' * (128 * 1024))
            self.assertEqual(channel.replay_rotations, 0)
            self.assertEqual(channel.replay_requests, 1)

    async def test_replay_rotation_does_not_interrupt_upload(self):
        channel = _HttpChannel(self.lane, 1, 'replay-upload-test')
        body = b'samekey!' + b'p' * 32
        await channel.send(body, False)
        self.respond(await asyncio.wait_for(self.requests.get(), 1))
        await asyncio.wait_for(channel.receive(), 1)
        upload_started, resume = asyncio.Event(), asyncio.Event()
        send_body = _Connection.send_body

        async def paused_send(connection, request, stream):
            upload_started.set()
            await resume.wait()
            await send_body(connection, request, stream)

        with patch.object(_Connection, 'send_body', paused_send), \
                patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            await asyncio.wait_for(upload_started.wait(), 1)
            probe = next(iter(channel.replay_pending))
            channel.pending_since[probe] = time.monotonic() - 40
            await asyncio.sleep(.03)
            self.assertFalse(probe.done())
            self.assertTrue(self.resets.empty())
            self.assertEqual(channel.replay_rotations, 0)
            resume.set()
            request = await asyncio.wait_for(self.requests.get(), 1)
            self.respond(request)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)

    async def test_recovery_deadline_starts_after_original_upload_finishes(self):
        channel = _HttpChannel(self.lane, 1, 'unfinished-upload-test')
        body = b'samekey!' + b'q' * 32
        started, resume = asyncio.Event(), asyncio.Event()
        send_body = _Connection.send_body

        async def paused_send(connection, request, stream):
            started.set()
            await resume.wait()
            await send_body(connection, request, stream)

        with patch.object(_Connection, 'send_body', paused_send), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .02), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            await channel.send(body, False)
            await asyncio.wait_for(started.wait(), .5)
            self.tasks.append(asyncio.create_task(channel._recover()))
            await asyncio.sleep(.06)
            self.assertEqual(channel.requests, 1, 'unsent original was duplicated')
            self.assertTrue(self.requests.empty())
            resume.set()
            original = await asyncio.wait_for(self.requests.get(), .5)
            probe = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(original[2], body)
            self.assertEqual(probe[2], body)
            self.respond(probe)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'r' * 40)
            self.assertTrue(self.resets.empty())

    async def test_body_tail_does_not_wait_for_unrelated_long_polls(self):
        slow1, _ = await self.post()
        slow2, _ = await self.post()
        fast, request = await self.post()
        self.respond(request, b'a' * 20, end=False, length=40)
        await asyncio.sleep(.03)
        self.assertFalse(fast.done())
        conn, sid, _ = request
        conn.h2.send_data(sid, b'b' * 20, end_stream=True)
        self.flush(conn)
        self.assertEqual(await asyncio.wait_for(fast, .5), b'a' * 20 + b'b' * 20)
        self.assertFalse(slow1.done())
        self.assertFalse(slow2.done())

    async def test_receiving_body_is_marked_before_request_coroutine_resumes(self):
        channel = _HttpChannel(self.lane, 1, 'early-headers-test')
        resume = asyncio.Event()
        send_body = _Connection.send_body

        async def paused_send(connection, request, stream):
            await send_body(connection, request, stream)
            await resume.wait()

        with patch.object(_Connection, 'send_body', paused_send):
            await channel.send(b'k' * 40, False)
            request = await asyncio.wait_for(self.requests.get(), 1)
            self.respond(request, b'a' * 20, end=False, length=40)

            async def headers_received():
                while channel.receiving_http == 0:
                    await asyncio.sleep(.005)
            try:
                await asyncio.wait_for(headers_received(), .5)
                self.assertEqual(channel.receiving_http, 1)
                self.assertTrue(channel.queue.empty())
            finally:
                resume.set()
            conn, sid, _ = request
            conn.h2.send_data(sid, b'b' * 20, end_stream=True)
            self.flush(conn)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'a' * 20 + b'b' * 20)
            self.assertEqual(channel.receiving_http, 0)

    async def test_native_channel_delivers_ready_reply_while_large_body_is_pending(self):
        class IdentityCipher:
            def update(self, data):
                return data

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.changed = asyncio.Event()

            def write(self, data):
                self.data.extend(data)
                self.changed.set()

            async def drain(self):
                pass

        reader, writer = asyncio.StreamReader(), Writer()
        channel = _HttpChannel(self.lane, 1, 'wire-test')
        ctx = SimpleNamespace(clt_enc=IdentityCipher(), clt_dec=IdentityCipher())
        bridge = asyncio.create_task(bridge_h2(reader, writer, channel, ctx, PROTO_TAG_INTERMEDIATE))
        self.tasks.append(bridge)
        reader.feed_data(struct.pack('<I', 40) + b'x' * 40)
        slow = await asyncio.wait_for(self.requests.get(), 2)
        self.respond(slow, b'a' * 65536, end=False, length=65576)
        reader.feed_data(struct.pack('<I', 40) + b'y' * 40)
        fast = await asyncio.wait_for(self.requests.get(), 2)
        self.respond(fast, b'f' * 40)
        await asyncio.wait_for(writer.changed.wait(), .5)
        self.assertEqual(writer.data, struct.pack('<I', 40) + b'f' * 40)
        writer.changed.clear()
        conn, sid, _ = slow
        # Finish after the first 64 KiB have passed through flow control.
        for _ in range(100):
            if sid not in conn.pending:
                break
            await asyncio.sleep(.001)
        self.assertNotIn(sid, conn.pending)
        conn.pending[sid] = (b'b' * 40, True)
        self.flush(conn)
        await asyncio.wait_for(writer.changed.wait(), .5)
        self.assertEqual(writer.data, struct.pack('<I', 40) + b'f' * 40
                         + struct.pack('<I', 65576) + b'a' * 65536 + b'b' * 40)

    async def test_parallel_first_windows_do_not_wait_for_connection_credit(self):
        first, request1 = await self.post()
        second, request2 = await self.post()
        self.respond(request1, b'a' * 65532)
        self.respond(request2, b'b' * 65532)
        # No event-loop yield between responses: the server has not received
        # any WINDOW_UPDATE credit for either body yet.
        self.assertFalse(request1[0].pending)
        self.assertEqual(await first, b'a' * 65532)
        self.assertEqual(await second, b'b' * 65532)

    async def test_http_404_is_forwarded_while_other_native_channel_keeps_working(self):
        class Cipher:
            def update(self, data):
                return data

        class Writer:
            def __init__(self):
                self.data = bytearray()
                self.changed = asyncio.Event()

            def write(self, data):
                self.data.extend(data)

            async def drain(self):
                self.changed.set()

        channel = _HttpChannel(self.lane, 1, 'bad-key-test')
        reader, writer = asyncio.StreamReader(), Writer()
        ctx = SimpleNamespace(clt_dec=Cipher(), clt_enc=Cipher())
        bridge = asyncio.create_task(bridge_h2(reader, writer, channel, ctx, PROTO_TAG_INTERMEDIATE))
        self.tasks.append(bridge)
        reader.feed_data(struct.pack('<I', 40) + b'\x00' * 40)
        self.respond(await asyncio.wait_for(self.requests.get(), 1), b'p' * 84)
        await asyncio.wait_for(writer.changed.wait(), 1)
        reader.feed_data((struct.pack('<I', 40) + b'k' * 40) * 2)
        bad = await asyncio.wait_for(self.requests.get(), 1)
        pending = await asyncio.wait_for(self.requests.get(), 1)
        healthy = _HttpChannel(self.lane, 2, 'healthy-key-test')
        await healthy.send(b'h' * 40, False)
        good = await asyncio.wait_for(self.requests.get(), 1)
        self.respond(bad, b'<html>not found</html>', status=404, end=False)
        await asyncio.wait_for(bridge, 1)
        self.assertEqual(writer.data, struct.pack('<I', 84) + b'p' * 84
                         + struct.pack('<Ii', 4, -404))
        self.assertTrue(channel.close_done)
        self.assertFalse(channel.pending)
        self.assertNotIn(pending[1], self.transport.connection.streams)
        self.assertFalse(healthy.closed)
        self.respond(good, b'g' * 40)
        self.assertEqual(await asyncio.wait_for(healthy.receive(), 1), b'g' * 40)
        recovered = _HttpChannel(self.lane, 3, 'replacement-key-test')
        await recovered.send(b'n' * 40, False)
        self.respond(await asyncio.wait_for(self.requests.get(), 1), b'r' * 40)
        self.assertEqual(await asyncio.wait_for(recovered.receive(), 1), b'r' * 40)
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.lane.failed_until, 0)

    async def test_binary_transport_error_is_terminal_without_domain_cooldown(self):
        channel = _HttpChannel(self.lane, 1, 'binary-error-test')
        await channel.send(b'k' * 40, False)
        self.respond(await asyncio.wait_for(self.requests.get(), 1), struct.pack('<i', -404))
        await asyncio.gather(*list(channel.pending))
        self.assertTrue(channel.closed)
        self.assertEqual(channel.transport_error, -404)
        self.assertEqual(self.lane.failed_until, 0)
        self.assertEqual(self.lane.reply_buffer_bytes, 0)
        await channel.close()

    async def test_obfuscated_clients_preserve_packets_through_full_h2_fallback(self):
        secret = os.urandom(16)
        channels = []
        clients = []
        handlers = []

        async def open_channel(dc, label):
            self.assertEqual(dc, 2)
            channel = _HttpChannel(self.lane, len(channels) + 1, label)
            channels.append(channel)
            return channel

        def accept(reader, writer):
            task = asyncio.create_task(tg_ws_proxy._handle_client(reader, writer, secret))
            handlers.append(task)
            self.tasks.append(task)

        def client_init(tag):
            raw = bytearray(os.urandom(64))
            raw[0] = 0x42
            raw[56:62] = tag + struct.pack('<h', -2)
            keys = bytes(raw[8:56])
            up = Cipher(algorithms.AES(hashlib.sha256(keys[:32] + secret).digest()),
                        modes.CTR(keys[32:])).encryptor()
            reverse = keys[::-1]
            down = Cipher(algorithms.AES(hashlib.sha256(reverse[:32] + secret).digest()),
                          modes.CTR(reverse[32:])).encryptor()
            encrypted = up.update(bytes(raw))
            return bytes(raw[:56]) + encrypted[56:], up, down

        def frame(body, tag, padding):
            if tag == PROTO_TAG_ABRIDGED:
                words = len(body) // 4
                prefix = (bytes([words | 0x80]) if words < 127
                          else b'\xff' + words.to_bytes(3, 'little'))
            else:
                if tag == PROTO_TAG_SECURE:
                    body += os.urandom(padding)
                prefix = struct.pack('<I', len(body) | 0x80000000)
            return prefix + body

        async def check_reply(peer, expected, tag):
            reader, _, _, down = peer

            async def read(count):
                return down.update(await asyncio.wait_for(reader.readexactly(count), 2))

            if tag == PROTO_TAG_ABRIDGED:
                words = (await read(1))[0]
                if words == 127:
                    words = int.from_bytes(await read(3), 'little')
                length = words * 4
            else:
                length, = struct.unpack('<I', await read(4))
            body = await read(length)
            self.assertEqual(body[:len(expected)], expected)
            self.assertIn(len(body) - len(expected), range(4) if tag == PROTO_TAG_SECURE else (0,))

        server = await asyncio.start_server(accept, '127.0.0.1', 0)
        pool = SimpleNamespace(open=AsyncMock(side_effect=open_channel))
        try:
            with patch.object(tg_ws_proxy, 'cf_h2_pool', pool), \
                    patch.object(tg_ws_proxy.ws_pool, 'get', AsyncMock(return_value=None)) as direct, \
                    patch.multiple(proxy_config, fallback_cfproxy=True, cfproxy_worker_domains=[],
                                   force_test_dc=False, fake_tls_domain='', proxy_protocol=False):
                for tag in (PROTO_TAG_ABRIDGED, PROTO_TAG_INTERMEDIATE, PROTO_TAG_SECURE):
                    with self.subTest(tag=tag.hex()):
                        peers = []
                        requests = {}
                        for index in range(2):
                            reader, writer = await asyncio.open_connection(
                                '127.0.0.1', server.sockets[0].getsockname()[1])
                            clients.append(writer)
                            init, up, down = client_init(tag)
                            peer = (reader, writer, up, down)
                            peers.append(peer)
                            bodies = [b'\x00' * 8 + os.urandom(8) + struct.pack('<I', 20) + os.urandom(20)]
                            bodies += [b'samekey!' + os.urandom(size - 8) for size in (40, 131112, 56)]
                            # Maximum extended plaintext padding: MTProtoKit
                            # (236 + 15) and Android (256), not just TCP's 15.
                            plain_padding = (251, 256)[index]
                            wire = init + up.update(b''.join(
                                frame(body, tag, padding)
                                for body, padding in zip(bodies, (plain_padding, 1, 7, 0))))
                            for start, end in ((0, 13), (13, 57), (57, 65), (65, 78), (78, len(wire))):
                                writer.write(wire[start:end])
                                await writer.drain()
                                await asyncio.sleep(0)
                            expected = {body: packet for packet, body in enumerate(bodies)}
                            for _ in bodies:
                                request = await asyncio.wait_for(self.requests.get(), 2)
                                self.assertTrue(request[2] in expected, 'HTTP body differs from native input')
                                packet = expected.pop(request[2])
                                requests[index, packet] = request

                        for index, packet in ((1, 2), (0, 2), (0, 0), (1, 0), (0, 1)):
                            reply = bytes([0x30 + index, packet]) * 36
                            self.respond(requests[index, packet], reply)
                            await check_reply(peers[index], reply, tag)
                        self.respond(requests[0, 3], b'not found', status=404)
                        await check_reply(peers[0], struct.pack('<i', -404), tag)
                        self.assertEqual(await asyncio.wait_for(peers[0][0].read(), 2), b'')
                        self.respond(requests[1, 1], b'healthy!' * 9)
                        await check_reply(peers[1], b'healthy!' * 9, tag)
                        self.respond(requests[1, 3], struct.pack('<i', -429))
                        await check_reply(peers[1], struct.pack('<i', -429), tag)
                        self.assertEqual(await asyncio.wait_for(peers[1][0].read(), 2), b'')
                await asyncio.wait_for(asyncio.gather(*handlers), 2)
                self.assertEqual(direct.await_count, 6)
                for call in direct.await_args_list:
                    self.assertEqual(call.args, (2, True))
                    self.assertEqual(call.kwargs, {'is_test_dc': False})
                self.assertEqual(pool.open.await_count, 6)
                self.assertTrue(all(channel.close_done and not channel.pending for channel in channels))
                self.assertTrue(all(channel.quick_requests == 4 for channel in channels))
                self.assertEqual(len(self.connections), 1)
                self.assertEqual(self.lane.failed_until, 0)
        finally:
            for writer in clients:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()

    async def test_file_chunk_fits_first_window_without_extra_network_round_trip(self):
        # A 128 KiB file part plus its MTProto envelope must fit before any
        # WINDOW_UPDATE arrives. This matters even when the consumer is fast.
        body = b'f' * (128 * 1024 + 168)
        task, request = await self.post()
        self.respond(request, body)
        self.assertNotIn(request[1], list(request[0].pending))
        self.assertEqual(await asyncio.wait_for(task, 1), body)

    async def test_stalled_body_does_not_block_recovery_of_another_native_request(self):
        channel = _HttpChannel(self.lane, 1, 'stalled-body-test')
        await channel.send(b'k' * 8 + b'a' * 32, False)
        slow = await asyncio.wait_for(self.requests.get(), 1)
        self.respond(slow, b'a' * 20, end=False, length=40)
        fresh_body = b'k' * 8 + b'b' * 32
        await channel.send(fresh_body, False)
        original = await asyncio.wait_for(self.requests.get(), 1)
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .05):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            probe = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(probe[2], fresh_body)
            self.respond(probe, b'r' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'r' * 40)
            self.assertIn(slow[1], self.transport.connection.streams)
            self.assertIn(original[1], self.transport.connection.streams)
            self.assertTrue(self.resets.empty())
            conn, sid, _ = slow
            conn.h2.send_data(sid, b'a' * 20, end_stream=True)
            self.flush(conn)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'a' * 40)
            self.assertEqual(self.lane.failed_until, 0)

    async def test_first_recovery_without_result_does_not_end_recovery(self):
        channel = _HttpChannel(self.lane, 1, 'second-recovery-test')
        body = b'k' * 40
        await channel.send(body, False)
        self.respond(await asyncio.wait_for(self.requests.get(), 1), b'')
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .02):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            first = await asyncio.wait_for(self.requests.get(), .5)
            self.respond(first, b'')
            second = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(first[2], body)
            self.assertEqual(second[2], body)
            self.respond(second, b'r' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'r' * 40)
            self.assertEqual(channel.replay_requests, 2)
            self.assertEqual(len(self.connections), 1)

    async def test_completed_http_ack_does_not_add_idle_delay_to_each_chunk(self):
        channel = _HttpChannel(self.lane, 1, 'chunked-download-poll-test')
        # An HTTP reply can be only an MTProto acknowledgement. The queued
        # file chunk needs another HTTP receiver, with no new native upload.
        # Keep the production 1s idle threshold; shorten only scheduler ticks.
        with patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            self.tasks.append(asyncio.create_task(channel._recover()))
            for index in range(4):
                body = b'samekey!' + bytes([index]) * 32
                await channel.send(body, False)
                original = await asyncio.wait_for(self.requests.get(), 1)
                self.assertEqual(original[2], body)
                self.respond(original, b'a' * 40)
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'a' * 40)
                poll = await asyncio.wait_for(self.requests.get(), .5)
                self.assertEqual(poll[2], body)
                reply = bytes([index]) * (128 * 1024)
                self.respond(poll, reply)
                self.assertEqual(await asyncio.wait_for(channel.receive(), 1), reply)
            self.assertTrue(self.resets.empty())
            self.assertEqual(len(self.connections), 1)

    async def test_drained_receiver_wakes_first_poll_without_waiting_for_timer(self):
        channel = _HttpChannel(self.lane, 1, 'receiver-wakeup-test')
        body = b'samekey!' + b'q' * 32
        with patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', 10):
            self.tasks.append(asyncio.create_task(channel._recover()))
            await channel.send(body, False)
            original = await asyncio.wait_for(self.requests.get(), 1)
            self.respond(original, b'a' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'a' * 40)
            poll = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(poll[2], body)
            self.respond(poll, b'f' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'f' * 40)
            await asyncio.sleep(.03)
            self.assertTrue(self.requests.empty(), 'a wakeup bypassed the retry backoff')
            self.assertEqual(channel.replay_requests, 1)

    async def test_native_write_completion_wakes_poll_but_backpressure_does_not(self):
        class IdentityCipher:
            def update(self, data):
                return data

        class Writer:
            def __init__(self):
                self.started, self.resume = asyncio.Event(), asyncio.Event()

            def write(self, data):
                self.started.set()

            async def drain(self):
                await self.resume.wait()

        channel = _HttpChannel(self.lane, 1, 'native-wakeup-test')
        reader, writer = asyncio.StreamReader(), Writer()
        ctx = SimpleNamespace(clt_enc=IdentityCipher(), clt_dec=IdentityCipher())
        body = b'samekey!' + b'q' * 32
        with patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', 10):
            self.tasks.append(asyncio.create_task(
                bridge_h2(reader, writer, channel, ctx, PROTO_TAG_INTERMEDIATE)))
            reader.feed_data(struct.pack('<I', len(body)) + body)
            original = await asyncio.wait_for(self.requests.get(), 1)
            self.respond(original)
            await asyncio.wait_for(writer.started.wait(), .5)
            await asyncio.sleep(.03)
            self.assertTrue(self.requests.empty())
            writer.resume.set()
            poll = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(poll[2], body)
            self.assertIsNone(channel.delivering_since)
            self.assertEqual(channel.down, 40)

    async def test_idle_poll_preserves_slot_for_new_overdue_request(self):
        channel = _HttpChannel(self.lane, 1, 'idle-poll-capacity-test')
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(4)]
        for body in bodies[:3]:
            await channel.send(body, False)
            self.respond(await asyncio.wait_for(self.requests.get(), 1))
            await asyncio.wait_for(channel.receive(), 1)
        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .06), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005):
            self.tasks.append(asyncio.create_task(channel._recover()))
            poll = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(poll[2], bodies[2])
            await asyncio.sleep(.04)
            self.assertTrue(self.requests.empty(), 'completed history occupied the spare recovery slot')
            await channel.send(bodies[3], False)
            original = await asyncio.wait_for(self.requests.get(), .5)
            recovery = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(original[2], bodies[3])
            self.assertEqual(recovery[2], bodies[3])
            self.respond(recovery, b'n' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'n' * 40)
            # Neither the old receiver nor the new original was cancelled.
            self.assertTrue(self.resets.empty())
            self.respond(poll, b'p' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'p' * 40)
            self.respond(original, b'o' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'o' * 40)
            self.assertEqual(channel.replay_rotations, 0)

    async def test_busy_channel_recovers_old_request_despite_newer_replies(self):
        channel = _HttpChannel(self.lane, 1, 'busy-recovery-test')
        body = b'samekey!' + b't' * 32
        original = None
        recovered = asyncio.Event()
        chatter_reply = asyncio.Event()
        chatter_requests = 0

        async def server():
            nonlocal original, chatter_requests
            while True:
                request = await self.requests.get()
                if request[2] == body:
                    if original is None:
                        original = request
                    else:
                        self.respond(request, b't' * 40)
                else:
                    chatter_requests += 1
                    self.respond(request, b'c' * 40)

        async def chatter():
            index = 0
            while True:
                chatter_reply.clear()
                await channel.send(b'samekey!' + struct.pack('<I', index) + b'c' * 28, False)
                index += 1
                await chatter_reply.wait()
                await asyncio.sleep(.005)

        async def receiver():
            while True:
                if await channel.receive() == b't' * 40:
                    recovered.set()
                else:
                    chatter_reply.set()

        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .05), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .12), \
                patch('proxy.cf_h2.REPLAY_HISTORY_PACKETS', 3):
            await channel.send(body, False)
            self.tasks.extend(asyncio.create_task(coro) for coro in (
                server(), chatter(), receiver(), channel._recover()))
            await asyncio.wait_for(recovered.wait(), .6)
            self.assertGreater(chatter_requests, 3)
            self.assertLessEqual(len(channel.replay_history), 3)
            self.assertIn(original[1], self.transport.connection.streams)
            self.assertTrue(self.resets.empty())
            self.assertEqual(len(self.connections), 1)

    async def test_receiving_other_body_does_not_mask_overdue_request(self):
        channel = _HttpChannel(self.lane, 1, 'parallel-body-recovery-test')
        body = b'samekey!' + b't' * 32
        await channel.send(body, False)
        original = await asyncio.wait_for(self.requests.get(), 1)
        await channel.send(b'samekey!' + b'b' * 32, False)
        other = await asyncio.wait_for(self.requests.get(), 1)
        self.respond(other, b'b' * 4, end=False, length=400)

        async def body_progress():
            for _ in range(90):
                await asyncio.sleep(.01)
                conn, sid, _ = other
                conn.h2.send_data(sid, b'b' * 4)
                self.flush(conn)

        with patch('proxy.cf_h2.REPLAY_IDLE_SECONDS', .03), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .07):
            self.tasks.extend(asyncio.create_task(coro) for coro in (body_progress(), channel._recover()))
            probe = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(probe[2], body)
            self.assertEqual(channel.receiving_http, 1)
            self.respond(probe, b't' * 40)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b't' * 40)
            self.assertIn(original[1], self.transport.connection.streams)
            self.assertIn(other[1], self.transport.connection.streams)
            self.assertTrue(self.resets.empty())

    async def test_silent_recovery_polls_refresh_without_new_native_input(self):
        channel = _HttpChannel(self.lane, 1, 'refresh-recovery-test')
        bodies = [b'samekey!' + bytes([index]) * 32 for index in range(2)]
        for body in bodies:
            await channel.send(body, False)
            await asyncio.wait_for(self.requests.get(), 1)
        with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_SLOT_SECONDS', .2), \
                patch('proxy.cf_h2.REPLAY_RETRY_SECONDS', .02):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            latest = await asyncio.wait_for(self.requests.get(), .5)
            older = await asyncio.wait_for(self.requests.get(), .5)
            channel.sent_since[channel.replay_history[0].pending] = time.monotonic() - 1
            refreshed = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(refreshed[2], bodies[0])
            self.assertEqual(latest[2], bodies[0])
            self.assertEqual(await asyncio.wait_for(self.resets.get(), .5), (latest[1], 8))
            self.assertIn(older[1], self.transport.connection.streams)
            self.assertEqual(len(channel.replay_pending), 2)
            self.respond(refreshed)
            self.assertEqual(await asyncio.wait_for(channel.receive(), .5), b'r' * 40)
            self.assertEqual(channel.replay_rotations, 1)

    async def test_recovery_never_resets_replays_with_partially_received_bodies(self):
        channel = _HttpChannel(self.lane, 1, 'protected-body-test')
        bodies = [b'k' * 8 + bytes([index]) * 32 for index in range(3)]
        for body in bodies[:2]:
            await channel.send(body, False)
            await asyncio.wait_for(self.requests.get(), 1)
        with patch('proxy.cf_h2.REPLAY_REQUEST_SECONDS', .01), \
                patch('proxy.cf_h2.REPLAY_CHECK_SECONDS', .005), \
                patch('proxy.cf_h2.REPLAY_SLOT_SECONDS', .04):
            recovery = asyncio.create_task(channel._recover())
            self.tasks.append(recovery)
            probes = [await asyncio.wait_for(self.requests.get(), 1) for _ in range(2)]
            for probe in probes:
                self.respond(probe, b'p' * 20, end=False, length=40)
            await channel.send(bodies[2], False)
            original = await asyncio.wait_for(self.requests.get(), 1)
            await asyncio.sleep(.1)
            self.assertEqual(channel.receiving_http, 2)
            self.assertTrue(self.requests.empty())
            self.assertTrue(self.resets.empty())
            self.assertIn(original[1], self.transport.connection.streams)
            # Finishing a partial response frees a slot without throwing its
            # MTProto result away, and recovery can then use that slot.
            conn, sid, _ = probes[0]
            conn.h2.send_data(sid, b'p' * 20, end_stream=True)
            self.flush(conn)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'p' * 40)
            fresh_probe = await asyncio.wait_for(self.requests.get(), .5)
            self.assertEqual(fresh_probe[2], bodies[2])
            self.respond(fresh_probe)
            self.assertEqual(await asyncio.wait_for(channel.receive(), 1), b'r' * 40)
            self.assertEqual(channel.replay_rotations, 0)

    async def test_preflight_and_posts_use_same_tls_connection(self):
        preflight = asyncio.create_task(self.lane._preflight())
        self.tasks.append(preflight)
        request = await asyncio.wait_for(self.requests.get(), 2)
        self.respond(request, b'', status=501)
        await preflight
        task, request = await self.post()
        self.respond(request)
        await task
        self.assertEqual(self.lane.tcp_connections, 1)
        self.assertEqual(len(self.connections), 1)

    async def test_concurrent_waiters_respect_remote_stream_limit(self):
        self.server_max_streams = 2
        pending = [asyncio.create_task(self.lane._post(b'x' * 40, i)) for i in range(12)]
        self.tasks.extend(pending)
        for _ in range(6):
            batch = [await asyncio.wait_for(self.requests.get(), 2) for _ in range(2)]
            await asyncio.sleep(.01)
            self.assertTrue(self.requests.empty())
            for request in batch:
                self.respond(request)
        self.assertEqual(await asyncio.gather(*pending), [b'r' * 40] * 12)
        self.assertEqual(len(self.connections), 1)

    async def test_cancellation_resets_stream_and_reuses_server_capacity(self):
        self.server_max_streams = 2
        healthy, healthy_request = await self.post()
        for _ in range(5):
            task, request = await self.post()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(await asyncio.wait_for(self.resets.get(), .5), (request[1], 8))
        self.respond(healthy_request)
        self.assertEqual(await asyncio.wait_for(healthy, .5), b'r' * 40)
        self.assertEqual(len(self.connections), 1)

    async def test_cancel_while_waiting_for_stream_slot_does_not_leak_capacity(self):
        self.server_max_streams = 1
        first, request = await self.post()
        waiting = asyncio.create_task(self.lane._post(b'w' * 40, 2))
        self.tasks.append(waiting)
        await asyncio.sleep(.03)
        self.assertTrue(self.requests.empty())
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        self.respond(request)
        await first
        next_task, next_request = await self.post()
        self.respond(next_request)
        await next_task
        self.assertFalse(self.transport.connection.streams)

    async def test_large_upload_and_download_cross_flow_control_windows(self):
        body = b'x' * (256 * 1024)
        task, request = await self.post(body)
        self.respond(request, body)
        self.assertEqual(await asyncio.wait_for(task, 2), body)
        self.assertEqual(len(self.connections), 1)

    async def test_stalled_body_consumer_does_not_block_other_streams(self):
        # Keep a streaming response open without reading it. Its window must
        # bound buffering while other responses can exceed a connection window.
        opening = asyncio.create_task(self.lane.client.send(
            self.lane.client.build_request('POST', 'https://' + self.lane.host + '/api', content=b'x' * 40),
            stream=True))
        self.tasks.append(opening)
        request = await asyncio.wait_for(self.requests.get(), 2)
        self.respond(request, b's' * (1024 * 1024))
        response = await opening
        fast, fast_request = await self.post()
        self.respond(fast_request, b'f' * (4 * 1024 * 1024))
        self.assertEqual(await asyncio.wait_for(fast, 2), b'f' * (4 * 1024 * 1024))
        stream = self.transport.connection.streams[request[1]]
        self.assertLessEqual(sum(len(data) for data, _ in stream.chunks), STREAM_RECEIVE_WINDOW)
        await response.aclose()
        self.assertEqual(await asyncio.wait_for(self.resets.get(), .5), (request[1], 8))

    async def test_read_timeout_resets_only_its_stream(self):
        self.lane.client.timeout = httpx.Timeout(2, read=.15)
        stalled, request = await self.post()
        self.lane.client.timeout = httpx.Timeout(2, read=1)
        healthy, healthy_request = await self.post()
        with self.assertRaises(httpx.ReadTimeout):
            await stalled
        self.assertEqual(await asyncio.wait_for(self.resets.get(), .5), (request[1], 8))
        self.assertIsNone(self.transport.connection.error)
        self.respond(healthy_request)
        self.assertEqual(await healthy, b'r' * 40)
        self.assertEqual(len(self.connections), 1)

    async def test_last_stream_timeout_reconnects_instead_of_reusing_silent_connection(self):
        self.lane.client.timeout = httpx.Timeout(2, read=.05)
        stalled, _ = await self.post()
        old_connection = self.transport.connection
        with self.assertRaises(httpx.ReadTimeout):
            await stalled
        self.assertIsNotNone(old_connection.error)
        self.assertTrue(old_connection.writer.is_closing())
        self.assertFalse(old_connection.streams)
        self.lane.client.timeout = httpx.Timeout(2, read=1)
        recovered, request = await self.post()
        self.respond(request)
        self.assertEqual(await recovered, b'r' * 40)
        self.assertIsNot(self.transport.connection, old_connection)
        self.assertEqual(len(self.connections), 2)

    async def test_peer_reset_does_not_break_other_streams(self):
        bad, request = await self.post()
        good, good_request = await self.post()
        conn, sid, _ = request
        conn.h2.reset_stream(sid, error_code=8)
        self.flush(conn)
        with self.assertRaises(httpx.RemoteProtocolError):
            await bad
        self.respond(good_request)
        self.assertEqual(await good, b'r' * 40)

    async def test_reset_during_response_closes_native_bridge_without_escaping(self):
        cipher = SimpleNamespace(update=lambda data: data)
        ctx = SimpleNamespace(clt_dec=cipher, clt_enc=cipher)
        output = bytearray()
        writer = SimpleNamespace(write=output.extend, drain=AsyncMock())
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack('<I', 40) + b'k' * 40)
        channel = _HttpChannel(self.lane, 1, 'reset-during-body')

        with patch.object(channel, '_recover', side_effect=asyncio.Event().wait), \
                self.assertLogs('tg-mtproto-proxy', level='DEBUG') as captured:
            bridge = asyncio.create_task(bridge_h2(reader, writer, channel, ctx, PROTO_TAG_INTERMEDIATE))
            self.tasks.append(bridge)
            request = await asyncio.wait_for(self.requests.get(), 1)
            healthy, good_request = await self.post(b'h' * 40)
            self.respond(request, b'partial-response!!!!', end=False, length=40)

            async def body_received():
                while self.lane.reply_buffer_bytes != 20:
                    await asyncio.sleep(.001)

            await asyncio.wait_for(body_received(), 1)
            conn, sid, _ = request
            conn.h2.reset_stream(sid, error_code=2)
            self.flush(conn)
            await asyncio.wait_for(bridge, 1)
            self.assertTrue(channel.close_done)
            self.assertFalse(channel.pending)
            self.assertIsNone(channel.transport_error)
            self.assertFalse(output, 'A partial HTTP response must never reach the native client')
            self.assertEqual(self.lane.reply_buffer_bytes, 0)
            self.assertIsNone(self.transport.connection.error)
            self.respond(good_request)
            self.assertEqual(await asyncio.wait_for(healthy, 1), b'r' * 40)
            self.assertEqual(len(self.connections), 1)
        failures = [record for record in captured.records if record.levelname in ('WARNING', 'ERROR')]
        self.assertEqual(len(failures), 1)
        # h2 4.1 (Python 3.8) formats IntEnum values by name.
        self.assertRegex(failures[0].getMessage(), r'reset: (?:2|ErrorCodes\.INTERNAL_ERROR)\b')
        self.assertIn('down=20', failures[0].getMessage())
        self.assertIn('sid=1', failures[0].getMessage())

    async def test_connection_loss_fails_waiters_and_next_request_reconnects(self):
        tasks = [await self.post() for _ in range(3)]
        self.connections[0].writer.close()
        for task, _ in tasks:
            with self.assertRaises(httpx.HTTPError):
                await asyncio.wait_for(task, .5)
        task, request = await self.post()
        self.respond(request)
        self.assertEqual(await task, b'r' * 40)
        self.assertEqual(len(self.connections), 2)

    async def test_goaway_fails_pending_without_replaying_and_reconnects(self):
        task, request = await self.post()
        conn, _, _ = request
        conn.h2.close_connection(error_code=0)
        self.flush(conn)
        with self.assertRaisesRegex(httpx.RemoteProtocolError, 'GOAWAY'):
            await task
        self.assertTrue(self.requests.empty())
        next_task, next_request = await self.post()
        self.respond(next_request)
        await next_task
        self.assertEqual(len(self.connections), 2)

    async def test_close_stops_reader_and_wakes_pending_requests(self):
        task, _ = await self.post()
        connection = self.transport.connection
        await self.lane.close()
        with self.assertRaises(httpx.HTTPError):
            await asyncio.wait_for(task, .5)
        self.assertTrue(connection.reader_task.done())
        self.assertTrue(connection.writer.is_closing())


if __name__ == '__main__':
    unittest.main()
