from __future__ import annotations

import ctypes
import json
import logging
import os
import psutil
import socket as _socket
import sys
import threading
import time
import webbrowser
import pystray
import pyperclip
import asyncio as _asyncio
import customtkinter as ctk
from pathlib import Path
from typing import Dict, Optional
from PIL import Image, ImageDraw, ImageFont

import proxy.tg_ws_proxy as tg_ws_proxy


APP_NAME = "TgWsProxy"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done"


DEFAULT_CONFIG = {
    "port": 1080,
    "host": "127.0.0.1",
    "listen_all": False,
    "advanced_host": False,
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
}


def _get_all_local_ips() -> list[str]:
    result: list[str] = []
    if sys.platform == "win32":
        try:
            import subprocess
            out = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace"
            )
            if out.returncode != 0:
                raise RuntimeError("ipconfig failed")
            for line in out.stdout.splitlines():
                line = line.strip()
                if "IPv4" in line and ":" in line:
                    # "   IPv4 Address. . . . . . . . . . . : 192.168.3.1(Preferred)"
                    part = line.split(":", 1)[-1].strip().split()[0].strip().split("(")[0].strip()
                    if part and part not in result:
                        try:
                            _socket.inet_aton(part)
                            if part != "0.0.0.0":
                                result.append(part)
                        except OSError:
                            pass
        except Exception:
            pass
    if not result:
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
                s.settimeout(0.5)
                s.connect(("8.8.8.8", 80))
                result.append(s.getsockname()[0])
        except Exception:
            pass
    return result


def _get_lan_ip() -> str:
    """Return LAN IP for display when listen_all is enabled. Prefers 192.168.* and avoids typical VPN ranges (10.8.*)."""
    ips = _get_all_local_ips()
    if not ips:
        return "?"
    # Priority: 192.168.* > other 10.* (except 10.8.* which is commonly VPN) > 10.8.* > others
    def rank(ip: str) -> tuple[int, str]:
        try:
            parts = ip.split(".")
            if len(parts) != 4:
                return (99, ip)
            a, b = int(parts[0]), int(parts[1])
            if a == 192 and b == 168:
                return (0, ip)
            if a == 10:
                if b == 8:
                    return (3, ip)
                return (1, ip)
            return (2, ip)
        except (ValueError, IndexError):
            return (99, ip)
    ips.sort(key=rank)
    return ips[0]


def _get_port(cfg: dict) -> int:
    return cfg.get("port", DEFAULT_CONFIG["port"]) if cfg.get("advanced_host", False) else DEFAULT_CONFIG["port"]


def get_local_connection_address(cfg: dict) -> tuple[str, int]:
    return "127.0.0.1", _get_port(cfg)


def is_network_available(cfg: dict) -> bool:
    if cfg.get("advanced_host", False):
        host = (cfg.get("host", DEFAULT_CONFIG["host"]) or "").strip()
        return host == "0.0.0.0"
    return bool(cfg.get("listen_all", False))


def get_network_connection_address(cfg: dict) -> tuple[str, int]:
    port = _get_port(cfg)
    if not is_network_available(cfg):
        return "—", port
    return _get_lan_ip(), port


_proxy_thread: Optional[threading.Thread] = None
_async_stop: Optional[object] = None
_tray_icon: Optional[object] = None
_config: dict = {}
_exiting: bool = False
_lock_file_path: Optional[Path] = None

log = logging.getLogger("tg-ws-tray")


def _same_process(lock_meta: dict, proc: psutil.Process) -> bool:
    try:
        lock_ct = float(lock_meta.get("create_time", 0.0))
        proc_ct = float(proc.create_time())
        if lock_ct > 0 and abs(lock_ct - proc_ct) > 1.0:
            return False
    except Exception:
        return False

    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return os.path.basename(sys.executable) == proc.name()

    return False


def _release_lock():
    global _lock_file_path
    if not _lock_file_path:
        return
    try:
        _lock_file_path.unlink(missing_ok=True)
    except Exception:
        pass
    _lock_file_path = None


def _acquire_lock() -> bool:
    global _lock_file_path
    _ensure_dirs()
    lock_files = list(APP_DIR.glob("*.lock"))

    for f in lock_files:
        pid = None
        meta: dict = {}

        try:
            pid = int(f.stem)
        except Exception:
            f.unlink(missing_ok=True)
            continue

        try:
            raw = f.read_text(encoding="utf-8").strip()
            if raw:
                meta = json.loads(raw)
        except Exception:
            meta = {}

        try:
            proc = psutil.Process(pid)
            if _same_process(meta, proc):
                return False
        except Exception:
            pass

        f.unlink(missing_ok=True)

    lock_file = APP_DIR / f"{os.getpid()}.lock"
    try:
        proc = psutil.Process(os.getpid())
        payload = {
            "create_time": proc.create_time(),
        }
        lock_file.write_text(json.dumps(payload, ensure_ascii=False),
                             encoding="utf-8")
    except Exception:
        lock_file.touch()

    _lock_file_path = lock_file
    return True


def _ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    _ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
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
    if Image is None:
        raise RuntimeError("Pillow is required for tray icon")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    margin = 2
    draw.ellipse([margin, margin, size - margin, size - margin],
                 fill=(0, 136, 204, 255))
                 
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
    icon_path = Path(__file__).parent / "icon.ico"
    if icon_path.exists() and Image:
        try:
            return Image.open(str(icon_path))
        except Exception:
            pass
    return _make_icon_image()



def _run_proxy_thread(port: int, dc_opt: Dict[int, str], verbose: bool,
                      host: str = '127.0.0.1'):
    global _async_stop
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    stop_ev = _asyncio.Event()
    _async_stop = (loop, stop_ev)

    try:
        loop.run_until_complete(
            tg_ws_proxy._run(port, dc_opt, stop_event=stop_ev, host=host))
    except Exception as exc:
        log.error("Proxy thread crashed: %s", exc)
        if "10048" in str(exc) or "Address already in use" in str(exc):
            _show_error("Не удалось запустить прокси:\nПорт уже используется другим приложением.\n\nЗакройте приложение, использующее этот порт, или измените порт в настройках прокси и перезапустите.")
    finally:
        loop.close()
        _async_stop = None


def start_proxy():
    global _proxy_thread, _config
    if _proxy_thread and _proxy_thread.is_alive():
        log.info("Proxy already running")
        return

    cfg = _config
    if cfg.get("advanced_host", False):
        host = cfg.get("host", DEFAULT_CONFIG["host"])
        port = cfg.get("port", DEFAULT_CONFIG["port"])
        dc_ip_list = cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])
    else:
        host = "0.0.0.0" if cfg.get("listen_all", False) else "127.0.0.1"
        port = DEFAULT_CONFIG["port"]
        dc_ip_list = DEFAULT_CONFIG["dc_ip"]
    verbose = cfg.get("verbose", False)

    try:
        dc_opt = tg_ws_proxy.parse_dc_ip_list(dc_ip_list)
    except ValueError as e:
        log.error("Bad config dc_ip: %s", e)
        _show_error(f"Ошибка конфигурации:\n{e}")
        return

    log.info("Starting proxy on %s:%d ...", host, port)
    _proxy_thread = threading.Thread(
        target=_run_proxy_thread,
        args=(port, dc_opt, verbose, host),
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
    if _tray_icon is not None:
        _tray_icon.menu = _build_menu()


def _show_error(text: str, title: str = "TG WS Proxy — Ошибка"):
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)


def _show_info(text: str, title: str = "TG WS Proxy"):
    ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)


def _on_open_in_telegram(icon=None, item=None):
    host, port = get_local_connection_address(_config)
    url = f"tg://socks?server={host}&port={port}"
    log.info("Opening %s", url)
    try:
        result = webbrowser.open(url)
        if not result:
            raise RuntimeError("webbrowser.open returned False")
    except Exception:
        log.info("Browser open failed, copying to clipboard")
        try:
            pyperclip.copy(url)
            _show_info(
                f"Не удалось открыть Telegram автоматически.\n\n"
                f"Ссылка скопирована в буфер обмена, отправьте её в телеграмм и нажмите по ней ЛКМ:\n{url}",
                "TG WS Proxy")
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
    DISABLED_TEXT = "#9ca3af"
    DISABLED_BG = "#e5e7eb"
    FONT_FAMILY = "Segoe UI"

    def _set_widget_cursor(widget, cursor_name: str):
        try:
            widget.configure(cursor=cursor_name)
        except Exception:
            pass
        try:
            for child in widget.winfo_children():
                _set_widget_cursor(child, cursor_name)
        except Exception:
            pass

    w, h = 420, 760
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")
    root.configure(fg_color=BG)

    frame = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=24, pady=20)

    # Access mode (using only when advanced settings are disabled)
    ctk.CTkLabel(frame, text="Режим доступа",
                 font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
                 anchor="w").pack(anchor="w", pady=(0, 6))
    listen_all_var = ctk.BooleanVar(value=cfg.get("listen_all", False))
    network_mode_frame = ctk.CTkFrame(frame, fg_color="transparent")
    network_mode_frame.pack(anchor="w", pady=(0, 4))
    ctk.CTkRadioButton(
        network_mode_frame, text="Только этот компьютер (127.0.0.1)",
        variable=listen_all_var, value=False,
        font=(FONT_FAMILY, 12), text_color=TEXT_PRIMARY,
        fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
    ).pack(anchor="w")
    ctk.CTkRadioButton(
        network_mode_frame, text="Вся локальная сеть (доступ с других устройств)",
        variable=listen_all_var, value=True,
        font=(FONT_FAMILY, 12), text_color=TEXT_PRIMARY,
        fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
    ).pack(anchor="w", pady=(4, 0))

    host_var = ctk.StringVar(value=cfg.get("host", "127.0.0.1"))
    port_var = ctk.StringVar(value=str(cfg.get("port", 1080)))
    advanced_host_var = ctk.BooleanVar(value=cfg.get("advanced_host", False))

    def get_port_val() -> int:
        if advanced_host_var.get():
            try:
                return int(port_var.get().strip()) if port_var.get().strip() else 1080
            except ValueError:
                return 1080
        return DEFAULT_CONFIG["port"]

    def is_network_available_here() -> bool:
        if advanced_host_var.get():
            return (host_var.get().strip() or "127.0.0.1") == "0.0.0.0"
        return listen_all_var.get()

    def update_connection_label():
        p = get_port_val()
        local_addr = f"127.0.0.1:{p}"
        net_available = is_network_available_here()
        if net_available:
            if advanced_host_var.get():
                net_addr = f"{_get_lan_ip()}:{p}"
            else:
                net_addr = f"{_get_lan_ip()}:{p}"
            conn_label_network.configure(text=f"Локальная сеть: {net_addr}", text_color=TG_BLUE)
            copy_btn_network.configure(state="normal", fg_color=TG_BLUE)
        else:
            conn_label_network.configure(text="Локальная сеть: недоступно", text_color=TEXT_SECONDARY)
            copy_btn_network.configure(state="disabled", fg_color=DISABLED_BG)
        conn_label_local.configure(text=f"Этот компьютер: {local_addr}")

    def copy_local_address():
        try:
            pyperclip.copy(f"127.0.0.1:{get_port_val()}")
        except Exception as exc:
            log.warning("Copy failed: %s", exc)

    def copy_network_address():
        if not is_network_available_here():
            return
        p = get_port_val()
        net_h = _get_lan_ip()
        try:
            pyperclip.copy(f"{net_h}:{p}")
        except Exception as exc:
            log.warning("Copy failed: %s", exc)

    def toggle_advanced():
        use_advanced = advanced_host_var.get()
        host_entry.configure(
            state="normal" if use_advanced else "disabled",
            fg_color=FIELD_BG if use_advanced else DISABLED_BG,
            text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT,
        )
        port_entry.configure(
            state="normal" if use_advanced else "disabled",
            fg_color=FIELD_BG if use_advanced else DISABLED_BG,
            text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT,
        )
        dc_textbox.configure(
            state="normal" if use_advanced else "disabled",
            fg_color=FIELD_BG if use_advanced else DISABLED_BG,
            text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT,
        )
        host_label.configure(text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT)
        port_label.configure(text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT)
        dc_label.configure(text_color=TEXT_PRIMARY if use_advanced else DISABLED_TEXT)
        cursor = "xterm" if use_advanced else "arrow"
        _set_widget_cursor(host_entry, cursor)
        _set_widget_cursor(port_entry, cursor)
        _set_widget_cursor(dc_textbox, cursor)
        update_connection_label()

    conn_block = ctk.CTkFrame(frame, fg_color="transparent")
    conn_block.pack(anchor="w", pady=(12, 12), fill="x")
    conn_row1 = ctk.CTkFrame(conn_block, fg_color="transparent")
    conn_row1.pack(anchor="w", fill="x", pady=(0, 2))
    conn_label_local = ctk.CTkLabel(
        conn_row1, text="", font=(FONT_FAMILY, 12, "bold"),
        text_color=TG_BLUE, anchor="w",
    )
    conn_label_local.pack(side="left")
    ctk.CTkButton(
        conn_row1, text="Копировать", width=44, height=24,
        font=(FONT_FAMILY, 11), corner_radius=6,
        fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
        text_color="#ffffff",
        command=copy_local_address,
    ).pack(side="left", padx=(8, 0))
    conn_row2 = ctk.CTkFrame(conn_block, fg_color="transparent")
    conn_row2.pack(anchor="w", fill="x", pady=(2, 0))
    conn_label_network = ctk.CTkLabel(
        conn_row2, text="", font=(FONT_FAMILY, 12, "bold"),
        text_color=TG_BLUE, anchor="w",
    )
    conn_label_network.pack(side="left")
    copy_btn_network = ctk.CTkButton(
        conn_row2, text="Копировать", width=44, height=24,
        font=(FONT_FAMILY, 11), corner_radius=6,
        fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
        text_color="#ffffff",
        command=copy_network_address,
    )
    copy_btn_network.pack(side="left", padx=(8, 0))
    update_connection_label()

    ctk.CTkCheckBox(
        frame, text="Расширенные настройки (IP, порт, маппинг DC)",
        variable=advanced_host_var, font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
        corner_radius=6, border_width=2,
        border_color=FIELD_BORDER,
    ).pack(anchor="w", pady=(4, 10))
    advanced_host_var.trace_add("write", lambda *a: toggle_advanced())

    host_label = ctk.CTkLabel(
        frame, text="IP-адрес прокси",
        font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
        anchor="w",
    )
    host_label.pack(anchor="w", pady=(0, 4))
    host_entry = ctk.CTkEntry(frame, textvariable=host_var, width=200, height=36,
                              font=(FONT_FAMILY, 13), corner_radius=10,
                              fg_color=FIELD_BG, border_color=FIELD_BORDER,
                              border_width=1, text_color=TEXT_PRIMARY)
    host_entry.pack(anchor="w", pady=(0, 12))
    host_var.trace_add("write", lambda *a: update_connection_label())
    listen_all_var.trace_add("write", lambda *a: update_connection_label())

    port_label = ctk.CTkLabel(
        frame, text="Порт прокси",
        font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
        anchor="w",
    )
    port_label.pack(anchor="w", pady=(0, 4))
    port_entry = ctk.CTkEntry(frame, textvariable=port_var, width=120, height=36,
                              font=(FONT_FAMILY, 13), corner_radius=10,
                              fg_color=FIELD_BG, border_color=FIELD_BORDER,
                              border_width=1, text_color=TEXT_PRIMARY)
    port_entry.pack(anchor="w", pady=(0, 12))
    port_var.trace_add("write", lambda *a: update_connection_label())

    network_hint = ctk.CTkLabel(
        frame,
        text="В режиме «Вся локальная сеть» прокси слушает на всех интерфейсах. "
             "На других устройствах в настройках прокси Telegram укажите адрес выше. "
             "Убедитесь, что брандмауэр Windows разрешает входящие подключения на выбранный порт.",
        font=(FONT_FAMILY, 11), text_color=TEXT_SECONDARY,
        anchor="w", justify="left", wraplength=370,
    )
    network_hint.pack(anchor="w", pady=(0, 12))

    # DC-IP mappings
    dc_label = ctk.CTkLabel(
        frame, text="DC → IP маппинги (по одному на строку, формат DC:IP)",
        font=(FONT_FAMILY, 13), text_color=TEXT_PRIMARY,
        anchor="w",
    )
    dc_label.pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(frame, width=370, height=120,
                                font=("Consolas", 12), corner_radius=10,
                                fg_color=FIELD_BG, border_color=FIELD_BORDER,
                                border_width=1, text_color=TEXT_PRIMARY)
    dc_textbox.pack(anchor="w", pady=(0, 12))
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])))

    if not advanced_host_var.get():
        host_entry.configure(
            state="disabled", fg_color=DISABLED_BG, text_color=DISABLED_TEXT,
        )
        port_entry.configure(
            state="disabled", fg_color=DISABLED_BG, text_color=DISABLED_TEXT,
        )
        dc_textbox.configure(
            state="disabled", fg_color=DISABLED_BG, text_color=DISABLED_TEXT,
        )
        host_label.configure(text_color=DISABLED_TEXT)
        port_label.configure(text_color=DISABLED_TEXT)
        dc_label.configure(text_color=DISABLED_TEXT)
        _set_widget_cursor(host_entry, "arrow")
        _set_widget_cursor(port_entry, "arrow")
        _set_widget_cursor(dc_textbox, "arrow")
    else:
        _set_widget_cursor(host_entry, "xterm")
        _set_widget_cursor(port_entry, "xterm")
        _set_widget_cursor(dc_textbox, "xterm")

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
        advanced_host_val = advanced_host_var.get()
        listen_all_val = listen_all_var.get()
        host_val = host_var.get().strip() or "127.0.0.1"
        if advanced_host_val:
            try:
                _socket.inet_aton(host_val)
            except OSError:
                _show_error("Некорректный IP-адрес.")
                return
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
            dc_ip_val = lines
            port_val_final = port_val
        else:
            port_val_final = DEFAULT_CONFIG["port"]
            dc_ip_val = DEFAULT_CONFIG["dc_ip"]

        new_cfg = {
            "host": host_val,
            "port": port_val_final,
            "listen_all": listen_all_val,
            "advanced_host": advanced_host_val,
            "dc_ip": dc_ip_val,
            "verbose": verbose_var.get(),
        }
        save_config(new_cfg)
        _config.update(new_cfg)
        log.info("Config saved: %s", new_cfg)

        _tray_icon.menu = _build_menu()

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


def _on_open_logs(icon=None, item=None):
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        os.startfile(str(LOG_FILE))
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

    local_host, port = get_local_connection_address(_config)
    net_available = is_network_available(_config)
    net_host, _ = get_network_connection_address(_config)
    tg_url = f"tg://socks?server={local_host}&port={port}"

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

    w, h = 520, 520
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

    ctk.CTkLabel(frame, text=f"На этом устройстве:  {local_host}:{port}",
                 font=(FONT_FAMILY, 15, "bold"), text_color=TG_BLUE,
                 anchor="w").pack(anchor="w", pady=(0, 4))
    if net_available:
        net_text = f"В локальной сети (другие устройства):  {net_host}:{port}"
        net_color = TG_BLUE
    else:
        net_text = "В локальной сети: недоступно"
        net_color = TEXT_SECONDARY
    ctk.CTkLabel(frame, text=net_text,
                 font=(FONT_FAMILY, 14, "bold"), text_color=net_color,
                 anchor="w").pack(anchor="w", pady=(0, 12))

    if net_available:
        ctk.CTkLabel(frame, text="С других устройств в настройках прокси Telegram укажите адрес «В локальной сети».",
                     font=(FONT_FAMILY, 12), text_color=TEXT_SECONDARY,
                     anchor="w", justify="left", wraplength=460).pack(anchor="w", pady=(0, 12))

    # Info sections
    sections = [
        ("Как подключить Telegram Desktop:", True),
        ("  Автоматически:", True),
        (f"  ПКМ по иконке в трее → «Открыть в Telegram»", False),
        (f"  Или ссылка: {tg_url}", False),
        ("\n  Вручную:", True),
        ("  Настройки → Продвинутые → Тип подключения → Прокси", False),
        (f"  На этом устройстве: SOCKS5 → {local_host} : {port} (без логина/пароля)", False),
        (f"  В сети: SOCKS5 → {net_host} : {port}" if net_available else "  В сети: недоступно", False),
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


def _on_copy_local_address():
    host, port = get_local_connection_address(_config)
    try:
        pyperclip.copy(f"{host}:{port}")
    except Exception as exc:
        log.warning("Copy failed: %s", exc)


def _on_copy_network_address():
    if not is_network_available(_config):
        return
    host, port = get_network_connection_address(_config)
    try:
        pyperclip.copy(f"{host}:{port}")
    except Exception as exc:
        log.warning("Copy failed: %s", exc)


def _build_menu():
    if pystray is None:
        return None
    local_host, port = get_local_connection_address(_config)
    net_available = is_network_available(_config)
    net_host, _ = get_network_connection_address(_config)
    if net_available:
        net_menu_text = f"Копировать адрес (локальная сеть) — {net_host}:{port}"
        net_menu_callback = _on_copy_network_address
    else:
        net_menu_text = "Копировать адрес (локальная сеть) — недоступно"
        net_menu_callback = lambda *a: None  # не копируем при недоступности
    return pystray.Menu(
        pystray.MenuItem(
            f"Открыть в Telegram ({local_host}:{port})",
            _on_open_in_telegram,
            default=True),
        pystray.MenuItem(
            f"Копировать адрес (этот компьютер) — {local_host}:{port}",
            _on_copy_local_address,
        ),
        pystray.MenuItem(net_menu_text, net_menu_callback),
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
    if not _acquire_lock():
        _show_info("Приложение уже запущено.", os.path.basename(sys.argv[0]))
        return

    try:
        run_tray()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
