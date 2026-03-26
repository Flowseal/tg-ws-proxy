"""
Общие значения по умолчанию для tray-приложений (Windows / Linux / macOS).
Единственное отличие по платформе — ключ autostart только на Windows.
"""
from __future__ import annotations

import sys
from typing import Any, Dict

_TRAY_DEFAULTS_COMMON: Dict[str, Any] = {
    "port": 1080,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "check_updates": True,
    "log_max_mb": 5,
    "buf_kb": 256,
    "pool_size": 4,
}


def default_tray_config() -> Dict[str, Any]:
    """Новая копия конфига по умолчанию для текущей ОС."""
    cfg = dict(_TRAY_DEFAULTS_COMMON)
    if sys.platform == "win32":
        cfg["autostart"] = False
    return cfg
