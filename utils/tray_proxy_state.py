"""Состояние прокси в tray: фаза запуска, uptime, краткий диагностический текст."""
from __future__ import annotations

import threading
import time
from typing import Literal, Optional

ProxyPhase = Literal["idle", "starting", "listening", "error", "stopping"]


class ProxyRuntimeState:
    """Потокобезопасное состояние для подсказки трея и диалога статуса."""

    __slots__ = ("_lock", "_phase", "_detail", "_listening_since")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._phase: ProxyPhase = "idle"
        self._detail = ""
        self._listening_since: Optional[float] = None

    def reset_for_start(self) -> None:
        with self._lock:
            self._phase = "starting"
            self._detail = ""
            self._listening_since = None

    def set_listening(self) -> None:
        with self._lock:
            self._phase = "listening"
            self._detail = ""
            self._listening_since = time.time()

    def set_error(self, detail: str) -> None:
        with self._lock:
            self._phase = "error"
            self._detail = (detail or "").strip()
            self._listening_since = None

    def set_stopping(self) -> None:
        with self._lock:
            self._phase = "stopping"
            self._detail = ""

    def mark_idle_after_thread(self, *, had_exception: bool) -> None:
        with self._lock:
            if had_exception:
                return
            self._phase = "idle"
            self._listening_since = None
            self._detail = ""

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "phase": self._phase,
                "detail": self._detail,
                "listening_since": self._listening_since,
            }


def format_uptime_short(started: float) -> str:
    """Человекочитаемый uptime для подсказки."""
    sec = max(0, int(time.time() - started))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m} мин {s} с"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h} ч {m} мин"
    d, h = divmod(h, 24)
    return f"{d} д {h} ч"


def phase_label_ru(phase: str) -> str:
    return {
        "idle": "остановлен",
        "starting": "запуск…",
        "listening": "слушает",
        "error": "ошибка",
        "stopping": "останавливается…",
    }.get(phase, phase)


def build_tray_tooltip(
    *,
    host: str,
    port: int,
    state: ProxyRuntimeState,
) -> str:
    snap = state.snapshot()
    phase = snap["phase"]
    addr = f"{host}:{port}"
    label = phase_label_ru(phase)

    if phase == "listening" and snap["listening_since"] is not None:
        up = format_uptime_short(snap["listening_since"])
        return f"TG WS Proxy | {addr} | {label} | {up}"
    if phase == "error" and snap["detail"]:
        short = snap["detail"]
        if len(short) > 80:
            short = short[:77] + "…"
        return f"TG WS Proxy | {addr} | {label}: {short}"
    return f"TG WS Proxy | {addr} | {label}"
