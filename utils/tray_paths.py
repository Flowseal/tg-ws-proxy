"""Пути данных tray-приложения (конфиг, логи, маркеры) по ОС."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


APP_NAME = "TgWsProxy"


@dataclass(frozen=True)
class TrayPaths:
    app_dir: Path
    config_file: Path
    log_file: Path
    first_run_marker: Path
    ipv6_warn_marker: Path


def tray_paths_windows() -> TrayPaths:
    base = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    return _paths_under(base)


def tray_paths_linux() -> TrayPaths:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) / APP_NAME if xdg else Path.home() / ".config" / APP_NAME
    return _paths_under(base)


def tray_paths_macos() -> TrayPaths:
    base = Path.home() / "Library" / "Application Support" / APP_NAME
    return _paths_under(base)


def _paths_under(app_dir: Path) -> TrayPaths:
    return TrayPaths(
        app_dir=app_dir,
        config_file=app_dir / "config.json",
        log_file=app_dir / "proxy.log",
        first_run_marker=app_dir / ".first_run_done",
        ipv6_warn_marker=app_dir / ".ipv6_warned",
    )
