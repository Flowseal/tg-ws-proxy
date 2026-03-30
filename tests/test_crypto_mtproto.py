import hashlib
import struct
import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from proxy.crypto_backend import create_aes_ctr_transform
from proxy.tg_ws_proxy import (
    PROTO_ABRIDGED_INT,
    PROTO_TAG_ABRIDGED,
    _MsgSplitter,
    _generate_relay_init,
    _try_handshake,
)


KEY = bytes(range(32))
IV = bytes(range(16))
SECRET = bytes.fromhex("0123456789abcdef0123456789abcdef")


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _keystream(size: int, key: bytes, iv: bytes) -> bytes:
    transform = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return transform.update(b"\x00" * size)


def _build_client_handshake(
    dc_raw: int,
    proto_tag: bytes = PROTO_TAG_ABRIDGED,
    secret: bytes = SECRET,
) -> bytes:
    packet = bytearray(64)
    packet[8:40] = KEY
    packet[40:56] = IV

    dec_key = hashlib.sha256(KEY + secret).digest()
    plain_tail = proto_tag + struct.pack("<h", dc_raw) + b"\x00\x00"
    packet[56:64] = _xor(plain_tail, _keystream(64, dec_key, IV)[56:64])
    return bytes(packet)


def _encrypt_after_init(relay_init: bytes, plaintext: bytes) -> bytes:
    transform = Cipher(
        algorithms.AES(relay_init[8:40]),
        modes.CTR(relay_init[40:56]),
    ).encryptor()
    transform.update(b"\x00" * 64)
    return transform.update(plaintext)


class CryptoBackendTests(unittest.TestCase):
    def test_python_backend_matches_cryptography_stream(self):
        cryptography_transform = create_aes_ctr_transform(
            KEY, IV, backend="cryptography")
        python_transform = create_aes_ctr_transform(KEY, IV, backend="python")

        chunks = [
            b"",
            b"\x00" * 16,
            bytes(range(31)),
            b"telegram-proxy",
            b"\xff" * 64,
        ]

        cryptography_out = b"".join(
            cryptography_transform.update(chunk) for chunk in chunks
        ) + cryptography_transform.finalize()
        python_out = b"".join(
            python_transform.update(chunk) for chunk in chunks
        ) + python_transform.finalize()

        self.assertEqual(python_out, cryptography_out)

    def test_unknown_backend_raises_error(self):
        with self.assertRaises(ValueError):
            create_aes_ctr_transform(KEY, IV, backend="missing")


class MtProtoHandshakeTests(unittest.TestCase):
    def test_try_handshake_reads_non_media_dc(self):
        handshake = _build_client_handshake(dc_raw=2)

        result = _try_handshake(handshake, SECRET)

        self.assertEqual(result[:3], (2, False, PROTO_TAG_ABRIDGED))

    def test_try_handshake_reads_media_dc(self):
        handshake = _build_client_handshake(dc_raw=-4)

        result = _try_handshake(handshake, SECRET)

        self.assertEqual(result[:3], (4, True, PROTO_TAG_ABRIDGED))

    def test_try_handshake_rejects_wrong_secret(self):
        handshake = _build_client_handshake(dc_raw=2)

        result = _try_handshake(
            handshake,
            bytes.fromhex("fedcba9876543210fedcba9876543210"),
        )

        self.assertIsNone(result)

    def test_generate_relay_init_encodes_proto_and_signed_dc(self):
        relay_init = _generate_relay_init(PROTO_TAG_ABRIDGED, -3)
        decryptor = Cipher(
            algorithms.AES(relay_init[8:40]),
            modes.CTR(relay_init[40:56]),
        ).encryptor()

        decrypted = decryptor.update(relay_init)

        self.assertEqual(decrypted[56:60], PROTO_TAG_ABRIDGED)
        self.assertEqual(struct.unpack("<h", decrypted[60:62])[0], -3)


class MsgSplitterTests(unittest.TestCase):
    def test_splitter_splits_multiple_abridged_messages(self):
        relay_init = _generate_relay_init(PROTO_TAG_ABRIDGED, -2)
        plain_chunk = b"\x01abcd\x02EFGH1234"
        encrypted_chunk = _encrypt_after_init(relay_init, plain_chunk)

        parts = _MsgSplitter(relay_init, PROTO_ABRIDGED_INT).split(encrypted_chunk)

        self.assertEqual(parts, [encrypted_chunk[:5], encrypted_chunk[5:14]])

    def test_splitter_leaves_single_message_intact(self):
        relay_init = _generate_relay_init(PROTO_TAG_ABRIDGED, 2)
        plain_chunk = b"\x02abcdefgh"
        encrypted_chunk = _encrypt_after_init(relay_init, plain_chunk)

        parts = _MsgSplitter(relay_init, PROTO_ABRIDGED_INT).split(encrypted_chunk)

        self.assertEqual(parts, [encrypted_chunk])


if __name__ == "__main__":
    unittest.main()
