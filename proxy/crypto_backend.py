from __future__ import annotations

import os
from typing import Protocol


_SBOX = (
    0x63, 0x7C, 0x77, 0x7B, 0xF2, 0x6B, 0x6F, 0xC5, 0x30, 0x01, 0x67, 0x2B,
    0xFE, 0xD7, 0xAB, 0x76, 0xCA, 0x82, 0xC9, 0x7D, 0xFA, 0x59, 0x47, 0xF0,
    0xAD, 0xD4, 0xA2, 0xAF, 0x9C, 0xA4, 0x72, 0xC0, 0xB7, 0xFD, 0x93, 0x26,
    0x36, 0x3F, 0xF7, 0xCC, 0x34, 0xA5, 0xE5, 0xF1, 0x71, 0xD8, 0x31, 0x15,
    0x04, 0xC7, 0x23, 0xC3, 0x18, 0x96, 0x05, 0x9A, 0x07, 0x12, 0x80, 0xE2,
    0xEB, 0x27, 0xB2, 0x75, 0x09, 0x83, 0x2C, 0x1A, 0x1B, 0x6E, 0x5A, 0xA0,
    0x52, 0x3B, 0xD6, 0xB3, 0x29, 0xE3, 0x2F, 0x84, 0x53, 0xD1, 0x00, 0xED,
    0x20, 0xFC, 0xB1, 0x5B, 0x6A, 0xCB, 0xBE, 0x39, 0x4A, 0x4C, 0x58, 0xCF,
    0xD0, 0xEF, 0xAA, 0xFB, 0x43, 0x4D, 0x33, 0x85, 0x45, 0xF9, 0x02, 0x7F,
    0x50, 0x3C, 0x9F, 0xA8, 0x51, 0xA3, 0x40, 0x8F, 0x92, 0x9D, 0x38, 0xF5,
    0xBC, 0xB6, 0xDA, 0x21, 0x10, 0xFF, 0xF3, 0xD2, 0xCD, 0x0C, 0x13, 0xEC,
    0x5F, 0x97, 0x44, 0x17, 0xC4, 0xA7, 0x7E, 0x3D, 0x64, 0x5D, 0x19, 0x73,
    0x60, 0x81, 0x4F, 0xDC, 0x22, 0x2A, 0x90, 0x88, 0x46, 0xEE, 0xB8, 0x14,
    0xDE, 0x5E, 0x0B, 0xDB, 0xE0, 0x32, 0x3A, 0x0A, 0x49, 0x06, 0x24, 0x5C,
    0xC2, 0xD3, 0xAC, 0x62, 0x91, 0x95, 0xE4, 0x79, 0xE7, 0xC8, 0x37, 0x6D,
    0x8D, 0xD5, 0x4E, 0xA9, 0x6C, 0x56, 0xF4, 0xEA, 0x65, 0x7A, 0xAE, 0x08,
    0xBA, 0x78, 0x25, 0x2E, 0x1C, 0xA6, 0xB4, 0xC6, 0xE8, 0xDD, 0x74, 0x1F,
    0x4B, 0xBD, 0x8B, 0x8A, 0x70, 0x3E, 0xB5, 0x66, 0x48, 0x03, 0xF6, 0x0E,
    0x61, 0x35, 0x57, 0xB9, 0x86, 0xC1, 0x1D, 0x9E, 0xE1, 0xF8, 0x98, 0x11,
    0x69, 0xD9, 0x8E, 0x94, 0x9B, 0x1E, 0x87, 0xE9, 0xCE, 0x55, 0x28, 0xDF,
    0x8C, 0xA1, 0x89, 0x0D, 0xBF, 0xE6, 0x42, 0x68, 0x41, 0x99, 0x2D, 0x0F,
    0xB0, 0x54, 0xBB, 0x16,
)
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


class AesCtrTransform(Protocol):
    def update(self, data: bytes) -> bytes:
        ...

    def finalize(self) -> bytes:
        ...


def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def _mul2(value: int) -> int:
    return _xtime(value)


def _mul3(value: int) -> int:
    return _xtime(value) ^ value


def _add_round_key(state: list[int], round_key: bytes):
    for idx in range(16):
        state[idx] ^= round_key[idx]


def _sub_bytes(state: list[int]):
    for idx in range(16):
        state[idx] = _SBOX[state[idx]]


def _shift_rows(state: list[int]):
    state[1], state[5], state[9], state[13] = (
        state[5], state[9], state[13], state[1]
    )
    state[2], state[6], state[10], state[14] = (
        state[10], state[14], state[2], state[6]
    )
    state[3], state[7], state[11], state[15] = (
        state[15], state[3], state[7], state[11]
    )


def _mix_columns(state: list[int]):
    for offset in range(0, 16, 4):
        s0, s1, s2, s3 = state[offset:offset + 4]
        state[offset + 0] = _mul2(s0) ^ _mul3(s1) ^ s2 ^ s3
        state[offset + 1] = s0 ^ _mul2(s1) ^ _mul3(s2) ^ s3
        state[offset + 2] = s0 ^ s1 ^ _mul2(s2) ^ _mul3(s3)
        state[offset + 3] = _mul3(s0) ^ s1 ^ s2 ^ _mul2(s3)


def _rot_word(word: list[int]) -> list[int]:
    return word[1:] + word[:1]


def _sub_word(word: list[int]) -> list[int]:
    return [_SBOX[value] for value in word]


def _expand_round_keys(key: bytes) -> tuple[list[bytes], int]:
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24, or 32 bytes long")

    nk = len(key) // 4
    nr = {4: 10, 6: 12, 8: 14}[nk]
    words = [list(key[idx:idx + 4]) for idx in range(0, len(key), 4)]
    total_words = 4 * (nr + 1)

    for idx in range(nk, total_words):
        temp = words[idx - 1][:]
        if idx % nk == 0:
            temp = _sub_word(_rot_word(temp))
            temp[0] ^= _RCON[idx // nk - 1]
        elif nk > 6 and idx % nk == 4:
            temp = _sub_word(temp)
        words.append([
            words[idx - nk][byte_idx] ^ temp[byte_idx]
            for byte_idx in range(4)
        ])

    round_keys = []
    for round_idx in range(nr + 1):
        start = round_idx * 4
        round_keys.append(bytes(sum(words[start:start + 4], [])))
    return round_keys, nr


class _PurePythonAesCtrTransform:
    def __init__(self, key: bytes, iv: bytes):
        if len(iv) != 16:
            raise ValueError("AES-CTR IV must be 16 bytes long")
        self._round_keys, self._rounds = _expand_round_keys(key)
        self._counter = bytearray(iv)
        self._buffer = b""
        self._buffer_offset = 0

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""

        out = bytearray(len(data))
        data_offset = 0

        while data_offset < len(data):
            if self._buffer_offset >= len(self._buffer):
                self._buffer = self._encrypt_block(bytes(self._counter))
                self._buffer_offset = 0
                self._increment_counter()

            available = len(self._buffer) - self._buffer_offset
            chunk_size = min(len(data) - data_offset, available)
            for chunk_idx in range(chunk_size):
                out[data_offset + chunk_idx] = (
                    data[data_offset + chunk_idx]
                    ^ self._buffer[self._buffer_offset + chunk_idx]
                )
            data_offset += chunk_size
            self._buffer_offset += chunk_size

        return bytes(out)

    def finalize(self) -> bytes:
        return b""

    def _encrypt_block(self, block: bytes) -> bytes:
        state = list(block)
        _add_round_key(state, self._round_keys[0])

        for round_idx in range(1, self._rounds):
            _sub_bytes(state)
            _shift_rows(state)
            _mix_columns(state)
            _add_round_key(state, self._round_keys[round_idx])

        _sub_bytes(state)
        _shift_rows(state)
        _add_round_key(state, self._round_keys[self._rounds])
        return bytes(state)

    def _increment_counter(self):
        for idx in range(15, -1, -1):
            self._counter[idx] = (self._counter[idx] + 1) & 0xFF
            if self._counter[idx] != 0:
                break


def _create_cryptography_transform(key: bytes,
                                   iv: bytes) -> AesCtrTransform:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    cipher = Cipher(algorithms.AES(key), modes.CTR(iv))
    return cipher.encryptor()


def create_aes_ctr_transform(key: bytes, iv: bytes,
                             backend: str | None = None) -> AesCtrTransform:
    """
    Create a stateful AES-CTR transform.

    Windows keeps using `cryptography` by default. Android can select the
    pure-Python backend to avoid native build dependencies.
    """
    selected = backend or os.environ.get(
        'TG_WS_PROXY_CRYPTO_BACKEND', 'cryptography')

    if selected == 'cryptography':
        return _create_cryptography_transform(key, iv)

    if selected == 'python':
        return _PurePythonAesCtrTransform(key, iv)

    raise ValueError(f"Unsupported AES-CTR backend: {selected}")
