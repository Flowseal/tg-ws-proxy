"""Эвристика наличия IPv6 и тексты предупреждения для tray."""
from __future__ import annotations

import ipaddress
import socket as _sock
from typing import Literal

Ipv6DetectMode = Literal["full", "simple"]


def has_ipv6_enabled(mode: Ipv6DetectMode = "simple") -> bool:
    if mode == "full":
        return _has_ipv6_full()
    return _has_ipv6_simple()


def _has_ipv6_simple() -> bool:
    try:
        addrs = _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET6)
        for addr in addrs:
            ip = addr[4][0]
            if ip and not ip.startswith("::1") and not ip.startswith("fe80::1"):
                return True
    except Exception:
        pass
    try:
        s = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except Exception:
        return False


def _has_ipv6_full() -> bool:
    try:
        addrs = _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET6)
        for addr in addrs:
            ip = addr[4][0]
            if not ip or ip.startswith("::1"):
                continue
            try:
                if ipaddress.IPv6Address(ip).is_link_local:
                    continue
            except ValueError:
                if ip.startswith("fe80:"):
                    continue
            return True
    except Exception:
        pass
    try:
        s = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except Exception:
        return False


IPV6_WARN_BODY_LONG = (
    "На вашем компьютере включена поддержка подключения по IPv6.\n\n"
    "Telegram может пытаться подключаться через IPv6, "
    "что не поддерживается и может привести к ошибкам.\n\n"
    "Если прокси не работает или в логах присутствуют ошибки, "
    "связанные с попытками подключения по IPv6 - "
    "попробуйте отключить в настройках прокси Telegram попытку соединения "
    "по IPv6. Если данная мера не помогает, попробуйте отключить IPv6 "
    "в системе.\n\n"
    "Это предупреждение будет показано только один раз."
)

IPV6_WARN_BODY_MACOS = (
    "На вашем компьютере включена поддержка подключения по IPv6.\n\n"
    "Telegram может пытаться подключаться через IPv6, "
    "что не поддерживается и может привести к ошибкам.\n\n"
    "Если прокси не работает, попробуйте отключить "
    "попытку соединения по IPv6 в настройках прокси Telegram.\n\n"
    "Это предупреждение будет показано только один раз."
)
