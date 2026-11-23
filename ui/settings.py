from __future__ import annotations

import math
import socket
from copy import deepcopy
from dataclasses import dataclass
from typing import Union

from proxy import coerce_domain_list, parse_dc_ip_list
from ui.i18n import t

LIVE_SETTINGS_KEYS = frozenset({
    "appearance", "autostart", "check_updates", "language", "verbose"
})


@dataclass(frozen=True)
class SettingsChange:
    config: dict
    changed_keys: frozenset

    @property
    def requires_restart(self) -> bool:
        return bool(self.changed_keys - LIVE_SETTINGS_KEYS)


def prepare_settings(current: dict, values: dict, defaults: dict) -> SettingsChange:
    config = deepcopy(defaults)
    config.update(deepcopy(current))
    changed_keys = frozenset(
        key for key, value in values.items() if value != config.get(key)
    )
    config.update(deepcopy(values))
    return SettingsChange(config, changed_keys)


def validate_settings(values: dict, defaults: dict) -> Union[dict, str]:
    config = dict(values)

    host = values["host"].strip()
    try:
        socket.inet_aton(host)
    except OSError:
        return t("validation.bad_host")
    
    try:
        port = int(values["port"].strip())
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return t("validation.bad_port")
    
    lines = [line.strip() for line in values["dc_ip"].splitlines() if line.strip()]
    try:
        parse_dc_ip_list(lines)
    except ValueError as exc:
        entry = getattr(exc, "entry", None)
        if entry is None:
            return str(exc)
        key = "dc_format" if getattr(exc, "kind", "invalid") == "format" else "dc_invalid"
        return t(f"validation.{key}", entry=entry)
    
    secret = values["secret"].strip()
    if len(secret) != 32:
        return t("validation.bad_secret_len")
    if any(char not in "0123456789abcdefABCDEF" for char in secret):
        return t("validation.bad_secret_hex")
    
    config.update(host=host, port=port, secret=secret, dc_ip=lines)

    for key in ("buf_kb", "pool_size", "log_max_mb"):
        try:
            value = float(values[key].strip())
            if not math.isfinite(value):
                raise ValueError
            config[key] = int(value) if key != "log_max_mb" else value
        except (ValueError, OverflowError):
            config[key] = defaults[key]

    for key in ("cfproxy_user_domain", "cfproxy_worker_domain"):
        if key in config:
            config[key] = coerce_domain_list(config[key])

    return config
