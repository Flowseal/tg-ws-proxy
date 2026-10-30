"""
AES-CTR backed by javax.crypto through the Chaquopy Java bridge.

Replaces proxy/_aes.py in Android builds: `cryptography` is not available
on Chaquopy and the ctypes/libcrypto fallback cannot load on Android.
Exposes the same subset used by proxy: Cipher(AES(key), CTR(iv)).encryptor().update(data).
"""
from __future__ import annotations

from java import jclass

_JCipher = jclass("javax.crypto.Cipher")
_JSecretKeySpec = jclass("javax.crypto.spec.SecretKeySpec")
_JIvParameterSpec = jclass("javax.crypto.spec.IvParameterSpec")


class algorithms:
    class AES:
        __slots__ = ("key",)

        def __init__(self, key: bytes):
            if len(key) not in (16, 24, 32):
                raise ValueError("AES key must be 16/24/32 bytes")
            self.key = bytes(key)


class modes:
    class CTR:
        __slots__ = ("iv",)

        def __init__(self, iv: bytes):
            if len(iv) != 16:
                raise ValueError("CTR IV must be 16 bytes")
            self.iv = bytes(iv)


class _CtrStream:
    __slots__ = ("_cipher",)

    def __init__(self, key: bytes, iv: bytes):
        cipher = _JCipher.getInstance("AES/CTR/NoPadding")
        cipher.init(
            _JCipher.ENCRYPT_MODE,
            _JSecretKeySpec(key, "AES"),
            _JIvParameterSpec(iv),
        )
        self._cipher = cipher

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        return bytes(self._cipher.update(bytes(data)))


class Cipher:
    __slots__ = ("_key", "_iv")

    def __init__(self, algorithm, mode):
        if not isinstance(algorithm, algorithms.AES):
            raise TypeError("only AES is supported")
        if not isinstance(mode, modes.CTR):
            raise TypeError("only CTR mode is supported")
        self._key = algorithm.key
        self._iv = mode.iv

    def encryptor(self) -> _CtrStream:
        return _CtrStream(self._key, self._iv)

    # CTR is symmetric: decryption is encryption with the same keystream.
    decryptor = encryptor
