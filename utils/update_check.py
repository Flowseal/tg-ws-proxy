"""One startup check against GitHub Releases and verified asset downloads."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import suppress
from http.client import HTTPException
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request

from proxy.utils import build_github_opener

REPO = "Flowseal/tg-ws-proxy"
RELEASES_LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"

_SHA256 = re.compile(r"sha256:([0-9a-f]{64})", re.IGNORECASE)
_state: Dict[str, Any] = {
    "checked": False,
    "has_update": False,
    "ahead_of_release": False,
    "latest": None,
    "html_url": RELEASES_PAGE_URL,
    "error": None,
    "assets": [],
}


def _parse_version_tuple(s: str) -> tuple:
    parts = []
    for part in (s or "").strip().lstrip("vV").split("."):
        match = re.match(r"\d+", part)
        parts.append(int(match[0]) if match else 0)
    return tuple(parts)


def _version_gt(a: str, b: str) -> bool:
    for x, y in zip_longest(_parse_version_tuple(a), _parse_version_tuple(b), fillvalue=0):
        if x != y:
            return x > y
    return False


def fetch_latest_release(timeout: float = 12.0) -> dict:
    req = Request(RELEASES_LATEST_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "tg-ws-proxy-update-check",
    })
    with build_github_opener().open(req, timeout=timeout) as resp:
        return json.load(resp)


def _extract_assets(data: dict) -> list:
    assets = (data or {}).get("assets") or []
    if not isinstance(assets, list):
        raise ValueError("Invalid release assets from GitHub")
    return [
        {"name": a["name"], "url": a["browser_download_url"],
         "digest": a.get("digest") if isinstance(a.get("digest"), str) else ""}
        for a in assets
        if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"]
        and isinstance(a.get("browser_download_url"), str) and a["browser_download_url"]
    ]


def run_check(current_version: str) -> None:
    """Publish the complete result at once; no persistent cache or error cooldown."""
    global _state
    state = {
        "checked": True, "has_update": False, "ahead_of_release": False,
        "latest": None, "html_url": RELEASES_PAGE_URL, "error": None, "assets": [],
    }
    try:
        data = fetch_latest_release()
        if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
            raise ValueError("Invalid release from GitHub")
        latest = data["tag_name"].strip().lstrip("vV")
        if not latest or not latest[0].isdigit():
            raise ValueError("Invalid release version from GitHub")
        state.update(
            latest=latest,
            has_update=_version_gt(latest, current_version),
            ahead_of_release=_version_gt(current_version, latest),
            html_url=data.get("html_url") or RELEASES_PAGE_URL,
            assets=_extract_assets(data),
        )
    except (OSError, ValueError, HTTPException) as exc:
        state["error"] = str(exc) or type(exc).__name__
        logging.getLogger(__name__).warning("Update check failed: %s", exc)
    _state = state


def download_asset(
    url: str, dest_dir: Path, suffix: str = ".tmp", digest: str = "",
    timeout: float = 30.0,
) -> Path:
    """Return a temporary file only after its mandatory SHA-256 has matched."""
    expected = _SHA256.fullmatch(digest if isinstance(digest, str) else "")
    if not expected:
        raise ValueError("The release asset has no valid SHA-256 checksum")

    fd, name = tempfile.mkstemp(dir=str(dest_dir), suffix=suffix, prefix="TgWsProxy_tmp_")
    path = Path(name)
    try:
        sha = hashlib.sha256()
        with os.fdopen(fd, "wb") as out, build_github_opener().open(url, timeout=timeout) as resp:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
        if sha.hexdigest() != expected[1].lower():
            raise OSError("SHA-256 checksum does not match the release asset")
        return path
    except BaseException:
        with suppress(OSError):
            path.unlink()
        raise


def find_asset(name: str) -> Optional[Dict[str, str]]:
    """Find an asset suitable for automatic installation."""
    for asset in _state["assets"]:
        if asset["name"] == name and asset["url"] and _SHA256.fullmatch(asset["digest"]):
            return dict(asset)
    return None


def get_status() -> Dict[str, Any]:
    state = _state
    return {**state, "assets": [dict(a) for a in state["assets"]]}


def relaunch_env() -> Dict[str, str]:
    """Use system libraries and unpack a fresh bundle with PyInstaller 5 or 6."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI_", "_MEIPASS"))}
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for var in ("PATH", "LD_LIBRARY_PATH", "GI_TYPELIB_PATH", "GTK_PATH",
                "GST_PLUGIN_PATH", "GIO_MODULE_DIR"):
        original = env.pop(var + "_ORIG", None)
        if original is not None:
            env[var] = original
        elif mei and env.get(var):
            bundle = os.path.normcase(os.path.abspath(mei))
            kept = [p for p in env[var].split(os.pathsep)
                    if not (os.path.normcase(os.path.abspath(p)) + os.sep).startswith(bundle + os.sep)]
            if kept:
                env[var] = os.pathsep.join(kept)
            else:
                env.pop(var, None)
    return env
