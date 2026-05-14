from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("tg-ws-tray")


@dataclass(frozen=True)
class MeiCleanupResult:
    removed: int
    failed: int
    temp_dir: str
    errors: tuple[str, ...]


def mei_cleanup_available() -> bool:
    return sys.platform == "win32"


def cleanup_pyinstaller_mei_dirs() -> MeiCleanupResult:
    if not mei_cleanup_available():
        return MeiCleanupResult(0, 0, "", ())

    temp = Path(tempfile.gettempdir())
    skip: set[str] = set()
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        skip.add(os.path.normcase(os.path.normpath(sys._MEIPASS)))

    try:
        entries = list(temp.iterdir())
    except OSError as exc:
        log.warning("MEI cleanup: cannot list %s: %s", temp, exc)
        return MeiCleanupResult(0, 1, str(temp), (str(exc),))

    removed = 0
    failed = 0
    errors: list[str] = []

    for p in sorted(entries):
        if not p.is_dir() or not p.name.startswith("_MEI"):
            continue
        norm = os.path.normcase(os.path.normpath(str(p)))
        if norm in skip:
            continue
        try:
            shutil.rmtree(p, ignore_errors=False)
            removed += 1
            log.info("MEI cleanup: removed %s", p)
        except OSError as exc:
            failed += 1
            msg = f"{p.name}: {exc}"
            errors.append(msg)
            log.warning("MEI cleanup: %s", msg)

    return MeiCleanupResult(
        removed=removed, failed=failed, temp_dir=str(temp), errors=tuple(errors)
    )
