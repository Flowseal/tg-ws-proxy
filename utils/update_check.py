"""
Проверка новой версии через GitHub Releases API

Ограничение частоты запросов: не чаще одного раза в час на машину (кэш в каталоге
данных приложения). Поддерживается If-None-Match (ETag) для ответа 304.
"""
from __future__ import annotations

import json
import sys
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request
from proxy.utils import build_github_opener

REPO = "Flowseal/tg-ws-proxy"
RELEASES_LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_BY_TAG_API = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}?t={{timestamp}}"
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"

# Не чаще одного полного запроса к API в час (без учёта 304 с тем же ETag).
_MIN_FETCH_INTERVAL_SEC = 3600.0

_state: Dict[str, Any] = {
    "checked": False,
    "has_update": False,
    "ahead_of_release": False,
    "latest": None,
    "html_url": None,
    "error": None,
    "assets": [],
}


def _cache_file() -> Optional[Path]:
    try:
        from utils.tray_common import APP_DIR
        root = APP_DIR
        root.mkdir(parents=True, exist_ok=True)
        return root / ".update_check_cache.json"
    except OSError:
        return None


def _load_cache(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(path: Optional[Path], data: Dict[str, Any]) -> None:
    if not path:
        return
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def _parse_version_tuple(s: str) -> tuple:
    s = (s or "").strip().lstrip("vV")
    if not s:
        return (0,)
    parts = []
    for seg in s.split("."):
        digits = next((seg[:i] for i, c in enumerate(seg) if not c.isdigit()), seg)
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


def _apply_release_tag(
    tag: str, html_url: str, current_version: str,
) -> None:
    global _state
    if not tag:
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = html_url.strip() or RELEASES_PAGE_URL
        return
    latest_clean = tag.lstrip("vV")
    cur = (current_version or "").strip().lstrip("vV")
    _state["latest"] = latest_clean
    _state["html_url"] = html_url.strip() or RELEASES_PAGE_URL
    _state["has_update"] = _version_gt(latest_clean, cur)
    _state["ahead_of_release"] = bool(latest_clean) and _version_gt(
        cur, latest_clean
    )


def fetch_latest_release(
    timeout: float = 12.0,
    etag: Optional[str] = None,
) -> Tuple[Optional[dict], Optional[str], int]:
    """
    GET releases/latest. Возвращает (data или None при 304, etag или None, HTTP-код).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tg-ws-proxy-update-check",
    }
    if etag:
        headers["If-None-Match"] = etag
    req = Request(
        RELEASES_LATEST_API,
        headers=headers,
        method="GET",
    )
    try:
        with build_github_opener().open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            new_etag = resp.headers.get("ETag")
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), new_etag, int(code)
    except HTTPError as e:
        if e.code == 304:
            hdrs = e.headers
            new_etag = hdrs.get("ETag") if hdrs else None
            return None, new_etag or etag, 304
        raise


def run_check(current_version: str) -> None:
    """Запрашивает последний релиз и обновляет внутреннее состояние."""
    global _state
    _state["checked"] = True
    _state["error"] = None

    cache_path = _cache_file()
    cache = _load_cache(cache_path)
    now = time.time()
    last_attempt = float(cache.get("last_attempt_at") or 0)

    if last_attempt and (now - last_attempt) < _MIN_FETCH_INTERVAL_SEC:
        tag = (cache.get("tag_name") or "").strip()
        if tag:
            _apply_release_tag(tag, cache.get("html_url") or "", current_version)
            _state["assets"] = cache.get("assets") or []
            return
        err = cache.get("last_error")
        _state["error"] = (
            err if err else "Проверка обновлений отложена (интервал между запросами)."
        )
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = RELEASES_PAGE_URL
        return

    etag = (cache.get("etag") or "").strip() or None
    try:
        data, new_etag, code = fetch_latest_release(etag=etag)
        cache["last_attempt_at"] = now
        if code == 304:
            tag = (cache.get("tag_name") or "").strip()
            url = (cache.get("html_url") or "").strip() or RELEASES_PAGE_URL
            _apply_release_tag(tag, url, current_version)
            _state["assets"] = cache.get("assets") or []
            if new_etag:
                cache["etag"] = new_etag
            _save_cache(cache_path, cache)
            return

        assert data is not None
        tag = (data.get("tag_name") or "").strip()
        html_url = (data.get("html_url") or "").strip() or RELEASES_PAGE_URL
        if not tag:
            _state["has_update"] = False
            _state["ahead_of_release"] = False
            _state["latest"] = None
            _state["html_url"] = html_url
        else:
            _apply_release_tag(tag, html_url, current_version)
        if new_etag:
            cache["etag"] = new_etag
        cache["tag_name"] = tag
        cache["html_url"] = html_url
        assets = [
            {"name": a.get("name", ""), "url": a.get("browser_download_url", ""), "digest": a.get("digest", "")}
            for a in (data.get("assets") or [])
            if a.get("name") and a.get("browser_download_url")
        ]
        _state["assets"] = assets
        cache["assets"] = assets
        cache.pop("last_error", None)
        _save_cache(cache_path, cache)
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        cache["last_attempt_at"] = now
        msg = str(e)
        if isinstance(e, HTTPError) and e.code == 403:
            msg = (
                "GitHub API вернул 403 (лимит или доступ). Повторите позже."
            )
        cache["last_error"] = msg
        _save_cache(cache_path, cache)
        _state["error"] = msg
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = RELEASES_PAGE_URL


def fetch_release_by_tag(
    tag: str, timeout: float = 12.0,
) -> Tuple[Optional[dict], int]:
    if not tag:
        return None, 0
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tg-ws-proxy-update-check",
    }
    req = Request(
        RELEASES_BY_TAG_API.format(tag=tag, timestamp=int(time.time())),
        headers=headers,
        method="GET",
    )
    try:
        with build_github_opener().open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw), int(code)
    except HTTPError as e:
        if e.code in [304, 404]:
            return None, e.code
        raise


def _extract_assets(data: Optional[dict]) -> list:
    if not data:
        return []
    return [
        {"name": a.get("name", ""), "url": a.get("browser_download_url", ""), "digest": a.get("digest", "")}
        for a in (data.get("assets") or [])
        if a.get("name") and a.get("browser_download_url")
    ]


def get_status() -> Dict[str, Any]:
    """Снимок состояния после run_check (для подписей в настройках)."""
    return dict(_state)


def get_update_asset(exe_path: Path, current_version: str) -> Optional[Tuple[str, str]]:
    new_assets = _state.get("assets") or []
    if not new_assets:
        return None

    target_name = None

    # SHA256 match
    try:
        import hashlib
        data, code = fetch_release_by_tag(f"v{current_version}")
        if code == 200 and data:
            cur_assets = _extract_assets(data)
            if cur_assets:
                h = hashlib.sha256()
                with open(exe_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        h.update(chunk)
                exe_sha = h.hexdigest().lower()
                for a in cur_assets:
                    d = (a.get("digest") or "").lower()
                    if d.startswith("sha256:") and d[7:] == exe_sha:
                        target_name = a["name"]
                        break
    except Exception:
        pass

    # Fallback
    if not target_name or target_name not in [a.get("name") for a in new_assets]:
        import platform
        import struct

        is_64 = struct.calcsize("P") * 8 == 64
        machine = platform.machine().lower()
        is_arm64 = machine in ("arm64", "aarch64")

        try:
            is_modern = sys.getwindowsversion().major >= 10
        except Exception:
            is_modern = True

        if is_arm64:
            target_name = "TgWsProxy_windows_arm64.exe"
        elif is_modern:
            target_name = "TgWsProxy_windows.exe"
        elif is_64:
            target_name = "TgWsProxy_windows_7_64bit.exe"
        else:
            target_name = "TgWsProxy_windows_7_32bit.exe"

    for a in new_assets:
        if a.get("name") == target_name:
            return a["url"], a["name"]

    return None
