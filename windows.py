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

from proxy import __version__, get_link_host

from utils.win32_theme import (
    is_windows_dark_theme, 
    apply_windows_dark_theme,
)
from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, bootstrap, check_ipv6_warning, check_update_async, ctk_run_dialog,
    ensure_ctk_thread, ensure_dirs, load_config, load_icon, log,
    quit_ctk, release_lock, restart_proxy,
    save_config, start_proxy, stop_proxy, tg_proxy_url,
)
from utils.update_check import find_asset, get_status, RELEASES_PAGE_URL
from utils.windows_update import apply_update, asset_name
from ui.ctk_tray_ui import (
    install_tray_config_buttons, install_tray_config_form,
    populate_first_run_window, tray_settings_scroll_and_footer,
)
from ui.ctk_theme import (
    CONFIG_DIALOG_FRAME_PAD, CONFIG_DIALOG_SIZE, FIRST_RUN_SIZE,
    create_ctk_toplevel, ctk_theme_for_platform, main_content_frame,
)
from ui.i18n import t
from ui.settings_dialog import SettingsDialog

_tray_icon: Optional[object] = None
_config: dict = {}
_exiting = False
_win_mutex_handle = None
_update_flow_lock = threading.Lock()

_ERROR_ALREADY_EXISTS = 183


def _acquire_win_mutex() -> bool | None:
    global _win_mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, True, "Local\\TgWsProxy_SingleInstance")
        if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        if not handle:
            return None
        _win_mutex_handle = handle
        return True
    except Exception:
        return None


def _release_win_mutex() -> None:
    global _win_mutex_handle
    if _win_mutex_handle:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.ReleaseMutex(ctypes.c_void_p(_win_mutex_handle))
            kernel32.CloseHandle(ctypes.c_void_p(_win_mutex_handle))
        except Exception:
            pass
        _win_mutex_handle = None

ICON_PATH = str(Path(__file__).parent / "icon.ico")

# win32 dialogs

_u32 = ctypes.windll.user32
_u32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
_u32.MessageBoxW.restype = ctypes.c_int

_MB_OK_ERR = 0x10
_MB_OK_INFO = 0x40
_MB_YESNO_Q = 0x24
_MB_YESNOCANCEL_Q = 0x23
_IDYES = 6
_IDNO = 7


def _show_error(text: str, title: Optional[str] = None) -> None:
    _u32.MessageBoxW(None, text, title or t("app.error_title"), _MB_OK_ERR)


def _show_info(text: str, title: Optional[str] = None) -> None:
    _u32.MessageBoxW(None, text, title or t("app.name"), _MB_OK_INFO)


def _ask_yes_no(text: str, title: Optional[str] = None) -> bool:
    return _u32.MessageBoxW(None, text, title or t("app.name"), _MB_YESNO_Q) == _IDYES


def update_ctk_form(
    text: str, title: Optional[str] = None, asset: Optional[dict] = None,
    release_url: Optional[str] = None,
) -> str:
    title = title or t("app.name")
    if ctk is None or not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        if not asset:
            return "open" if _ask_yes_no(t("update.ask_open", version=get_status()["latest"]), title) else "close"
        result = _u32.MessageBoxW(None, text, title, _MB_YESNOCANCEL_Q)
        if result == _IDYES:
            _perform_update(asset)
        if result == _IDNO:
            return "open"
        return "close"

    result = {"value": "close"}

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        root = create_ctk_toplevel(
            ctk,
            title=title,
            width=310 if asset else 210,
            height=130 if asset else 100,
            theme=theme,
            topmost=True,
            after_create=lambda r: r.iconbitmap(ICON_PATH),
        )
        frame = main_content_frame(ctk, root, theme, padx=16, pady=14)

        ctk.CTkLabel(
            frame,
            text=text,
            justify="left",
            anchor="w",
            wraplength=270,
            font=(theme.ui_font_family, 12),
            text_color=theme.text_primary,
        ).pack(fill="x", pady=(0, 10))

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="x")

        status_label = ctk.CTkLabel(
            frame, text="", justify="left", anchor="w", wraplength=270,
            font=(theme.ui_font_family, 11), text_color=theme.text_secondary,
        )
        status_label.pack(fill="x", pady=(6, 0))

        btns: list = []

        def _set_status(msg: str) -> None:
            root.after(0, lambda: status_label.configure(text=msg))

        def _close_with(value: str) -> None:
            result["value"] = value
            root.destroy()
            done.set()

        def _on_update() -> None:
            for b in btns:
                b.configure(state="disabled")
            root.protocol("WM_DELETE_WINDOW", lambda: None)
            def _run():
                _perform_update(asset, set_status=_set_status)
                root.after(0, lambda: [b.configure(state="normal") for b in btns])
                root.after(0, lambda: root.protocol("WM_DELETE_WINDOW", lambda: _close_with("close")))
            threading.Thread(target=_run, daemon=True).start()

        if asset:
            btn_upd = ctk.CTkButton(
                row, text=t("button.update"), width=88, height=34,
                font=(theme.ui_font_family, 13), command=_on_update,
            )
            btn_upd.pack(side="left", padx=(0, 6))
            btns.append(btn_upd)
        btn_pg = ctk.CTkButton(
            row, text=t("button.page"), width=88, height=34,
            font=(theme.ui_font_family, 13), command=lambda: webbrowser.open(release_url or RELEASES_PAGE_URL),
        )
        btn_pg.pack(side="left", padx=(0, 6))
        btns.append(btn_pg)
        btn_cl = ctk.CTkButton(
            row, text=t("button.close"), width=88, height=34,
            font=(theme.ui_font_family, 13),
            fg_color=theme.field_bg, hover_color=theme.field_border,
            text_color=theme.text_primary, border_width=1, border_color=theme.field_border,
            command=lambda: _close_with("close"),
        )
        btn_cl.pack(side="left")
        btns.append(btn_cl)

        root.protocol("WM_DELETE_WINDOW", lambda: _close_with("close"))

    ctk_run_dialog(_build)
    return result["value"]


def _perform_update(asset: dict, set_status=None) -> None:
    restarting = False

    def _step(msg: str) -> None:
        log.info("Update: %s", msg)
        if set_status:
            set_status(msg)

    def _before_restart() -> None:
        nonlocal restarting
        _step(t("update.restarting"))
        restarting = True
        stop_proxy()
        release_lock()
        _release_win_mutex()

    _step(t("update.downloading"))
    try:
        apply_update(asset, _before_restart)
    except Exception as exc:
        log.exception("Update failed")
        if restarting:
            mutex = _acquire_win_mutex()
            if mutex is True or mutex is None and acquire_lock():
                start_proxy(_config, _show_error)
        msg = t("update.error", msg=exc)
        if set_status:
            set_status(msg)
        else:
            _show_error(msg)
        return
    os._exit(0)


def _trigger_update_flow(icon=None, item=None) -> None:
    """Show the update dialog for an update already known to be available.

    Reused by the tray "Update" item and the settings dialog's "Update"
    button, so an update can still be started after the initial startup
    prompt was skipped/closed.
    """
    if not _update_flow_lock.acquire(blocking=False):
        return

    def _show() -> None:
        try:
            if _exiting:
                return
            st = get_status()
            if not st.get("has_update"):
                return
            if _tray_icon is not None:
                _tray_icon.menu = _build_menu()
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"
            asset = find_asset(asset_name()) if IS_FROZEN else None

            choice = update_ctk_form(
                t("update.available", version=ver),
                asset=asset,
                release_url=url,
            )
            if choice == "open":
                webbrowser.open(url)
        except Exception as exc:
            log.warning("Update flow failed: %s", repr(exc))
        finally:
            _update_flow_lock.release()

    threading.Thread(target=_show, daemon=True, name="manual-update").start()


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
            t("dialog.autostart_fail", error=exc)
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
                t("dialog.open_tg_fail_manual", url=url)
            )
            return
        try:
            pyperclip.copy(url)
            _show_info(
                t("dialog.open_tg_fail_clipboard", url=url)
            )
        except Exception as exc:
            log.error("Clipboard copy failed: %s", exc)
            _show_error(t("dialog.copy_fail", error=exc))


def _on_copy_link(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying link: %s", url)
    if pyperclip is None:
        _show_error(t("dialog.pyperclip_missing"))
        return
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
        try:
            os.startfile(str(LOG_FILE))
        except Exception as exc:
            log.error("Failed to open log file: %s", exc)
            _show_error(t("dialog.log_open_fail", error=exc))
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
    cfg["autostart"] = is_autostart_enabled()

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        if _supports_autostart():
            h += 100

        root = create_ctk_toplevel(
            ctk, title=t("app.settings_title"), width=w, height=h, theme=theme,
            topmost=False, after_create=lambda r: r.iconbitmap(ICON_PATH),
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)

        def _refresh_tray_menu() -> None:
            if _tray_icon is not None:
                _tray_icon.menu = _build_menu()

        widgets = install_tray_config_form(
            ctk, scroll, theme, cfg, DEFAULT_CONFIG,
            show_autostart=_supports_autostart(),
            autostart_value=cfg.get("autostart", False),
            on_update_click=_trigger_update_flow,
        )

        def _finish() -> None:
            root.destroy()
            done.set()

        dialog = SettingsDialog(
            ctk=ctk, root=root, widgets=widgets, config=_config,
            defaults=DEFAULT_CONFIG, persist=save_config,
            refresh_menu=_refresh_tray_menu, finish=_finish,
            restart=lambda config: restart_proxy(config, _show_error),
            include_autostart=_supports_autostart(),
            apply_autostart=set_autostart_enabled if _supports_autostart() else None,
            read_autostart=is_autostart_enabled if _supports_autostart() else None,
        )

        root.protocol("WM_DELETE_WINDOW", dialog.cancel)
        install_tray_config_buttons(ctk, footer, theme, on_save=dialog.save, on_cancel=dialog.cancel)

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
            topmost=False, after_create=lambda r: r.iconbitmap(ICON_PATH),
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
    link_host = get_link_host(host)
    items = [
        pystray.MenuItem(t("tray.open_telegram", host=link_host, port=port), _on_open_in_telegram, default=True),
        pystray.MenuItem(t("tray.copy_link"), _on_copy_link),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.restart"), _on_restart),
        pystray.MenuItem(t("tray.settings"), _on_edit_config),
        pystray.MenuItem(t("tray.logs"), _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.exit"), _on_exit),
    ]

    st = get_status()
    if st.get("has_update"):
        items[-2:-2] = [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                t("tray.update", current=__version__, new=st.get("latest") or "?"),
                _trigger_update_flow,
            )
        ]
    return pystray.Menu(*items)


# entry point

def run_tray() -> None:
    global _tray_icon, _config

    _config = load_config()

    if is_windows_dark_theme():
        apply_windows_dark_theme()

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
    check_update_async(_config, lambda: _exiting, lambda version, url: _trigger_update_flow())
    _show_first_run()
    check_ipv6_warning(_show_info)

    _tray_icon = pystray.Icon(APP_NAME, load_icon(), t("app.name"), menu=_build_menu())
    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def main() -> None:
    if (mutex_result := _acquire_win_mutex()) is False or mutex_result is None and not acquire_lock():
        _show_info(t("dialog.already_running"), os.path.basename(sys.argv[0]))
        return

    if IS_FROZEN:
        def _cleanup_old_exes():
            time.sleep(3)
            exe = Path(sys.executable)
            backup = exe.with_name(exe.stem + "_oldtgws.exe")
            try:
                backup.unlink(missing_ok=True)
            except OSError:
                pass
        threading.Thread(target=_cleanup_old_exes, daemon=True, name="cleanup-old").start()

    try:
        run_tray()
    finally:
        release_lock()
        _release_win_mutex()


if __name__ == "__main__":
    main()
