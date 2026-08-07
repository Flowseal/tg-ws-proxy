import os
import unittest

from proxy._aes import Cipher, algorithms, modes
from proxy.bridge import MsgSplitter
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
        splitter = MsgSplitter(relay_init, proto_int)
        enc = _encryptor(relay_init)
        stream = enc.update(b''.join(packets))

        chunks = []
        if chunk_sizes is None:
            chunks = [stream]
        else:
            offset = 0
            for size in chunk_sizes:
                chunks.append(stream[offset:offset + size])
                offset += size
            if offset < len(stream):
                chunks.append(stream[offset:])

        parts = []
        for chunk in chunks:
            parts.extend(splitter.split(chunk))
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
        splitter = MsgSplitter(_relay_init(), PROTO_INTERMEDIATE_INT)
        self.assertEqual(splitter.split(b''), [])

    def test_zero_length_packet_disables_splitting(self):
        relay_init = _relay_init()
        splitter = MsgSplitter(relay_init, PROTO_INTERMEDIATE_INT)
        enc = _encryptor(relay_init)
        stream = enc.update((0).to_bytes(4, 'little') + b'tail')
        parts = splitter.split(stream)
        self.assertEqual(parts, [stream])
        self.assertEqual(splitter.split(b'raw'), [b'raw'])

    def test_flush_returns_buffered_tail_once(self):
        relay_init = _relay_init()
        splitter = MsgSplitter(relay_init, PROTO_INTERMEDIATE_INT)
        enc = _encryptor(relay_init)
        partial = enc.update(_intermediate(b'x' * 32)[:10])
        self.assertEqual(splitter.split(partial), [])
        self.assertEqual(splitter.flush(), [partial])
        self.assertEqual(splitter.flush(), [])


if __name__ == '__main__':
    unittest.main()
