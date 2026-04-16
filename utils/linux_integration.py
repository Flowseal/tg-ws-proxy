from __future__ import annotations

import logging
import os
import sys
import tempfile
from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.tray_common import APP_NAME

log = logging.getLogger("tg-ws-tray")


@dataclass(frozen=True)
class LinuxAutostartCapabilities:
    gui_autostart_supported: bool
    gui_autostart_reason: Optional[str]


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_autostart_dir() -> Path:
    return _xdg_config_home() / "autostart"


def _xdg_autostart_file() -> Path:
    return _xdg_autostart_dir() / "tg-ws-proxy.desktop"


def _shell_quote(value: str) -> str:
    if not value:
        return '""'
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _is_frozen_build() -> bool:
    return bool(getattr(sys, "frozen", False))


def _desktop_exec() -> Optional[str]:
    if not _is_frozen_build():
        return None
    return _shell_quote(sys.executable)


def _desktop_entry_text(exec_cmd: str) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "GenericName=Telegram Proxy",
            "Comment=Telegram Desktop WebSocket Bridge Proxy",
            f"Exec={exec_cmd}",
            "Icon=tg-ws-proxy",
            "Terminal=false",
            "Categories=Network;",
            "StartupNotify=true",
            "Hidden=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def _read_desktop_entry(path: Path) -> Optional[ConfigParser]:
    if not path.exists():
        return None

    parser = ConfigParser(interpolation=None)
    parser.optionxform = str
    try:
        with path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, ConfigError) as exc:
        log.warning("Failed to read Linux autostart entry %s: %s", path, exc)
        return None

    if not parser.has_section("Desktop Entry"):
        log.warning("Linux autostart entry has no [Desktop Entry] section: %s", path)
        return None

    return parser


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def linux_autostart_capabilities() -> LinuxAutostartCapabilities:
    gui_exec = _desktop_exec()
    gui_reason = None if gui_exec else "XDG autostart доступен только в упакованной Linux-сборке."
    if gui_exec:
        log.debug("Linux GUI autostart supported, exec=%s", gui_exec)
    else:
        log.info("Linux GUI autostart unavailable: %s", gui_reason)
    return LinuxAutostartCapabilities(
        gui_autostart_supported=gui_exec is not None,
        gui_autostart_reason=gui_reason,
    )


def is_linux_gui_autostart_enabled() -> bool:
    path = _xdg_autostart_file()
    exec_cmd = _desktop_exec()
    if exec_cmd is None:
        return False

    parser = _read_desktop_entry(path)
    if parser is None:
        return False

    section = parser["Desktop Entry"]
    file_exec = section.get("Exec", "").strip()
    file_type = section.get("Type", "").strip()
    hidden = section.get("Hidden", "false").strip().lower()
    enabled = section.get("X-GNOME-Autostart-enabled", "true").strip().lower()

    if file_type != "Application":
        log.warning("Linux autostart entry has unexpected Type=%s in %s", file_type, path)
        return False
    if hidden in {"1", "true", "yes", "on"}:
        log.info("Linux autostart entry is marked Hidden=true: %s", path)
        return False
    if enabled in {"0", "false", "no", "off"}:
        log.info("Linux autostart entry is disabled by X-GNOME-Autostart-enabled=false: %s", path)
        return False
    if file_exec != exec_cmd:
        log.warning(
            "Linux autostart exec mismatch in %s: current=%s expected=%s",
            path,
            file_exec,
            exec_cmd,
        )
        return False

    return True


def set_linux_gui_autostart_enabled(enabled: bool) -> None:
    path = _xdg_autostart_file()
    exec_cmd = _desktop_exec()
    if exec_cmd is None:
        raise RuntimeError("XDG autostart поддерживается только для упакованной Linux-сборки.")

    if enabled:
        content = _desktop_entry_text(exec_cmd)
        current = None
        if path.exists():
            try:
                current = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("Failed to read existing Linux autostart entry %s: %s", path, exc)
        if current == content:
            log.info("Linux autostart already up to date: %s", path)
            return
        _atomic_write_text(path, content)
        log.info("Linux autostart enabled: %s", path)
        return

    if path.exists():
        path.unlink(missing_ok=True)
        log.info("Linux autostart disabled: %s", path)
    else:
        log.info("Linux autostart already disabled: %s", path)


def sync_linux_autostart(
    *,
    gui_enabled: bool,
) -> List[Tuple[str, str]]:
    errors: List[Tuple[str, str]] = []
    log.info("Syncing Linux autostart: gui_enabled=%s", gui_enabled)

    try:
        set_linux_gui_autostart_enabled(gui_enabled)
    except Exception as exc:
        log.error("Failed to sync Linux autostart: %s", exc)
        errors.append(("gui", str(exc)))

    return errors


def read_linux_autostart_state(cfg: Dict[str, object]) -> Dict[str, object]:
    merged = dict(cfg)
    merged["linux_gui_autostart"] = is_linux_gui_autostart_enabled()
    log.info("Detected Linux autostart state: linux_gui_autostart=%s", merged["linux_gui_autostart"])
    return merged
