import os
import threading
import time
import json
from pathlib import Path
from typing import Iterable, Optional

from proxy.app_runtime import ProxyAppRuntime
from proxy import __version__
import proxy.tg_ws_proxy as tg_ws_proxy


RELEASES_PAGE_URL = "https://github.com/Dark-Avery/tg-ws-proxy/releases/latest"


_RUNTIME_LOCK = threading.RLock()
_RUNTIME: Optional[ProxyAppRuntime] = None
_LAST_ERROR: Optional[str] = None


def _remember_error(message: str) -> None:
    global _LAST_ERROR
    _LAST_ERROR = message


def _normalize_dc_ip_list(dc_ip_list: Iterable[object]) -> list[str]:
    if dc_ip_list is None:
        return []

    values: list[object]
    try:
        values = list(dc_ip_list)
    except TypeError:
        # Chaquopy may expose Kotlin's List<String> as java.util.ArrayList,
        # which isn't always directly iterable from Python.
        if hasattr(dc_ip_list, "toArray"):
            values = list(dc_ip_list.toArray())
        elif hasattr(dc_ip_list, "size") and hasattr(dc_ip_list, "get"):
            size = int(dc_ip_list.size())
            values = [dc_ip_list.get(i) for i in range(size)]
        else:
            values = [dc_ip_list]

    return [str(item).strip() for item in values if str(item).strip()]


def start_proxy(app_dir: str, host: str, port: int,
                dc_ip_list: Iterable[object], log_max_mb: float = 5.0,
                buf_kb: int = 256, pool_size: int = 4,
                verbose: bool = False) -> str:
    global _RUNTIME, _LAST_ERROR

    with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            _RUNTIME.stop_proxy()
            _RUNTIME = None

        _LAST_ERROR = None
        os.environ["TG_WS_PROXY_CRYPTO_BACKEND"] = "python"
        tg_ws_proxy.reset_stats()

        runtime = ProxyAppRuntime(
            Path(app_dir),
            logger_name="tg-ws-android",
            on_error=_remember_error,
        )
        runtime.reset_log_file()
        runtime.setup_logging(verbose=verbose, log_max_mb=float(log_max_mb))

        config = {
            "host": host,
            "port": int(port),
            "dc_ip": _normalize_dc_ip_list(dc_ip_list),
            "log_max_mb": float(log_max_mb),
            "buf_kb": int(buf_kb),
            "pool_size": int(pool_size),
            "verbose": bool(verbose),
        }
        runtime.save_config(config)

        if not runtime.start_proxy(config):
            _RUNTIME = None
            raise RuntimeError(_LAST_ERROR or "Failed to start proxy runtime.")

        _RUNTIME = runtime

    # Give the proxy thread a short warm-up window so immediate bind failures
    # surface before Kotlin reports the service as running.
    for _ in range(10):
        time.sleep(0.1)
        with _RUNTIME_LOCK:
            if _LAST_ERROR:
                runtime.stop_proxy()
                _RUNTIME = None
                raise RuntimeError(_LAST_ERROR)
            if runtime.is_proxy_running():
                return str(runtime.log_file)

    with _RUNTIME_LOCK:
        runtime.stop_proxy()
        _RUNTIME = None
    raise RuntimeError("Proxy runtime did not become ready in time.")


def stop_proxy() -> None:
    global _RUNTIME, _LAST_ERROR

    with _RUNTIME_LOCK:
        _LAST_ERROR = None
        if _RUNTIME is not None:
            _RUNTIME.stop_proxy()
            _RUNTIME = None


def is_running() -> bool:
    with _RUNTIME_LOCK:
        return bool(_RUNTIME and _RUNTIME.is_proxy_running())


def get_last_error() -> Optional[str]:
    return _LAST_ERROR


def get_runtime_stats_json() -> str:
    with _RUNTIME_LOCK:
        running = bool(_RUNTIME and _RUNTIME.is_proxy_running())

    payload = dict(tg_ws_proxy.get_stats_snapshot())
    payload["running"] = running
    payload["last_error"] = _LAST_ERROR
    return json.dumps(payload)


def _load_update_check():
    from utils import update_check
    return update_check


def get_update_status_json(check_now: bool = False) -> str:
    payload = {
        "current_version": __version__,
        "latest": "",
        "has_update": False,
        "ahead_of_release": False,
        "checked": False,
        "html_url": RELEASES_PAGE_URL,
        "error": "",
    }
    try:
        update_check = _load_update_check()
        if check_now:
            update_check.run_check(__version__)
        payload.update(update_check.get_status())
        payload["current_version"] = __version__
        payload["latest"] = payload.get("latest") or ""
        payload["html_url"] = payload.get("html_url") or update_check.RELEASES_PAGE_URL
        payload["error"] = payload.get("error") or ""
    except Exception as exc:
        payload["error"] = str(exc)
    return json.dumps(payload)
