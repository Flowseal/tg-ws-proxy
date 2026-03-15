"""
TG WS Proxy — macOS menu bar application.
Requires: pip install rumps cryptography psutil
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

import psutil
import rumps

# ── proxy core is a sibling package ────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import proxy.tg_ws_proxy as tg_ws_proxy

# ── paths ───────────────────────────────────────────────────────────────────
APP_NAME   = "TgWsProxy"
APP_DIR    = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_FILE      = APP_DIR / "config.json"
LOG_FILE         = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done"

DEFAULT_CONFIG = {
    "port":    1080,
    "host":    "127.0.0.1",
    "dc_ip":   ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
}

# ── state ───────────────────────────────────────────────────────────────────
_proxy_thread: Optional[threading.Thread] = None
_stop_event:   Optional[object]           = None   # asyncio.Event, set from thread
_config: dict  = {}
_exiting: bool = False

log = logging.getLogger("tg-ws-tray")


# ── helpers ─────────────────────────────────────────────────────────────────

def _ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)


def _setup_logging():
    _ensure_dirs()
    fmt = "%(asctime)s %(levelname)-5s %(name)s %(message)s"
    logging.basicConfig(
        level=logging.DEBUG if _config.get("verbose") else logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _acquire_lock() -> bool:
    _ensure_dirs()
    lock_files = list(APP_DIR.glob("*.lock"))
    for f in lock_files:
        try:
            pid = int(f.stem)
            if psutil.pid_exists(pid):
                try:
                    psutil.Process(pid).status()
                    return False
                except psutil.NoSuchProcess:
                    pass
            f.unlink(missing_ok=True)
        except (ValueError, OSError):
            f.unlink(missing_ok=True)

    lock_path = APP_DIR / f"{os.getpid()}.lock"
    lock_path.touch()
    return True


def _release_lock():
    for f in APP_DIR.glob("*.lock"):
        try:
            if int(f.stem) == os.getpid():
                f.unlink(missing_ok=True)
        except (ValueError, OSError):
            pass


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as fh:
                cfg = json.load(fh)
            merged = {**DEFAULT_CONFIG, **cfg}
            return merged
        except Exception as exc:
            log.warning("Config load failed: %s — using defaults", exc)
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    _ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ── proxy lifecycle ──────────────────────────────────────────────────────────

def _run_proxy(cfg: dict):
    """Runs in a daemon thread. Blocks until the proxy stops."""
    import asyncio

    stop = asyncio.Event()

    global _stop_event
    _stop_event = stop

    dc_opt = tg_ws_proxy.parse_dc_ip_list(cfg["dc_ip"])
    log.info("Starting proxy on %s:%d", cfg["host"], cfg["port"])

    try:
        tg_ws_proxy.run_proxy(
            port=cfg["port"],
            dc_opt=dc_opt,
            stop_event=stop,
            host=cfg["host"],
        )
    except Exception as exc:
        log.error("Proxy crashed: %s", exc)
    finally:
        log.info("Proxy thread exited")


def start_proxy(cfg: dict):
    global _proxy_thread, _stop_event
    _stop_event = None
    t = threading.Thread(target=_run_proxy, args=(cfg,), daemon=True)
    t.start()
    _proxy_thread = t
    log.info("Proxy thread started (port=%d)", cfg["port"])


def stop_proxy():
    global _stop_event, _proxy_thread
    if _stop_event is not None:
        try:
            # _stop_event is an asyncio.Event living in the proxy thread's loop
            # We signal it thread-safely via call_soon_threadsafe on its loop
            import asyncio
            loop = getattr(_stop_event, "_loop", None)
            if loop and not loop.is_closed():
                loop.call_soon_threadsafe(_stop_event.set)
            else:
                _stop_event.set()
        except Exception:
            pass
        _stop_event = None

    if _proxy_thread and _proxy_thread.is_alive():
        _proxy_thread.join(timeout=5)
    _proxy_thread = None
    log.info("Proxy stopped")


def restart_proxy():
    stop_proxy()
    time.sleep(0.5)
    start_proxy(_config)


# ── settings window (tkinter) ────────────────────────────────────────────────

def open_settings():
    """Show a settings dialog using tkinter (no extra deps needed on macOS)."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("TG WS Proxy — Настройки")
    root.resizable(False, False)

    pad = {"padx": 10, "pady": 5}

    # Host
    tk.Label(root, text="Host:").grid(row=0, column=0, sticky="e", **pad)
    host_var = tk.StringVar(value=_config.get("host", "127.0.0.1"))
    tk.Entry(root, textvariable=host_var, width=20).grid(row=0, column=1, sticky="w", **pad)

    # Port
    tk.Label(root, text="Port:").grid(row=1, column=0, sticky="e", **pad)
    port_var = tk.StringVar(value=str(_config.get("port", 1080)))
    tk.Entry(root, textvariable=port_var, width=10).grid(row=1, column=1, sticky="w", **pad)

    # DC IPs
    tk.Label(root, text="DC IPs\n(DC:IP, по одному\nна строку):").grid(
        row=2, column=0, sticky="ne", **pad)
    dc_text = tk.Text(root, width=30, height=6)
    dc_text.grid(row=2, column=1, sticky="w", **pad)
    dc_text.insert("1.0", "\n".join(_config.get("dc_ip", [])))

    # Verbose
    verbose_var = tk.BooleanVar(value=_config.get("verbose", False))
    tk.Checkbutton(root, text="Verbose logging", variable=verbose_var).grid(
        row=3, column=1, sticky="w", **pad)

    def on_save():
        try:
            host_val = host_var.get().strip()
            port_val  = int(port_var.get().strip())
        except ValueError:
            messagebox.showerror("Ошибка", "Порт должен быть числом", parent=root)
            return

        lines = [l.strip() for l in dc_text.get("1.0", "end").splitlines() if l.strip()]
        try:
            tg_ws_proxy.parse_dc_ip_list(lines)
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=root)
            return

        new_cfg = {
            "host":    host_val,
            "port":    port_val,
            "dc_ip":   lines,
            "verbose": verbose_var.get(),
        }
        save_config(new_cfg)
        _config.update(new_cfg)
        log.info("Config saved: %s", new_cfg)

        if messagebox.askyesno(
            "Перезапустить?",
            "Настройки сохранены.\n\nПерезапустить прокси сейчас?",
            parent=root,
        ):
            root.destroy()
            restart_proxy()
        else:
            root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
    ttk.Button(btn_frame, text="Сохранить", command=on_save).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Отмена", command=root.destroy).pack(side="left", padx=5)

    root.mainloop()


# ── first-run dialog ─────────────────────────────────────────────────────────

def show_first_run():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("TG WS Proxy — Первый запуск")
    root.resizable(False, False)

    msg = (
        "TG WS Proxy запущен!\n\n"
        "Чтобы подключить Telegram Desktop:\n\n"
        "1. Откройте Telegram → Настройки\n"
        "   → Продвинутые → Тип подключения → Прокси\n\n"
        "2. Добавьте прокси:\n"
        f"   Тип: SOCKS5\n"
        f"   Сервер: {_config['host']}\n"
        f"   Порт: {_config['port']}\n"
        "   Логин/Пароль: пусто\n\n"
        "Или нажмите «Открыть в Telegram» в меню строки меню."
    )

    tk.Label(root, text=msg, justify="left", padx=20, pady=10).pack()
    ttk.Button(root, text="Понятно", command=root.destroy).pack(pady=10)
    root.mainloop()

    FIRST_RUN_MARKER.touch()


# ── rumps app ────────────────────────────────────────────────────────────────

class TgWsProxyApp(rumps.App):
    def __init__(self):
        super().__init__(
            name=APP_NAME,
            title="🔵",   # menu bar icon (emoji fallback)
            quit_button=None,
        )
        self.menu = self._build_menu()

    def _build_menu(self):
        host = _config.get("host", "127.0.0.1")
        port = _config.get("port", 1080)
        dc_list = ", ".join(
            f"DC{e.split(':')[0]}" for e in _config.get("dc_ip", [])
        )
        items = [
            rumps.MenuItem("Открыть в Telegram", callback=self.open_in_telegram),
            rumps.separator,
            rumps.MenuItem(f"Прокси: {host}:{port}  [{dc_list}]"),
            rumps.MenuItem("Перезапустить прокси", callback=self.restart),
            rumps.separator,
            rumps.MenuItem("Настройки…", callback=self.settings),
            rumps.MenuItem("Открыть логи", callback=self.open_logs),
            rumps.separator,
            rumps.MenuItem("Выход", callback=self.quit_app),
        ]
        return items

    # ── callbacks ────────────────────────────────────────────────────────────

    def open_in_telegram(self, _sender):
        host = _config.get("host", "127.0.0.1")
        port = _config.get("port", 1080)
        url  = f"tg://socks?server={host}&port={port}"
        webbrowser.open(url)
        log.info("Opened telegram socks link: %s", url)

    def restart(self, _sender):
        log.info("Restart requested from tray")
        threading.Thread(target=restart_proxy, daemon=True).start()
        rumps.notification(
            APP_NAME, "Прокси перезапускается", "", sound=False
        )

    def settings(self, _sender):
        threading.Thread(target=open_settings, daemon=True).start()

    def open_logs(self, _sender):
        subprocess.Popen(["open", str(LOG_FILE)])

    def quit_app(self, _sender):
        global _exiting
        _exiting = True
        log.info("Quit requested")
        stop_proxy()
        _release_lock()
        rumps.quit_application()


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    global _config

    _ensure_dirs()
    _config = load_config()
    _setup_logging()

    log.info("TG WS Proxy tray app starting (macOS)")

    if not _acquire_lock():
        rumps.alert("TG WS Proxy уже запущен!")
        sys.exit(0)

    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    start_proxy(_config)

    if not FIRST_RUN_MARKER.exists():
        threading.Thread(target=show_first_run, daemon=True).start()

    app = TgWsProxyApp()
    app.run()


if __name__ == "__main__":
    main()
