from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Optional

import customtkinter as ctk
import pyperclip
import pystray
from PIL import Image, ImageTk

from proxy import get_link_host

from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, LOG_FILE,
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

# dialogs (tkinter messagebox)


def _msgbox(kind: str, text: str, title: str, **kw):
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    result = getattr(_mb, kind)(title, text, parent=root, **kw)
    root.destroy()
    return result


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка") -> None:
    _msgbox("showerror", text, title)


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    _msgbox("showinfo", text, title)


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    return bool(_msgbox("askyesno", text, title))


def _apply_window_icon(root) -> None:
    icon_img = load_icon()
    if icon_img:
        root._ctk_icon_photo = ImageTk.PhotoImage(icon_img.resize((64, 64)))
        root.iconphoto(False, root._ctk_icon_photo)


# tray callbacks


def _on_open_in_telegram(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying %s", url)
    try:
        pyperclip.copy(url)
        _show_info(
            f"Ссылка скопирована в буфер обмена, отправьте её в Telegram и нажмите по ней ЛКМ:\n{url}"
        )
    except Exception as exc:
        log.error("Clipboard copy failed: %s", exc)
        _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_copy_link(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying link: %s", url)
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
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
        subprocess.Popen(
            ["xdg-open", str(LOG_FILE)], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
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
    if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        _show_error("customtkinter не установлен.")
        return

    cfg = dict(_config)

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        root = create_ctk_toplevel(
            ctk, title="TG WS Proxy — Настройки", width=w, height=h, theme=theme,
            after_create=_apply_window_icon,
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)
        widgets = install_tray_config_form(ctk, scroll, theme, cfg, DEFAULT_CONFIG, show_autostart=False)

        _original_appearance = ctk.get_appearance_mode()

        def _finish() -> None:
            root.destroy()
            done.set()

        def _cancel() -> None:
            ctk.set_appearance_mode(_original_appearance)
            _finish()

        def on_save() -> None:
            from tkinter import messagebox
            merged = validate_config_form(widgets, DEFAULT_CONFIG, include_autostart=False)
            if isinstance(merged, str):
                messagebox.showerror("TG WS Proxy — Ошибка", merged, parent=root)
                return

            _ui_only_keys = {"appearance", "check_updates"}
            config_changed = any(merged.get(k) != cfg.get(k) for k in merged)
            proxy_changed = any(merged.get(k) != cfg.get(k) for k in merged if k not in _ui_only_keys)

            if not config_changed:
                _finish()
                return

            save_config(merged)
            _config.update(merged)
            log.info("Config saved: %s", merged)
            _tray_icon.menu = _build_menu()

            if not proxy_changed:
                _finish()
                return

            do_restart = messagebox.askyesno(
                "Перезапустить?",
                "Настройки сохранены.\n\nПерезапустить прокси сейчас?",
                parent=root,
            )
            _finish()
            if do_restart:
                threading.Thread(target=lambda: restart_proxy(_config, _show_error), daemon=True).start()

        root.protocol("WM_DELETE_WINDOW", _cancel)
        install_tray_config_buttons(ctk, footer, theme, on_save=on_save, on_cancel=_cancel)

    ctk_run_dialog(_build)


# first run


def _show_first_run() -> None:
    ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return
    if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
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
            after_create=_apply_window_icon,
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
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    link_host = get_link_host(host)
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

    if pystray is None or Image is None:
        log.error("pystray or Pillow not installed; running in console mode")
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
    if not acquire_lock():
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return
    try:
        run_tray()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
