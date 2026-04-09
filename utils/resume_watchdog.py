from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional


class ResumeWatchdog:
    def __init__(
        self,
        on_resume: Callable[[], None],
        logger: logging.Logger,
        *,
        interval: float = 15.0,
        resume_gap: float = 45.0,
        cooldown: float = 30.0,
        name: str = "resume-watchdog",
    ) -> None:
        self._on_resume = on_resume
        self._log = logger
        self._interval = interval
        self._resume_gap = resume_gap
        self._cooldown = cooldown
        self._name = name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_trigger = 0.0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=self._name,
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._thread = None

    def _run(self) -> None:
        last_seen = time.time()
        while not self._stop_event.wait(self._interval):
            now = time.time()
            gap = now - last_seen
            last_seen = now

            if gap < self._resume_gap:
                continue

            if now - self._last_trigger < self._cooldown:
                continue

            self._last_trigger = now
            self._log.warning(
                "Detected a %.1fs pause; restarting proxy to recover after resume",
                gap,
            )
            try:
                self._on_resume()
            except Exception:
                self._log.exception("Failed to recover proxy after resume")
            finally:
                last_seen = time.time()
