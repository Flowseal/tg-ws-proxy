import asyncio
import unittest

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
    def test_roundtrip(self):
        data = bytes(range(256)) * 3
        mask = b'\x01\x02\x03\x04'
        self.assertEqual(_xor_mask(_xor_mask(data, mask), mask), data)

    def test_empty_payload(self):
        self.assertEqual(_xor_mask(b'', b'\x01\x02\x03\x04'), b'')


class BuildFrameTest(unittest.TestCase):
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


class HandshakeErrorTest(unittest.TestCase):
    def test_redirect_status_codes(self):
        for code in (301, 302, 303, 307, 308):
            self.assertTrue(WsHandshakeError(code, '').is_redirect)
        for code in (0, 200, 429, 502):
            self.assertFalse(WsHandshakeError(code, '').is_redirect)


if __name__ == '__main__':
    unittest.main()
