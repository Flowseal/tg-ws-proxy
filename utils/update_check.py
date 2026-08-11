"""
Проверка новой версии через GitHub Releases API
Ограничение частоты запросов: не чаще одного раза в час на машину (кэш в каталоге
данных приложения). Поддерживается If-None-Match (ETag) для ответа 304.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import struct
import sys
import tempfile
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
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

        root = Path(APP_DIR)
        root.mkdir(parents=True, exist_ok=True)
        return root / ".update_check_cache.json"
    except (ImportError, OSError, AttributeError, TypeError):
        return None


def _load_cache(path: Optional[Path]) -> Dict[str, Any]:
    if not path or not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(path: Optional[Path], data: Dict[str, Any]) -> None:
    if not path:
        return

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )

        try:
            os.close(fd)

            with open(tmp_name, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
    except (OSError, TypeError, ValueError):
        pass


def _parse_version_tuple(s: str) -> Tuple[int, ...]:
    s = (s or "").strip().lstrip("vV")
    if not s:
        return (0,)

    parts = []
    for seg in s.split("."):
        seg = seg.strip()
        digits = next((seg[:i] for i, c in enumerate(seg) if not c.isdigit()), seg)

        try:
            parts.append(int(digits) if digits else 0)
        except ValueError:
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
    tag: str,
    html_url: str,
    current_version: str,
) -> None:
    global _state

    html_url = (html_url or "").strip() or RELEASES_PAGE_URL
    latest_clean = (tag or "").strip().lstrip("vV")
    cur = (current_version or "").strip().lstrip("vV")

    _state["latest"] = latest_clean or None
    _state["html_url"] = html_url
    _state["has_update"] = bool(latest_clean) and _version_gt(latest_clean, cur)
    _state["ahead_of_release"] = bool(latest_clean) and _version_gt(cur, latest_clean)


def fetch_latest_release(
    timeout: float = 12.0,
    etag: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
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

            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Некорректный ответ GitHub API")

            return payload, new_etag, int(code)
    except HTTPError as e:
        if e.code == 304:
            headers = getattr(e, "headers", None)
            new_etag = headers.get("ETag") if headers else None
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

    try:
        last_attempt = float(cache.get("last_attempt_at") or 0)
    except (TypeError, ValueError):
        last_attempt = 0.0

    if last_attempt and (now - last_attempt) < _MIN_FETCH_INTERVAL_SEC:
        tag = str(cache.get("tag_name") or "").strip()

        if tag:
            _apply_release_tag(tag, cache.get("html_url") or "", current_version)
            assets = cache.get("assets") or []
            _state["assets"] = list(assets) if isinstance(assets, list) else []
            return

        err = cache.get("last_error")
        _state["error"] = str(err) if err else "Проверка обновлений отложена (интервал между запросами)."
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = RELEASES_PAGE_URL
        return

    etag = str(cache.get("etag") or "").strip() or None

    try:
        data, new_etag, code = fetch_latest_release(etag=etag)
        cache["last_attempt_at"] = now

        if code == 304:
            tag = str(cache.get("tag_name") or "").strip()
            url = str(cache.get("html_url") or "").strip() or RELEASES_PAGE_URL
            _apply_release_tag(tag, url, current_version)

            assets = cache.get("assets") or []
            _state["assets"] = list(assets) if isinstance(assets, list) else []

            if new_etag:
                cache["etag"] = new_etag

            _save_cache(cache_path, cache)
            return

        if data is None:
            raise ValueError("Пустой ответ от GitHub API")

        tag = str(data.get("tag_name") or "").strip()
        html_url = str(data.get("html_url") or "").strip() or RELEASES_PAGE_URL
        _apply_release_tag(tag, html_url, current_version)

        if new_etag:
            cache["etag"] = new_etag

        cache["tag_name"] = tag
        cache["html_url"] = html_url

        assets = []
        for a in data.get("assets") or []:
            if not isinstance(a, dict):
                continue

            name = str(a.get("name") or "").strip()
            url = str(a.get("browser_download_url") or "").strip()
            digest = str(a.get("digest") or "").strip()

            if name and url:
                assets.append(
                    {
                        "name": name,
                        "url": url,
                        "digest": digest,
                    }
                )

        _state["assets"] = assets
        cache["assets"] = assets
        cache.pop("last_error", None)

        _save_cache(cache_path, cache)

    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as e:
        cache["last_attempt_at"] = now
        msg = str(e)

        if isinstance(e, HTTPError) and e.code == 403:
            msg = "GitHub API вернул 403 (лимит или доступ). Повторите позже."

        cache["last_error"] = msg
        _save_cache(cache_path, cache)

        _state["error"] = msg
        _state["has_update"] = False
        _state["ahead_of_release"] = False
        _state["latest"] = None
        _state["html_url"] = RELEASES_PAGE_URL


def fetch_release_by_tag(
    tag: str,
    timeout: float = 12.0,
) -> Tuple[Optional[Dict[str, Any]], int]:
    tag = (tag or "").strip()
    if not tag:
        return None, 0

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "tg-ws-proxy-update-check",
    }

    req = Request(
        RELEASES_BY_TAG_API.format(tag=quote(tag, safe=""), timestamp=int(time.time())),
        headers=headers,
        method="GET",
    )

    try:
        with build_github_opener().open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")

            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Некорректный ответ GitHub API")

            return payload, int(code)
    except HTTPError as e:
        if e.code in (304, 404):
            return None, e.code
        raise


def _extract_assets(data: Optional[Dict[str, Any]]) -> list:
    if not data:
        return []

    result = []

    for a in data.get("assets") or []:
        if not isinstance(a, dict):
            continue

        name = str(a.get("name") or "").strip()
        url = str(a.get("browser_download_url") or "").strip()
        digest = str(a.get("digest") or "").strip()

        if name and url:
            result.append(
                {
                    "name": name,
                    "url": url,
                    "digest": digest,
                }
            )

    return result


def get_status() -> Dict[str, Any]:
    """Снимок состояния после run_check (для подписей в настройках)."""
    snapshot = dict(_state)
    snapshot["assets"] = list(_state.get("assets") or [])
    return snapshot


def get_update_asset(exe_path: Path, current_version: str) -> Optional[Tuple[str, str]]:
    new_assets = _state.get("assets") or []
    if not new_assets:
        return None

    target_name: Optional[str] = None
    current_version = (current_version or "").strip()
    clean_current = current_version.lstrip("vV")

    # SHA256 match
    if clean_current:
        try:
            tag = f"v{clean_current}"
            data, code = fetch_release_by_tag(tag)

            if code == 200 and data:
                cur_assets = _extract_assets(data)

                if cur_assets:
                    exe_path = Path(exe_path)

                    if exe_path.is_file():
                        h = hashlib.sha256()

                        with exe_path.open("rb") as f:
                            while True:
                                chunk = f.read(65536)
                                if not chunk:
                                    break
                                h.update(chunk)

                        exe_sha = h.hexdigest().lower()

                        for a in cur_assets:
                            digest = (a.get("digest") or "").lower().strip()
                            if not digest:
                                continue

                            if digest == exe_sha or (
                                digest.startswith("sha256:") and digest[7:] == exe_sha
                            ):
                                target_name = a.get("name")
                                break
        except Exception:
            target_name = None

    # Fallback
    new_asset_names = {a.get("name") for a in new_assets if isinstance(a, dict)}

    if not target_name or target_name not in new_asset_names:
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
        if not isinstance(a, dict):
            continue

        if a.get("name") == target_name:
            url = a.get("url")
            name = a.get("name")

            if url and name:
                return str(url), str(name)

    return None
