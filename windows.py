from __future__ import annotations

import ctypes
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

import proxy.tg_ws_proxy as tg_ws_proxy

from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, bootstrap, check_ipv6_warning, ctk_run_dialog,
    ensure_ctk_thread, ensure_dirs, load_config, load_icon, log,
    maybe_notify_update, quit_ctk, release_lock, restart_proxy,
    save_config, start_proxy, stop_proxy, tg_proxy_url,
)
from ui.ctk_tray_ui import (
    install_tray_config_buttons, install_tray_config_form,
    populate_first_run_window, tray_settings_scroll_and_footer,
    validate_config_form,
)
from ui.ctk_theme import (
    CONFIG_DIALOG_FRAME_PAD, CONFIG_DIALOG_SIZE, FIRST_RUN_SIZE,
    create_ctk_toplevel, ctk_theme_for_platform, main_content_frame,
)

_tray_icon: Optional[object] = None
_config: dict = {}
_exiting = False

ICON_PATH = str(Path(__file__).parent / "icon.ico")

# win32 dialogs

_u32 = ctypes.windll.user32
_u32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
_u32.MessageBoxW.restype = ctypes.c_int

_MB_OK_ERR = 0x10
_MB_OK_INFO = 0x40
_MB_YESNO_Q = 0x24
_IDYES = 6


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка") -> None:
    _u32.MessageBoxW(None, text, title, _MB_OK_ERR)


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    _u32.MessageBoxW(None, text, title, _MB_OK_INFO)


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    return _u32.MessageBoxW(None, text, title, _MB_YESNO_Q) == _IDYES


# autostart (registry)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _supports_autostart() -> bool:
    return IS_FROZEN


def _autostart_command() -> str:
    return f'"{sys.executable}"'


def is_autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
        return str(val).strip() == _autostart_command().strip()
    except (FileNotFoundError, OSError):
        return False


def set_autostart_enabled(enabled: bool) -> None:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        log.error("Failed to update autostart: %s", exc)
        _show_error(
            "Не удалось изменить автозапуск.\n\n"
            "Попробуйте запустить приложение от имени пользователя "
            f"с правами на реестр.\n\nОшибка: {exc}"
        )


# tray callbacks

def _on_open_in_telegram(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Opening %s", url)
    try:
        if not webbrowser.open(url):
            raise RuntimeError
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
                "Не удалось открыть Telegram автоматически.\n\n"
                f"Ссылка скопирована в буфер обмена, отправьте её в Telegram и нажмите по ней ЛКМ:\n{url}"
            )
        except Exception as exc:
            log.error("Clipboard copy failed: %s", exc)
            _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_copy_link(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying link: %s", url)
    if pyperclip is None:
        _show_error(
            "Установите пакет pyperclip для копирования в буфер обмена."
        )
        return
    try:
        pyperclip.copy(url)
    except Exception as exc:
        log.error("Clipboard copy failed: %s", exc)
        _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_restart(icon=None, item=None) -> None:
    threading.Thread(
        target=lambda: restart_proxy(_config, _show_error), daemon=True
    ).start()


def _on_edit_config(icon=None, item=None) -> None:
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


def _on_open_logs(icon=None, item=None) -> None:
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        os.startfile(str(LOG_FILE))
    else:
        _show_info("Файл логов ещё не создан.")


def _on_exit(icon=None, item=None) -> None:
    global _exiting
    if _exiting:
        os._exit(0)
        return
    _exiting = True
    log.info("User requested exit")
    quit_ctk()
    threading.Thread(target=lambda: (time.sleep(3), os._exit(0)), daemon=True, name="force-exit").start()
    if icon:
        icon.stop()


# settings dialog

def _edit_config_dialog() -> None:
    if not ensure_ctk_thread(ctk):
        _show_error("customtkinter не установлен.")
        return

    cfg = dict(_config)
    cfg["autostart"] = is_autostart_enabled()
    if _supports_autostart() and not cfg["autostart"]:
        set_autostart_enabled(False)

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        if _supports_autostart():
            h += 100

        root = create_ctk_toplevel(
            ctk, title="TG WS Proxy — Настройки", width=w, height=h, theme=theme,
            after_create=lambda r: r.iconbitmap(ICON_PATH),
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)
        widgets = install_tray_config_form(
            ctk, scroll, theme, cfg, DEFAULT_CONFIG,
            show_autostart=_supports_autostart(),
            autostart_value=cfg.get("autostart", False),
        )

        def _finish() -> None:
            root.destroy()
            done.set()

        def on_save() -> None:
            from tkinter import messagebox
            merged = validate_config_form(widgets, DEFAULT_CONFIG, include_autostart=_supports_autostart())
            if isinstance(merged, str):
                messagebox.showerror("TG WS Proxy — Ошибка", merged, parent=root)
                return
            save_config(merged)
            _config.update(merged)
            log.info("Config saved: %s", merged)
            if _supports_autostart():
                set_autostart_enabled(bool(merged.get("autostart", False)))
            _tray_icon.menu = _build_menu()

            do_restart = messagebox.askyesno(
                "Перезапустить?",
                "Настройки сохранены.\n\nПерезапустить прокси сейчас?",
                parent=root,
            )
            _finish()
            if do_restart:
                threading.Thread(target=lambda: restart_proxy(_config, _show_error), daemon=True).start()

        root.protocol("WM_DELETE_WINDOW", _finish)
        install_tray_config_buttons(ctk, footer, theme, on_save=on_save, on_cancel=_finish)

    ctk_run_dialog(_build)


# first run

def _show_first_run() -> None:
    ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return
    if not ensure_ctk_thread(ctk):
        FIRST_RUN_MARKER.touch()
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    secret = _config.get("secret", DEFAULT_CONFIG["secret"])

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = FIRST_RUN_SIZE
        root = create_ctk_toplevel(
            ctk, title="TG WS Proxy", width=w, height=h, theme=theme,
            after_create=lambda r: r.iconbitmap(ICON_PATH),
        )

        def on_done(open_tg: bool) -> None:
            FIRST_RUN_MARKER.touch()
            root.destroy()
            done.set()
            if open_tg:
                _on_open_in_telegram()

        populate_first_run_window(ctk, root, theme, host=host, port=port, secret=secret, on_done=on_done)

    ctk_run_dialog(_build)


# tray menu

def _build_menu():
    if pystray is None:
        return None
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    link_host = tg_ws_proxy.get_link_host(host)
    return pystray.Menu(
        pystray.MenuItem(f"Открыть в Telegram ({link_host}:{port})", _on_open_in_telegram, default=True),
        pystray.MenuItem("Скопировать ссылку", _on_copy_link),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Перезапустить прокси", _on_restart),
        pystray.MenuItem("Настройки...", _on_edit_config),
        pystray.MenuItem("Открыть логи", _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", _on_exit),
    )


# entry point

def run_tray() -> None:
    global _tray_icon, _config

    _config = load_config()
    bootstrap(_config)

    if pystray is None or Image is None or ctk is None:
        log.error("pystray, Pillow or customtkinter not installed; running in console mode")
        start_proxy(_config, _show_error)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_proxy()
        return

    start_proxy(_config, _show_error)
    maybe_notify_update(_config, lambda: _exiting, _ask_yes_no)
    _show_first_run()
    check_ipv6_warning(_show_info)

    _tray_icon = pystray.Icon(APP_NAME, load_icon(), "TG WS Proxy", menu=_build_menu())
    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def main() -> None:
    if not acquire_lock("windows.py"):
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return
    try:
        run_tray()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
