from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import socket as _socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import psutil

from proxy import __version__, get_link_host, parse_dc_ip_list, proxy_config, coerce_domain_list
from proxy.tg_ws_proxy import _run
from utils.default_config import default_tray_config
from utils.diagnostics import diagnose_listen_error
from utils.logging_setup import build_log_handler

log = logging.getLogger("tg-ws-tray")

APP_NAME = "TgWsProxy"
PORTABLE_DIR_NAME = "TgWsProxy_data"


def _standard_app_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def _exe_dir() -> Optional[Path]:
    try:
        base = getattr(sys, "frozen", False) and sys.executable or sys.argv[0]
    except Exception:
        return None
    if not base:
        return None
    p = Path(base).resolve()
    return p.parent if p.is_file() else p


def _detect_portable() -> Optional[Path]:
    exe_dir = _exe_dir()
    if exe_dir is None:
        return None
    portable_dir = exe_dir / PORTABLE_DIR_NAME
    if "--portable" in sys.argv:
        try:
            portable_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Cannot create portable dir %s: %s", portable_dir, repr(exc))
            return None
    if portable_dir.is_dir():
        _migrate_into_portable(portable_dir)
        return portable_dir
    return None


def _migrate_into_portable(portable_dir: Path) -> None:
    try:
        if any(portable_dir.iterdir()):
            return
    except OSError:
        return
    std = _standard_app_dir()
    if not std.exists():
        return
    try:
        for src in std.iterdir():
            if ".log" in src.name:
                continue
            dst = portable_dir / src.name
            try:
                if not src.is_dir():
                    shutil.copy2(src, dst)
            except OSError as exc:
                log.warning("Portable migration: skip %s: %s", src.name, repr(exc))
    except OSError as exc:
        log.warning("Portable migration failed: %s", repr(exc))


def _app_dir() -> Path:
    return _detect_portable() or _standard_app_dir()


APP_DIR = _app_dir()
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "proxy.log"
FIRST_RUN_MARKER = APP_DIR / ".first_run_done_mtproto"
IPV6_WARN_MARKER = APP_DIR / ".ipv6_warned"

DEFAULT_CONFIG: Dict[str, Any] = default_tray_config()

IS_FROZEN = bool(getattr(sys, "frozen", False))


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


# single-instance lock

_lock_file_path: Optional[Path] = None


def _same_process(meta: dict, proc: psutil.Process) -> bool:
    try:
        lock_ct = float(meta.get("create_time", 0.0))
        if lock_ct > 0 and abs(lock_ct - proc.create_time()) > 1.0:
            return False
    except Exception:
        return False
    if IS_FROZEN:
        return APP_NAME.lower() in proc.name().lower()
    return False


def acquire_lock() -> bool:
    global _lock_file_path
    ensure_dirs()
    for f in list(APP_DIR.glob("*.lock")):
        try:
            pid = int(f.stem)
        except Exception:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        meta: dict = {}
        try:
            raw = f.read_text(encoding="utf-8").strip()
            if raw:
                meta = json.loads(raw)
        except Exception:
            pass
        is_running = False
        try:
            is_running = _same_process(meta, psutil.Process(pid))
        except Exception:
            pass
        if is_running:
            return False
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

    lock_file = APP_DIR / f"{os.getpid()}.lock"
    try:
        proc = psutil.Process(os.getpid())
        lock_file.write_text(
            json.dumps({"create_time": proc.create_time()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        try:
            lock_file.touch()
        except Exception:
            pass
    _lock_file_path = lock_file
    return True


def release_lock() -> None:
    global _lock_file_path
    if _lock_file_path:
        try:
            _lock_file_path.unlink(missing_ok=True)
        except Exception:
            pass
        _lock_file_path = None


# config

def _apply_ui_language(cfg: dict) -> None:
    from ui.i18n import set_language

    set_language(cfg.get("language", DEFAULT_CONFIG["language"]))


def load_config() -> dict:
    ensure_dirs()
    cfg: Optional[dict] = None
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                data.setdefault(k, v)
            cfg = data
        except Exception as exc:
            log.warning("Failed to load config: %s", repr(exc))
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
    _apply_ui_language(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    ensure_dirs()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# logging

_LOG_FMT_FILE = "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s"
_LOG_FMT_CONSOLE = "%(asctime)s  %(levelname)-5s  %(message)s"


def setup_logging(verbose: bool = False, log_max_mb: float = 5) -> None:
    ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

    fh = build_log_handler(str(LOG_FILE), log_max_mb=log_max_mb, backups=1)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_LOG_FMT_FILE, datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(fh)

    if not IS_FROZEN:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(_LOG_FMT_CONSOLE, datefmt="%H:%M:%S"))
        root.addHandler(ch)


# icon

def make_icon_image(size: int = 64, *, color: Tuple[int, ...] = (0, 136, 204, 255)):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 2
    draw.ellipse([margin, margin, size - margin, size - margin], fill=color)

    for path in _font_paths():
        try:
            font = ImageFont.truetype(path, size=int(size * 0.55))
            break
        except Exception:
            continue
    else:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1]),
        "T",
        fill=(255, 255, 255, 255),
        font=font,
    )
    return img


def _font_paths():
    if sys.platform == "win32":
        return ["arial.ttf"]
    if sys.platform == "darwin":
        return ["/System/Library/Fonts/Helvetica.ttc"]
    return [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]


def load_icon():
    from PIL import Image

    icon_path = Path(__file__).parents[1] / "icon.ico"
    if icon_path.exists():
        try:
            return Image.open(str(icon_path))
        except Exception:
            pass
    return make_icon_image(64)


# proxy lifecycle

_proxy_thread: Optional[threading.Thread] = None
_async_stop: Optional[Tuple[asyncio.AbstractEventLoop, asyncio.Event]] = None


def _run_proxy_thread(show_error: Callable[[str], None]) -> None:
    global _async_stop

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_ev = asyncio.Event()
    _async_stop = (loop, stop_ev)

    try:
        loop.run_until_complete(_run(stop_event=stop_ev))
    except Exception as exc:
        log.error("Proxy thread crashed: %s", repr(exc))
        msg, diagnose_called = diagnose_listen_error(exc)
        if msg:
            show_error(msg)
        if diagnose_called:
            diagnose_called()
    finally:
        loop.close()
        _async_stop = None


def apply_proxy_config(cfg: dict) -> bool:
    dc_ip_list = cfg.get("dc_ip", DEFAULT_CONFIG["dc_ip"])
    try:
        dc_redirects = parse_dc_ip_list(dc_ip_list)
    except ValueError as e:
        log.error("Bad config dc_ip: %s", e)
        return False

    pc = proxy_config
    pc.port = cfg.get("port", DEFAULT_CONFIG["port"])
    pc.host = cfg.get("host", DEFAULT_CONFIG["host"])
    pc.secret = cfg.get("secret", DEFAULT_CONFIG["secret"])
    pc.dc_redirects = dc_redirects
    pc.buffer_size = max(4, cfg.get("buf_kb", DEFAULT_CONFIG["buf_kb"])) * 1024
    pc.pool_size = max(0, cfg.get("pool_size", DEFAULT_CONFIG["pool_size"]))
    pc.fallback_cfproxy = cfg.get("cfproxy", DEFAULT_CONFIG["cfproxy"])
    pc.cfproxy_user_domains = coerce_domain_list(cfg.get("cfproxy_user_domain", DEFAULT_CONFIG["cfproxy_user_domain"]))
    pc.cfproxy_worker_domains = coerce_domain_list(cfg.get("cfproxy_worker_domain", DEFAULT_CONFIG["cfproxy_worker_domain"]))
    pc.ws_keepalive_interval = max(0, cfg.get("ws_keepalive_interval", DEFAULT_CONFIG["ws_keepalive_interval"]))
    return True


def start_proxy(cfg: dict, on_error: Callable[[str], None]) -> None:
    global _proxy_thread
    if _proxy_thread and _proxy_thread.is_alive():
        log.info("Proxy already running")
        return

    if not apply_proxy_config(cfg):
        from ui.i18n import t
        on_error(t("error.dc_config"))
        return

    pc = proxy_config
    log.info("Starting proxy on %s:%d ...", pc.host, pc.port)
    _proxy_thread = threading.Thread(
        target=_run_proxy_thread, args=(on_error,), daemon=True, name="proxy"
    )
    _proxy_thread.start()


def stop_proxy() -> None:
    global _proxy_thread, _async_stop
    if _async_stop:
        loop, stop_ev = _async_stop
        loop.call_soon_threadsafe(stop_ev.set)
        if _proxy_thread:
            _proxy_thread.join(timeout=5)
            if _proxy_thread.is_alive():
                log.warning("Proxy thread did not stop within timeout; "
                            "port may still be in use")
    _proxy_thread = None
    log.info("Proxy stopped")


def restart_proxy(cfg: dict, on_error: Callable[[str], None]) -> None:
    log.info("Restarting proxy...")
    stop_proxy()
    time.sleep(1.0)
    start_proxy(cfg, on_error)


def tg_proxy_url(cfg: dict) -> str:
    host = cfg.get("host", DEFAULT_CONFIG["host"])
    port = cfg.get("port", DEFAULT_CONFIG["port"])
    secret = cfg.get("secret", DEFAULT_CONFIG["secret"])
    link_host = get_link_host(host)
    return f"tg://proxy?server={link_host}&port={port}&secret=dd{secret}"


def _has_ipv6() -> bool:
    try:
        for addr in _socket.getaddrinfo(_socket.gethostname(), None, _socket.AF_INET6):
            ip = addr[4][0]
            if ip and not ip.startswith("::1") and not ip.startswith("fe80::1"):
                return True
    except Exception:
        pass
    try:
        s = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except Exception:
        return False


def check_ipv6_warning(show_info: Callable[[str, str], None]) -> None:
    ensure_dirs()
    if IPV6_WARN_MARKER.exists() or not _has_ipv6():
        return
    IPV6_WARN_MARKER.touch()
    from ui.i18n import t

    threading.Thread(
        target=lambda: show_info(t("ipv6.warning"), t("app.name")),
        daemon=True,
    ).start()


# update check

def maybe_notify_update(
    cfg: dict,
    is_exiting: Callable[[], bool],
    ask_open: Callable[[str, str], bool],
) -> None:
    if not cfg.get("check_updates", True):
        return

    def _work():
        time.sleep(1.5)
        if is_exiting():
            return
        try:
            from utils.update_check import RELEASES_PAGE_URL, get_status, run_check
            import webbrowser

            run_check(__version__)
            st = get_status()
            if not st.get("has_update"):
                return
            url = (st.get("html_url") or "").strip() or RELEASES_PAGE_URL
            ver = st.get("latest") or "?"
            from ui.i18n import t

            if ask_open(
                t("update.ask_open", version=ver),
                t("app.update_title"),
            ):
                webbrowser.open(url)
        except Exception as exc:
            log.warning("Update check failed: %s", repr(exc))

    threading.Thread(target=_work, daemon=True, name="update-check").start()


# ctk thread (windows / linux)

_ctk_root: Any = None
_ctk_root_ready = threading.Event()


def ensure_ctk_thread(ctk: Any, mode: str = "auto") -> bool:
    global _ctk_root
    if ctk is None:
        return False
    if _ctk_root_ready.is_set():
        return True

    def _run():
        global _ctk_root
        from ui.ctk_theme import apply_ctk_appearance, install_tkinter_variable_del_guard

        install_tkinter_variable_del_guard()
        apply_ctk_appearance(ctk, mode)
        _ctk_root = ctk.CTk()
        _ctk_root.withdraw()
        _ctk_root_ready.set()
        _ctk_root.mainloop()

    threading.Thread(target=_run, daemon=True, name="ctk-root").start()
    _ctk_root_ready.wait(timeout=5.0)
    return _ctk_root is not None


def ctk_run_dialog(build_fn: Callable[[threading.Event], None]) -> None:
    if _ctk_root is None:
        return
    done = threading.Event()

    def _invoke():
        try:
            build_fn(done)
        except Exception:
            log.exception("CTk dialog failed")
            done.set()

    _ctk_root.after(0, _invoke)
    done.wait()
    import gc
    gc.collect()


def quit_ctk() -> None:
    if _ctk_root is not None:
        try:
            _ctk_root.after(0, _ctk_root.quit)
        except Exception:
            pass


# common bootstrap

def bootstrap(cfg: dict) -> None:
    save_config(cfg)
    if LOG_FILE.exists():
        try:
            LOG_FILE.unlink()
        except Exception:
            pass
    setup_logging(
        cfg.get("verbose", False),
        log_max_mb=cfg.get("log_max_mb", DEFAULT_CONFIG["log_max_mb"]),
    )
    log.info("TG WS Proxy версия %s starting", __version__)
    log.info("Config: %s", cfg)
    log.info("Log file: %s", LOG_FILE)
