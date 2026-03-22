from __future__ import annotations

import json
import logging
import logging.handlers
import os
import psutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

try:
    import rumps
except ImportError:
    rumps = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = ImageDraw = ImageFont = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

import proxy.tg_ws_proxy as tg_ws_proxy
from proxy.app_runtime import ProxyAppRuntime

APP_NAME = "TgWsProxy"
APP_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done"
IPV6_WARN_MARKER = APP_DIR / ".ipv6_warned"
MENUBAR_ICON_PATH = APP_DIR / "menubar_icon.png"

DEFAULT_CONFIG = {
    "port": 1080,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
    "log_max_mb": 5,
    "buf_kb": 256,
    "pool_size": 4,
}

_app: Optional[object] = None
_config: dict = {}
_exiting: bool = False
_lock_file_path: Optional[Path] = None

log = logging.getLogger("tg-ws-tray")
_runtime = ProxyAppRuntime(
    APP_DIR,
    default_config=DEFAULT_CONFIG,
    logger_name="tg-ws-tray",
    on_error=lambda text: _show_error(text),
)
CONFIG_FILE = _runtime.config_file
LOG_FILE = _runtime.log_file


# Single-instance lock

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
        payload = {"create_time": proc.create_time()}
        lock_file.write_text(json.dumps(payload, ensure_ascii=False),
                             encoding="utf-8")
    except Exception:
        lock_file.touch()

    _lock_file_path = lock_file
    return True


# Filesystem helpers

def _ensure_dirs():
    _runtime.ensure_dirs()


def load_config() -> dict:
    return _runtime.load_config()


def save_config(cfg: dict):
    _runtime.save_config(cfg)


def setup_logging(verbose: bool = False, log_max_mb: float = 5):
    _runtime.setup_logging(verbose, log_max_mb=log_max_mb)


# Menubar icon

def _make_menubar_icon(size: int = 44):
    if Image is None:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = size // 11
    draw.ellipse([margin, margin, size - margin, size - margin],
                 fill=(0, 0, 0, 255))

    try:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Helvetica.ttc",
            size=int(size * 0.55))
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "T", fill=(255, 255, 255, 255), font=font)
    return img

# Generate menubar icon PNG if it does not exist.
def _ensure_menubar_icon():
    if MENUBAR_ICON_PATH.exists():
        return
    _ensure_dirs()
    img = _make_menubar_icon(44)
    if img:
        img.save(str(MENUBAR_ICON_PATH), "PNG")


# Native macOS dialogs

def _osascript(script: str) -> str:
    r = subprocess.run(
        ['osascript', '-e', script],
        capture_output=True, text=True)
    return r.stdout.strip()


def _show_error(text: str, title: str = "TG WS Proxy"):
    text_esc = text.replace('\\', '\\\\').replace('"', '\\"')
    title_esc = title.replace('\\', '\\\\').replace('"', '\\"')
    _osascript(
        f'display dialog "{text_esc}" with title "{title_esc}" '
        f'buttons {{"OK"}} default button "OK" with icon stop')


def _show_info(text: str, title: str = "TG WS Proxy"):
    text_esc = text.replace('\\', '\\\\').replace('"', '\\"')
    title_esc = title.replace('\\', '\\\\').replace('"', '\\"')
    _osascript(
        f'display dialog "{text_esc}" with title "{title_esc}" '
        f'buttons {{"OK"}} default button "OK" with icon note')


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    text_esc = text.replace('\\', '\\\\').replace('"', '\\"')
    title_esc = title.replace('\\', '\\\\').replace('"', '\\"')
    result = _osascript(
        f'display dialog "{text_esc}" with title "{title_esc}" '
        f'buttons {{"Нет", "Да"}} default button "Да" with icon note')
    return "Да" in result


# Proxy lifecycle

def start_proxy():
    _runtime.start_proxy(_config)


def stop_proxy():
    _runtime.stop_proxy()


def restart_proxy():
    _runtime.restart_proxy()


# Menu callbacks

def _on_open_in_telegram(_=None):
    port = _config.get("port", DEFAULT_CONFIG["port"])
    url = f"tg://socks?server=127.0.0.1&port={port}"
    log.info("Opening %s", url)
    try:
        result = subprocess.call(['open', url])
        if result != 0:
            raise RuntimeError("open command failed")
    except Exception:
        log.info("open command failed, trying webbrowser")
        try:
            if not webbrowser.open(url):
                raise RuntimeError("webbrowser.open returned False")
        except Exception:
            log.info("Browser open failed, copying to clipboard")
            try:
                if pyperclip:
                    pyperclip.copy(url)
                else:
                    subprocess.run(['pbcopy'], input=url.encode(),
                                   check=True)
                _show_info(
                    "Не удалось открыть Telegram автоматически.\n\n"
                    f"Ссылка скопирована в буфер обмена:\n{url}")
            except Exception as exc:
                log.error("Clipboard copy failed: %s", exc)
                _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_restart(_=None):
    def _do_restart():
        global _config
        _config = load_config()
        if _app:
            _app.update_menu_title()
        restart_proxy()

    threading.Thread(target=_do_restart, daemon=True).start()


def _on_open_logs(_=None):
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        subprocess.call(['open', str(LOG_FILE)])
    else:
        _show_info("Файл логов ещё не создан.")

# Show a native text input dialog. Returns None if cancelled.
def _osascript_input(prompt: str, default: str,
                     title: str = "TG WS Proxy") -> Optional[str]:
    prompt_esc = prompt.replace('\\', '\\\\').replace('"', '\\"')
    default_esc = default.replace('\\', '\\\\').replace('"', '\\"')
    title_esc = title.replace('\\', '\\\\').replace('"', '\\"')
    r = subprocess.run(
        ['osascript', '-e',
         f'text returned of (display dialog "{prompt_esc}" '
         f'default answer "{default_esc}" '
         f'with title "{title_esc}" '
         f'buttons {{"Отмена", "OK"}} default button "OK")'],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\r\n")


def _on_edit_config(_=None):
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


# Settings via native macOS dialogs
def _edit_config_dialog():
    cfg = load_config()

    # Host
    host = _osascript_input(
        "IP-адрес прокси:",
        cfg.get("host", DEFAULT_CONFIG["host"]))
    if host is None:
        return
    host = host.strip()

    import socket as _sock
    try:
        _sock.inet_aton(host)
    except OSError:
        _show_error("Некорректный IP-адрес.")
        return

    # Port
    port_str = _osascript_input(
        "Порт прокси:",
        str(cfg.get("port", DEFAULT_CONFIG["port"])))
    if port_str is None:
        return
    try:
        port = int(port_str.strip())
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        _show_error("Порт должен быть числом 1-65535")
        return

    # DC-IP mappings
    dc_default = ", ".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"]))
    dc_str = _osascript_input(
        "DC → IP маппинги (через запятую, формат DC:IP):\n"
        "Например: 2:149.154.167.220, 4:149.154.167.220",
        dc_default)
    if dc_str is None:
        return
    dc_lines = [s.strip() for s in dc_str.replace(',', '\n').splitlines()
                if s.strip()]
    try:
        tg_ws_proxy.parse_dc_ip_list(dc_lines)
    except ValueError as e:
        _show_error(str(e))
        return

    # Verbose
    verbose = _ask_yes_no("Включить подробное логирование (verbose)?")

    # Advanced settings
    adv_str = _osascript_input(
        "Расширенные настройки (буфер KB, WS пул, лог MB):\n"
        "Формат: buf_kb,pool_size,log_max_mb",
        f"{cfg.get('buf_kb', DEFAULT_CONFIG['buf_kb'])},"
        f"{cfg.get('pool_size', DEFAULT_CONFIG['pool_size'])},"
        f"{cfg.get('log_max_mb', DEFAULT_CONFIG['log_max_mb'])}")

    adv = {}
    if adv_str:
        parts = [s.strip() for s in adv_str.split(',')]
        keys = [("buf_kb", int), ("pool_size", int),
                ("log_max_mb", float)]
        for i, (k, typ) in enumerate(keys):
            if i < len(parts):
                try:
                    adv[k] = typ(parts[i])
                except ValueError:
                    pass

    new_cfg = {
        "host": host,
        "port": port,
        "dc_ip": dc_lines,
        "verbose": verbose,
        "buf_kb": adv.get("buf_kb", cfg.get("buf_kb", DEFAULT_CONFIG["buf_kb"])),
        "pool_size": adv.get("pool_size", cfg.get("pool_size", DEFAULT_CONFIG["pool_size"])),
        "log_max_mb": adv.get("log_max_mb", cfg.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"])),
    }
    save_config(new_cfg)
    log.info("Config saved: %s", new_cfg)

    global _config
    _config = new_cfg
    if _app:
        _app.update_menu_title()

    if _ask_yes_no("Настройки сохранены.\n\nПерезапустить прокси сейчас?"):
        restart_proxy()


# First-run & IPv6 dialogs

def _show_first_run():
    _ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    tg_url = f"tg://socks?server={host}&port={port}"

    text = (
        f"Прокси запущен и работает в строке меню.\n\n"
        f"Как подключить Telegram Desktop:\n\n"
        f"Автоматически:\n"
        f"  Нажмите «Открыть в Telegram» в меню\n"
        f"  Или ссылка: {tg_url}\n\n"
        f"Вручную:\n"
        f"  Настройки → Продвинутые → Тип подключения → Прокси\n"
        f"  SOCKS5 → {host} : {port} (без логина/пароля)\n\n"
        f"Открыть прокси в Telegram сейчас?"
    )

    FIRST_RUN_MARKER.touch()

    if _ask_yes_no(text, "TG WS Proxy"):
        _on_open_in_telegram()


def _has_ipv6_enabled() -> bool:
    import socket as _sock
    try:
        addrs = _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET6)
        for addr in addrs:
            ip = addr[4][0]
            if ip and not ip.startswith('::1') and not ip.startswith('fe80::1'):
                return True
    except Exception:
        pass
    try:
        s = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
        s.bind(('::1', 0))
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

    _show_info(
        "На вашем компьютере включена поддержка подключения по IPv6.\n\n"
        "Telegram может пытаться подключаться через IPv6, "
        "что не поддерживается и может привести к ошибкам.\n\n"
        "Если прокси не работает, попробуйте отключить "
        "попытку соединения по IPv6 в настройках прокси Telegram.\n\n"
        "Это предупреждение будет показано только один раз.")


# rumps menubar app

_TgWsProxyAppBase = rumps.App if rumps else object


class TgWsProxyApp(_TgWsProxyAppBase):
    def __init__(self):
        _ensure_menubar_icon()
        icon_path = (str(MENUBAR_ICON_PATH)
                     if MENUBAR_ICON_PATH.exists() else None)

        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = _config.get("port", DEFAULT_CONFIG["port"])

        self._open_tg_item = rumps.MenuItem(
            f"Открыть в Telegram ({host}:{port})",
            callback=_on_open_in_telegram)
        self._restart_item = rumps.MenuItem(
            "Перезапустить прокси",
            callback=_on_restart)
        self._settings_item = rumps.MenuItem(
            "Настройки...",
            callback=_on_edit_config)
        self._logs_item = rumps.MenuItem(
            "Открыть логи",
            callback=_on_open_logs)

        super().__init__(
            "TG WS Proxy",
            icon=icon_path,
            template=False,
            quit_button="Выход",
            menu=[
                self._open_tg_item,
                None,
                self._restart_item,
                self._settings_item,
                self._logs_item,
            ])

    def update_menu_title(self):
        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = _config.get("port", DEFAULT_CONFIG["port"])
        self._open_tg_item.title = (
            f"Открыть в Telegram ({host}:{port})")


def run_menubar():
    global _app, _config

    _config = _runtime.prepare()
    _runtime.reset_log_file()

    setup_logging(_config.get("verbose", False),
                  log_max_mb=_config.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"]))
    log.info("TG WS Proxy menubar app starting")
    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    if rumps is None or Image is None:
        log.error("rumps or Pillow not installed; running in console mode")
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

    _app = TgWsProxyApp()
    log.info("Menubar app running")
    _app.run()

    stop_proxy()
    log.info("Menubar app exited")


def main():
    if not _acquire_lock():
        _show_info("Приложение уже запущено.")
        return

    try:
        run_menubar()
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
