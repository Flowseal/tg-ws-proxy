from __future__ import annotations

import os
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
from proxy import __version__

from utils.tray_common import (
    APP_DIR, APP_NAME, DEFAULT_CONFIG, FIRST_RUN_MARKER, IPV6_WARN_MARKER,
    LOG_FILE, acquire_lock, apply_proxy_config, ensure_dirs, load_config,
    log, release_lock, save_config, setup_logging, stop_proxy, tg_proxy_url,
)

MENUBAR_ICON_PATH = APP_DIR / "menubar_icon.png"

_proxy_thread: Optional[threading.Thread] = None
_async_stop: Optional[object] = None
_app: Optional[object] = None
_config: dict = {}
_exiting: bool = False

# osascript dialogs


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _osascript(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


def _show_error(text: str, title: str = "TG WS Proxy") -> None:
    _osascript(
        f'display dialog "{_esc(text)}" with title "{_esc(title)}" '
        f'buttons {{"OK"}} default button "OK" with icon stop'
    )


def _show_info(text: str, title: str = "TG WS Proxy") -> None:
    _osascript(
        f'display dialog "{_esc(text)}" with title "{_esc(title)}" '
        f'buttons {{"OK"}} default button "OK" with icon note'
    )


def _ask_yes_no(text: str, title: str = "TG WS Proxy") -> bool:
    return _ask_yes_no_close(text, title) is True


def _ask_yes_no_close(text: str, title: str = "TG WS Proxy") -> Optional[bool]:
    r = subprocess.run(
        [
            "osascript", "-e",
            f'button returned of (display dialog "{_esc(text)}" '
            f'with title "{_esc(title)}" '
            f'buttons {{"Закрыть", "Нет", "Да"}} '
            f'default button "Да" cancel button "Закрыть" with icon note)',
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    btn = r.stdout.strip()
    if btn == "Да":
        return True
    if btn == "Нет":
        return False
    return None


def _osascript_input(prompt: str, default: str, title: str = "TG WS Proxy") -> Optional[str]:
    r = subprocess.run(
        [
            "osascript", "-e",
            f'text returned of (display dialog "{_esc(prompt)}" '
            f'default answer "{_esc(default)}" '
            f'with title "{_esc(title)}" '
            f'buttons {{"Закрыть", "OK"}} '
            f'default button "OK" cancel button "Закрыть")',
        ],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout.rstrip("\r\n")


# menubar icon


def _make_menubar_icon(size: int = 44):
    if Image is None:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 11
    draw.ellipse([margin, margin, size - margin, size - margin], fill=(0, 0, 0, 255))
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size=int(size * 0.55))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1]),
        "T", fill=(255, 255, 255, 255), font=font,
    )
    return img


def _ensure_menubar_icon() -> None:
    if MENUBAR_ICON_PATH.exists():
        return
    ensure_dirs()
    img = _make_menubar_icon(44)
    if img:
        img.save(str(MENUBAR_ICON_PATH), "PNG")


# proxy lifecycle (macOS-local)

import asyncio as _asyncio


def _run_proxy_thread() -> None:
    global _async_stop
    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    stop_ev = _asyncio.Event()
    _async_stop = (loop, stop_ev)
    try:
        loop.run_until_complete(tg_ws_proxy._run(stop_event=stop_ev))
    except Exception as exc:
        log.error("Proxy thread crashed: %s", exc)
        if "Address already in use" in str(exc):
            _show_error(
                "Не удалось запустить прокси:\n"
                "Порт уже используется другим приложением.\n\n"
                "Закройте приложение, использующее этот порт, "
                "или измените порт в настройках прокси и перезапустите."
            )
    finally:
        loop.close()
        _async_stop = None


def _start_proxy() -> None:
    global _proxy_thread
    if _proxy_thread and _proxy_thread.is_alive():
        log.info("Proxy already running")
        return
    if not apply_proxy_config(_config):
        _show_error("Ошибка конфигурации DC → IP.")
        return
    pc = tg_ws_proxy.proxy_config
    log.info("Starting proxy on %s:%d ...", pc.host, pc.port)
    _proxy_thread = threading.Thread(target=_run_proxy_thread, daemon=True, name="proxy")
    _proxy_thread.start()


def _stop_proxy() -> None:
    global _proxy_thread, _async_stop
    if _async_stop:
        loop, stop_ev = _async_stop
        loop.call_soon_threadsafe(stop_ev.set)
        if _proxy_thread:
            _proxy_thread.join(timeout=2)
    _proxy_thread = None
    log.info("Proxy stopped")


def _restart_proxy() -> None:
    log.info("Restarting proxy...")
    _stop_proxy()
    time.sleep(0.3)
    _start_proxy()


# menu callbacks


def _on_open_in_telegram(_=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Opening %s", url)
    try:
        result = subprocess.call(["open", url])
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
                    subprocess.run(["pbcopy"], input=url.encode(), check=True)
                _show_info(
                    "Не удалось открыть Telegram автоматически.\n\n"
                    f"Ссылка скопирована в буфер обмена:\n{url}"
                )
            except Exception as exc:
                log.error("Clipboard copy failed: %s", exc)
                _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_copy_link(_=None) -> None:
    url = tg_proxy_url(_config)
    log.info("Copying link: %s", url)
    try:
        if pyperclip:
            pyperclip.copy(url)
        else:
            subprocess.run(["pbcopy"], input=url.encode(), check=True)
    except Exception as exc:
        log.error("Clipboard copy failed: %s", exc)
        _show_error(f"Не удалось скопировать ссылку:\n{exc}")


def _on_restart(_=None) -> None:
    def _do():
        global _config
        _config = load_config()
        if _app:
            _app.update_menu_title()
        _restart_proxy()

    threading.Thread(target=_do, daemon=True).start()


def _on_open_logs(_=None) -> None:
    log.info("Opening log file: %s", LOG_FILE)
    if LOG_FILE.exists():
        subprocess.call(["open", str(LOG_FILE)])
    else:
        _show_info("Файл логов ещё не создан.")


def _on_edit_config(_=None) -> None:
    threading.Thread(target=_edit_config_dialog, daemon=True).start()


def _check_updates_menu_title() -> str:
    on = bool(_config.get("check_updates", True))
    return "✓ Проверять обновления при запуске" if on else "Проверять обновления при запуске (выкл)"


def _toggle_check_updates(_=None) -> None:
    global _config
    _config["check_updates"] = not bool(_config.get("check_updates", True))
    save_config(_config)
    if _app is not None:
        _app._check_updates_item.title = _check_updates_menu_title()


def _on_open_release_page(_=None) -> None:
    from utils.update_check import RELEASES_PAGE_URL
    webbrowser.open(RELEASES_PAGE_URL)


# update check


def _maybe_notify_update_async() -> None:
    def _work():
        time.sleep(1.5)
        if _exiting:
            return
        if not _config.get("check_updates", True):
            return
        try:
            from utils.update_check import RELEASES_PAGE_URL, get_status, run_check
            run_check(__version__)
            st = get_status()
            if not st.get("has_update"):
                return
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"
            if _ask_yes_no(
                f"Доступна новая версия: {ver}\n\nОткрыть страницу релиза в браузере?",
                "TG WS Proxy — обновление",
            ):
                webbrowser.open(url)
        except Exception as exc:
            log.debug("Update check failed: %s", exc)

    threading.Thread(target=_work, daemon=True, name="update-check").start()


# settings dialog


def _edit_config_dialog() -> None:
    cfg = load_config()

    host = _osascript_input("IP-адрес прокси:", cfg.get("host", DEFAULT_CONFIG["host"]))
    if host is None:
        return
    host = host.strip()
    import socket as _sock
    try:
        _sock.inet_aton(host)
    except OSError:
        _show_error("Некорректный IP-адрес.")
        return

    port_str = _osascript_input("Порт прокси:", str(cfg.get("port", DEFAULT_CONFIG["port"])))
    if port_str is None:
        return
    try:
        port = int(port_str.strip())
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        _show_error("Порт должен быть числом 1-65535")
        return

    secret_str = _osascript_input(
        "MTProto Secret (32 hex символа):", cfg.get("secret", DEFAULT_CONFIG["secret"])
    )
    if secret_str is None:
        return
    secret_str = secret_str.strip().lower()
    if len(secret_str) != 32 or not all(c in "0123456789abcdef" for c in secret_str):
        _show_error("Secret должен быть строкой из 32 шестнадцатеричных символов.")
        return

    dc_default = ", ".join(cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"]))
    dc_str = _osascript_input(
        "DC → IP маппинги (через запятую, формат DC:IP):\n"
        "Например: 2:149.154.167.220, 4:149.154.167.220",
        dc_default,
    )
    if dc_str is None:
        return
    dc_lines = [s.strip() for s in dc_str.replace(",", "\n").splitlines() if s.strip()]
    try:
        tg_ws_proxy.parse_dc_ip_list(dc_lines)
    except ValueError as e:
        _show_error(str(e))
        return

    verbose = _ask_yes_no_close("Включить подробное логирование (verbose)?")
    if verbose is None:
        return

    adv_str = _osascript_input(
        "Расширенные настройки (буфер KB, WS пул, лог MB):\n"
        "Формат: buf_kb,pool_size,log_max_mb",
        f"{cfg.get('buf_kb', DEFAULT_CONFIG['buf_kb'])},"
        f"{cfg.get('pool_size', DEFAULT_CONFIG['pool_size'])},"
        f"{cfg.get('log_max_mb', DEFAULT_CONFIG['log_max_mb'])}",
    )
    if adv_str is None:
        return

    adv = {}
    if adv_str:
        parts = [s.strip() for s in adv_str.split(",")]
        keys = [("buf_kb", int), ("pool_size", int), ("log_max_mb", float)]
        for i, (k, typ) in enumerate(keys):
            if i < len(parts):
                try:
                    adv[k] = typ(parts[i])
                except ValueError:
                    pass

    new_cfg = {
        "host": host,
        "port": port,
        "secret": secret_str,
        "dc_ip": dc_lines,
        "verbose": verbose,
        "buf_kb": adv.get("buf_kb", cfg.get("buf_kb", DEFAULT_CONFIG["buf_kb"])),
        "pool_size": adv.get("pool_size", cfg.get("pool_size", DEFAULT_CONFIG["pool_size"])),
        "log_max_mb": adv.get("log_max_mb", cfg.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"])),
        "check_updates": cfg.get("check_updates", True),
    }
    save_config(new_cfg)
    log.info("Config saved: %s", new_cfg)

    global _config
    _config = new_cfg
    if _app:
        _app.update_menu_title()

    if _ask_yes_no_close("Настройки сохранены.\n\nПерезапустить прокси сейчас?"):
        _restart_proxy()


# first run & ipv6


def _show_first_run() -> None:
    ensure_dirs()
    if FIRST_RUN_MARKER.exists():
        return

    host = _config.get("host", DEFAULT_CONFIG["host"])
    port = _config.get("port", DEFAULT_CONFIG["port"])
    secret = _config.get("secret", DEFAULT_CONFIG["secret"])
    tg_url = tg_proxy_url(_config)
    link_host = tg_ws_proxy.get_link_host(host)

    text = (
        f"Прокси запущен и работает в строке меню.\n\n"
        f"Как подключить Telegram Desktop:\n\n"
        f"Автоматически:\n"
        f"  Нажмите «Открыть в Telegram» в меню\n"
        f"  Или ссылка: {tg_url}\n\n"
        f"Вручную:\n"
        f"  Настройки → Продвинутые → Тип подключения → Прокси\n"
        f"  MTProto → {link_host} : {port} \n"
        f"  Secret: dd{secret} \n\n"
        f"Открыть прокси в Telegram сейчас?"
    )

    FIRST_RUN_MARKER.touch()
    if _ask_yes_no(text, "TG WS Proxy"):
        _on_open_in_telegram()


def _check_ipv6_warning() -> None:
    ensure_dirs()
    if IPV6_WARN_MARKER.exists():
        return

    import socket as _sock
    has = False
    try:
        for addr in _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET6):
            ip = addr[4][0]
            if ip and not ip.startswith("::1") and not ip.startswith("fe80::1"):
                has = True
                break
    except Exception:
        pass
    if not has:
        try:
            s = _sock.socket(_sock.AF_INET6, _sock.SOCK_STREAM)
            s.bind(("::1", 0))
            s.close()
            has = True
        except Exception:
            pass
    if not has:
        return

    IPV6_WARN_MARKER.touch()
    _show_info(
        "На вашем компьютере включена поддержка подключения по IPv6.\n\n"
        "Telegram может пытаться подключаться через IPv6, "
        "что не поддерживается и может привести к ошибкам.\n\n"
        "Если прокси не работает, попробуйте отключить "
        "попытку соединения по IPv6 в настройках прокси Telegram.\n\n"
        "Это предупреждение будет показано только один раз."
    )


# rumps app

_TgWsProxyAppBase = rumps.App if rumps else object


class TgWsProxyApp(_TgWsProxyAppBase):
    def __init__(self):
        _ensure_menubar_icon()
        icon_path = str(MENUBAR_ICON_PATH) if MENUBAR_ICON_PATH.exists() else None

        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = _config.get("port", DEFAULT_CONFIG["port"])
        link_host = tg_ws_proxy.get_link_host(host)

        self._open_tg_item = rumps.MenuItem(
            f"Открыть в Telegram ({link_host}:{port})", callback=_on_open_in_telegram
        )
        self._copy_link_item = rumps.MenuItem("Скопировать ссылку", callback=_on_copy_link)
        self._restart_item = rumps.MenuItem("Перезапустить прокси", callback=_on_restart)
        self._settings_item = rumps.MenuItem("Настройки...", callback=_on_edit_config)
        self._logs_item = rumps.MenuItem("Открыть логи", callback=_on_open_logs)
        self._release_page_item = rumps.MenuItem(
            "Страница релиза на GitHub…", callback=_on_open_release_page
        )
        self._check_updates_item = rumps.MenuItem(
            _check_updates_menu_title(), callback=_toggle_check_updates
        )
        self._version_item = rumps.MenuItem(f"Версия {__version__}", callback=lambda _: None)

        super().__init__(
            "TG WS Proxy",
            icon=icon_path,
            template=False,
            quit_button="Выход",
            menu=[
                self._open_tg_item,
                self._copy_link_item,
                None,
                self._restart_item,
                self._settings_item,
                self._logs_item,
                None,
                self._release_page_item,
                self._check_updates_item,
                None,
                self._version_item,
            ],
        )

    def update_menu_title(self) -> None:
        host = _config.get("host", DEFAULT_CONFIG["host"])
        port = _config.get("port", DEFAULT_CONFIG["port"])
        link_host = tg_ws_proxy.get_link_host(host)
        self._open_tg_item.title = f"Открыть в Telegram ({link_host}:{port})"


# entry point


def run_menubar() -> None:
    global _app, _config

    _config = load_config()
    save_config(_config)

    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
        except Exception:
            pass

    setup_logging(
        _config.get("verbose", False),
        log_max_mb=_config.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"]),
    )
    log.info("TG WS Proxy версия %s, menubar app starting", __version__)
    log.info("Config: %s", _config)
    log.info("Log file: %s", LOG_FILE)

    if rumps is None or Image is None:
        log.error("rumps or Pillow not installed; running in console mode")
        _start_proxy()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            _stop_proxy()
        return

    _start_proxy()
    _maybe_notify_update_async()
    _show_first_run()
    _check_ipv6_warning()

    _app = TgWsProxyApp()
    log.info("Menubar app running")
    _app.run()

    _stop_proxy()
    log.info("Menubar app exited")


def main() -> None:
    if not acquire_lock("macos.py"):
        _show_info("Приложение уже запущено.")
        return
    try:
        run_menubar()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
