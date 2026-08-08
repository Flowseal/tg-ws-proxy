import hashlib
import hmac
import os
import struct
import time
import unittest

from proxy.fake_tls import (
    CLIENT_RANDOM_LEN,
    CLIENT_RANDOM_OFFSET,
    SESSION_ID_LEN,
    SESSION_ID_OFFSET,
    TLS_APPDATA_MAX,
    TLS_RECORD_HANDSHAKE,
    build_server_hello,
    verify_client_hello,
    wrap_tls_record,
)

SECRET = bytes.fromhex('00112233445566778899aabbccddeeff')


def _client_hello(secret: bytes = SECRET, timestamp: int = None,
                  session_id: bytes = None) -> bytes:
    if timestamp is None:
        timestamp = int(time.time())
    if session_id is None:
        session_id = os.urandom(SESSION_ID_LEN)

    body = bytearray(517)
    body[0] = TLS_RECORD_HANDSHAKE
    body[1:3] = b'\x03\x01'
    struct.pack_into('>H', body, 3, len(body) - 5)
    body[5] = 0x01
    body[43] = 0x20
    body[SESSION_ID_OFFSET:SESSION_ID_OFFSET + SESSION_ID_LEN] = session_id

    digest = hmac.new(secret, bytes(body), hashlib.sha256).digest()
    client_random = bytearray(digest[:CLIENT_RANDOM_LEN])
    ts_bytes = struct.pack('<I', timestamp)
    for i in range(4):
        client_random[28 + i] = digest[28 + i] ^ ts_bytes[i]

    body[CLIENT_RANDOM_OFFSET:CLIENT_RANDOM_OFFSET + CLIENT_RANDOM_LEN] = client_random
    return bytes(body)


class VerifyClientHelloTest(unittest.TestCase):
    def test_accepts_well_formed_hello(self):
        session_id = os.urandom(SESSION_ID_LEN)
        now = int(time.time())
        result = verify_client_hello(_client_hello(timestamp=now,
                                                   session_id=session_id), SECRET)
        self.assertIsNotNone(result)
        client_random, got_session_id, ts = result
        self.assertEqual(len(client_random), CLIENT_RANDOM_LEN)
        self.assertEqual(got_session_id, session_id)
        self.assertEqual(ts, now)

    def test_rejects_wrong_secret(self):
        other = bytes.fromhex('ffeeddccbbaa99887766554433221100')
        self.assertIsNone(verify_client_hello(_client_hello(), other))

    def test_rejects_stale_timestamp(self):
        stale = int(time.time()) - 3600
        self.assertIsNone(verify_client_hello(_client_hello(timestamp=stale), SECRET))

    def test_rejects_tampered_body(self):
        hello = bytearray(_client_hello())
        hello[300] ^= 0xFF
        self.assertIsNone(verify_client_hello(bytes(hello), SECRET))

    def test_rejects_short_and_non_handshake_records(self):
        self.assertIsNone(verify_client_hello(b'\x16\x03\x01\x00\x10', SECRET))
        hello = bytearray(_client_hello())
        hello[0] = 0x17
        self.assertIsNone(verify_client_hello(bytes(hello), SECRET))
        hello = bytearray(_client_hello())
        hello[5] = 0x02
        self.assertIsNone(verify_client_hello(bytes(hello), SECRET))


class BuildServerHelloTest(unittest.TestCase):
    def test_echoes_session_id_and_binds_client_random(self):
        session_id = os.urandom(SESSION_ID_LEN)
        client_random = os.urandom(CLIENT_RANDOM_LEN)
        response = build_server_hello(SECRET, client_random, session_id)

        self.assertEqual(response[0], TLS_RECORD_HANDSHAKE)
        self.assertEqual(
            response[SESSION_ID_OFFSET:SESSION_ID_OFFSET + SESSION_ID_LEN],
            session_id,
        )

        zeroed = bytearray(response)
        zeroed[11:11 + 32] = b'\x00' * 32
        expected = hmac.new(SECRET, client_random + bytes(zeroed),
                            hashlib.sha256).digest()
        self.assertEqual(response[11:11 + 32], expected)

    def test_padding_length_varies_between_calls(self):
        sizes = {
            len(build_server_hello(SECRET, os.urandom(32), os.urandom(32)))
            for _ in range(20)
        }
        self.assertGreater(len(sizes), 1)


class WrapTlsRecordTest(unittest.TestCase):
    def test_short_payload_becomes_one_record(self):
        wrapped = wrap_tls_record(b'hello')
        self.assertEqual(wrapped, b'\x17\x03\x03\x00\x05hello')

    def test_long_payload_is_chunked_to_the_record_limit(self):
        payload = os.urandom(TLS_APPDATA_MAX + 100)
        wrapped = wrap_tls_record(payload)

        offset = 0
        chunks = []
        while offset < len(wrapped):
            length = struct.unpack('>H', wrapped[offset + 3:offset + 5])[0]
            self.assertLessEqual(length, TLS_APPDATA_MAX)
            chunks.append(wrapped[offset + 5:offset + 5 + length])
            offset += 5 + length
        self.assertEqual(len(chunks), 2)
        self.assertEqual(b''.join(chunks), payload)

    def test_empty_payload_produces_no_records(self):
        self.assertEqual(wrap_tls_record(b''), b'')


if __name__ == '__main__':
    unittest.main()
