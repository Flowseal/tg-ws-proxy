from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

try:
    import rumps
except ImportError:
    rumps = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

import proxy.tg_ws_proxy as tg_ws_proxy
from proxy import __version__
from ui.tray_icons import apply_status_badge, normalize_tray_icon_image
from utils.default_config import default_tray_config
from utils.tray_diagnostics import format_status_tcp_report
from utils.tray_io import load_tray_config, save_tray_config, setup_tray_logging
from utils.tray_ipv6 import IPV6_WARN_BODY_MACOS, has_ipv6_enabled
from utils.tray_lock import (
    SingleInstanceLock,
    frozen_match_app_name_contains,
    make_same_process_checker,
)
from utils.tray_paths import APP_NAME, tray_paths_macos
from utils.tray_proxy_state import ProxyRuntimeState
from utils.tray_proxy_runner import ProxyThreadRunner
from utils.tray_updates import spawn_notify_update_async

PATHS = tray_paths_macos()
APP_DIR = PATHS.app_dir
CONFIG_FILE = PATHS.config_file
LOG_FILE = PATHS.log_file
FIRST_RUN_MARKER = PATHS.first_run_marker
IPV6_WARN_MARKER = PATHS.ipv6_warn_marker
MENUBAR_ICON_PATH = APP_DIR / "menubar_icon.png"
MENUBAR_LIVE_ICON = APP_DIR / "menubar_status.png"

DEFAULT_CONFIG = default_tray_config()

_app: Optional[object] = None
_macos_tray_base: Optional[object] = None
_last_macos_icon_phase: Optional[str] = None
_config: dict = {}
_exiting: bool = False
_proxy_state = ProxyRuntimeState()

log = logging.getLogger("tg-ws-tray")

_instance_lock = SingleInstanceLock(
    PATHS.app_dir,
    make_same_process_checker(
        script_marker=None,
        frozen_match=frozen_match_app_name_contains(APP_NAME),
    ),
    log=log,
)


def _ensure_dirs() -> None:
    PATHS.app_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    return load_tray_config(PATHS, DEFAULT_CONFIG, log)


def save_config(cfg: dict) -> None:
    save_tray_config(PATHS, cfg)


def setup_logging(verbose: bool = False, log_max_mb: float = 5) -> None:
    setup_tray_logging(PATHS, verbose=verbose, log_max_mb=log_max_mb)


def _escape_osascript_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


def _show_error(text: str, title: str = "TG WS Proxy") -> None:
    text_esc = _escape_osascript_text(text)
    title_esc = _escape_osascript_text(title)
    _osascript(
        f'display dialog "{text_esc}" with title "{title_esc}" '
        f'buttons {{"OK"}} default button "OK" with icon stop'
    )


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    text_esc = _escape_osascript_text(text)
    title_esc = _escape_osascript_text(title)
    _osascript(
        f'display dialog "{text_esc}" with title "{title_esc}" '
        f'buttons {{"OK"}} default button "OK" with icon note'
    )


_proxy_runner = ProxyThreadRunner(
    default_config=DEFAULT_CONFIG,
    get_config=lambda: _config,
    log=log,
    show_error=_show_error,
    join_timeout=2.0,
    warn_on_join_stuck=False,
    runtime_state=_proxy_state,
    check_port_before_start=True,
)


def start_proxy() -> None:
    _proxy_runner.start()


def stop_proxy() -> None:
    _proxy_runner.stop()


def restart_proxy() -> None:
    _proxy_runner.restart()


def _make_menubar_icon(size: int = 44):
    if Image is None:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = size // 11
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(0, 0, 0, 255),
    )

    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Helvetica.ttc",
            size=int(size * 0.55),
        )
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "T", fill=(255, 255, 255, 255), font=font)
    return img


def _ensure_menubar_icon() -> None:
    if MENUBAR_ICON_PATH.exists():
        return
    _ensure_dirs()
    img = _make_menubar_icon(44)
    if img:
        img.save(str(MENUBAR_ICON_PATH), "PNG")


def _macos_get_tray_base_image():
    """RGBA 44×44 без бейджа (кэш)."""
    global _macos_tray_base
    if _macos_tray_base is not None:
        return _macos_tray_base
    if Image is None:
        return None
    _ensure_menubar_icon()
    if MENUBAR_ICON_PATH.exists():
        _macos_tray_base = normalize_tray_icon_image(
            Image.open(str(MENUBAR_ICON_PATH)), size=44
        )
    else:
        raw = _make_menubar_icon(44)
        if raw is None:
            return None
        _macos_tray_base = normalize_tray_icon_image(raw, size=44)
    return _macos_tray_base


def _macos_write_badged_icon(phase: str) -> Optional[str]:
    """PNG с бейджем для rumps; путь к файлу."""
    base = _macos_get_tray_base_image()
    if base is None:
        return None
    badged = apply_status_badge(base, phase)
    _ensure_dirs()
    badged.save(str(MENUBAR_LIVE_ICON), "PNG")
    return str(MENUBAR_LIVE_ICON)


def _macos_refresh_menubar_icon() -> None:
    global _last_macos_icon_phase
    if _app is None or _exiting:
        return
    try:
        phase = _proxy_state.snapshot()["phase"]
        if phase == _last_macos_icon_phase:
            return
        path = _macos_write_badged_icon(phase)
        if path:
            _app.icon = path
            _last_macos_icon_phase = phase
    except Exception:
        pass


def _macos_icon_refresh_loop() -> None:
    while not _exiting:
        _macos_refresh_menubar_icon()
        time.sleep(2.0)


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    result = _ask_yes_no_close(text, title)
    return result is True


def _ask_yes_no_close(text: str, title: str = "TG WS Proxy") -> Optional[bool]:
    text_esc = _escape_osascript_text(text)
    title_esc = _escape_osascript_text(title)
    r = subprocess.run(
        [
            "osascript",
            "-e",
            f'button returned of (display dialog "{text_esc}" '
            f'with title "{title_esc}" '
            f'buttons {{"Закрыть", "Нет", "Да"}} '
            f'default button "Да" cancel button "Закрыть" with icon note)',
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None

    result = r.stdout.strip()
    if result == "Да":
        return True
    if result == "Нет":
        return False
    return None


def _on_status_tcp_dialog(_=None):
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = int(_config.get("port", DEFAULT_CONFIG["port"]))
    _show_info(
        format_status_tcp_report(host, port, _proxy_state),
        "TG WS Proxy — статус",
    )


def _on_open_in_telegram(_=None):
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    url = f"tg://socks?server={host}&port={port}"
    log.info("Opening %s", url)
    try:
        result = subprocess.call(["open", url])
        if result != 0:
            raise RuntimeError("open command failed")
    except Exception:
        log.info("open command failed, trying webbrowser")
        try:
            if not webbrowser.open(url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception:
            log.info("Browser open failed, copying to clipboard")
            try:
                if pyperclip:
                    pyperclip.copy(url)
                else:
                    subprocess.run(["pbcopy"], input=url.encode(), check=True)
                _show_info(
                    "Не удалось открыть Telegram автоматически.\n\n"
                    f"Ссылка скопирована в буфер обмена:\n{url}"
                )
            except Exception as exc:
                log.error("Clipboard copy failed: %s", exc)
                _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_restart(_=None):
    def _do_restart():
        global _config
        _config = load_config()
        if _app:
            _app.update_menu_title()
        restart_proxy()

    threading.Thread(target=_do_restart, daemon=True).start()


def _on_open_logs(_=None):
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        subprocess.call(["open", str(LOG_FILE)])
    else:
        _show_info("Файл логов ещё не создан.")


def _osascript_input(
    prompt: str, default: str, title: str = "TG WS Proxy"
) -> Optional[str]:
    prompt_esc = _escape_osascript_text(prompt)
    default_esc = _escape_osascript_text(default)
    title_esc = _escape_osascript_text(title)
    r = subprocess.run(
        [
            "osascript",
            "-e",
            f'text returned of (display dialog "{prompt_esc}" '
            f'default answer "{default_esc}" '
            f'with title "{title_esc}" '
            f'buttons {{"Закрыть", "OK"}} '
            f'default button "OK" cancel button "Закрыть")',
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\r\n")


def _on_edit_config(_=None):
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


def _check_updates_menu_title() -> str:
    on = bool(_config.get("check_updates", True))
    return (
        "✓ Проверять обновления при запуске"
        if on
        else "Проверять обновления при запуске (выкл)"
    )


def _toggle_check_updates(_=None):
    global _config
    _config["check_updates"] = not bool(_config.get("check_updates", True))
    save_config(_config)
    if _app is not None:
        _app._check_updates_item.title = _check_updates_menu_title()


def _on_open_release_page(_=None):
    from utils.update_check import RELEASES_PAGE_URL

    webbrowser.open(RELEASES_PAGE_URL)


def _maybe_notify_update_async() -> None:
    spawn_notify_update_async(
        get_config=lambda: _config,
        exiting=lambda: _exiting,
        ask_open_release=lambda ver, _url: _ask_yes_no(
            f"Доступна новая версия: {ver}\n\n"
            f"Открыть страницу релиза в браузере?",
            "TG WS Proxy — обновление",
        ),
        log=log,
    )


def _edit_config_dialog():
    cfg = load_config()

    host = _osascript_input(
        "IP-адрес прокси:",
        cfg.get("host", DEFAULT_CONFIG["host"]),
    )
    if host is None:
        return
    host = host.strip()

    import socket as _sock

    try:
        _sock.inet_aton(host)
    except OSError:
        _show_error("Некорректный IP-адрес.")
        return

    port_str = _osascript_input(
        "Порт прокси:",
        str(cfg.get("port", DEFAULT_CONFIG["port"])),
    )
    if port_str is None:
        return
    try:
        port = int(port_str.strip())
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        _show_error("Порт должен быть числом 1-65535")
        return

    dc_default = ", ".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"]))
    dc_str = _osascript_input(
        "DC → IP маппинги (через запятую, формат DC:IP):\n"
        "Например: 2:149.154.167.220, 4:149.154.167.220",
        dc_default,
    )
    if dc_str is None:
        return
    dc_lines = [
        s.strip() for s in dc_str.replace(",", "\n").splitlines() if s.strip()
    ]
    try:
        tg_ws_proxy.parse_dc_ip_list(dc_lines)
    except ValueError as e:
        _show_error(str(e))
        return

    verbose = _ask_yes_no_close("Включить подробное логирование (verbose)?")
    if verbose is None:
        return

    adv_str = _osascript_input(
        "Расширенные настройки (буфер KB, WS пул, лог MB):\n"
        "Формат: buf_kb,pool_size,log_max_mb",
        f"{cfg.get('buf_kb', DEFAULT_CONFIG['buf_kb'])},"
        f"{cfg.get('pool_size', DEFAULT_CONFIG['pool_size'])},"
        f"{cfg.get('log_max_mb', DEFAULT_CONFIG['log_max_mb'])}",
    )
    if adv_str is None:
        return

    adv = {}
    if adv_str:
        parts = [s.strip() for s in adv_str.split(",")]
        keys = [("buf_kb", int), ("pool_size", int), ("log_max_mb", float)]
        for i, (k, typ) in enumerate(keys):
            if i < len(parts):
                try:
                    adv[k] = typ(parts[i])
                except ValueError:
                    pass

    new_cfg = {
        "host": host,
        "port": port,
        "dc_ip": dc_lines,
        "verbose": verbose,
        "buf_kb": adv.get("buf_kb", cfg.get("buf_kb", DEFAULT_CONFIG["buf_kb"])),
        "pool_size": adv.get(
            "pool_size", cfg.get("pool_size", DEFAULT_CONFIG["pool_size"])
        ),
        "log_max_mb": adv.get(
            "log_max_mb", cfg.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"])
        ),
    }
    save_config(new_cfg)
    log.info("Config saved: %s", new_cfg)

    global _config
    _config = new_cfg
    if _app:
        _app.update_menu_title()

    if _ask_yes_no_close("Настройки сохранены.\n\nПерезапустить прокси сейчас?"):
        restart_proxy()


def _show_first_run():
    _ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    tg_url = f"tg://socks?server={host}&port={port}"

    text = (
        f"Прокси запущен и работает в строке меню.\n\n"
        f"Как подключить Telegram Desktop:\n\n"
        f"Автоматически:\n"
        f"  Нажмите «Открыть в Telegram» в меню\n"
        f"  Или ссылка: {tg_url}\n\n"
        f"Вручную:\n"
        f"  Настройки → Продвинутые → Тип подключения → Прокси\n"
        f"  SOCKS5 → {host} : {port} (без логина/пароля)\n\n"
        f"Открыть прокси в Telegram сейчас?"
    )

    FIRST_RUN_MARKER.touch()

    if _ask_yes_no(text, "TG WS Proxy"):
        _on_open_in_telegram()


def _check_ipv6_warning():
    _ensure_dirs()
    if IPV6_WARN_MARKER.exists():
        return
    if not has_ipv6_enabled("simple"):
        return

    IPV6_WARN_MARKER.touch()

    _show_info(IPV6_WARN_BODY_MACOS)


_TgWsProxyAppBase = rumps.App if rumps else object


class TgWsProxyApp(_TgWsProxyAppBase):
    def __init__(self, *, menubar_icon_path: Optional[str] = None):
        _ensure_menubar_icon()
        icon_path = menubar_icon_path or (
            str(MENUBAR_ICON_PATH) if MENUBAR_ICON_PATH.exists() else None
        )

        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = int(_config.get("port", DEFAULT_CONFIG["port"]))

        self._open_tg_item = rumps.MenuItem(
            f"Открыть в Telegram ({host}:{port})",
            callback=_on_open_in_telegram,
        )
        self._status_tcp_item = rumps.MenuItem(
            "Статус",
            callback=_on_status_tcp_dialog,
        )
        self._restart_item = rumps.MenuItem(
            "Перезапустить прокси",
            callback=_on_restart,
        )
        self._settings_item = rumps.MenuItem(
            "Настройки...",
            callback=_on_edit_config,
        )
        self._logs_item = rumps.MenuItem(
            "Открыть логи",
            callback=_on_open_logs,
        )
        self._release_page_item = rumps.MenuItem(
            "Страница релиза на GitHub…",
            callback=_on_open_release_page,
        )
        self._check_updates_item = rumps.MenuItem(
            _check_updates_menu_title(),
            callback=_toggle_check_updates,
        )
        self._version_item = rumps.MenuItem(
            f"Версия {__version__}",
            callback=lambda _: None,
        )

        super().__init__(
            "TG WS Proxy",
            icon=icon_path,
            template=False,
            quit_button="Выход",
            menu=[
                self._open_tg_item,
                None,
                self._status_tcp_item,
                None,
                self._restart_item,
                self._settings_item,
                self._logs_item,
                None,
                self._release_page_item,
                self._check_updates_item,
                None,
                self._version_item,
            ],
        )

    def update_menu_title(self):
        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = int(_config.get("port", DEFAULT_CONFIG["port"]))
        self._open_tg_item.title = f"Открыть в Telegram ({host}:{port})"


def run_menubar():
    global _app, _config, _last_macos_icon_phase

    _config = load_config()
    save_config(_config)

    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
        except Exception:
            pass

    setup_logging(
        _config.get("verbose", False),
        log_max_mb=_config.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"]),
    )
    log.info("TG WS Proxy версия %s, menubar app starting", __version__)
    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    if rumps is None or Image is None:
        log.error("rumps or Pillow not installed; running in console mode")
        start_proxy()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_proxy()
        return

    start_proxy()

    _maybe_notify_update_async()

    _show_first_run()
    _check_ipv6_warning()

    phase0 = _proxy_state.snapshot()["phase"]
    live_path = _macos_write_badged_icon(phase0)
    _last_macos_icon_phase = phase0
    _app = TgWsProxyApp(
        menubar_icon_path=live_path
        if live_path
        else (str(MENUBAR_ICON_PATH) if MENUBAR_ICON_PATH.exists() else None),
    )

    threading.Thread(
        target=_macos_icon_refresh_loop, daemon=True, name="macos-icon-refresh"
    ).start()

    log.info("Menubar app running")
    _app.run()

    stop_proxy()
    log.info("Menubar app exited")


def main():
    if not _instance_lock.acquire():
        _show_info("Приложение уже запущено.")
        return

    try:
        run_menubar()
    finally:
        _instance_lock.release()


if __name__ == "__main__":
    main()
