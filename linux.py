from __future__ import annotations

import errno
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Callable, Optional

import customtkinter as ctk
import pyperclip
import pystray
from PIL import Image, ImageTk

from proxy import get_link_host

from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, bootstrap, check_ipv6_warning, check_update_async,
    ctk_run_dialog, ensure_ctk_thread, ensure_dirs, load_config, load_icon,
    log, quit_ctk, release_lock, restart_proxy,
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
from ui.i18n import set_language, t

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


def _show_error(text: str, title: Optional[str] = None) -> None:
    _msgbox("showerror", text, title or t("app.error_title"))


def _show_info(text: str, title: Optional[str] = None) -> None:
    _msgbox("showinfo", text, title or t("app.name"))


def _ask_yes_no(text: str, title: Optional[str] = None) -> bool:
    return bool(_msgbox("askyesno", text, title or t("app.name")))


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
        _show_info(t("dialog.copy_ok", url=url))
    except Exception as exc:
        log.error("Clipboard copy failed: %s", exc)
        _show_error(t("dialog.copy_fail", error=exc))


def _on_copy_link(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying link: %s", url)
    try:
        pyperclip.copy(url)
    except Exception as exc:
        log.error("Clipboard copy failed: %s", exc)
        _show_error(t("dialog.copy_fail", error=exc))


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
        _show_info(t("dialog.log_not_found"))


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
        _show_error(t("dialog.ctk_missing"))
        return

    cfg = dict(_config)

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        root = create_ctk_toplevel(
            ctk, title=t("app.settings_title"), width=w, height=h, 
            theme=theme, topmost=False, after_create=_apply_window_icon
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)

        def _refresh_tray_menu() -> None:
            if _tray_icon is not None:
                _tray_icon.menu = _build_menu()

        _original_language = _config.get("language", DEFAULT_CONFIG["language"])

        widgets = install_tray_config_form(
            ctk, scroll, theme, cfg, DEFAULT_CONFIG,
            show_autostart=False,
            on_language_change=_refresh_tray_menu,
        )

        _original_appearance = ctk.get_appearance_mode()

        def _restore_ui_locale() -> None:
            set_language(_original_language)
            _refresh_tray_menu()

        def _finish() -> None:
            root.destroy()
            done.set()

        def _cancel() -> None:
            ctk.set_appearance_mode(_original_appearance)
            _restore_ui_locale()
            _finish()

        def on_save() -> None:
            from tkinter import messagebox
            merged = validate_config_form(widgets, DEFAULT_CONFIG, include_autostart=False)
            if isinstance(merged, str):
                messagebox.showerror(t("app.error_title"), merged, parent=root)
                return

            merged["force_test_dc"] = _config.get("force_test_dc", DEFAULT_CONFIG["force_test_dc"])

            _ui_only_keys = {"appearance", "check_updates", "language"}
            config_changed = any(merged.get(k) != _config.get(k) for k in merged)
            proxy_changed = any(merged.get(k) != _config.get(k) for k in merged if k not in _ui_only_keys)

            if not config_changed:
                _restore_ui_locale()
                _finish()
                return

            save_config(merged)
            _config.update(merged)
            set_language(merged.get("language", DEFAULT_CONFIG["language"]))
            log.info("Config saved: %s", merged)
            _tray_icon.menu = _build_menu()

            if not proxy_changed:
                _finish()
                return

            do_restart = messagebox.askyesno(
                t("dialog.restart_title"),
                t("dialog.restart_body"),
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
            ctk, title=t("app.name"), width=w, height=h, theme=theme,
            topmost=False, after_create=_apply_window_icon,
        )

        def on_done(open_tg: bool) -> None:
            FIRST_RUN_MARKER.touch()
            root.destroy()
            done.set()
            if open_tg:
                _on_open_in_telegram()

        populate_first_run_window(ctk, root, theme, host=host, port=port, secret=secret, on_done=on_done)

    ctk_run_dialog(_build)


# update


def _wait_port_free(host: str, port: int, timeout: float = 5.0) -> bool:
    # the successor binds the same port, so don't hand it over too early
    deadline = time.monotonic() + timeout
    while True:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                return True  # some other error: waiting will not help
        finally:
            sock.close()
        if time.monotonic() >= deadline:
            log.warning("Port %s:%s still busy before restart", host, port)
            return False
        time.sleep(0.2)


def _restart_after_update() -> None:
    # the old process must release the lock and the port before the new one starts
    from utils.linux_update import exe_path, relaunch_env

    release_lock()
    stop_proxy()
    _wait_port_free(
        _config.get("host", DEFAULT_CONFIG["host"]),
        int(_config.get("port", DEFAULT_CONFIG["port"])),
    )
    try:
        subprocess.Popen(
            [str(exe_path())], env=relaunch_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError as exc:
        log.error("Failed to launch updated binary: %s", exc)
    time.sleep(0.5)
    os._exit(0)


def _maybe_do_update(cfg: dict, is_exiting: Callable[[], bool]) -> None:
    def _on_update(version: str, page_url: str) -> None:
        from utils.linux_update import asset_name, detect_install_kind
        from utils.update_check import find_asset

        kind = detect_install_kind() if IS_FROZEN else None
        asset = find_asset(asset_name(kind)) if kind else None
        if not asset:
            log.info("In-place update unavailable (kind=%s)", kind)
            if _ask_yes_no(t("update.ask_open", version=version), t("app.update_title")):
                webbrowser.open(page_url)
            return

        if not _ask_yes_no(t("update.ask_install", version=version), t("app.update_title")):
            return
        _install_update(kind, asset, page_url)

    check_update_async(cfg, is_exiting, _on_update)


def _install_update(kind: str, asset: dict, page_url: str) -> None:
    from utils.linux_update import apply_update

    err = apply_update(
        kind, asset["url"], digest=asset.get("digest", ""), log=log,
    )
    if err is None:
        log.info("Update installed, restarting")
        _restart_after_update()
        return

    log.error("Update failed: %s", err)
    if _ask_yes_no(t("update.install_fail", error=err), t("app.update_title")):
        webbrowser.open(page_url)


# tray menu


def _build_menu():
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    link_host = get_link_host(host)
    return pystray.Menu(
        pystray.MenuItem(t("tray.open_telegram", host=link_host, port=port), _on_open_in_telegram, default=True),
        pystray.MenuItem(t("tray.copy_link"), _on_copy_link),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.restart"), _on_restart),
        pystray.MenuItem(t("tray.settings"), _on_edit_config),
        pystray.MenuItem(t("tray.logs"), _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.exit"), _on_exit),
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
    _maybe_do_update(_config, lambda: _exiting)
    _show_first_run()
    check_ipv6_warning(_show_info)

    _tray_icon = pystray.Icon(APP_NAME, load_icon(), t("app.name"), menu=_build_menu())
    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def _smoke_test() -> None:
    """Exercise the frozen GUI stack without starting the proxy or writing config."""
    import gi

    gi.require_version('AppIndicator3', '0.1')
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import AppIndicator3, AyatanaAppIndicator3

    # Namespace imports alone do not resolve app_indicator_new: call it too.
    indicators = [
        namespace.Indicator.new(
            'tg-ws-proxy-smoke-test', '', namespace.IndicatorCategory.APPLICATION_STATUS,
        )
        for namespace in (AppIndicator3, AyatanaAppIndicator3)
    ]
    icon = pystray.Icon('tg-ws-proxy-smoke-test', Image.new('RGB', (16, 16)))
    root = ctk.CTk()
    root.withdraw()
    root.update_idletasks()
    root.destroy()

    # A successful constructor on the build host can hide missing bundled
    # libraries. Check where the dynamic loader actually obtained them.
    bundle_dir = os.path.realpath(sys._MEIPASS) + os.sep
    prefixes = ('libglib-', 'libgobject-', 'libgio-', 'libgtk-',
                'libappindicator', 'libayatana-', 'libdbusmenu-')
    with open('/proc/self/maps', encoding='utf-8') as maps:
        paths = {line.split(maxsplit=5)[-1].strip() for line in maps}
    for path in sorted(paths):
        if os.path.basename(path).startswith(prefixes):
            if not os.path.realpath(path).startswith(bundle_dir):
                raise RuntimeError('GUI library loaded outside the bundle: ' + path)
            print(path)
    assert icon is not None and all(indicators)
    print('Linux bundle smoke test passed')


def main() -> None:
    if sys.argv[1:] == ['--smoke-test']:
        _smoke_test()
        return
    if not acquire_lock():
        _show_info(t("dialog.already_running"), os.path.basename(sys.argv[0]))
        return
    try:
        run_tray()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
