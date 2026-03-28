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

import customtkinter as ctk
import pyperclip
import pystray
from PIL import Image

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
    frozen_match_app_name_contains,
    make_same_process_checker,
)
from utils.tray_paths import APP_NAME, tray_paths_linux
from utils.tray_proxy_state import ProxyRuntimeState, build_tray_tooltip
from utils.tray_proxy_runner import ProxyThreadRunner
from utils.tray_updates import spawn_notify_update_async

PATHS = tray_paths_linux()
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

_instance_lock = SingleInstanceLock(
    PATHS.app_dir,
    make_same_process_checker(
        script_marker="linux.py",
        frozen_match=frozen_match_app_name_contains(APP_NAME),
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
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    _mb.showerror(title, text, parent=root)
    root.destroy()


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    _mb.showinfo(title, text, parent=root)
    root.destroy()


def _ask_yes_no_dialog(text: str, title: str = "TG WS Proxy") -> bool:
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    r = _mb.askyesno(title, text, parent=root)
    root.destroy()
    return bool(r)


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


def _load_icon():
    assets = Path(__file__).parent
    return load_ico_or_synthesize(
        assets / "icon.ico",
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        ],
    )


def _apply_linux_ctk_window_icon(root) -> None:
    icon_img = _load_icon()
    if icon_img:
        from PIL import ImageTk

        root._ctk_icon_photo = ImageTk.PhotoImage(icon_img.resize((64, 64)))
        root.iconphoto(False, root._ctk_icon_photo)


def _maybe_notify_update_async() -> None:
    spawn_notify_update_async(
        get_config=lambda: _config,
        exiting=lambda: _exiting,
        ask_open_release=lambda ver, _url: _ask_yes_no_dialog(
            f"Доступна новая версия: {ver}\n\n"
            f"Открыть страницу релиза в браузере?",
            "TG WS Proxy — обновление",
        ),
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
    log.info("Copying %s", url)

    try:
        pyperclip.copy(url)
        _show_info(
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

    theme = ctk_theme_for_platform()
    w, h = CONFIG_DIALOG_SIZE

    root = create_ctk_root(
        ctk,
        title="TG WS Proxy — Настройки",
        width=w,
        height=h,
        theme=theme,
        after_create=_apply_linux_ctk_window_icon,
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
        show_autostart=False,
        proxy_state=_proxy_state,
    )

    def on_save():
        merged = validate_config_form(
            widgets, DEFAULT_CONFIG, include_autostart=False
        )
        if isinstance(merged, str):
            _show_error(merged)
            return

        new_cfg = merged
        save_config(new_cfg)
        _config.update(new_cfg)
        log.info("Config saved: %s", new_cfg)

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
        env = os.environ.copy()
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)

        subprocess.Popen(
            ["xdg-open", str(LOG_FILE)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
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
    w, h = FIRST_RUN_SIZE

    root = create_ctk_root(
        ctk,
        title="TG WS Proxy",
        width=w,
        height=h,
        theme=theme,
        after_create=_apply_linux_ctk_window_icon,
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
    if not has_ipv6_enabled("simple"):
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

    if pystray is None or Image is None:
        log.error("pystray or Pillow not installed; running in console mode")
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
