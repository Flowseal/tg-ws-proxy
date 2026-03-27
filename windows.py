from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
import webbrowser
import winreg
from pathlib import Path
from typing import Optional

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import pystray
except ImportError:
    pystray = None

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

try:
    from PIL import Image
except ImportError:
    Image = None

from proxy import __version__
from ui.ctk_tray_ui import (
    install_tray_config_buttons,
    install_tray_config_form,
    populate_first_run_window,
    tray_settings_scroll_and_footer,
    validate_config_form,
)
from ui.ctk_theme import (
    CONFIG_DIALOG_FRAME_PAD,
    CONFIG_DIALOG_SIZE,
    FIRST_RUN_SIZE,
    create_ctk_root,
    ctk_theme_for_platform,
    main_content_frame,
)
from ui.tray_ctk import destroy_root_safely
from ui.tray_icons import (
    apply_status_badge,
    load_ico_or_synthesize,
    normalize_tray_icon_image,
)
from utils.default_config import default_tray_config
from utils.tray_diagnostics import format_status_tcp_report
from utils.tray_io import load_tray_config, save_tray_config, setup_tray_logging
from utils.tray_ipv6 import IPV6_WARN_BODY_LONG, has_ipv6_enabled
from utils.tray_lock import (
    SingleInstanceLock,
    frozen_match_executable_basename,
    make_same_process_checker,
)
from utils.tray_paths import APP_NAME, tray_paths_windows
from utils.tray_proxy_state import ProxyRuntimeState, build_tray_tooltip
from utils.tray_proxy_runner import ProxyThreadRunner
from utils.tray_updates import spawn_notify_update_async

IS_FROZEN = bool(getattr(sys, "frozen", False))

PATHS = tray_paths_windows()
APP_DIR = PATHS.app_dir
CONFIG_FILE = PATHS.config_file
LOG_FILE = PATHS.log_file
FIRST_RUN_MARKER = PATHS.first_run_marker
IPV6_WARN_MARKER = PATHS.ipv6_warn_marker

DEFAULT_CONFIG = default_tray_config()

_config: dict = {}
_exiting: bool = False
_tray_icon: Optional[object] = None
_tray_base_icon: Optional[object] = None
_last_tray_icon_phase: Optional[str] = None
_proxy_state = ProxyRuntimeState()

log = logging.getLogger("tg-ws-tray")

_user32 = ctypes.windll.user32
_user32.MessageBoxW.argtypes = [
    ctypes.c_void_p,
    ctypes.c_wchar_p,
    ctypes.c_wchar_p,
    ctypes.c_uint,
]
_user32.MessageBoxW.restype = ctypes.c_int

# Колбэки меню pystray выполняются в потоке лотка; MessageBox без родителя и
# без SYSTEMMODAL часто не обрабатывает нажатие OK (фокус/очередь сообщений).
_MB_SYSTEMMODAL = 0x1000
_MB_SETFOREGROUND = 0x10000


def _tray_messagebox_parent() -> Optional[int]:
    if _tray_icon is None:
        return None
    try:
        hwnd = getattr(_tray_icon, "_hwnd", None)
        if hwnd is not None:
            return int(hwnd)
    except Exception:
        pass
    return None


def _messagebox_flags(base: int) -> int:
    return base | _MB_SYSTEMMODAL | _MB_SETFOREGROUND

_instance_lock = SingleInstanceLock(
    PATHS.app_dir,
    make_same_process_checker(
        script_marker="windows.py",
        frozen_match=frozen_match_executable_basename,
    ),
    log=log,
)


def load_config() -> dict:
    return load_tray_config(PATHS, DEFAULT_CONFIG, log)


def save_config(cfg: dict) -> None:
    save_tray_config(PATHS, cfg)


def setup_logging(verbose: bool = False, log_max_mb: float = 5) -> None:
    setup_tray_logging(PATHS, verbose=verbose, log_max_mb=log_max_mb)


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка") -> None:
    parent = _tray_messagebox_parent()
    _user32.MessageBoxW(
        parent,
        text,
        title,
        _messagebox_flags(0x10),
    )


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    parent = _tray_messagebox_parent()
    _user32.MessageBoxW(
        parent,
        text,
        title,
        _messagebox_flags(0x40),
    )


_proxy_runner = ProxyThreadRunner(
    default_config=DEFAULT_CONFIG,
    get_config=lambda: _config,
    log=log,
    show_error=_show_error,
    join_timeout=5.0,
    warn_on_join_stuck=True,
    runtime_state=_proxy_state,
    check_port_before_start=True,
)


def start_proxy() -> None:
    _proxy_runner.start()


def stop_proxy() -> None:
    _proxy_runner.stop()


def restart_proxy() -> None:
    _proxy_runner.restart()


def _autostart_reg_name() -> str:
    return APP_NAME


def _supports_autostart() -> bool:
    return IS_FROZEN


def _autostart_command() -> str:
    return f'"{sys.executable}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        ) as k:
            val, _ = winreg.QueryValueEx(k, _autostart_reg_name())
        stored = str(val).strip()
        expected = _autostart_command().strip()
        return stored == expected
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart_enabled(enabled: bool) -> None:
    try:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        ) as k:
            if enabled:
                winreg.SetValueEx(
                    k,
                    _autostart_reg_name(),
                    0,
                    winreg.REG_SZ,
                    _autostart_command(),
                )
            else:
                try:
                    winreg.DeleteValue(k, _autostart_reg_name())
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.error("Failed to update autostart: %s", exc)
        _show_error(
            "Не удалось изменить автозапуск.\n\n"
            "Попробуйте запустить приложение от имени пользователя с правами на реестр.\n\n"
            f"Ошибка: {exc}"
        )


def _load_icon():
    if Image is None:
        raise RuntimeError("Pillow is required for tray icon")
    assets = Path(__file__).parent
    return load_ico_or_synthesize(
        assets / "icon.ico",
        ["arial.ttf", str(Path(os.environ.get("WINDIR", "C:\\Windows")) / "Fonts" / "arial.ttf")],
    )


def _ask_open_release_page(latest_version: str, _url: str) -> bool:
    MB_YESNO = 0x4
    MB_ICONQUESTION = 0x20
    IDYES = 6
    text = (
        f"Доступна новая версия: {latest_version}\n\n"
        f"Открыть страницу релиза в браузере?"
    )
    parent = _tray_messagebox_parent()
    r = _user32.MessageBoxW(
        parent,
        text,
        "TG WS Proxy — обновление",
        _messagebox_flags(MB_YESNO | MB_ICONQUESTION),
    )
    return r == IDYES


def _maybe_notify_update_async() -> None:
    def ask(ver: str, url: str) -> bool:
        return _ask_open_release_page(ver, url)

    spawn_notify_update_async(
        get_config=lambda: _config,
        exiting=lambda: _exiting,
        ask_open_release=ask,
        log=log,
    )


def _on_status_tcp_dialog(icon=None, item=None):
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = int(_config.get("port", DEFAULT_CONFIG["port"]))
    _show_info(
        format_status_tcp_report(host, port, _proxy_state),
        "TG WS Proxy — статус",
    )


def _tray_refresh_visuals() -> None:
    global _last_tray_icon_phase
    if _tray_icon is None or _exiting:
        return
    try:
        _tray_icon.title = build_tray_tooltip(
            host=_config.get("host", DEFAULT_CONFIG["host"]),
            port=int(_config.get("port", DEFAULT_CONFIG["port"])),
            state=_proxy_state,
        )
        phase = _proxy_state.snapshot()["phase"]
        if _tray_base_icon is not None and phase != _last_tray_icon_phase:
            _tray_icon.icon = apply_status_badge(_tray_base_icon, phase)
            _last_tray_icon_phase = phase
    except Exception:
        pass


def _start_tray_refresh_thread() -> None:
    def loop() -> None:
        while not _exiting:
            _tray_refresh_visuals()
            time.sleep(2.0)

    threading.Thread(target=loop, daemon=True, name="tray-refresh").start()


def _on_open_in_telegram(icon=None, item=None):
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    url = f"tg://socks?server={host}&port={port}"
    log.info("Opening %s", url)
    try:
        result = webbrowser.open(url)
        if not result:
            raise RuntimeError("webbrowser.open returned False")
    except Exception:
        log.info("Browser open failed, copying to clipboard")
        if pyperclip is None:
            _show_error(
                "Не удалось открыть Telegram автоматически.\n\n"
                f"Установите пакет pyperclip для копирования в буфер или откройте вручную:\n{url}"
            )
            return
        try:
            pyperclip.copy(url)
            _show_info(
                f"Не удалось открыть Telegram автоматически.\n\n"
                f"Ссылка скопирована в буфер обмена, отправьте её в Telegram и нажмите по ней ЛКМ:\n{url}",
                "TG WS Proxy",
            )
        except Exception as exc:
            log.error("Clipboard copy failed: %s", exc)
            _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_restart(icon=None, item=None):
    threading.Thread(target=restart_proxy, daemon=True).start()


def _on_edit_config(icon=None, item=None):
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


def _edit_config_dialog():
    if ctk is None:
        _show_error("customtkinter не установлен.")
        return

    cfg = dict(_config)
    cfg["autostart"] = is_autostart_enabled()

    if _supports_autostart() and not cfg["autostart"]:
        set_autostart_enabled(False)

    theme = ctk_theme_for_platform()
    w, h = CONFIG_DIALOG_SIZE
    if _supports_autostart():
        h += 100

    icon_path = str(Path(__file__).parent / "icon.ico")

    root = create_ctk_root(
        ctk,
        title="TG WS Proxy — Настройки",
        width=w,
        height=h,
        theme=theme,
        after_create=lambda r: r.iconbitmap(icon_path),
    )

    fpx, fpy = CONFIG_DIALOG_FRAME_PAD
    frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)

    scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)

    widgets = install_tray_config_form(
        ctk,
        scroll,
        theme,
        cfg,
        DEFAULT_CONFIG,
        show_autostart=_supports_autostart(),
        autostart_value=cfg.get("autostart", False),
        proxy_state=_proxy_state,
    )

    def on_save():
        merged = validate_config_form(
            widgets,
            DEFAULT_CONFIG,
            include_autostart=_supports_autostart(),
        )
        if isinstance(merged, str):
            _show_error(merged)
            return

        new_cfg = merged
        save_config(new_cfg)
        _config.update(new_cfg)
        log.info("Config saved: %s", new_cfg)

        if _supports_autostart():
            set_autostart_enabled(bool(new_cfg.get("autostart", False)))

        _tray_icon.menu = _build_menu()

        from tkinter import messagebox

        if messagebox.askyesno(
            "Перезапустить?",
            "Настройки сохранены.\n\nПерезапустить прокси сейчас?",
            parent=root,
        ):
            root.destroy()
            restart_proxy()
        else:
            root.destroy()

    def on_cancel():
        root.destroy()

    install_tray_config_buttons(
        ctk, footer, theme, on_save=on_save, on_cancel=on_cancel
    )

    try:
        root.mainloop()
    finally:
        destroy_root_safely(root)


def _on_open_logs(icon=None, item=None):
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        os.startfile(str(LOG_FILE))
    else:
        _show_info("Файл логов ещё не создан.", "TG WS Proxy")


def _on_exit(icon=None, item=None):
    global _exiting
    if _exiting:
        os._exit(0)
        return
    _exiting = True
    log.info("User requested exit")

    def _force_exit():
        time.sleep(3)
        os._exit(0)

    threading.Thread(target=_force_exit, daemon=True, name="force-exit").start()

    if icon:
        icon.stop()


def _show_first_run():
    PATHS.app_dir.mkdir(parents=True, exist_ok=True)
    if FIRST_RUN_MARKER.exists():
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])

    if ctk is None:
        FIRST_RUN_MARKER.touch()
        return

    theme = ctk_theme_for_platform()
    icon_path = str(Path(__file__).parent / "icon.ico")
    w, h = FIRST_RUN_SIZE
    root = create_ctk_root(
        ctk,
        title="TG WS Proxy",
        width=w,
        height=h,
        theme=theme,
        after_create=lambda r: r.iconbitmap(icon_path),
    )

    def on_done(open_tg: bool):
        FIRST_RUN_MARKER.touch()
        root.destroy()
        if open_tg:
            _on_open_in_telegram()

    populate_first_run_window(
        ctk, root, theme, host=host, port=port, on_done=on_done
    )

    try:
        root.mainloop()
    finally:
        destroy_root_safely(root)


def _check_ipv6_warning():
    PATHS.app_dir.mkdir(parents=True, exist_ok=True)
    if IPV6_WARN_MARKER.exists():
        return
    if not has_ipv6_enabled("full"):
        return

    IPV6_WARN_MARKER.touch()

    threading.Thread(target=_show_ipv6_dialog, daemon=True).start()


def _show_ipv6_dialog():
    _show_info(IPV6_WARN_BODY_LONG, "TG WS Proxy")


def _build_menu():
    if pystray is None:
        return None
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    return pystray.Menu(
        pystray.MenuItem(
            f"Открыть в Telegram ({host}:{port})",
            _on_open_in_telegram,
            default=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Статус", _on_status_tcp_dialog),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Перезапустить прокси", _on_restart),
        pystray.MenuItem("Настройки...", _on_edit_config),
        pystray.MenuItem("Открыть логи", _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", _on_exit),
    )


def run_tray():
    global _tray_icon, _config, _tray_base_icon, _last_tray_icon_phase

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
    log.info("TG WS Proxy версия %s, tray app starting", __version__)
    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    if pystray is None or Image is None or ctk is None:
        log.error(
            "pystray, Pillow or customtkinter not installed; "
            "running in console mode"
        )
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

    raw_icon = _load_icon()
    _tray_base_icon = normalize_tray_icon_image(raw_icon)
    _phase0 = _proxy_state.snapshot()["phase"]
    icon_image = apply_status_badge(_tray_base_icon, _phase0)
    _last_tray_icon_phase = _phase0
    _tray_icon = pystray.Icon(
        APP_NAME,
        icon_image,
        "TG WS Proxy",
        menu=_build_menu(),
    )
    _tray_refresh_visuals()
    _start_tray_refresh_thread()

    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def main():
    if not _instance_lock.acquire():
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return

    try:
        run_tray()
    finally:
        _instance_lock.release()


if __name__ == "__main__":
    main()
