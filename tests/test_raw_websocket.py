import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy.raw_websocket import RawWebSocket, WsHandshakeError, _xor_mask


def _raw_frame(opcode, data, fin=True):
    b0 = (0x80 if fin else 0x00) | opcode
    n = len(data)
    if n < 126:
        return bytes([b0, n]) + data
    if n < 65536:
        return bytes([b0, 126]) + n.to_bytes(2, 'big') + data
    return bytes([b0, 127]) + n.to_bytes(8, 'big') + data


class _NullWriter:
    def write(self, data):
        pass

    async def drain(self):
        pass


def _recv(chunks, cls=RawWebSocket):
    async def _run():
        reader = asyncio.StreamReader()
        for chunk in chunks:
            reader.feed_data(chunk)
        reader.feed_eof()
        ws = cls(reader, _NullWriter())
        return ws, await ws.recv()

    return asyncio.run(_run())


class XorMaskTest(unittest.TestCase):
    def test_matches_bytewise_xor_at_fast_path_and_frame_boundaries(self):
        for length in (0, 1, 2, 3, 4, 5, 125, 126, 127, 255, 511, 512, 513, 514, 515,
                       16384, 65535, 65536, 65537, 1024 * 1024 + 3):
            data = (bytes(range(256)) * (length // 256 + 1))[:length]
            for mask in (b'\x00' * 4, b'\xff' * 4, b'\x01\x7f\x80\xff'):
                with self.subTest(length=length, mask=mask):
                    expected = bytes(value ^ mask[index % 4] for index, value in enumerate(data))
                    self.assertEqual(_xor_mask(data, mask), expected)

    def test_roundtrip(self):
        data = bytes(range(256)) * 3
        mask = b'\x01\x02\x03\x04'
        self.assertEqual(_xor_mask(_xor_mask(data, mask), mask), data)

    def test_empty_payload(self):
        self.assertEqual(_xor_mask(b'', b'\x01\x02\x03\x04'), b'')


class BuildFrameTest(unittest.TestCase):
    def test_masked_frame_wire_format_across_all_length_encodings(self):
        mask = b'\x01\x7f\x80\xff'
        for length in (1, 125, 126, 511, 512, 513, 65535, 65536, 65537):
            payload = (bytes(range(256)) * (length // 256 + 1))[:length]
            with self.subTest(length=length), patch('proxy.raw_websocket.os.urandom', return_value=mask):
                frame = RawWebSocket._build_frame(RawWebSocket.OP_BINARY, payload, mask=True)
                if length < 126:
                    header = bytes([0x82, 0x80 | length])
                elif length < 65536:
                    header = b'\x82\xfe' + length.to_bytes(2, 'big')
                else:
                    header = b'\x82\xff' + length.to_bytes(8, 'big')
                masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
                self.assertEqual(frame, header + mask + masked)

    def test_short_unmasked_frame(self):
        self.assertEqual(
            RawWebSocket._build_frame(RawWebSocket.OP_BINARY, b'abc'),
            b'\x82\x03abc',
        )

    def test_extended_length_selects_16bit_header(self):
        frame = RawWebSocket._build_frame(RawWebSocket.OP_BINARY, b'x' * 200)
        self.assertEqual(frame[:2], b'\x82\x7e')
        self.assertEqual(int.from_bytes(frame[2:4], 'big'), 200)

    def test_masked_frame_sets_mask_bit_and_is_reversible(self):
        payload = b'payload'
        frame = RawWebSocket._build_frame(
            RawWebSocket.OP_BINARY, payload, mask=True)
        self.assertTrue(frame[1] & 0x80)
        self.assertEqual(_xor_mask(frame[6:], frame[2:6]), payload)


class RecvTest(unittest.TestCase):
    def test_returns_unfragmented_message(self):
        _, msg = _recv([_raw_frame(RawWebSocket.OP_BINARY, b'hello')])
        self.assertEqual(msg, b'hello')

    def test_reassembles_fragmented_message(self):
        _, msg = _recv([
            _raw_frame(RawWebSocket.OP_BINARY, b'AAA', False),
            _raw_frame(RawWebSocket.OP_CONT, b'BBB', False),
            _raw_frame(RawWebSocket.OP_CONT, b'CCC', True),
        ])
        self.assertEqual(msg, b'AAABBBCCC')

    def test_control_frame_between_fragments_is_skipped(self):
        _, msg = _recv([
            _raw_frame(RawWebSocket.OP_BINARY, b'AAA', False),
            _raw_frame(RawWebSocket.OP_PONG, b''),
            _raw_frame(RawWebSocket.OP_CONT, b'BBB', True),
        ])
        self.assertEqual(msg, b'AAABBB')

    def test_close_frame_returns_none(self):
        ws, msg = _recv([_raw_frame(RawWebSocket.OP_CLOSE, b'\x03\xe8')])
        self.assertIsNone(msg)
        self.assertTrue(ws._closed)

    def test_oversized_frame_is_rejected_before_reading_payload(self):
        header = bytes([0x82, 127]) + (1 << 40).to_bytes(8, 'big')
        with self.assertRaises(ConnectionError):
            _recv([header])

    def test_reassembled_message_exceeding_limit_is_rejected(self):
        class _Capped(RawWebSocket):
            __slots__ = ()
            MAX_MESSAGE_LEN = 1500

        chunk = b'x' * 1024
        with self.assertRaises(ConnectionError):
            _recv([
                _raw_frame(RawWebSocket.OP_BINARY, chunk, False),
                _raw_frame(RawWebSocket.OP_CONT, chunk, False),
            ], cls=_Capped)


class ParseCloseTest(unittest.TestCase):
    def test_known_code_gets_name(self):
        code, reason = RawWebSocket._parse_close(b'\x03\xe8bye')
        self.assertEqual(code, 1000)
        self.assertIn('normal', reason)

    def test_empty_payload(self):
        self.assertEqual(RawWebSocket._parse_close(b''), (None, ''))


class CloseTest(unittest.IsolatedAsyncioTestCase):
    async def test_peer_close_still_closes_transport_without_another_close_frame(self):
        reader = asyncio.StreamReader()
        reader.feed_data(_raw_frame(RawWebSocket.OP_CLOSE, b'\x03\xe8'))
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        ws = RawWebSocket(reader, writer)

        self.assertIsNone(await ws.recv())
        await ws.close()

        writer.close.assert_called_once()
        writer.wait_closed.assert_awaited_once()
        self.assertEqual(writer.write.call_count, 1)

    async def test_repeated_close_sends_only_one_close_frame(self):
        writer = Mock(drain=AsyncMock(), wait_closed=AsyncMock())
        ws = RawWebSocket(asyncio.StreamReader(), writer)

        await ws.close()
        await ws.close()

        self.assertTrue(ws._closed)
        self.assertEqual(writer.write.call_count, 1)
        writer.close.assert_called()

    async def test_cancelling_close_during_drain_still_closes_transport(self):
        draining = asyncio.Event()

        async def drain():
            draining.set()
            await asyncio.Future()

        writer = Mock(drain=AsyncMock(side_effect=drain), wait_closed=AsyncMock())
        ws = RawWebSocket(asyncio.StreamReader(), writer)
        task = asyncio.create_task(ws.close())
        try:
            await asyncio.wait_for(draining.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            writer.close.assert_called_once()
            writer.wait_closed.assert_awaited_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class HandshakeErrorTest(unittest.TestCase):
    def test_redirect_status_codes(self):
        for code in (301, 302, 303, 307, 308):
            self.assertTrue(WsHandshakeError(code, '').is_redirect)
        for code in (0, 200, 429, 502):
            self.assertFalse(WsHandshakeError(code, '').is_redirect)


class HandshakeCleanupTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_pending_upgrade_closes_socket(self):
        reader = asyncio.StreamReader()
        writer = Mock(drain=AsyncMock())
        started = asyncio.Event()

        async def drain():
            started.set()

        writer.drain.side_effect = drain
        with patch('proxy.raw_websocket.asyncio.open_connection',
                   AsyncMock(return_value=(reader, writer))), \
                patch('proxy.raw_websocket.set_sock_opts'):
            task = asyncio.create_task(RawWebSocket.connect('192.0.2.1', 'example.org'))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        writer.close.assert_called_once()

    async def test_failed_upgrade_closes_socket(self):
        for error in (ConnectionResetError(), asyncio.TimeoutError()):
            with self.subTest(error=type(error)):
                reader = Mock(readline=AsyncMock(side_effect=error))
                writer = Mock(drain=AsyncMock())
                with patch('proxy.raw_websocket.asyncio.open_connection',
                           AsyncMock(return_value=(reader, writer))), \
                        patch('proxy.raw_websocket.set_sock_opts'):
                    with self.assertRaises(type(error)):
                        await RawWebSocket.connect('192.0.2.1', 'example.org')
                writer.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
