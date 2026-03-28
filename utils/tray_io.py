"""Конфиг tray-приложения и логирование в файл."""
from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

from utils.tray_paths import TrayPaths


def ensure_app_dirs(paths: TrayPaths) -> None:
    paths.app_dir.mkdir(parents=True, exist_ok=True)


def load_tray_config(
    paths: TrayPaths,
    defaults: Mapping[str, Any],
    log: logging.Logger,
) -> Dict[str, Any]:
    ensure_app_dirs(paths)
    if paths.config_file.exists():
        try:
            with open(paths.config_file, "r", encoding="utf-8") as f:
                data: Dict[str, Any] = json.load(f)
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
        except Exception as exc:
            log.warning("Failed to load config: %s", exc)
    return dict(defaults)


def save_tray_config(paths: TrayPaths, cfg: Mapping[str, Any]) -> None:
    ensure_app_dirs(paths)
    with open(paths.config_file, "w", encoding="utf-8") as f:
        json.dump(dict(cfg), f, indent=2, ensure_ascii=False)


def setup_tray_logging(
    paths: TrayPaths,
    *,
    verbose: bool = False,
    log_max_mb: float = 5,
) -> None:
    ensure_app_dirs(paths)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fh = logging.handlers.RotatingFileHandler(
        str(paths.log_file),
        maxBytes=max(32 * 1024, log_max_mb * 1024 * 1024),
        backupCount=0,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(fh)

    if not getattr(sys, "frozen", False):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-5s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(ch)
