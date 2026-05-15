from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_ICON_PATH = str(Path(__file__).parent / "icon.ico")

import customtkinter as ctk
import pyperclip
import pystray
from PIL import Image, ImageTk

from proxy import get_link_host

from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, add_status_dot, bootstrap, check_ipv6_warning, ctk_run_dialog,
    ensure_ctk_thread, ensure_dirs, is_proxy_running, load_config, load_icon, log,
    ProxyStatus, quit_ctk, release_lock, restart_proxy,
    save_config, start_proxy, StatusManager, stop_proxy, tg_proxy_url,
)
import utils.tray_common as _tray_common
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

# dialogs


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка") -> None:
    try:
        subprocess.Popen(
            ["notify-send", "--urgency=critical", "--app-name=TG WS Proxy", title, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception as exc:
        log.error("notify-send (error) failed: %s", exc)


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=TG WS Proxy", title, text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception as exc:
        log.debug("notify-send (info) failed: %s", exc)


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    try:
        result = subprocess.run(
            ["zenity", "--question", "--title", title, "--text", text,
             "--width=360", "--no-wrap", f"--window-icon={_ICON_PATH}"],
            timeout=60,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # zenity не установлен — показываем уведомление и открываем браузер
        _show_info(text, title)
        return True
    except Exception:
        return False


def _apply_window_icon(root) -> None:
    icon_img = load_icon()
    if icon_img:
        root._ctk_icon_photo = ImageTk.PhotoImage(icon_img.resize((64, 64)))
        root.iconphoto(False, root._ctk_icon_photo)


# tray callbacks


def _on_open_in_telegram(icon=None, item=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Opening %s", url)
    try:
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
        result = subprocess.run(
            ["xdg-open", url], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if result.returncode == 0:
            return
        raise RuntimeError(f"xdg-open exited with {result.returncode}")
    except Exception as exc:
        log.info("xdg-open failed (%s), copying to clipboard", exc)
        try:
            pyperclip.copy(url)
            _notify_send("TG WS Proxy", f"Ссылка скопирована в буфер обмена:\n{url}")
        except Exception as exc2:
            log.error("Clipboard copy failed: %s", exc2)
            _show_error(f"Не удалось скопировать ссылку:\n{exc2}")


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


def _fix_scroll(widget) -> None:
    try:
        canvas = widget._parent_canvas
    except AttributeError:
        return

    canvas.configure(yscrollincrement=20)

    _last_scroll = [0.0]

    def _scroll(direction):
        now = time.monotonic()
        if now - _last_scroll[0] < 0.016:  # ~60 fps cap
            return "break"
        _last_scroll[0] = now
        canvas.yview_scroll(direction, "units")
        return "break"

    def _rebind(w):
        w.bind("<MouseWheel>", lambda e: _scroll(-1 if e.delta > 0 else 1), add="+")
        w.bind("<Button-4>", lambda e: _scroll(-1), add="+")
        w.bind("<Button-5>", lambda e: _scroll(1), add="+")
        for child in w.winfo_children():
            _rebind(child)

    _rebind(widget)


def _edit_config_dialog() -> None:
    if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        _show_error("customtkinter не установлен.")
        return

    cfg = dict(_config)
    cfg["autostart"] = is_autostart_enabled()

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        w, h = CONFIG_DIALOG_SIZE
        if _supports_autostart():
            h += 100
        root = create_ctk_toplevel(
            ctk, title="TG WS Proxy — Настройки", width=w, height=h, theme=theme,
            after_create=_apply_window_icon,
        )
        fpx, fpy = CONFIG_DIALOG_FRAME_PAD
        frame = main_content_frame(ctk, root, theme, padx=fpx, pady=fpy)
        scroll, footer = tray_settings_scroll_and_footer(ctk, frame, theme)
        widgets = install_tray_config_form(
            ctk, scroll, theme, cfg, DEFAULT_CONFIG,
            show_autostart=_supports_autostart(),
            autostart_value=cfg.get("autostart", False),
        )
        root.after(50, lambda: _fix_scroll(scroll))

        _original_appearance = ctk.get_appearance_mode()

        def _finish() -> None:
            root.destroy()
            done.set()

        def _cancel() -> None:
            ctk.set_appearance_mode(_original_appearance)
            _finish()

        def on_save() -> None:
            from tkinter import messagebox
            merged = validate_config_form(widgets, DEFAULT_CONFIG, include_autostart=_supports_autostart())
            if isinstance(merged, str):
                messagebox.showerror("TG WS Proxy — Ошибка", merged, parent=root)
                return

            _ui_only_keys = {"appearance", "autostart", "check_updates"}
            config_changed = any(merged.get(k) != cfg.get(k) for k in merged)
            proxy_changed = any(merged.get(k) != cfg.get(k) for k in merged if k not in _ui_only_keys)

            if not config_changed:
                _finish()
                return

            save_config(merged)
            _config.update(merged)
            log.info("Config saved: %s", merged)
            if _supports_autostart():
                set_autostart_enabled(bool(merged.get("autostart", False)))
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


# autostart (XDG)

_XDG_AUTOSTART = Path.home() / ".config" / "autostart" / "tg-ws-proxy.desktop"

_DESKTOP_TEMPLATE = """\
[Desktop Entry]
Type=Application
Name=TG WS Proxy
Exec={cmd}
Icon=tg-ws-proxy
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""


def _supports_autostart() -> bool:
    return True


def _autostart_command() -> str:
    if IS_FROZEN:
        return sys.executable
    return f"{sys.executable} {Path(__file__).resolve()}"


def is_autostart_enabled() -> bool:
    return _XDG_AUTOSTART.exists()


def set_autostart_enabled(enabled: bool, ignore_errors: bool = False) -> None:
    try:
        if enabled:
            _XDG_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            _XDG_AUTOSTART.write_text(
                _DESKTOP_TEMPLATE.format(cmd=_autostart_command()),
                encoding="utf-8",
            )
        else:
            _XDG_AUTOSTART.unlink(missing_ok=True)
    except OSError as exc:
        log.error("Failed to update autostart: %s", exc)
        if not ignore_errors:
            _show_error(f"Не удалось изменить автозапуск.\n\nОшибка: {exc}")


# update check (CTK dialog)

def _maybe_do_update(cfg: dict, is_exiting) -> None:
    if not cfg.get("check_updates", True):
        return

    def _work():
        time.sleep(1.5)
        if is_exiting():
            return
        try:
            import webbrowser
            from proxy import __version__
            from utils.update_check import RELEASES_PAGE_URL, get_status, run_check

            run_check(__version__)
            st = get_status()
            if not st.get("has_update") or is_exiting():
                return
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"

            if not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
                if _ask_yes_no(
                    f"Доступна новая версия: {ver}\n\nОткрыть страницу релиза в браузере?",
                    "TG WS Proxy — обновление",
                ):
                    webbrowser.open(url)
                return

            result = {"open": False}

            def _build(done: threading.Event) -> None:
                from ui.ctk_theme import main_content_frame
                theme = ctk_theme_for_platform()
                root = create_ctk_toplevel(
                    ctk, title="TG WS Proxy — обновление",
                    width=310, height=110, theme=theme,
                    after_create=_apply_window_icon,
                )
                frame = main_content_frame(ctk, root, theme, padx=16, pady=14)
                ctk.CTkLabel(
                    frame,
                    text=f"Доступна новая версия: {ver}",
                    justify="left", anchor="w", wraplength=270,
                    font=(theme.ui_font_family, 12),
                    text_color=theme.text_primary,
                ).pack(fill="x", pady=(0, 10))
                row = ctk.CTkFrame(frame, fg_color="transparent")
                row.pack(fill="x")

                def _close(open_browser: bool) -> None:
                    result["open"] = open_browser
                    root.destroy()
                    done.set()

                ctk.CTkButton(
                    row, text="Страница", width=100, height=34,
                    font=(theme.ui_font_family, 13),
                    command=lambda: _close(True),
                ).pack(side="left", padx=(0, 6))
                ctk.CTkButton(
                    row, text="Закрыть", width=100, height=34,
                    font=(theme.ui_font_family, 13),
                    fg_color=theme.field_bg, hover_color=theme.field_border,
                    text_color=theme.text_primary, border_width=1, border_color=theme.field_border,
                    command=lambda: _close(False),
                ).pack(side="left")
                root.protocol("WM_DELETE_WINDOW", lambda: _close(False))

            ctk_run_dialog(_build)
            if result["open"]:
                webbrowser.open(url)
        except Exception as exc:
            log.warning("Update check failed: %s", repr(exc))

    threading.Thread(target=_work, daemon=True, name="update-check").start()


# notify-send

def _notify_send(title: str, message: str) -> None:
    try:
        subprocess.Popen(
            ["notify-send", "--app-name=TG WS Proxy", title, message],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception as exc:
        log.debug("notify-send failed: %s", exc)


# DC pinger

_dc_pings: dict[int, int | None] = {}


def _ping_tcp(ip: str, port: int = 443, timeout: float = 3.0) -> int | None:
    import socket
    try:
        t0 = time.monotonic()
        with socket.create_connection((ip, port), timeout=timeout):
            pass
        return int((time.monotonic() - t0) * 1000)
    except Exception:
        return None


def _start_dc_pinger() -> None:
    def _work() -> None:
        while not _exiting:
            from proxy.config import proxy_config
            for dc, ip in list(proxy_config.dc_redirects.items()):
                _dc_pings[dc] = _ping_tcp(ip)
            time.sleep(30)

    threading.Thread(target=_work, daemon=True, name="dc-pinger").start()


# status dot + tooltip updater

def _start_icon_updater() -> None:
    from proxy.stats import stats
    from proxy.utils import human_bytes

    _base_icon = load_icon()
    _prev_bytes: list[int] = [0, 0]

    def _on_status_change(status: ProxyStatus, previous: ProxyStatus) -> None:
        if _tray_icon is None:
            return
        try:
            _tray_icon.icon = add_status_dot(_base_icon, status.value)
        except Exception:
            pass
        if status == ProxyStatus.STOPPED and previous is not None:
            reason = _tray_common._crash_reason
            if reason == "port_busy":
                msg = "Прокси остановлен: порт занят другим приложением"
            elif reason:
                msg = "Прокси упал — проверьте логи"
            else:
                msg = "Прокси остановлен"
            threading.Thread(
                target=lambda: _notify_send("TG WS Proxy", msg),
                daemon=True,
            ).start()
            _prev_bytes[0] = 0
            _prev_bytes[1] = 0

    def _on_tick() -> None:
        if _tray_icon is None:
            return

        ping_str = "  ".join(
            f"DC{dc}: {ms}ms" if ms is not None else f"DC{dc}: —"
            for dc, ms in sorted(_dc_pings.items())
        )

        speed_up   = max(0, stats.bytes_up   - _prev_bytes[0])
        speed_down = max(0, stats.bytes_down - _prev_bytes[1])
        _prev_bytes[0] = stats.bytes_up
        _prev_bytes[1] = stats.bytes_down

        if not is_proxy_running():
            title = "TG WS Proxy — не запущен"
        elif stats.connections_active > 0:
            title = (
                f"TG WS Proxy\n"
                f"Активных: {stats.connections_active}\n"
                f"↑ {human_bytes(speed_up)}/s  ↓ {human_bytes(speed_down)}/s"
            )
            if ping_str:
                title += f"\n{ping_str}"
        else:
            title = "TG WS Proxy"
            if ping_str:
                title += f"\n{ping_str}"

        try:
            _tray_icon.title = title
        except Exception:
            pass

    StatusManager(_on_status_change, on_tick=_on_tick).start(lambda: _exiting)


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
    _maybe_do_update(_config, lambda: _exiting)
    _show_first_run()
    check_ipv6_warning(_show_info)

    with open(os.devnull, "w") as _devnull:
        import contextlib
        with contextlib.redirect_stderr(_devnull):
            _tray_icon = pystray.Icon(APP_NAME, load_icon(), "TG WS Proxy", menu=_build_menu())
    _start_icon_updater()
    _start_dc_pinger()
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
