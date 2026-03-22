from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Optional

import customtkinter as ctk
import psutil
import pyperclip
import pystray
from PIL import Image, ImageDraw, ImageFont

import proxy.tg_ws_proxy as tg_ws_proxy

APP_NAME = "TgWsProxy"
APP_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done"
IPV6_WARN_MARKER = APP_DIR / ".ipv6_warned"


DEFAULT_CONFIG = {
    "port": 1080,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "log_max_mb": 5,
    "buf_kb": 256,
    "pool_size": 4,
}


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

    try:
        cmdline = proc.cmdline()
        for arg in cmdline:
            if "linux.py" in arg:
                return True
    except Exception:
        pass

    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        return APP_NAME.lower() in proc.name().lower()

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
        lock_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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


def setup_logging(verbose: bool = False, log_max_mb: float = 5):
    _ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)

    fh = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=max(32 * 1024, log_max_mb * 1024 * 1024),
        backupCount=0,
        encoding='utf-8',
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(fh)

    if not getattr(sys, "frozen", False):
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        ch.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-5s  %(message)s", datefmt="%H:%M:%S"
            )
        )
        root.addHandler(ch)


def _make_icon_image(size: int = 64):
    if Image is None:
        raise RuntimeError("Pillow is required for tray icon")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin], fill=(0, 136, 204, 255)
    )

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            size=int(size * 0.55),
        )
    except Exception:
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf", size=int(size * 0.55)
            )
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


def _run_proxy_thread(
    port: int, dc_opt: Dict[int, str], verbose: bool, host: str = "127.0.0.1"
):
    global _async_stop
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    stop_ev = _asyncio.Event()
    _async_stop = (loop, stop_ev)

    try:
        loop.run_until_complete(
            tg_ws_proxy._run(port, dc_opt, stop_event=stop_ev, host=host)
        )
    except Exception as exc:
        log.error("Proxy thread crashed: %s", exc)
        if "Address already in use" in str(exc):
            _show_error(
                "Не удалось запустить прокси:\nПорт уже используется другим приложением.\n\nЗакройте приложение, использующее этот порт, или измените порт в настройках прокси и перезапустите."
            )
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
    host = cfg.get("host", DEFAULT_CONFIG["host"])
    dc_ip_list = cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])
    verbose = cfg.get("verbose", False)

    try:
        dc_opt = tg_ws_proxy.parse_dc_ip_list(dc_ip_list)
    except ValueError as e:
        log.error("Bad config dc_ip: %s", e)
        _show_error(f"Ошибка конфигурации:\n{e}")
        return

    log.info("Starting proxy on %s:%d ...", host, port)

    buf_kb = cfg.get("buf_kb", DEFAULT_CONFIG["buf_kb"])
    pool_size = cfg.get("pool_size", DEFAULT_CONFIG["pool_size"])
    tg_ws_proxy._RECV_BUF = max(4, buf_kb) * 1024
    tg_ws_proxy._SEND_BUF = tg_ws_proxy._RECV_BUF
    tg_ws_proxy._WS_POOL_SIZE = max(0, pool_size)

    _proxy_thread = threading.Thread(
        target=_run_proxy_thread,
        args=(port, dc_opt, verbose, host),
        daemon=True,
        name="proxy",
    )
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
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    _mb.showerror(title, text, parent=root)
    root.destroy()


def _show_info(text: str, title: str = "TG WS Proxy"):
    import tkinter as _tk
    from tkinter import messagebox as _mb

    root = _tk.Tk()
    root.withdraw()
    _mb.showinfo(title, text, parent=root)
    root.destroy()


def _on_open_in_telegram(icon=None, item=None):
    port = _config.get("port", DEFAULT_CONFIG["port"])
    url = f"tg://socks?server=127.0.0.1&port={port}"
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

    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    root = ctk.CTk()
    root.title("TG WS Proxy — Настройки")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    icon_img = _load_icon()
    if icon_img:
        from PIL import ImageTk

        _photo = ImageTk.PhotoImage(icon_img.resize((64, 64)))
        root.iconphoto(False, _photo)

    TG_BLUE = "#3390ec"
    TG_BLUE_HOVER = "#2b7cd4"
    BG = "#ffffff"
    FIELD_BG = "#f0f2f5"
    FIELD_BORDER = "#d6d9dc"
    TEXT_PRIMARY = "#000000"
    TEXT_SECONDARY = "#707579"
    FONT_FAMILY = "Sans"

    w, h = 420, 540
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.configure(fg_color=BG)

    frame = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=24, pady=20)

    # Host
    ctk.CTkLabel(
        frame,
        text="IP-адрес прокси",
        font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    host_var = ctk.StringVar(value=cfg.get("host", "127.0.0.1"))
    host_entry = ctk.CTkEntry(
        frame,
        textvariable=host_var,
        width=200,
        height=36,
        font=(FONT_FAMILY, 13),
        corner_radius=10,
        fg_color=FIELD_BG,
        border_color=FIELD_BORDER,
        border_width=1,
        text_color=TEXT_PRIMARY,
    )
    host_entry.pack(anchor="w", pady=(0, 12))

    # Port
    ctk.CTkLabel(
        frame,
        text="Порт прокси",
        font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    port_var = ctk.StringVar(value=str(cfg.get("port", 1080)))
    port_entry = ctk.CTkEntry(
        frame,
        textvariable=port_var,
        width=120,
        height=36,
        font=(FONT_FAMILY, 13),
        corner_radius=10,
        fg_color=FIELD_BG,
        border_color=FIELD_BORDER,
        border_width=1,
        text_color=TEXT_PRIMARY,
    )
    port_entry.pack(anchor="w", pady=(0, 12))

    # DC-IP mappings
    ctk.CTkLabel(
        frame,
        text="DC → IP маппинги (по одному на строку, формат DC:IP)",
        font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        anchor="w",
    ).pack(anchor="w", pady=(0, 4))
    dc_textbox = ctk.CTkTextbox(
        frame,
        width=370,
        height=120,
        font=("Monospace", 12),
        corner_radius=10,
        fg_color=FIELD_BG,
        border_color=FIELD_BORDER,
        border_width=1,
        text_color=TEXT_PRIMARY,
    )
    dc_textbox.pack(anchor="w", pady=(0, 12))
    dc_textbox.insert("1.0", "\n".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])))

    # Verbose
    verbose_var = ctk.BooleanVar(value=cfg.get("verbose", False))
    ctk.CTkCheckBox(
        frame,
        text="Подробное логирование (verbose)",
        variable=verbose_var,
        font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        fg_color=TG_BLUE,
        hover_color=TG_BLUE_HOVER,
        corner_radius=6,
        border_width=2,
        border_color=FIELD_BORDER,
    ).pack(anchor="w", pady=(0, 8))

    # Advanced: buf_kb, pool_size, log_max_mb
    adv_frame = ctk.CTkFrame(frame, fg_color="transparent")
    adv_frame.pack(anchor="w", fill="x", pady=(4, 8))

    for col, (lbl, key, w_) in enumerate([
        ("Буфер (KB, 256 default)", "buf_kb", 120),
        ("WS пулов (4 default)", "pool_size", 120),
        ("Log size (MB, 5 def)", "log_max_mb", 120),
    ]):
        col_frame = ctk.CTkFrame(adv_frame, fg_color="transparent")
        col_frame.pack(side="left", padx=(0, 10))
        ctk.CTkLabel(col_frame, text=lbl, font=(FONT_FAMILY, 11),
                     text_color=TEXT_SECONDARY, anchor="w").pack(anchor="w")
        ctk.CTkEntry(col_frame, width=w_, height=30, font=(FONT_FAMILY, 12),
                     corner_radius=8, fg_color=FIELD_BG,
                     border_color=FIELD_BORDER, border_width=1,
                     text_color=TEXT_PRIMARY,
                     textvariable=ctk.StringVar(
                         value=str(cfg.get(key, DEFAULT_CONFIG[key]))
                     )).pack(anchor="w")

    _adv_entries = list(adv_frame.winfo_children())
    _adv_keys = ["buf_kb", "pool_size", "log_max_mb"]

    def on_save():
        import socket as _sock

        host_val = host_var.get().strip()
        try:
            _sock.inet_aton(host_val)
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

        lines = [
            l.strip()
            for l in dc_textbox.get("1.0", "end").strip().splitlines()
            if l.strip()
        ]
        try:
            tg_ws_proxy.parse_dc_ip_list(lines)
        except ValueError as e:
            _show_error(str(e))
            return

        new_cfg = {
            "host": host_val,
            "port": port_val,
            "dc_ip": lines,
            "verbose": verbose_var.get(),
        }

        for i, key in enumerate(_adv_keys):
            col_frame = _adv_entries[i]
            entry = col_frame.winfo_children()[1]
            try:
                val = float(entry.get().strip())
                if key in ("buf_kb", "pool_size"):
                    val = int(val)
                new_cfg[key] = val
            except ValueError:
                new_cfg[key] = DEFAULT_CONFIG[key]
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

    btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
    btn_frame.pack(fill="x", pady=(20, 0))
    ctk.CTkButton(btn_frame, text="Сохранить", height=38,
                  font=(FONT_FAMILY, 14, "bold"), corner_radius=10,
                  fg_color=TG_BLUE, hover_color=TG_BLUE_HOVER,
                  text_color="#ffffff",
                  command=on_save).pack(side="left", fill="x", expand=True, padx=(0, 8))
    ctk.CTkButton(btn_frame, text="Отмена", height=38,
                  font=(FONT_FAMILY, 14), corner_radius=10,
                  fg_color=FIELD_BG, hover_color=FIELD_BORDER,
                  text_color=TEXT_PRIMARY, border_width=1,
                  border_color=FIELD_BORDER,
                  command=on_cancel).pack(side="right", fill="x", expand=True)

    root.mainloop()


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
    _ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    tg_url = f"tg://socks?server={host}&port={port}"

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
    FONT_FAMILY = "Sans"

    root = ctk.CTk()
    root.title("TG WS Proxy")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    icon_img = _load_icon()
    if icon_img:
        from PIL import ImageTk

        _photo = ImageTk.PhotoImage(icon_img.resize((64, 64)))
        root.iconphoto(False, _photo)

    w, h = 520, 440
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
    root.configure(fg_color=BG)

    frame = ctk.CTkFrame(root, fg_color=BG, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=28, pady=24)

    title_frame = ctk.CTkFrame(frame, fg_color="transparent")
    title_frame.pack(anchor="w", pady=(0, 16), fill="x")

    # Blue accent bar
    accent_bar = ctk.CTkFrame(
        title_frame, fg_color=TG_BLUE, width=4, height=32, corner_radius=2
    )
    accent_bar.pack(side="left", padx=(0, 12))

    ctk.CTkLabel(
        title_frame,
        text="Прокси запущен и работает в системном трее",
        font=(FONT_FAMILY, 17, "bold"),
        text_color=TEXT_PRIMARY,
    ).pack(side="left")

    # Info sections
    sections = [
        ("Как подключить Telegram Desktop:", True),
        ("  Автоматически:", True),
        (f"  ПКМ по иконке в трее → «Открыть в Telegram»", False),
        (f"  Или ссылка: {tg_url}", False),
        ("\n  Вручную:", True),
        ("  Настройки → Продвинутые → Тип подключения → Прокси", False),
        (f"  SOCKS5 → {host} : {port} (без логина/пароля)", False),
    ]

    for text, bold in sections:
        weight = "bold" if bold else "normal"
        ctk.CTkLabel(
            frame,
            text=text,
            font=(FONT_FAMILY, 13, weight),
            text_color=TEXT_PRIMARY,
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=1)

    # Spacer
    ctk.CTkFrame(frame, fg_color="transparent", height=16).pack()

    # Separator
    ctk.CTkFrame(frame, fg_color=FIELD_BORDER, height=1, corner_radius=0).pack(
        fill="x", pady=(0, 12)
    )

    # Checkbox
    auto_var = ctk.BooleanVar(value=True)
    ctk.CTkCheckBox(
        frame,
        text="Открыть прокси в Telegram сейчас",
        variable=auto_var,
        font=(FONT_FAMILY, 13),
        text_color=TEXT_PRIMARY,
        fg_color=TG_BLUE,
        hover_color=TG_BLUE_HOVER,
        corner_radius=6,
        border_width=2,
        border_color=FIELD_BORDER,
    ).pack(anchor="w", pady=(0, 16))

    def on_ok():
        FIRST_RUN_MARKER.touch()
        open_tg = auto_var.get()
        root.destroy()
        if open_tg:
            _on_open_in_telegram()

    ctk.CTkButton(
        frame,
        text="Начать",
        width=180,
        height=42,
        font=(FONT_FAMILY, 15, "bold"),
        corner_radius=10,
        fg_color=TG_BLUE,
        hover_color=TG_BLUE_HOVER,
        text_color="#ffffff",
        command=on_ok,
    ).pack(pady=(0, 0))

    root.protocol("WM_DELETE_WINDOW", on_ok)
    root.mainloop()


def _has_ipv6_enabled() -> bool:
    import socket as _sock

    try:
        addrs = _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET6)
        for addr in addrs:
            ip = addr[4][0]
            if ip and not ip.startswith("::1") and not ip.startswith("fe80::1"):
                return True
    except Exception:
        pass
    try:
        s = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except Exception:
        return False


def _check_ipv6_warning():
    _ensure_dirs()
    if IPV6_WARN_MARKER.exists():
        return
    if not _has_ipv6_enabled():
        return

    IPV6_WARN_MARKER.touch()

    threading.Thread(target=_show_ipv6_dialog, daemon=True).start()


def _show_ipv6_dialog():
    _show_info(
        "На вашем компьютере включена поддержка подключения по IPv6.\n\n"
        "Telegram может пытаться подключаться через IPv6, "
        "что не поддерживается и может привести к ошибкам.\n\n"
        "Если прокси не работает или в логах присутствуют ошибки, "
        "связанные с попытками подключения по IPv6 - "
        "попробуйте отключить в настройках прокси Telegram попытку соединения "
        "по IPv6. Если данная мера не помогает, попробуйте отключить IPv6 "
        "в системе.\n\n"
        "Это предупреждение будет показано только один раз.",
        "TG WS Proxy",
    )


def _build_menu():
    if pystray is None:
        return None
    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    return pystray.Menu(
        pystray.MenuItem(
            f"Открыть в Telegram ({host}:{port})", _on_open_in_telegram, default=True
        ),
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

    setup_logging(_config.get("verbose", False),
                  log_max_mb=_config.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"]))
    log.info("TG WS Proxy tray app starting")
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

    _show_first_run()
    _check_ipv6_warning()

    icon_image = _load_icon()
    _tray_icon = pystray.Icon(APP_NAME, icon_image, "TG WS Proxy", menu=_build_menu())

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
