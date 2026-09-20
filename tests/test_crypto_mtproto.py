import importlib.util
import subprocess
import sys
from pathlib import Path


def test_android_aes_ctr_without_native_backends(monkeypatch):
    import builtins
    original_import = builtins.__import__
    def no_crypto(name, *args, **kwargs):
        if name.startswith('cryptography') or name.startswith('ctypes'):
            raise ImportError(name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_crypto)
    monkeypatch.setenv('TG_WS_PROXY_CRYPTO_BACKEND', 'python')
    path = Path(__file__).resolve().parents[1] / 'proxy/_aes.py'
    spec = importlib.util.spec_from_file_location('proxy._aes_android_test', path)
    aes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(aes)
    key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
    iv = bytes.fromhex('f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff')
    plain = bytes.fromhex('6bc1bee22e409f96e93d7e117393172a')
    expected = bytes.fromhex('874d6191b620e3261bef6864990db6ce')
    cipher = aes.Cipher(aes.algorithms.AES(key), aes.modes.CTR(iv))
    stream = cipher.encryptor()
    assert stream.update(plain[:5]) + stream.update(plain[5:]) == expected
    assert cipher.decryptor().update(expected) == plain


def test_real_aes_import_selects_android_backend_before_bridge():
    root = Path(__file__).resolve().parents[1]
    code = """import builtins, os, sys
os.environ.pop('TG_WS_PROXY_CRYPTO_BACKEND', None)
sys.getandroidapilevel = lambda: 35
real_import = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.startswith(('cryptography', 'ctypes')):
        raise ImportError(name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = blocked
from proxy._aes import Cipher, algorithms, modes
key = bytes.fromhex('2b7e151628aed2a6abf7158809cf4f3c')
iv = bytes.fromhex('f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff')
plain = bytes.fromhex('6bc1bee22e409f96e93d7e117393172a')
assert Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor().update(plain).hex() == '874d6191b620e3261bef6864990db6ce'
"""
    result = subprocess.run([sys.executable, '-c', code], cwd=str(root),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
