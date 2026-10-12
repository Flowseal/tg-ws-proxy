"""
In-place update on Linux.

Detects how the app is installed (deb/rpm package or a standalone binary)
and updates it without a manual download: packages go through the package
manager, a standalone binary is replaced with the release file. Without
write access the install runs through pkexec. Unsupported setups (AUR, pip,
non-amd64, no pkexec) yield None and the caller shows the release page.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from http.client import HTTPException
from pathlib import Path
from typing import List, Optional

from utils.update_check import download_asset, relaunch_env

BINARY_ASSET = "TgWsProxy_linux_amd64"

# install kind -> release asset suffix
ASSET_SUFFIX = {"binary": "", "deb": ".deb", "rpm": ".rpm"}

_INSTALL_TIMEOUT_SEC = 600.0


def _run_quiet(cmd: List[str]) -> bool:
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            env=relaunch_env(),
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def exe_path() -> Path:
    return Path(os.path.realpath(sys.executable))


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _can_elevate() -> bool:
    return _is_root() or bool(shutil.which("pkexec"))


def _elevate() -> List[str]:
    return [] if _is_root() else ["pkexec"]


def detect_install_kind(exe: Optional[Path] = None) -> Optional[str]:
    """How the app is installed: "deb", "rpm", "binary" or None."""
    exe = exe or exe_path()
    if platform.machine().lower() not in ("x86_64", "amd64"):
        return None  # releases ship amd64 only
    if shutil.which("pacman") and _run_quiet(["pacman", "-Qo", str(exe)]):
        return None  # AUR install, updated by its own package manager

    can_elevate = _can_elevate()
    if shutil.which("dpkg-query") and _run_quiet(["dpkg-query", "-S", str(exe)]):
        return "deb" if can_elevate else None
    if shutil.which("rpm") and _run_quiet(["rpm", "-qf", str(exe)]):
        return "rpm" if can_elevate else None

    # replacing the binary needs a writable directory or root
    if os.access(str(exe.parent), os.W_OK) or can_elevate:
        return "binary"
    return None


def asset_name(kind: str) -> str:
    return BINARY_ASSET + ASSET_SUFFIX[kind]


def install_cmd(kind: str, path: Path) -> List[str]:
    """Command that installs the downloaded package."""
    elevate = _elevate()
    if kind == "deb":
        if shutil.which("apt-get"):
            return elevate + [
                "apt-get", "install", "-y", str(path),
            ]
        return elevate + ["dpkg", "-i", str(path)]
    if kind == "rpm":
        if shutil.which("dnf"):
            return elevate + ["dnf", "install", "-y", str(path)]
        return elevate + ["rpm", "-U", str(path)]
    raise ValueError("no install command for kind: {0}".format(kind))


def replace_binary_cmd(src: Path, exe: Path) -> List[str]:
    """Stage beside the executable so an interrupted copy cannot destroy it."""
    script = '''set -e
staged=$(mktemp "$2.XXXXXX")
trap 'rm -f -- "$staged"' EXIT
install -m 755 -- "$1" "$staged"
mv -fT -- "$staged" "$2"
'''
    return _elevate() + ["sh", "-c", script, "sh", str(src), str(exe)]


def _run_install(cmd: List[str], log: Optional[logging.Logger] = None) -> Optional[str]:
    if log:
        log.info("Linux update: %s", " ".join(cmd))
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=_INSTALL_TIMEOUT_SEC,
        env=relaunch_env(),
    )
    if proc.returncode == 0:
        return None
    err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
    return err[-500:] or "exit code {0}".format(proc.returncode)


def apply_update(
    kind: str, url: str, digest: str = "",
    log: Optional[logging.Logger] = None,
) -> Optional[str]:
    """Download and install the update.

    Returns:
        Error text, or None on success.
    """
    exe = exe_path()
    suffix = ASSET_SUFFIX[kind] or ".new"
    # with a writable directory download next to the binary and os.replace it
    in_place = kind == "binary" and os.access(str(exe.parent), os.W_OK)

    if log:
        log.info("Linux update (%s): downloading %s", kind, url)
    try:
        with tempfile.TemporaryDirectory(prefix="tgws-update-", dir=exe.parent if in_place else None) as directory:
            tmp = download_asset(url, Path(directory), suffix=suffix, digest=digest)
            if kind != "binary":
                return _run_install(install_cmd(kind, tmp), log=log)
            if in_place:
                os.chmod(str(tmp), 0o755)
                os.replace(str(tmp), str(exe))
                return None
            return _run_install(replace_binary_cmd(tmp, exe), log=log)
    except (OSError, ValueError, HTTPException, subprocess.SubprocessError) as exc:
        return str(exc)
