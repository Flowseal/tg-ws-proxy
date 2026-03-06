from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
import asyncio as _asyncio
from pathlib import Path
from typing import Dict, List, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None  # type: ignore

try:
    import pystray
except ImportError:
    pystray = None  # type: ignore

try:
    import customtkinter as ctk
    from tkinter import messagebox, filedialog
except ImportError:
    ctk = None  # type: ignore
    messagebox = None  # type: ignore
    filedialog = None  # type: ignore

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

# Proxy engine
import tg_ws_proxy


APP_NAME = "TgWsProxy"
IS_WINDOWS = platform.system() == "Windows"
IS_LINUX = platform.system() == "Linux"

# Platform-specific paths
if IS_WINDOWS:
    APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
elif IS_LINUX:
    APP_DIR = Path.home() / ".config" / APP_NAME.lower()
else:
    APP_DIR = Path.home() / f".{APP_NAME.lower()}"

CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done"


DEFAULT_CONFIG = {
    "port": 1080,
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
}


_proxy_thread: Optional[threading.Thread] = None
_stop_event: Optional[threading.Event] = None
_async_stop: Optional[object] = None
_tray_icon: Optional[object] = None
_config: dict = {}
_exiting: bool = False

log = logging.getLogger("tg-ws-tray")


def is_already_running():
    current_proc = os.path.basename(sys.argv[0])
    count = 0
    for process in psutil.process_iter(['name']):
        if process.info['name'] == current_proc:
            count += 1
    return count > 2


def _ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults for missing keys
            for k, v in DEFAULT_CONFIG.items():
                data.setdefault(k, v)
            return data
        except Exception as exc:
            log.warning("Failed to load config: %s", exc)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    _ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def setup_logging(verbose: bool = False):
    _ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fh = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(fh)

    if not getattr(sys, "frozen", False):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(message)s",
            datefmt="%H:%M:%S"))
        root.addHandler(ch)


def _make_icon_image(size: int = 64):
    """Create a simple tray icon: blue circle with a white 'T' letter."""
    if Image is None:
        raise RuntimeError("Pillow is required for tray icon")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # Blue circle
    margin = 2
    draw.ellipse([margin, margin, size - margin, size - margin],
                 fill=(0, 136, 204, 255))
                 
    # White "T"
    try:
        font = ImageFont.truetype("arial.ttf", size=int(size * 0.55))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "T", fill=(255, 255, 255, 255), font=font)

    return img


def _load_icon():
    """Load icon from file or generate one."""
    icon_path = Path(__file__).parent / "icon.ico"
    if icon_path.exists() and Image:
        try:
            return Image.open(str(icon_path))
        except Exception:
            pass
    return _make_icon_image()



def _run_proxy_thread(port: int, dc_opt: Dict[int, str], verbose: bool):
    """Target for the proxy thread — runs asyncio event loop."""
    global _async_stop
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    stop_ev = _asyncio.Event()
    _async_stop = (loop, stop_ev)

    try:
        loop.run_until_complete(
            tg_ws_proxy._run(port, dc_opt, stop_event=stop_ev))
    except Exception as exc:
        log.error("Proxy thread crashed: %s", exc)
    finally:
        loop.close()
        _async_stop = None


def start_proxy():
    global _proxy_thread, _config
    if _proxy_thread and _proxy_thread.is_alive():
        log.info("Proxy already running")
        return

    cfg = _config
    port = cfg.get("port", DEFAULT_CONFIG["port"])
    dc_ip_list = cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])
    verbose = cfg.get("verbose", False)

    try:
        dc_opt = tg_ws_proxy.parse_dc_ip_list(dc_ip_list)
    except ValueError as e:
        log.error("Bad config dc_ip: %s", e)
        _show_error(f"Ошибка конфигурации:\n{e}")
        return

    log.info("Starting proxy on port %d ...", port)
    _proxy_thread = threading.Thread(
        target=_run_proxy_thread,
        args=(port, dc_opt, verbose),
        daemon=True, name="proxy")
    _proxy_thread.start()


def stop_proxy():
    global _proxy_thread, _async_stop
    if _async_stop:
        loop, stop_ev = _async_stop
        loop.call_soon_threadsafe(stop_ev.set)
        if _proxy_thread:
            _proxy_thread.join(timeout=2)
    _proxy_thread = None
    log.info("Proxy stopped")


def restart_proxy():
    log.info("Restarting proxy...")
    stop_proxy()
    time.sleep(0.3)
    start_proxy()


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка"):
    """Показать диалог ошибки (кроссплатформенно)."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
            return
        except Exception:
            pass
    # Linux / fallback
    if messagebox:
        root = ctk.CTk() if ctk else None
        if root:
            root.withdraw()
            messagebox.showerror(title, text, parent=root)
            root.destroy()
        else:
            log.error("%s: %s", title, text)
    else:
        log.error("%s: %s", title, text)


def _show_info(text: str, title: str = "TG WS Proxy"):
    """Показать информационный диалог (кроссплатформенно)."""
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
            return
        except Exception:
            pass
    # Linux / fallback
    if messagebox:
        root = ctk.CTk() if ctk else None
        if root:
            root.withdraw()
            messagebox.showinfo(title, text, parent=root)
            root.destroy()
        else:
            log.info("%s: %s", title, text)
    else:
        log.info("%s: %s", title, text)


def _copy_to_clipboard(text: str):
    """Copy text to clipboard (кроссплатформенно)."""
    if IS_WINDOWS:
        try:
            import ctypes.wintypes
            CF_UNICODETEXT = 13
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            user32.OpenClipboard(0)
            user32.EmptyClipboard()

            encoded = text.encode("utf-16-le") + b"\x00\x00"
            h = kernel32.GlobalAlloc(0x0042, len(encoded))
            p = kernel32.GlobalLock(h)
            ctypes.memmove(p, encoded, len(encoded))
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
            user32.CloseClipboard()
            return
        except Exception as exc:
            log.error("Windows clipboard failed: %s", exc)

    # Linux: try xclip or xsel
    if IS_LINUX:
        for cmd in [["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"]]:
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                proc.communicate(text.encode("utf-8"))
                if proc.returncode == 0:
                    log.debug("Copied via %s", cmd[0])
                    return
            except Exception:
                continue
        log.warning("xclip/xsel not available for clipboard")

    # Fallback: try tkinter clipboard
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update()
        root.destroy()
        log.debug("Copied via tkinter clipboard")
        return
    except Exception:
        pass

    log.error("Failed to copy to clipboard on this platform")



def _on_open_in_telegram(icon=None, item=None):
    port = _config.get("port", DEFAULT_CONFIG["port"])
    url = f"tg://socks?server=127.0.0.1&port={port}"
    log.info("Opening %s", url)
    try:
        result = webbrowser.open(url)
        if not result:
            raise RuntimeError("webbrowser.open returned False")
    except Exception:
        log.info("Browser open failed, copying to clipboard")
        try:
            _copy_to_clipboard(url)
            _show_info(
                f"Не удалось открыть Telegram автоматически.\n\n"
                f"Ссылка скопирована в буфер обмена, отправьте её в телеграмм и нажмите по ней ЛКМ:\n{url}",
                "TG WS Proxy")
        except Exception as exc:
            log.error("Clipboard copy failed: %s", exc)
            _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _copy_to_clipboard(text: str):
    """Copy text to Windows clipboard using ctypes."""
    import ctypes.wintypes
    CF_UNICODETEXT = 13
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    user32.OpenClipboard(0)
    user32.EmptyClipboard()

    encoded = text.encode("utf-16-le") + b"\x00\x00"
    h = kernel32.GlobalAlloc(0x0042, len(encoded))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, encoded, len(encoded))
    kernel32.GlobalUnlock(h)
    user32.SetClipboardData(CF_UNICODETEXT, h)
    user32.CloseClipboard()


def _on_restart(icon=None, item=None):
    threading.Thread(target=restart_proxy, daemon=True).start()


def _on_edit_config(icon=None, item=None):
    """Open a simple dialog to edit config."""
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


def _edit_config_dialog():
    if ctk is None:
        _show_error("customtkinter не установлен.")
        return

    cfg = dict(_config)

    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("TG WS Proxy — Настройки")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    TG_BLUE = "#3390ec"
    TG_BLUE_HOVER = "#2b7cd4"
    BG = "#ffffff"
    FIELD_BG = "#f0f2f5"
    FIELD_BORDER = "#d6d9dc"
    TEXT_PRIMARY = "#000000"
    TEXT_SECONDARY = "#707579"
    FONT_FAMILY = "Segoe UI"

    w, h = 420, 400
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    root.configure(fg_color=BG)

    frame = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=24, pady=20)

    # Port
    ctk.CTkLabel(frame, text="Порт прокси",
                 font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
                 anchor="w").pack(anchor="w", pady=(0, 4))
    port_var = ctk.StringVar(value=str(cfg.get("port", 1080)))
    port_entry = ctk.CTkEntry(frame, textvariable=port_var, width=120, height=36,
                              font=(FONT_FAMILY, 13), corner_radius=10,
                              fg_color=FIELD_BG, border_color=FIELD_BORDER,
                              border_width=1, text_color=TEXT_PRIMARY)
    port_entry.pack(anchor="w", pady=(0, 12))

    # DC-IP mappings
    ctk.CTkLabel(frame, text="DC → IP маппинги (по одному на строку, формат DC:IP)",
                 font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
                 anchor="w").pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(frame, width=370, height=120,
                                font=("Consolas", 12), corner_radius=10,
                                fg_color=FIELD_BG, border_color=FIELD_BORDER,
                                border_width=1, text_color=TEXT_PRIMARY)
    dc_textbox.pack(anchor="w", pady=(0, 12))
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])))

    # Verbose
    verbose_var = ctk.BooleanVar(value=cfg.get("verbose", False))
    ctk.CTkCheckBox(frame, text="Подробное логирование (verbose)",
                    variable=verbose_var, font=(FONT_FAMILY, 13),
                    text_color=TEXT_PRIMARY,
                    fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
                    corner_radius=6, border_width=2,
                    border_color=FIELD_BORDER).pack(anchor="w", pady=(0, 8))

    # Info label
    ctk.CTkLabel(frame, text="Изменения вступят в силу после перезапуска прокси.",
                 font=(FONT_FAMILY, 11), text_color=TEXT_SECONDARY,
                 anchor="w").pack(anchor="w", pady=(0, 16))

    def on_save():
        try:
            port_val = int(port_var.get().strip())
            if not (1 <= port_val <= 65535):
                raise ValueError
        except ValueError:
            _show_error("Порт должен быть числом 1-65535")
            return

        lines = [l.strip() for l in dc_textbox.get("1.0", "end").strip().splitlines()
                 if l.strip()]
        try:
            tg_ws_proxy.parse_dc_ip_list(lines)
        except ValueError as e:
            _show_error(str(e))
            return

        new_cfg = {
            "port": port_val,
            "dc_ip": lines,
            "verbose": verbose_var.get(),
        }
        save_config(new_cfg)
        _config.update(new_cfg)
        log.info("Config saved: %s", new_cfg)

        from tkinter import messagebox
        if messagebox.askyesno("Перезапустить?",
                               "Настройки сохранены.\n\n"
                               "Перезапустить прокси сейчас?",
                               parent=root):
            root.destroy()
            restart_proxy()
        else:
            root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x")
    ctk.CTkButton(btn_frame, text="Сохранить", width=140, height=38,
                  font=(FONT_FAMILY, 14, "bold"), corner_radius=10,
                  fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
                  text_color="#ffffff",
                  command=on_save).pack(side="left", padx=(0, 10))
    ctk.CTkButton(btn_frame, text="Отмена", width=140, height=38,
                  font=(FONT_FAMILY, 14), corner_radius=10,
                  fg_color=FIELD_BG, hover_color=FIELD_BORDER,
                  text_color=TEXT_PRIMARY, border_width=1,
                  border_color=FIELD_BORDER,
                  command=on_cancel).pack(side="left")

    root.mainloop()


def _open_file_platform_specific(path: Path):
    """Открыть файл в стандартном приложении платформы."""
    try:
        if IS_WINDOWS:
            os.startfile(str(path))
        elif IS_LINUX:
            # для xdg-open (freedesktop standard)
            subprocess.Popen(["xdg-open", str(path)])
        else:
            # для macOS 
            subprocess.Popen(["open", str(path)])
    except Exception as exc:
        log.error("Failed to open file: %s", exc)
        _show_error(f"Не удалось открыть файл:\n{exc}")


def _on_open_logs(icon=None, item=None):
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        _open_file_platform_specific(LOG_FILE)
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
    _ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return

    port = _config.get("port", DEFAULT_CONFIG["port"])
    tg_url = f"tg://socks?server=127.0.0.1&port={port}"

    if ctk is None:
        FIRST_RUN_MARKER.touch()
        return

    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    TG_BLUE = "#3390ec"
    TG_BLUE_HOVER = "#2b7cd4"
    BG = "#ffffff"
    FIELD_BG = "#f0f2f5"
    FIELD_BORDER = "#d6d9dc"
    TEXT_PRIMARY = "#000000"
    TEXT_SECONDARY = "#707579"
    FONT_FAMILY = "Segoe UI"

    root = ctk.CTk()
    root.title("TG WS Proxy")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    w, h = 520, 440
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    root.configure(fg_color=BG)

    frame = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=28, pady=24)

    title_frame = ctk.CTkFrame(frame, fg_color="transparent")
    title_frame.pack(anchor="w", pady=(0, 16), fill="x")

    # Blue accent bar
    accent_bar = ctk.CTkFrame(title_frame, fg_color=TG_BLUE,
                              width=4, height=32, corner_radius=2)
    accent_bar.pack(side="left", padx=(0, 12))

    ctk.CTkLabel(title_frame, text="Прокси запущен и работает в системном трее",
                 font=(FONT_FAMILY, 17, "bold"),
                 text_color=TEXT_PRIMARY).pack(side="left")

    # Info sections
    sections = [
        ("Как подключить Telegram Desktop:", True),
        ("  Автоматически:", True),
        (f"  ПКМ по иконке в трее → «Открыть в Telegram»", False),
        (f"  Или ссылка: {tg_url}", False),
        ("\n  Вручную:", True),
        ("  Настройки → Продвинутые → Тип подключения → Прокси", False),
        (f"  SOCKS5 → 127.0.0.1 : {port} (без логина/пароля)", False),
    ]

    for text, bold in sections:
        weight = "bold" if bold else "normal"
        ctk.CTkLabel(frame, text=text,
                     font=(FONT_FAMILY, 13, weight),
                     text_color=TEXT_PRIMARY,
                     anchor="w", justify="left").pack(anchor="w", pady=1)

    # Spacer
    ctk.CTkFrame(frame, fg_color="transparent", height=16).pack()

    # Separator
    ctk.CTkFrame(frame, fg_color=FIELD_BORDER, height=1,
                 corner_radius=0).pack(fill="x", pady=(0, 12))

    # Checkbox
    auto_var = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(frame, text="Открыть прокси в Telegram сейчас",
                    variable=auto_var, font=(FONT_FAMILY, 13),
                    text_color=TEXT_PRIMARY,
                    fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
                    corner_radius=6, border_width=2,
                    border_color=FIELD_BORDER).pack(anchor="w", pady=(0, 16))

    def on_ok():
        FIRST_RUN_MARKER.touch()
        open_tg = auto_var.get()
        root.destroy()
        if open_tg:
            _on_open_in_telegram()

    ctk.CTkButton(frame, text="Начать", width=180, height=42,
                  font=(FONT_FAMILY, 15, "bold"), corner_radius=10,
                  fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
                  text_color="#ffffff",
                  command=on_ok).pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", on_ok)
    root.mainloop()


def _build_menu():
    if pystray is None:
        return None
    port = _config.get("port", DEFAULT_CONFIG["port"])
    return pystray.Menu(
        pystray.MenuItem(
            f"Открыть в Telegram (:{port})",
            _on_open_in_telegram,
            default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Перезапустить прокси", _on_restart),
        pystray.MenuItem("Настройки...", _on_edit_config),
        pystray.MenuItem("Открыть логи", _on_open_logs),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", _on_exit),
    )


def run_tray():
    global _tray_icon, _config

    _config = load_config()
    save_config(_config)

    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
        except Exception:
            pass

    setup_logging(_config.get("verbose", False))
    log.info("TG WS Proxy tray app starting")
    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    if pystray is None or Image is None:
        log.error("pystray or Pillow not installed; "
                  "running in console mode")
        start_proxy()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            stop_proxy()
        return

    start_proxy()

    _show_first_run()

    icon_image = _load_icon()
    _tray_icon = pystray.Icon(
        APP_NAME,
        icon_image,
        "TG WS Proxy",
        menu=_build_menu())

    log.info("Tray icon running")
    _tray_icon.run()

    stop_proxy()
    log.info("Tray app exited")


def main():
    if is_already_running():
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return

    # Hide console window if running as frozen exe on Windows
    if getattr(sys, "frozen", False) and IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.user32.ShowWindow(
                ctypes.windll.kernel32.GetConsoleWindow(), 0)
        except Exception:
            pass

    run_tray()


if __name__ == "__main__":
    main()
