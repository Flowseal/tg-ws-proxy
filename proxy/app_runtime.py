from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import proxy.tg_ws_proxy as tg_ws_proxy


DEFAULT_CONFIG = {
    "port": 1080,
    "host": "127.0.0.1",
    "dc_ip": ["2:149.154.167.220", "4:149.154.167.220"],
    "verbose": False,
}


class ProxyAppRuntime:
    def __init__(self, app_dir: Path,
                 default_config: Optional[dict] = None,
                 logger_name: str = "tg-ws-runtime",
                 on_error: Optional[Callable[[str], None]] = None,
                 parse_dc_ip_list: Optional[
                     Callable[[list[str]], Dict[int, str]]
                 ] = None,
                 run_proxy: Optional[Callable[..., object]] = None,
                 thread_factory: Optional[Callable[..., object]] = None):
        self.app_dir = Path(app_dir)
        self.config_file = self.app_dir / "config.json"
        self.log_file = self.app_dir / "proxy.log"
        self.default_config = dict(default_config or DEFAULT_CONFIG)
        self.log = logging.getLogger(logger_name)
        self.on_error = on_error
        self.parse_dc_ip_list = parse_dc_ip_list or \
            tg_ws_proxy.parse_dc_ip_list
        self.run_proxy = run_proxy or tg_ws_proxy._run
        self.thread_factory = thread_factory or threading.Thread
        self.config: dict = {}
        self._proxy_thread = None
        self._async_stop = None

    def ensure_dirs(self):
        self.app_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> dict:
        self.ensure_dirs()
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key, value in self.default_config.items():
                    data.setdefault(key, value)
                self.config = data
                return data
            except Exception as exc:
                self.log.warning("Failed to load config: %s", exc)

        self.config = dict(self.default_config)
        return dict(self.config)

    def save_config(self, cfg: dict):
        self.ensure_dirs()
        self.config = dict(cfg)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def reset_log_file(self):
        if self.log_file.exists():
            try:
                self.log_file.unlink()
            except Exception:
                pass

    def setup_logging(self, verbose: bool = False):
        self.ensure_dirs()
        root = logging.getLogger()
        root.setLevel(logging.DEBUG if verbose else logging.INFO)

        for handler in list(root.handlers):
            if getattr(handler, "_tg_ws_proxy_runtime_handler", False):
                root.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass

        fh = logging.FileHandler(str(self.log_file), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
        fh._tg_ws_proxy_runtime_handler = True
        root.addHandler(fh)

        if not getattr(sys, "frozen", False):
            ch = logging.StreamHandler(sys.stdout)
            ch.setLevel(logging.DEBUG if verbose else logging.INFO)
            ch.setFormatter(logging.Formatter(
                "%(asctime)s  %(levelname)-5s  %(message)s",
                datefmt="%H:%M:%S"))
            ch._tg_ws_proxy_runtime_handler = True
            root.addHandler(ch)

    def prepare(self) -> dict:
        cfg = self.load_config()
        self.save_config(cfg)
        return cfg

    def _emit_error(self, text: str):
        if self.on_error:
            self.on_error(text)

    def _run_proxy_thread(self, port: int, dc_opt: Dict[int, str],
                          host: str = "127.0.0.1"):
        loop = _asyncio.new_event_loop()
        _asyncio.set_event_loop(loop)
        stop_ev = _asyncio.Event()
        self._async_stop = (loop, stop_ev)

        try:
            loop.run_until_complete(
                self.run_proxy(port, dc_opt, stop_event=stop_ev, host=host))
        except Exception as exc:
            self.log.error("Proxy thread crashed: %s", exc)
            if ("10048" in str(exc) or
                    "Address already in use" in str(exc)):
                self._emit_error(
                    "Не удалось запустить прокси:\n"
                    "Порт уже используется другим приложением.\n\n"
                    "Закройте приложение, использующее этот порт, "
                    "или измените порт в настройках прокси и перезапустите.")
        finally:
            loop.close()
            self._async_stop = None

    def start_proxy(self, cfg: Optional[dict] = None) -> bool:
        if self._proxy_thread and self._proxy_thread.is_alive():
            self.log.info("Proxy already running")
            return True

        active_cfg = dict(cfg or self.config or self.default_config)
        self.config = dict(active_cfg)
        port = active_cfg.get("port", self.default_config["port"])
        host = active_cfg.get("host", self.default_config["host"])
        dc_ip_list = active_cfg.get("dc_ip", self.default_config["dc_ip"])

        try:
            dc_opt = self.parse_dc_ip_list(dc_ip_list)
        except ValueError as exc:
            self.log.error("Bad config dc_ip: %s", exc)
            self._emit_error("Ошибка конфигурации:\n%s" % exc)
            return False

        self.log.info("Starting proxy on %s:%d ...", host, port)
        self._proxy_thread = self.thread_factory(
            target=self._run_proxy_thread,
            args=(port, dc_opt, host),
            daemon=True,
            name="proxy")
        self._proxy_thread.start()
        return True

    def stop_proxy(self):
        if self._async_stop:
            loop, stop_ev = self._async_stop
            loop.call_soon_threadsafe(stop_ev.set)
            if self._proxy_thread:
                self._proxy_thread.join(timeout=2)
        self._proxy_thread = None
        self.log.info("Proxy stopped")

    def restart_proxy(self, delay_seconds: float = 0.3) -> bool:
        self.log.info("Restarting proxy...")
        self.stop_proxy()
        time.sleep(delay_seconds)
        return self.start_proxy()

    def is_proxy_running(self) -> bool:
        return bool(self._proxy_thread and self._proxy_thread.is_alive())
