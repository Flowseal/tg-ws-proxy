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

# ── bootstrap: ensure proxy core exists, then import ───────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import updater

_proxy_dir  = Path(__file__).parent / 'proxy'
_proxy_core = _proxy_dir / 'tg_ws_proxy.py'
_proxy_init = _proxy_dir / '__init__.py'

if not _proxy_core.exists():
    print('proxy/tg_ws_proxy.py not found — downloading from GitHub...')
    _proxy_dir.mkdir(exist_ok=True)
    _proxy_init.touch()
    downloaded = updater.check_and_update()
    if not downloaded and not _proxy_core.exists():
        print('ERROR: could not download proxy core. Check internet connection.')
        sys.exit(1)

if not _proxy_init.exists():
    _proxy_init.touch()

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


# ── settings window (rumps — runs on main thread) ────────────────────────────

def open_settings(_sender=None):
    """
    Settings dialog using rumps.Window (native macOS, main-thread safe).
    Each field is edited in a separate window because rumps.Window has
    one text input. We collect all fields sequentially.
    """
    host = _config.get("host", "127.0.0.1")
    port = _config.get("port", 1080)
    dc_ip = _config.get("dc_ip", [])
    verbose = _config.get("verbose", False)

    # ── Host ──
    w = rumps.Window(
        message="Введите host (например 127.0.0.1):",
        title="Настройки — Host",
        default_text=host,
        ok="Далее",
        cancel="Отмена",
        dimensions=(320, 24),
    )
    resp = w.run()
    if not resp.clicked:
        return
    new_host = resp.text.strip() or host

    # ── Port ──
    w = rumps.Window(
        message="Введите порт (например 1080):",
        title="Настройки — Port",
        default_text=str(port),
        ok="Далее",
        cancel="Отмена",
        dimensions=(320, 24),
    )
    resp = w.run()
    if not resp.clicked:
        return
    try:
        new_port = int(resp.text.strip())
    except ValueError:
        rumps.alert(title="Ошибка", message="Порт должен быть числом.")
        return

    # ── DC IPs ──
    w = rumps.Window(
        message="DC IPs через запятую (например: 2:149.154.167.220, 4:149.154.167.220):",
        title="Настройки — DC IPs",
        default_text=", ".join(dc_ip),
        ok="Далее",
        cancel="Отмена",
        dimensions=(400, 24),
    )
    resp = w.run()
    if not resp.clicked:
        return
    new_dc_ip = [x.strip() for x in resp.text.split(",") if x.strip()]
    try:
        tg_ws_proxy.parse_dc_ip_list(new_dc_ip)
    except ValueError as exc:
        rumps.alert(title="Ошибка DC IPs", message=str(exc))
        return

    # ── Verbose ──
    w = rumps.Window(
        message="Verbose logging? Введите 'да' или 'нет':",
        title="Настройки — Verbose",
        default_text="да" if verbose else "нет",
        ok="Сохранить",
        cancel="Отмена",
        dimensions=(320, 24),
    )
    resp = w.run()
    if not resp.clicked:
        return
    new_verbose = resp.text.strip().lower() in ("да", "yes", "1", "true")

    new_cfg = {
        "host":    new_host,
        "port":    new_port,
        "dc_ip":   new_dc_ip,
        "verbose": new_verbose,
    }
    save_config(new_cfg)
    _config.update(new_cfg)
    log.info("Config saved: %s", new_cfg)

    resp2 = rumps.alert(
        title="Настройки сохранены",
        message="Перезапустить прокси сейчас?",
        ok="Перезапустить",
        cancel="Позже",
    )
    if resp2 == 1:
        threading.Thread(target=restart_proxy, daemon=True).start()


# ── first-run dialog ─────────────────────────────────────────────────────────

def show_first_run():
    host = _config.get("host", "127.0.0.1")
    port = _config.get("port", 1080)
    rumps.alert(
        title="TG WS Proxy — Первый запуск",
        message=(
            "Прокси запущен!\n\n"
            "Чтобы подключить Telegram Desktop:\n\n"
            "1. Telegram → Настройки → Продвинутые\n"
            "   → Тип подключения → Прокси\n\n"
            "2. Добавьте SOCKS5:\n"
            f"   Сервер: {host}   Порт: {port}\n"
            "   Логин/Пароль: пусто\n\n"
            "Или нажмите «Открыть в Telegram» в строке меню."
        ),
        ok="Понятно",
    )
    FIRST_RUN_MARKER.touch()


# ── rumps app ────────────────────────────────────────────────────────────────

def _load_icon() -> Optional[str]:
    """
    Return path to icon PNG.
    Works both in dev (next to macos.py) and inside a PyInstaller .app bundle
    where datas land in sys._MEIPASS.
    """
    import sys as _sys
    bases = [Path(__file__).parent]
    if hasattr(_sys, '_MEIPASS'):
        bases.insert(0, Path(_sys._MEIPASS))
    for base in bases:
        for name in ("icon_tray.png", "icon.png"):
            p = base / name
            if p.exists():
                return str(p)
    return None


class TgWsProxyApp(rumps.App):
    def __init__(self):
        icon_path = _load_icon()
        super().__init__(
            name=APP_NAME,
            title=None if icon_path else "TG",
            icon=icon_path,
            template=False,   # monochrome template: respects Dark/Light mode
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
        open_settings()

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

    # ── Auto-update proxy core at startup ────────────────────────────────
    def _do_update():
        updated = updater.check_and_update()
        if updated:
            log.info("Proxy core updated — reloading and restarting proxy")
            import importlib
            global tg_ws_proxy
            try:
                import proxy.tg_ws_proxy as _fresh
                importlib.reload(_fresh)
                tg_ws_proxy = _fresh
            except Exception as exc:
                log.error("Failed to reload proxy core after update: %s", exc)
            restart_proxy()
            rumps.notification(
                APP_NAME,
                "Обновление установлено",
                "Proxy core обновлён и перезапущен.",
                sound=False,
            )
        else:
            start_proxy(_config)

    threading.Thread(target=_do_update, daemon=True).start()
    # ─────────────────────────────────────────────────────────────────────

    if not FIRST_RUN_MARKER.exists():
        # Delay slightly so rumps app loop is ready before showing alert
        def _first_run_timer(sender):
            sender.stop()
            show_first_run()
        t = rumps.Timer(_first_run_timer, 1)
        t.start()

    app = TgWsProxyApp()
    app.run()


if __name__ == "__main__":
    main()