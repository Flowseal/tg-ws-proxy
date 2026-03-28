import struct
import unittest

from proxy.crypto_backend import create_aes_ctr_transform
from proxy.tg_ws_proxy import _MsgSplitter, _dc_from_init, _patch_init_dc


KEY = bytes(range(32))
IV = bytes(range(16))
PROTO_TAG = 0xEFEFEFEF


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right))


def _keystream(size: int) -> bytes:
    transform = create_aes_ctr_transform(KEY, IV)
    return transform.update(b"\x00" * size) + transform.finalize()


def _build_init_packet(dc_raw: int, proto: int = PROTO_TAG) -> bytes:
    packet = bytearray(64)
    packet[8:40] = KEY
    packet[40:56] = IV

    plain_tail = struct.pack("<Ih", proto, dc_raw) + b"\x00\x00"
    packet[56:64] = _xor(plain_tail, _keystream(64)[56:64])
    return bytes(packet)


def _encrypt_after_init(init_packet: bytes, plaintext: bytes) -> bytes:
    transform = create_aes_ctr_transform(init_packet[8:40], init_packet[40:56])
    transform.update(b"\x00" * 64)
    return transform.update(plaintext) + transform.finalize()


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


class MtProtoInitTests(unittest.TestCase):
    def test_dc_from_init_reads_non_media_dc(self):
        init_packet = _build_init_packet(dc_raw=2)

        self.assertEqual(_dc_from_init(init_packet), (2, False))

    def test_dc_from_init_reads_media_dc(self):
        init_packet = _build_init_packet(dc_raw=-4)

        self.assertEqual(_dc_from_init(init_packet), (4, True))

    def test_patch_init_dc_updates_signed_dc_and_preserves_tail(self):
        original = _build_init_packet(dc_raw=99) + b"tail"

        patched = _patch_init_dc(original, -3)

        self.assertEqual(_dc_from_init(patched[:64]), (3, True))
        self.assertEqual(patched[64:], b"tail")


class MsgSplitterTests(unittest.TestCase):
    def test_splitter_splits_multiple_abridged_messages(self):
        init_packet = _build_init_packet(dc_raw=-2)
        plain_chunk = b"\x01abcd\x02EFGH1234"
        encrypted_chunk = _encrypt_after_init(init_packet, plain_chunk)

        parts = _MsgSplitter(init_packet).split(encrypted_chunk)

        self.assertEqual(parts, [encrypted_chunk[:5], encrypted_chunk[5:14]])

    def test_splitter_leaves_single_message_intact(self):
        init_packet = _build_init_packet(dc_raw=2)
        plain_chunk = b"\x02abcdefgh"
        encrypted_chunk = _encrypt_after_init(init_packet, plain_chunk)

        parts = _MsgSplitter(init_packet).split(encrypted_chunk)

        self.assertEqual(parts, [encrypted_chunk])


if __name__ == "__main__":
    unittest.main()
