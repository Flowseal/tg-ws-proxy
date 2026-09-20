"""Verify Android's pinned certifi 2026.7.22 files without modifying them.

Source: https://pypi.org/project/certifi/2026.7.22/
Artifact: https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/certifi-2026.7.22-py3-none-any.whl
SHA-256: 62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775
"""

import argparse
import hashlib
import io
from pathlib import Path
import sys
from urllib.request import urlopen
import zipfile


WHEEL_URL = (
    "https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/"
    "certifi-2026.7.22-py3-none-any.whl"
)
WHEEL_SHA256 = "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775"
VENDOR_DIR = Path(__file__).resolve().parent / "app/src/main/python/certifi"
FILES = {
    "__init__.py": (
        "certifi/__init__.py",
        "7a52e5f4205f9b3d12a31898ceaa7e3f6837d19cc230700bcae1a9d91d1486c1",
    ),
    "core.py": (
        "certifi/core.py",
        "5c55f2727746e697f7edac9e17c377d8752e0da7ecca191531b3b80403d61dad",
    ),
    "cacert.pem": (
        "certifi/cacert.pem",
        "9cc2a774b5198dcff14d9be1e66091f538975d867ce029a96bce15a55dfd730f",
    ),
    "LICENSE": (
        "certifi-2026.7.22.dist-info/licenses/LICENSE",
        "e93716da6b9c0d5a4a1df60fe695b370f0695603d21f6f83f053e42cfc10caf7",
    ),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def verify(vendor_dir, offline):
    local = {}
    for name, (_, expected_hash) in FILES.items():
        data = (vendor_dir / name).read_bytes()
        actual_hash = sha256(data)
        if actual_hash != expected_hash:
            raise ValueError(f"{name}: SHA-256 {actual_hash} != {expected_hash}")
        local[name] = data

    if offline:
        return

    with urlopen(WHEEL_URL, timeout=30) as response:
        wheel_bytes = response.read()
    if sha256(wheel_bytes) != WHEEL_SHA256:
        raise ValueError("upstream wheel SHA-256 mismatch")
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as wheel:
        for name, (member, _) in FILES.items():
            if wheel.read(member) != local[name]:
                raise ValueError(f"{name}: vendored bytes differ from upstream wheel")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="check pinned local SHA-256 only")
    parser.add_argument("--vendor-dir", type=Path, default=VENDOR_DIR)
    args = parser.parse_args()
    try:
        verify(args.vendor_dir, args.offline)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"certifi verification failed: {error}", file=sys.stderr)
        return 1
    print("certifi 2026.7.22 vendor verified" + (" (offline hashes)" if args.offline else " (PyPI wheel)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
