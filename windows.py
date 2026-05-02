from __future__ import annotations

import ctypes
import os
import subprocess
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

from proxy import get_link_host

from utils.win32_theme import (
    is_windows_dark_theme, 
    apply_windows_dark_theme,
)
from utils.tray_common import (
    APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IS_FROZEN, LOG_FILE,
    acquire_lock, bootstrap, check_ipv6_warning, ctk_run_dialog,
    ensure_ctk_thread, ensure_dirs, load_config, load_icon, log,
    quit_ctk, release_lock, restart_proxy,
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
_win_mutex_handle = None

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


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка") -> None:
    _u32.MessageBoxW(None, text, title, _MB_OK_ERR)


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    _u32.MessageBoxW(None, text, title, _MB_OK_INFO)


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    return _u32.MessageBoxW(None, text, title, _MB_YESNO_Q) == _IDYES


def update_ctk_form(
    text: str, title: str = "TG WS Proxy", download_url: Optional[str] = None,
    release_url: Optional[str] = None,
) -> str:
    if ctk is None or not ensure_ctk_thread(ctk, _config.get("appearance", "auto")):
        result = _u32.MessageBoxW(None, text, title, _MB_YESNOCANCEL_Q)
        if result == _IDYES:
            return "update"
        if result == _IDNO:
            return "open"
        return "close"

    result = {"value": "close"}

    def _build(done: threading.Event) -> None:
        theme = ctk_theme_for_platform()
        root = create_ctk_toplevel(
            ctk,
            title=title,
            width=310 if IS_FROZEN else 210,
            height=130 if IS_FROZEN else 100,
            theme=theme,
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
            if not download_url:
                if release_url:
                    webbrowser.open(release_url)
                _close_with("open")
                return
            for b in btns:
                b.configure(state="disabled")
            root.protocol("WM_DELETE_WINDOW", lambda: None)
            def _run():
                _perform_update(download_url, set_status=_set_status)
                root.after(0, lambda: [b.configure(state="normal") for b in btns])
                root.after(0, lambda: root.protocol("WM_DELETE_WINDOW", lambda: _close_with("close")))
            threading.Thread(target=_run, daemon=True).start()

        if IS_FROZEN:
            btn_upd = ctk.CTkButton(
                row, text="Обновить", width=88, height=34,
                font=(theme.ui_font_family, 13), command=_on_update,
            )
            btn_upd.pack(side="left", padx=(0, 6))
            btns.append(btn_upd)
        btn_pg = ctk.CTkButton(
            row, text="Страница", width=88, height=34,
            font=(theme.ui_font_family, 13), command=lambda: _close_with("open"),
        )
        btn_pg.pack(side="left", padx=(0, 6))
        btns.append(btn_pg)
        btn_cl = ctk.CTkButton(
            row, text="Закрыть", width=88, height=34,
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


def _perform_update(download_url: str, set_status=None) -> None:
    import tempfile
    import urllib.request

    def _step(msg: str) -> None:
        log.info("Update: %s", msg)
        if set_status:
            set_status(msg)
            time.sleep(0.8)

    def _err(msg: str) -> None:
        log.error("Update error: %s", msg)
        if set_status:
            set_status(f"Ошибка: {msg}")
        else:
            _show_error(msg)

    _step("Скачивание...")
    cur_exe = Path(sys.executable)
    old_exe = cur_exe.with_name(cur_exe.stem + "_oldtgws.exe")
    tmp_path = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=cur_exe.parent, suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)
        log.info("Downloading update from %s", download_url)
        urllib.request.urlretrieve(download_url, str(tmp_path))
    except Exception as exc:
        _err(f"Не удалось скачать:\n{exc}")
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return

    _step("Замена файла...")
    try:
        if old_exe.exists():
            old_exe.unlink()
        cur_exe.rename(old_exe)
    except Exception as exc:
        _err(f"Не удалось переименовать файл:\n{exc}")
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    try:
        tmp_path.rename(cur_exe)
    except Exception as exc:
        _err(f"Не удалось переместить файл:\n{exc}")
        try:
            old_exe.rename(cur_exe)
        except OSError:
            pass
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return

    _step("Перезапуск...")
    _release_win_mutex()
    stop_proxy()

    # Don't reuse existing _MEI* dir
    env = os.environ.copy()
    for _k in [k for k in env if k.startswith("_PYI_") or k == "_MEIPASS"]:
        del env[_k]
    if hasattr(sys, "_MEIPASS"):
        _mei = os.path.normcase(sys._MEIPASS.rstrip("\\/"))
        env["PATH"] = os.pathsep.join(
            p for p in env.get("PATH", "").split(os.pathsep)
            if os.path.normcase(p.rstrip("\\/")) != _mei
        )

    try:
        subprocess.Popen(
            [str(cur_exe)],
            env=env,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except Exception as exc:
        log.error("Failed to launch updated exe: %s", exc)
    time.sleep(0.5)
    os._exit(0)


def _maybe_do_update(cfg: dict, is_exiting) -> None:
    if not cfg.get("check_updates", True):
        return

    def _work():
        time.sleep(1.5)
        if is_exiting():
            return
        try:
            from proxy import __version__
            from utils.update_check import RELEASES_PAGE_URL, get_status, get_update_asset, run_check

            run_check(__version__)
            st = get_status()
            if not st.get("has_update") or is_exiting():
                return
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"
            asset = get_update_asset(Path(sys.executable)) if IS_FROZEN else None
            choice = update_ctk_form(
                f"Доступна новая версия: {ver}",
                download_url=asset[0] if asset else None,
                release_url=url,
            )
            if choice == "open":
                webbrowser.open(url)
        except Exception as exc:
            log.warning("Update check failed: %s", repr(exc))

    threading.Thread(target=_work, daemon=True, name="update-check").start()


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
        try:
            os.startfile(str(LOG_FILE))
        except Exception as exc:
            log.error("Failed to open log file: %s", exc)
            _show_error(f"Не удалось открыть файл логов:\n{exc}")
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
    _maybe_do_update(_config, lambda: _exiting)
    _show_first_run()
    check_ipv6_warning(_show_info)

    _tray_icon = pystray.Icon(APP_NAME, load_icon(), "TG WS Proxy", menu=_build_menu())
    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def main() -> None:
    if (mutex_result := _acquire_win_mutex()) is False or mutex_result is None and not acquire_lock():
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return

    if IS_FROZEN:
        def _cleanup_old_exes():
            exe_dir = Path(sys.executable).parent
            time.sleep(3)
            for _f in exe_dir.glob("*_oldtgws.exe"):
                try:
                    _f.unlink()
                    log.info("Deleted leftover: %s", _f)
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
