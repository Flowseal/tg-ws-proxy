import asyncio
import datetime
import hashlib
import hmac
import os
import ssl
import struct
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy import fake_tls
from proxy.fake_tls import (
    APPDATA_MIN,
    CLIENT_RANDOM_LEN,
    CLIENT_RANDOM_OFFSET,
    EXT_KEY_SHARE,
    EXT_SUPPORTED_VERSIONS,
    FALLBACK_APPDATA_RANGE,
    GROUP_X25519,
    GROUP_X25519MLKEM768,
    MLKEM768_CIPHERTEXT_LEN,
    SESSION_ID_LEN,
    SESSION_ID_OFFSET,
    TLS_APPDATA_MAX,
    TLS_RECORD_APPDATA,
    TLS_RECORD_CCS,
    TLS_RECORD_HANDSHAKE,
    X25519_KEY_LEN,
    X25519MLKEM768_CLIENT_SHARE_LEN,
    build_server_hello,
    first_appdata_record_len,
    parse_client_hello,
    probe_appdata_len,
    set_profiled_appdata_len,
    verify_client_hello,
    wrap_tls_record,
)

SECRET = bytes.fromhex('00112233445566778899aabbccddeeff')

# Telegram's current ClientHello offers these, like Chrome
TELEGRAM_SUITES = (0x1301, 0x1302, 0x1303, 0xc02b, 0xc02f)
TELEGRAM_SHARES = ((GROUP_X25519MLKEM768, X25519MLKEM768_CLIENT_SHARE_LEN),
                   (GROUP_X25519, X25519_KEY_LEN))


def _sign_client_hello(body: bytearray, secret: bytes, timestamp: int) -> bytes:
    digest = hmac.new(secret, bytes(body), hashlib.sha256).digest()
    client_random = bytearray(digest[:CLIENT_RANDOM_LEN])
    ts_bytes = struct.pack('<I', timestamp)
    for i in range(4):
        client_random[28 + i] = digest[28 + i] ^ ts_bytes[i]
    body[CLIENT_RANDOM_OFFSET:CLIENT_RANDOM_OFFSET + CLIENT_RANDOM_LEN] = client_random
    return bytes(body)


def _structured_client_hello(suites=TELEGRAM_SUITES, shares=TELEGRAM_SHARES,
                             secret: bytes = SECRET) -> bytes:
    key_share_list = b''.join(struct.pack('>HH', group, length) + os.urandom(length)
                              for group, length in shares)
    extensions = (
        struct.pack('>HH', 0x0000, 0)
        + struct.pack('>HHH', EXT_KEY_SHARE, 2 + len(key_share_list),
                      len(key_share_list))
        + key_share_list
        + struct.pack('>HHB', EXT_SUPPORTED_VERSIONS, 3, 2) + b'\x03\x04'
    )
    hello = (
        b'\x03\x03' + b'\x00' * CLIENT_RANDOM_LEN
        + b'\x20' + os.urandom(SESSION_ID_LEN)
        + struct.pack('>H', 2 * len(suites))
        + b''.join(struct.pack('>H', s) for s in suites)
        + b'\x01\x00'
        + struct.pack('>H', len(extensions)) + extensions
    )
    message = b'\x01' + struct.pack('>I', len(hello))[1:] + hello
    body = bytearray(b'\x16\x03\x01' + struct.pack('>H', len(message)) + message)
    return _sign_client_hello(body, secret, int(time.time()))


def _split_records(data: bytes):
    records = []
    pos = 0
    while pos < len(data):
        rtype = data[pos]
        length = struct.unpack_from('>H', data, pos + 3)[0]
        records.append((rtype, data[pos + 5:pos + 5 + length]))
        pos += 5 + length
    return records


def _server_hello_fields(record: bytes):
    # record = handshake message: type(1) len(3) version(2) random(32) sid ...
    pos = 4 + 2 + 32
    pos += 1 + record[pos]
    cipher = struct.unpack_from('>H', record, pos)[0]
    pos += 3
    ext_end = pos + 2 + struct.unpack_from('>H', record, pos)[0]
    pos += 2
    extensions = []
    while pos < ext_end:
        ext_type, ext_len = struct.unpack_from('>HH', record, pos)
        extensions.append((ext_type, record[pos + 4:pos + 4 + ext_len]))
        pos += 4 + ext_len
    return cipher, extensions


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
    return _sign_client_hello(body, secret, timestamp)


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


class ParseClientHelloTest(unittest.TestCase):
    def test_extracts_suites_and_key_shares(self):
        info = parse_client_hello(_structured_client_hello())
        self.assertEqual(info.cipher_suites, frozenset(TELEGRAM_SUITES))
        self.assertEqual(info.key_shares, dict(TELEGRAM_SHARES))

    def test_structured_hello_still_verifies(self):
        self.assertIsNotNone(verify_client_hello(_structured_client_hello(), SECRET))

    def test_malformed_hello_yields_empty_info(self):
        self.assertEqual(parse_client_hello(b'\x16\x03\x01\x00'),
                         fake_tls._EMPTY_HELLO_INFO)
        truncated = _structured_client_hello()[:60]
        self.assertEqual(parse_client_hello(truncated), fake_tls._EMPTY_HELLO_INFO)

    def test_unstructured_hello_does_not_raise(self):
        parse_client_hello(_client_hello())


class BuildServerHelloTest(unittest.TestCase):
    def setUp(self):
        set_profiled_appdata_len(None)

    def tearDown(self):
        set_profiled_appdata_len(None)

    def _build(self, hello: bytes, client_random: bytes = None) -> bytes:
        client_random = client_random or os.urandom(CLIENT_RANDOM_LEN)
        return build_server_hello(SECRET, client_random, os.urandom(SESSION_ID_LEN),
                                  parse_client_hello(hello))

    def test_flight_is_server_hello_ccs_and_one_appdata_record(self):
        records = _split_records(self._build(_structured_client_hello()))
        self.assertEqual([r[0] for r in records],
                         [TLS_RECORD_HANDSHAKE, TLS_RECORD_CCS, TLS_RECORD_APPDATA])
        self.assertEqual(records[1][1], b'\x01')

    def test_hmac_covers_whole_flight(self):
        client_random = os.urandom(CLIENT_RANDOM_LEN)
        response = self._build(_structured_client_hello(), client_random)
        zeroed = bytearray(response)
        zeroed[11:11 + 32] = b'\x00' * 32
        expected = hmac.new(SECRET, client_random + bytes(zeroed),
                            hashlib.sha256).digest()
        self.assertEqual(response[11:11 + 32], expected)

    def test_mirrors_post_quantum_group_when_offered(self):
        records = _split_records(self._build(_structured_client_hello()))
        cipher, extensions = _server_hello_fields(records[0][1])
        self.assertEqual([e[0] for e in extensions],
                         [EXT_SUPPORTED_VERSIONS, EXT_KEY_SHARE])
        key_share = extensions[1][1]
        group, length = struct.unpack_from('>HH', key_share)
        self.assertEqual(group, GROUP_X25519MLKEM768)
        self.assertEqual(length, MLKEM768_CIPHERTEXT_LEN + X25519_KEY_LEN)
        self.assertEqual(len(key_share), 4 + length)
        self.assertEqual(cipher, 0x1301)

    def test_falls_back_to_x25519(self):
        hello = _structured_client_hello(shares=((GROUP_X25519, X25519_KEY_LEN),))
        records = _split_records(self._build(hello))
        _, extensions = _server_hello_fields(records[0][1])
        group, length = struct.unpack_from('>HH', extensions[1][1])
        self.assertEqual((group, length), (GROUP_X25519, X25519_KEY_LEN))
        # real X25519 public keys are below 2^255
        self.assertEqual(extensions[1][1][-1] & 0x80, 0)

    def test_ignores_post_quantum_share_of_wrong_size(self):
        hello = _structured_client_hello(
            shares=((GROUP_X25519MLKEM768, 100), (GROUP_X25519, X25519_KEY_LEN)))
        records = _split_records(self._build(hello))
        _, extensions = _server_hello_fields(records[0][1])
        self.assertEqual(struct.unpack_from('>H', extensions[1][1])[0], GROUP_X25519)

    def test_picks_cipher_offered_by_client(self):
        hello = _structured_client_hello(suites=(0x1302, 0x1303))
        cipher, _ = _server_hello_fields(_split_records(self._build(hello))[0][1])
        self.assertEqual(cipher, 0x1303)

    def test_telegram_flight_matches_telemt_size(self):
        # telemt answered every Telegram client with exactly 1290 bytes when the
        # masking site's first encrypted record was below the 64-byte floor
        set_profiled_appdata_len(30)
        self.assertEqual(len(self._build(_structured_client_hello())), 1290)

    def test_uses_profiled_appdata_len(self):
        set_profiled_appdata_len(3000)
        records = _split_records(self._build(_structured_client_hello()))
        self.assertEqual(len(records[2][1]), 3000)

    def test_profiled_len_is_clamped(self):
        set_profiled_appdata_len(10)
        self.assertEqual(fake_tls._profiled_appdata_len, APPDATA_MIN)
        set_profiled_appdata_len(10 ** 6)
        self.assertEqual(fake_tls._profiled_appdata_len, TLS_APPDATA_MAX)

    def test_fallback_len_stays_in_range(self):
        for _ in range(20):
            records = _split_records(self._build(_structured_client_hello()))
            self.assertGreaterEqual(len(records[2][1]), FALLBACK_APPDATA_RANGE[0])
            self.assertLessEqual(len(records[2][1]), FALLBACK_APPDATA_RANGE[1])

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


def _record(rtype: int, length: int) -> bytes:
    return bytes([rtype]) + b'\x03\x03' + struct.pack('>H', length) + b'\x00' * length


class FirstAppdataRecordLenTest(unittest.TestCase):
    def test_finds_record_after_server_hello_and_ccs(self):
        flight = (_record(TLS_RECORD_HANDSHAKE, 122) + _record(TLS_RECORD_CCS, 1)
                  + _record(TLS_RECORD_APPDATA, 27) + _record(TLS_RECORD_APPDATA, 3000))
        self.assertEqual(first_appdata_record_len(flight), 27)

    def test_needs_more_data_until_header_arrives(self):
        partial = _record(TLS_RECORD_HANDSHAKE, 122) + _record(TLS_RECORD_CCS, 1)
        self.assertIsNone(first_appdata_record_len(partial))
        self.assertIsNone(first_appdata_record_len(partial + b'\x17\x03'))

    def test_alert_means_no_profile(self):
        self.assertEqual(first_appdata_record_len(_record(0x15, 2)), 0)


def _self_signed_context() -> ssl.SSLContext:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))

    with tempfile.TemporaryDirectory() as tmp:
        cert_path = os.path.join(tmp, 'cert.pem')
        key_path = os.path.join(tmp, 'key.pem')
        with open(cert_path, 'wb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, 'wb') as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,
                                      serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
    return ctx


@unittest.skipUnless(getattr(ssl, 'HAS_TLSv1_3', False), 'needs TLS 1.3 in ssl')
class ProbeAppdataLenTest(unittest.TestCase):
    def test_continuous_records_do_not_extend_probe_timeout(self):
        async def scenario():
            async def read(_size):
                await asyncio.sleep(0.01)
                return _record(TLS_RECORD_CCS, 1)

            reader = Mock(read=AsyncMock(side_effect=read))
            writer = Mock(drain=AsyncMock())
            with patch.object(fake_tls.asyncio, 'open_connection',
                              AsyncMock(return_value=(reader, writer))):
                result = await asyncio.wait_for(
                    probe_appdata_len('localhost', timeout=0.1), timeout=2)
            self.assertIsNone(result)
            self.assertGreater(reader.read.await_count, 1)
            writer.close.assert_called_once()

        asyncio.run(scenario())

    def test_blocked_drain_obeys_probe_timeout(self):
        async def scenario():
            reader = Mock(read=AsyncMock())
            writer = Mock(drain=AsyncMock(side_effect=asyncio.Event().wait))
            with patch.object(fake_tls.asyncio, 'open_connection',
                              AsyncMock(return_value=(reader, writer))):
                result = await asyncio.wait_for(
                    probe_appdata_len('localhost', timeout=0.1), timeout=2)
            self.assertIsNone(result)
            reader.read.assert_not_called()
            hello = writer.write.call_args[0][0]
            self.assertEqual(parse_client_hello(hello).key_shares,
                             {GROUP_X25519: X25519_KEY_LEN})
            writer.close.assert_called_once()

        asyncio.run(scenario())

    def test_measures_first_encrypted_record_of_real_tls13_server(self):
        async def scenario():
            server = await asyncio.start_server(
                lambda r, w: None, '127.0.0.1', 0, ssl=_self_signed_context())
            port = server.sockets[0].getsockname()[1]
            try:
                return await probe_appdata_len('127.0.0.1', port, timeout=3)
            finally:
                server.close()
                await server.wait_closed()

        length = asyncio.run(scenario())
        self.assertIsNotNone(length)
        self.assertGreater(length, 0)

    def test_unreachable_site_gives_no_profile(self):
        async def scenario():
            server = await asyncio.start_server(lambda r, w: None, '127.0.0.1', 0)
            port = server.sockets[0].getsockname()[1]
            server.close()
            await server.wait_closed()
            return await probe_appdata_len('127.0.0.1', port, timeout=1)

        self.assertIsNone(asyncio.run(scenario()))


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
