"""
Общие значения по умолчанию для tray-приложений (Windows / Linux / macOS).
Единственное отличие по платформе — ключ autostart только на Windows.
"""
from __future__ import annotations

import sys
import os
from typing import Any, Dict
from copy import deepcopy

from ui.i18n import detect_system_language

_TRAY_DEFAULTS_COMMON: Dict[str, Any] = {
    "port": 1443,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "check_updates": True,
    "log_max_mb": 5,
    "buf_kb": 0,
    "pool_size": 4,
    "cfproxy": True,
    "h2": True,
    "cfproxy_user_domain_enabled": False,
    "cfproxy_user_domain": [],
    "cfproxy_worker_enabled": False,
    "cfproxy_worker_domain": [],
    "force_test_dc": False,
    "no_secure": False,
    "appearance": "auto",
    "config_version": 2,
}


def default_tray_config() -> Dict[str, Any]:
    cfg = deepcopy(_TRAY_DEFAULTS_COMMON)
    cfg["secret"] = os.urandom(16).hex()
    cfg["language"] = detect_system_language().value

    if sys.platform == "win32":
        cfg["autostart"] = False

    return cfg
