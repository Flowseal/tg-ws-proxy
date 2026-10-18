"""Windows build selection and executable replacement with rollback."""
from __future__ import annotations

import ctypes
import subprocess
import sys
import sysconfig
from contextlib import suppress
from pathlib import Path
from typing import Callable, Optional

from utils.update_check import download_asset, relaunch_env


def asset_name() -> Optional[str]:
    # Use the running build, including x86/x64 emulation and renamed executables.
    target = sysconfig.get_platform()
    if target == "win-arm64":
        return "TgWsProxy_windows_arm64.exe"
    if target == "win32":
        return "TgWsProxy_windows_7_32bit.exe"
    if target == "win-amd64":
        return "TgWsProxy_windows_7_64bit.exe" if sys.version_info < (3, 9) else "TgWsProxy_windows.exe"
    return None


def apply_update(asset: dict, before_restart: Callable[[], None]) -> None:
    """Verify, replace and launch; keep the old executable if any step fails."""
    exe = Path(sys.executable)
    backup = exe.with_name(exe.stem + "_oldtgws.exe")
    downloaded = download_asset(asset["url"], exe.parent, digest=asset["digest"])
    try:
        backup.unlink(missing_ok=True)
        exe.rename(backup)
        try:
            downloaded.replace(exe)
            before_restart()
            bundle = getattr(sys, "_MEIPASS", None)
            if bundle:
                ctypes.windll.kernel32.SetDllDirectoryW(None)
            try:
                subprocess.Popen(
                    [str(exe), *sys.argv[1:]], env=relaunch_env(),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            finally:
                if bundle:
                    ctypes.windll.kernel32.SetDllDirectoryW(ctypes.c_wchar_p(bundle))
        except BaseException:
            backup.replace(exe)
            raise
    finally:
        with suppress(OSError):
            downloaded.unlink(missing_ok=True)
