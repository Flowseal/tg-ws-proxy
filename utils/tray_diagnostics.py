"""Локальная диагностика: занятость порта, TCP до SOCKS5-слушателя."""
from __future__ import annotations

import errno
import socket
from typing import Tuple


def try_bind_listen_socket(host: str, port: int) -> Tuple[bool, str]:
    """
    Пробует занять тот же адрес, что и прокси (TCP).
    Возвращает (True, "") если порт свободен, иначе (False, краткое сообщение).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
    except OSError as e:
        msg = str(e)
        winerr = getattr(e, "winerror", None)
        # 10048 EADDRINUSE, 10013 WSAEACCES — часто при втором экземпляре на том же порту (Windows)
        if winerr in (10048, 10013) or e.errno == errno.EADDRINUSE:
            return (
                False,
                "Порт уже занят или недоступен (возможно, уже запущен TG WS Proxy).",
            )
        return False, msg or "Не удалось занять порт."
    finally:
        try:
            s.close()
        except OSError:
            pass
    return True, ""


def tcp_connect_ok(host: str, port: int, *, timeout: float = 3.0) -> Tuple[bool, str]:
    """Локальное TCP-подключение к host:port (проверка, что слушатель отвечает)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as e:
        return False, str(e) or "Нет соединения"
    return True, ""


def format_status_tcp_report(host: str, port: int, state: "ProxyRuntimeState") -> str:
    """
    Текст одного диалога: состояние прокси (фаза, uptime) + проверка TCP к host:port.
    """
    from utils.tray_proxy_state import format_uptime_short

    snap = state.snapshot()
    lines = [
        f"Адрес: {host}:{port}",
        f"Состояние: {snap['phase']}",
    ]
    if snap["listening_since"] is not None:
        lines.append(f"uptime: {format_uptime_short(snap['listening_since'])}")
    if snap["detail"]:
        lines.append(f"Детали: {snap['detail']}")
    lines.append("")
    ok, msg = tcp_connect_ok(host, port)
    if ok:
        lines.append("TCP: подключение к локальному порту успешно.")
    else:
        lines.append(f"TCP: не удалось подключиться — {msg}")
        lines.append("")
        lines.append(
            "Проверьте, что прокси запущен и в настройках указаны тот же хост и порт."
        )
    return "\n".join(lines)
