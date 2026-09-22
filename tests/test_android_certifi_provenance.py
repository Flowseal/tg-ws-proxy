from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "android" / "verify_vendored_certifi.py"
VENDOR = ROOT / "android" / "app" / "src" / "main" / "python" / "certifi"


def run_verifier(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--offline", *args],
        cwd=ROOT, capture_output=True, text=True,
    )


def test_pinned_vendor_hashes_pass():
    result = run_verifier()
    assert result.returncode == 0, result.stderr


def test_changed_vendor_file_is_rejected(tmp_path):
    for name in ("__init__.py", "core.py", "cacert.pem", "LICENSE"):
        (tmp_path / name).write_bytes((VENDOR / name).read_bytes())
    (tmp_path / "core.py").write_text("tampered\n")

    result = run_verifier("--vendor-dir", str(tmp_path))
    assert result.returncode != 0
    assert "core.py" in result.stderr
