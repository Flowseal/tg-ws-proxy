from __future__ import annotations

import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Callable, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None


def render_app_icon(size: int):
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outer = tuple(round(value * scale) for value in (92, 92, 932, 932))
    draw.ellipse(outer, fill=(0, 151, 221, 255))
    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Helvetica.ttc",
            round(430 * scale),
        )
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), "T", font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(
        (
            (size - width) / 2 - box[0],
            (size - height) / 2 - box[1] - round(10 * scale),
        ),
        "T",
        font=font,
        fill=(255, 255, 255, 255),
    )
    return image


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--render-app-icon":
    if Image is None:
        raise SystemExit("Pillow is required to render the macOS app icon")
    output_path = sys.argv[2] if len(sys.argv) > 2 else "icon.icns"
    render_app_icon(1024).save(output_path, format="ICNS")
    raise SystemExit(0)

try:
    import customtkinter as ctk
except ImportError:
    ctk = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import pystray
except ImportError:
    pystray = None

try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
except ImportError:
    NSApplication = None
    NSApplicationActivationPolicyAccessory = None

from proxy import get_link_host
from ui.ctk_theme import (
    CONFIG_DIALOG_FRAME_PAD,
    CONFIG_DIALOG_SIZE,
    FIRST_RUN_SIZE,
    apply_ctk_appearance,
    create_ctk_toplevel,
    ctk_theme_for_platform,
    install_tkinter_variable_del_guard,
    main_content_frame,
)
from ui.ctk_tray_ui import (
    install_tray_config_buttons,
    install_tray_config_form,
    populate_first_run_window,
    tray_settings_scroll_and_footer,
    validate_config_form,
)
from ui.i18n import set_language, t
from utils.tray_common import (
    APP_NAME,
    DEFAULT_CONFIG,
    FIRST_RUN_MARKER,
    LOG_FILE,
    acquire_lock,
    bootstrap,
    check_ipv6_warning,
    ensure_dirs,
    load_config,
    load_icon,
    log,
    maybe_notify_update,
    release_lock,
    restart_proxy,
    save_config,
    start_proxy,
    stop_proxy,
    tg_proxy_url,
)

_tray_icon: Optional[Any] = None
_ctk_root: Optional[Any] = None
_settings_window: Optional[Any] = None
_ns_app: Optional[Any] = None
_config: dict = {}
_exiting = False


def _activate_app() -> None:
    if _ns_app is not None:
        try:
            _ns_app.activateIgnoringOtherApps_(True)
        except Exception as exc:
            log.warning("Failed to activate macOS app: %s", repr(exc))


def _dispatch(callback: Callable[[], None]) -> None:
    if _ctk_root is None:
        return
    if threading.current_thread() is threading.main_thread():
        callback()
        return
    try:
        _ctk_root.after(0, callback)
    except Exception as exc:
        log.warning("Failed to dispatch UI callback: %s", repr(exc))


def _messagebox(kind: str, text: str, title: str) -> Any:
    from tkinter import messagebox

    result: list[Any] = []
    done = threading.Event()

    def show() -> None:
        try:
            _activate_app()
            result.append(getattr(messagebox, kind)(title, text, parent=_ctk_root))
        finally:
            done.set()

    if threading.current_thread() is threading.main_thread():
        show()
    elif _ctk_root is not None:
        _dispatch(show)
        done.wait()
    return result[0] if result else False


def _standalone_info(text: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(t("app.name"), text, parent=root)
    root.destroy()


def _show_error(text: str, title: Optional[str] = None) -> None:
    _messagebox("showerror", text, title or t("app.error_title"))


def _show_info(text: str, title: Optional[str] = None) -> None:
    _messagebox("showinfo", text, title or t("app.name"))


def _ask_yes_no(text: str, title: Optional[str] = None) -> bool:
    return bool(_messagebox("askyesno", text, title or t("app.name")))


def _refresh_tray_menu() -> None:
    if _tray_icon is None:
        return
    _tray_icon.menu = _build_menu()
    try:
        _tray_icon.update_menu()
    except Exception as exc:
        log.warning("Failed to refresh tray menu: %s", repr(exc))


def _on_open_in_telegram(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Opening %s", url)
    try:
        if subprocess.call(["open", url]) != 0:
            raise OSError("open command failed")
    except OSError:
        try:
            if not webbrowser.open(url):
                raise OSError("webbrowser.open returned False")
        except OSError:
            try:
                if pyperclip is not None:
                    pyperclip.copy(url)
                else:
                    subprocess.run(["pbcopy"], input=url.encode(), check=True)
                _show_info(t("dialog.open_tg_fail_clipboard", url=url))
            except (OSError, subprocess.SubprocessError) as exc:
                _show_error(t("dialog.copy_fail", error=exc))


def _on_copy_link(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    try:
        if pyperclip is not None:
            pyperclip.copy(url)
        else:
            subprocess.run(["pbcopy"], input=url.encode(), check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        _show_error(t("dialog.copy_fail", error=exc))


def _on_restart(icon=None, item=None) -> None:
    threading.Thread(
        target=lambda: restart_proxy(_config, _show_error),
        daemon=True,
        name="proxy-restart",
    ).start()


def _on_edit_config(icon=None, item=None) -> None:
    _dispatch(_edit_config_dialog)


def _on_open_logs(icon=None, item=None) -> None:
    if LOG_FILE.exists():
        try:
            subprocess.run(["open", str(LOG_FILE)], check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            _show_error(t("dialog.log_open_fail", error=exc))
    else:
        _show_info(t("dialog.log_not_found"))


def _finish_exit() -> None:
    global _exiting
    if _exiting:
        return
    _exiting = True
    log.info("User requested exit")
    if _tray_icon is not None:
        try:
            _tray_icon.stop()
        except Exception as exc:
            log.warning("Failed to stop tray icon: %s", repr(exc))
    if _ctk_root is not None:
        _ctk_root.quit()


def _on_exit(icon=None, item=None) -> None:
    _dispatch(_finish_exit)


def _edit_config_dialog() -> None:
    global _settings_window
    if _settings_window is not None and _settings_window.winfo_exists():
        _activate_app()
        _settings_window.lift()
        _settings_window.focus_force()
        return

    cfg = dict(_config)
    theme = ctk_theme_for_platform()
    width, height = CONFIG_DIALOG_SIZE
    root = create_ctk_toplevel(
        ctk,
        title=t("app.settings_title"),
        width=width,
        height=height,
        theme=theme,
        after_create=lambda window: _activate_app(),
    )
    _settings_window = root
    frame_pad_x, frame_pad_y = CONFIG_DIALOG_FRAME_PAD
    frame = main_content_frame(
        ctk,
        root,
        theme,
        padx=frame_pad_x,
        pady=frame_pad_y,
    )
    scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)
    original_language = _config.get("language", DEFAULT_CONFIG["language"])
    widgets = install_tray_config_form(
        ctk,
        scroll,
        theme,
        cfg,
        DEFAULT_CONFIG,
        show_autostart=False,
        on_language_change=_refresh_tray_menu,
    )
    original_appearance = ctk.get_appearance_mode()

    def restore_ui_locale() -> None:
        set_language(original_language)
        _refresh_tray_menu()

    def finish() -> None:
        global _settings_window
        root.destroy()
        _settings_window = None

    def cancel() -> None:
        ctk.set_appearance_mode(original_appearance)
        restore_ui_locale()
        finish()

    def save() -> None:
        from tkinter import messagebox

        merged = validate_config_form(
            widgets,
            DEFAULT_CONFIG,
            include_autostart=False,
        )
        if isinstance(merged, str):
            messagebox.showerror(t("app.error_title"), merged, parent=root)
            return

        merged["force_test_dc"] = _config.get(
            "force_test_dc",
            DEFAULT_CONFIG["force_test_dc"],
        )
        ui_only_keys = {"appearance", "check_updates", "language"}
        config_changed = any(merged.get(key) != _config.get(key) for key in merged)
        proxy_changed = any(
            merged.get(key) != _config.get(key)
            for key in merged
            if key not in ui_only_keys
        )

        if not config_changed:
            restore_ui_locale()
            finish()
            return

        save_config(merged)
        _config.update(merged)
        set_language(merged.get("language", DEFAULT_CONFIG["language"]))
        log.info("Config saved: %s", merged)
        _refresh_tray_menu()

        if not proxy_changed:
            finish()
            return

        do_restart = messagebox.askyesno(
            t("dialog.restart_title"),
            t("dialog.restart_body"),
            parent=root,
        )
        finish()
        if do_restart:
            threading.Thread(
                target=lambda: restart_proxy(_config, _show_error),
                daemon=True,
                name="proxy-restart",
            ).start()

    root.protocol("WM_DELETE_WINDOW", cancel)
    install_tray_config_buttons(
        ctk,
        footer,
        theme,
        on_save=save,
        on_cancel=cancel,
    )
    _activate_app()


def _show_first_run() -> None:
    ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        check_ipv6_warning(_show_info)
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    secret = _config.get("secret", DEFAULT_CONFIG["secret"])
    theme = ctk_theme_for_platform()
    width, height = FIRST_RUN_SIZE
    root = create_ctk_toplevel(
        ctk,
        title=t("app.name"),
        width=width,
        height=height,
        theme=theme,
        after_create=lambda window: _activate_app(),
    )

    def done(open_telegram: bool) -> None:
        FIRST_RUN_MARKER.touch()
        root.destroy()
        if open_telegram:
            _on_open_in_telegram()
        check_ipv6_warning(_show_info)

    root.protocol("WM_DELETE_WINDOW", lambda: done(False))
    populate_first_run_window(
        ctk,
        root,
        theme,
        host=host,
        port=port,
        secret=secret,
        on_done=done,
    )
    _activate_app()


def _build_menu():
    if pystray is None:
        return None
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    link_host = get_link_host(host)
    return pystray.Menu(
        pystray.MenuItem(
            t("tray.open_telegram", host=link_host, port=port),
            _on_open_in_telegram,
            default=True,
        ),
        pystray.MenuItem(t("tray.copy_link"), _on_copy_link),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.restart"), _on_restart),
        pystray.MenuItem(t("tray.settings"), _on_edit_config),
        pystray.MenuItem(t("tray.logs"), _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.exit"), _on_exit),
    )


def _initialize_gui() -> bool:
    global _ctk_root, _ns_app
    if ctk is None or pystray is None or Image is None or NSApplication is None:
        return False
    install_tkinter_variable_del_guard()
    apply_ctk_appearance(ctk, _config.get("appearance", "auto"))
    _ctk_root = ctk.CTk()
    _ctk_root.withdraw()
    _ns_app = NSApplication.sharedApplication()
    if NSApplicationActivationPolicyAccessory is not None:
        _ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return True


def run_tray() -> None:
    global _tray_icon, _config
    _config = load_config()
    bootstrap(_config)

    if not _initialize_gui():
        log.error("pystray, Pillow, customtkinter or AppKit not installed; running in console mode")
        start_proxy(_config, _show_error)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_proxy()
        return

    start_proxy(_config, _show_error)
    _tray_icon = pystray.Icon(
        APP_NAME,
        load_icon(),
        t("app.name"),
        menu=_build_menu(),
        darwin_nsapplication=_ns_app,
    )
    _tray_icon.run_detached()
    maybe_notify_update(_config, lambda: _exiting, _ask_yes_no)
    _ctk_root.after(0, _show_first_run)
    log.info("Tray icon running")
    _ctk_root.mainloop()
    stop_proxy()
    if _ctk_root is not None:
        _ctk_root.destroy()
    log.info("Tray app exited")


def main() -> None:
    if not acquire_lock():
        _standalone_info(t("dialog.already_running"))
        return
    try:
        run_tray()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
