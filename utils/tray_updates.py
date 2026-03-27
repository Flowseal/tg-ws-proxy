"""Фоновая проверка обновлений GitHub Releases для tray."""
from __future__ import annotations

import logging
import threading
import time
import webbrowser
from typing import Callable, Mapping

from proxy import __version__


def spawn_notify_update_async(
    *,
    get_config: Callable[[], Mapping[str, object]],
    exiting: Callable[[], bool],
    ask_open_release: Callable[[str, str], bool],
    log: logging.Logger,
) -> None:
    """Пауза, затем run_check; при наличии обновления — ask и открытие браузера."""

    def _work() -> None:
        time.sleep(1.5)
        if exiting():
            return
        cfg = get_config()
        if not cfg.get("check_updates", True):
            return
        try:
            from utils.update_check import RELEASES_PAGE_URL, get_status, run_check

            run_check(__version__)
            st = get_status()
            if not st.get("has_update"):
                return
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"
            if ask_open_release(str(ver), url):
                webbrowser.open(url)
        except Exception as exc:
            cfg = get_config()
            if bool(cfg.get("verbose")):
                log.info("Update check failed: %s", exc)
            else:
                log.debug("Update check failed: %s", exc)

    threading.Thread(target=_work, daemon=True, name="update-check").start()
