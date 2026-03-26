"""
Минимальная проверка новой версии через GitHub Releases API (без сторонних зависимостей).
"""
from __future__ import annotations

import json
from itertools import zip_longest
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = "Flowseal/tg-ws-proxy"
RELEASES_LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"

_state: Dict[str, Any] = {
    "checked": False,
    "has_update": False,
    "ahead_of_release": False,
    "latest": None,
    "html_url": None,
    "error": None,
}


def _parse_version_tuple(s: str) -> tuple:
    s = (s or "").strip().lstrip("vV")
    if not s:
        return (0,)
    parts = []
    for seg in s.split("."):
        digits = "".join(c for c in seg if c.isdigit())
        if digits:
            try:
                parts.append(int(digits))
            except ValueError:
                parts.append(0)
        else:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def _version_gt(a: str, b: str) -> bool:
    """True, если версия a новее b (простое сравнение по сегментам)."""
    ta = _parse_version_tuple(a)
    tb = _parse_version_tuple(b)
    for x, y in zip_longest(ta, tb, fillvalue=0):
        if x > y:
            return True
        if x < y:
            return False
    return False


def fetch_latest_release(timeout: float = 12.0) -> Optional[dict]:
    req = Request(
        RELEASES_LATEST_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tg-ws-proxy-update-check",
        },
        method="GET",
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def run_check(current_version: str) -> None:
    """Запрашивает последний релиз и обновляет внутреннее состояние."""
    global _state
    _state["checked"] = True
    _state["error"] = None
    try:
        data = fetch_latest_release()
        tag = (data.get("tag_name") or "").strip()
        html_url = (data.get("html_url") or "").strip() or RELEASES_PAGE_URL
        if not tag:
            _state["has_update"] = False
            _state["ahead_of_release"] = False
            _state["latest"] = None
            _state["html_url"] = html_url
            return
        latest_clean = tag.lstrip("vV")
        cur = (current_version or "").strip().lstrip("vV")
        _state["latest"] = latest_clean
        _state["html_url"] = html_url
        _state["has_update"] = _version_gt(latest_clean, cur)
        _state["ahead_of_release"] = bool(latest_clean) and _version_gt(
            cur, latest_clean
        )
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        _state["error"] = str(e)
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = RELEASES_PAGE_URL


def get_status() -> Dict[str, Any]:
    """Снимок состояния после run_check (для подписей в настройках)."""
    return dict(_state)
