from __future__ import annotations

import faulthandler
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Any, Callable, Optional

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = ImageChops = None


def render_app_icon(size: int):
    # Use the same artwork as Windows, including when generating the ICNS in CI.
    with Image.open(Path(__file__).resolve().parent / "icon.ico") as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def render_tray_icon(size: int = 64):
    """Use the Windows circle with its white T cut out for AppKit tinting."""
    with Image.open(Path(__file__).resolve().parent / "icon.ico") as source:
        source = source.convert("RGBA")
    # The artwork has a blue circle and a white T. Saturation retains the
    # circle and removes the letter, including its antialiased edge pixels.
    red, green, blue, alpha = source.split()
    maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
    saturation = ImageChops.subtract(maximum, minimum)
    peak = saturation.getpixel((source.width // 2, source.height // 8))
    if peak:
        saturation = saturation.point(lambda value: min(255, round(value * 255 / peak)))
    mask = ImageChops.multiply(alpha, saturation)
    image = Image.new("RGBA", source.size, (0, 0, 0, 0))
    image.putalpha(mask)
    # Keep the status-item canvas unchanged, with artwork 1.5 times smaller.
    artwork_size = round(size / 1.5)
    artwork = image.resize((artwork_size, artwork_size), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = (size - artwork_size) // 2
    canvas.paste(artwork, (offset, offset))
    return canvas


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


if pystray is not None:
    class MacTemplateIcon(pystray.Icon):
        def _assert_image(self):
            # pystray 0.19.5 recreates NSImage here without setting template mode.
            super()._assert_image()
            self._icon_image.setTemplate_(True)

try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
except ImportError:
    NSApplication = None
    NSApplicationActivationPolicyAccessory = None

from proxy import __version__, get_link_host
from utils.update_check import RELEASES_PAGE_URL, get_status
from utils import macos_integration as mac_integration
from proxy.utils import build_github_opener
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
    APP_DIR,
    APP_NAME,
    DEFAULT_CONFIG,
    FIRST_RUN_MARKER,
    LOG_FILE,
    acquire_lock,
    bootstrap,
    check_ipv6_warning,
    ensure_dirs,
    load_config,
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
_update_busy = False
_crash_log: Optional[Any] = None
_ui_queue: queue.Queue = queue.Queue()


def _activate_app() -> None:
    if _ns_app is not None:
        try:
            _ns_app.activateIgnoringOtherApps_(True)
        except Exception as exc:
            log.warning("Failed to activate macOS app: %s", repr(exc))


def _hide_ctk_root() -> None:
    if _ctk_root is None:
        return
    try:
        _ctk_root.withdraw()
    except Exception as exc:
        log.warning("Failed to hide CTk root: %s", repr(exc))


def _dispatch(callback: Callable[[], None], delay_ms: int = 0) -> None:
    if _ctk_root is None:
        return

    def invoke() -> None:
        try:
            callback()
        except Exception as exc:
            log.exception("UI callback failed")
            try:
                _show_error(str(exc))
            except Exception as dialog_exc:
                log.error("Failed to show UI error: %s", repr(dialog_exc))

    _ui_queue.put((time.monotonic() + delay_ms / 1000.0, invoke))


def _pump_ui_queue() -> None:
    root = _ctk_root
    if root is None:
        return

    now = time.monotonic()
    deferred = []
    for _ in range(32):
        try:
            due_at, callback = _ui_queue.get_nowait()
        except queue.Empty:
            break
        if due_at <= now:
            callback()
        else:
            deferred.append((due_at, callback))
    for item in deferred:
        _ui_queue.put(item)

    try:
        root.after(20, _pump_ui_queue)
    except Exception:
        pass


def _messagebox(kind: str, text: str, title: str) -> Any:
    import tkinter as tk
    from tkinter import messagebox

    result: list[Any] = []
    done = threading.Event()

    def show() -> None:
        parent = None
        try:
            _activate_app()
            _hide_ctk_root()
            parent = tk.Toplevel(_ctk_root)
            parent.withdraw()
            result.append(getattr(messagebox, kind)(title, text, parent=parent))
        finally:
            if parent is not None:
                try:
                    parent.destroy()
                except tk.TclError:
                    pass
            _hide_ctk_root()
            if _ctk_root is not None:
                try:
                    _ctk_root.after_idle(_hide_ctk_root)
                    _ctk_root.after(50, _hide_ctk_root)
                except tk.TclError:
                    pass
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
    log.info("Settings requested")
    _dispatch(_edit_config_dialog, delay_ms=300)


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
    if _update_busy:
        _show_info(t("update.mac_busy"))
        return
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
    if _settings_window is not None:
        try:
            if _settings_window.winfo_exists():
                _activate_app()
                _settings_window.lift()
                _settings_window.focus_force()
                return
        except Exception as exc:
            log.warning("Failed to reuse settings window: %s", repr(exc))
        _settings_window = None

    log.info("Creating settings window")
    cfg = dict(_config)
    bundle = mac_integration.app_bundle()
    cfg["autostart"] = bool(bundle and mac_integration.startup_enabled(bundle))
    theme = ctk_theme_for_platform()
    width, height = CONFIG_DIALOG_SIZE
    root = create_ctk_toplevel(
        ctk,
        title=t("app.settings_title"),
        width=width,
        height=height,
        theme=theme,
        topmost=False,
        after_create=lambda window: _activate_app(),
    )
    _settings_window = root
    log.info("Settings window created")
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
    log.info("Building settings form")
    widgets = install_tray_config_form(
        ctk,
        scroll,
        theme,
        cfg,
        DEFAULT_CONFIG,
        show_autostart=bundle is not None,
        autostart_value=cfg["autostart"],
        startup_platform="macos",
        on_language_change=_refresh_tray_menu,
        on_update_click=_on_update,
    )
    log.info("Settings form built")
    original_appearance = ctk.get_appearance_mode()

    def restore_ui_locale() -> None:
        set_language(original_language)
        _refresh_tray_menu()

    def finish() -> None:
        global _settings_window
        root.destroy()
        _settings_window = None
        _hide_ctk_root()

    def cancel() -> None:
        ctk.set_appearance_mode(original_appearance)
        restore_ui_locale()
        finish()

    def save() -> None:
        from tkinter import messagebox

        merged = validate_config_form(
            widgets,
            DEFAULT_CONFIG,
            include_autostart=bundle is not None,
        )
        if isinstance(merged, str):
            messagebox.showerror(t("app.error_title"), merged, parent=root)
            return

        merged["force_test_dc"] = _config.get(
            "force_test_dc",
            DEFAULT_CONFIG["force_test_dc"],
        )
        if bundle is not None:
            try:
                mac_integration.set_startup(bundle, bool(merged.get("autostart")))
            except (OSError, ValueError) as exc:
                messagebox.showerror(t("app.error_title"),
                                     t("dialog.mac_autostart_fail", error=exc), parent=root)
                return
        ui_only_keys = {"appearance", "check_updates", "language", "autostart"}
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
    log.info("Settings window ready")


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
        topmost=False,
        after_create=lambda window: _activate_app(),
    )

    def done(open_telegram: bool) -> None:
        FIRST_RUN_MARKER.touch()
        root.destroy()
        _hide_ctk_root()
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


def _on_update(icon=None, item=None) -> None:
    _dispatch(_show_update_dialog, delay_ms=300)


def _show_update_dialog() -> None:
    global _update_busy
    if _exiting or _update_busy:
        return
    status = get_status()
    if not status.get("has_update"):
        return
    bundle = mac_integration.app_bundle()
    if bundle is None:
        webbrowser.open((status.get("html_url") or "").strip() or RELEASES_PAGE_URL)
        return
    if not _ask_yes_no(t("update.mac_confirm", version=status.get("latest") or "?"),
                       t("app.update_title")):
        return
    _update_busy = True
    window = ctk.CTkToplevel(_ctk_root)
    window.title(t("app.update_title"))
    window.geometry("420x140")
    window.protocol("WM_DELETE_WINDOW", lambda: None)
    label = ctk.CTkLabel(window, text=t("update.downloading"))
    label.pack(padx=20, pady=35)
    _activate_app()

    def finish(error=None, work=None):
        global _update_busy
        _update_busy = False
        window.destroy()
        if error:
            _show_error(t("update.error", msg=error))
        elif work is not None:
            try:
                mac_integration.launch_installer(bundle, work)
            except OSError as exc:
                _show_error(t("update.error", msg=exc))
                return
            _finish_exit()

    def download():
        try:
            work = mac_integration.prepare_update(
                bundle, status, build_github_opener(),
                progress=lambda key: _dispatch(lambda: label.configure(text=t(key))),
            )
        except Exception as exc:
            log.exception("macOS update preparation failed")
            _dispatch(lambda error=str(exc): finish(error=error))
        else:
            _dispatch(lambda: finish(work=work))

    threading.Thread(target=download, daemon=True, name="macos-update").start()


def _build_menu():
    if pystray is None:
        return None
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    link_host = get_link_host(host)
    items = [
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
    ]
    status = get_status()
    if status.get("has_update"):
        items[-2:-2] = [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                t("tray.update", current=__version__, new=status.get("latest") or "?"),
                _on_update,
            ),
        ]
    return pystray.Menu(*items)


def _initialize_gui() -> bool:
    global _ctk_root, _ns_app
    if ctk is None or pystray is None or Image is None or NSApplication is None:
        return False
    install_tkinter_variable_del_guard()
    apply_ctk_appearance(ctk, _config.get("appearance", "auto"))
    _ctk_root = ctk.CTk()
    _ctk_root.title(t("app.name"))
    _ctk_root.geometry("1x1+0+0")
    try:
        _ctk_root.attributes("-alpha", 0.0)
    except Exception as exc:
        log.warning("Failed to make CTk root transparent: %s", repr(exc))
    _ctk_root.withdraw()
    _ctk_root.after(20, _pump_ui_queue)
    _ns_app = NSApplication.sharedApplication()
    if NSApplicationActivationPolicyAccessory is not None:
        _ns_app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    return True


def _enable_crash_log() -> None:
    global _crash_log
    try:
        ensure_dirs()
        _crash_log = open(APP_DIR / "crash.log", "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_crash_log, all_threads=True)
    except OSError as exc:
        log.warning("Failed to enable crash log: %s", repr(exc))


def run_tray() -> None:
    global _tray_icon, _config
    _config = load_config()
    bootstrap(_config)
    _enable_crash_log()
    bundle = mac_integration.app_bundle()
    if bundle is not None:
        try:
            mac_integration.cleanup_old_updates(bundle)
        except OSError as exc:
            log.warning("Failed to clean old macOS updates: %s", repr(exc))

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
    _tray_icon = MacTemplateIcon(
        APP_NAME,
        render_tray_icon(),
        t("app.name"),
        menu=_build_menu(),
        darwin_nsapplication=_ns_app,
    )
    _tray_icon.run_detached()
    maybe_notify_update(
        _config, lambda: _exiting, _ask_yes_no,
        on_checked=lambda: _dispatch(_refresh_tray_menu),
        on_update_available=(
            (lambda: _dispatch(_show_update_dialog)) if bundle is not None else None
        ),
    )
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
