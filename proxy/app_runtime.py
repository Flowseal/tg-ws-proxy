"""Small lifecycle adapter for embedding the upstream proxy in Android."""
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

from . import config as core_config
from . import tg_ws_proxy


DEFAULT_CONFIG = {
    'host': '127.0.0.1', 'port': 1443,
    'secret': os.urandom(16).hex(),
    'dc_ip': ['2:149.154.167.220', '4:149.154.167.220'],
    'buf_kb': 256, 'pool_size': 4, 'cfproxy': True,
    'cfproxy_user_domain': '', 'cfproxy_user_domain_enabled': True,
    'cfproxy_worker_domain': [], 'cfproxy_worker_enabled': False,
    'no_secure': False, 'force_test_dc': False,
    'fake_tls_domain': '', 'proxy_protocol': False,
    'verbose': False, 'log_max_mb': 5,
}


class ProxyAppRuntime:
    def __init__(self, app_dir, default_config=None, logger_name='tg-ws-runtime',
                 on_error=None, parse_dc_ip_list=None, run_proxy=None,
                 thread_factory=None):
        self.app_dir = Path(app_dir)
        self.config_file = self.app_dir / 'config.json'
        self.log_file = self.app_dir / 'proxy.log'
        self.default_config = dict(default_config or DEFAULT_CONFIG)
        self.log = logging.getLogger(logger_name)
        self.on_error = on_error
        self.parse_dc_ip_list = parse_dc_ip_list or core_config.parse_dc_ip_list
        self.run_proxy = run_proxy or tg_ws_proxy._run
        self.thread_factory = thread_factory or threading.Thread
        self.config = {}
        self._proxy_thread = None
        self._async_stop = None

    def ensure_dirs(self):
        self.app_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self):
        self.ensure_dirs()
        try:
            with self.config_file.open(encoding='utf-8') as handle:
                data = json.load(handle)
            return dict(self.default_config, **data)
        except (OSError, ValueError, TypeError):
            return dict(self.default_config)

    def save_config(self, cfg):
        self.ensure_dirs()
        with self.config_file.open('w', encoding='utf-8') as handle:
            json.dump(cfg, handle, indent=2, ensure_ascii=False)
        self.config = dict(cfg)

    def reset_log_file(self):
        try:
            self.log_file.unlink()
        except FileNotFoundError:
            pass

    def setup_logging(self, verbose=False, log_max_mb=5):
        self.ensure_dirs()
        root = logging.getLogger()
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
        for handler in list(root.handlers):
            if getattr(handler, '_tg_ws_proxy_runtime_handler', False):
                root.removeHandler(handler)
                handler.close()
        file_handler = logging.handlers.RotatingFileHandler(
            str(self.log_file), maxBytes=max(32768, int(log_max_mb * 1024 * 1024)),
            backupCount=0, encoding='utf-8')
        file_handler._tg_ws_proxy_runtime_handler = True
        root.addHandler(file_handler)
        if not getattr(sys, 'frozen', False):
            stream = logging.StreamHandler(sys.stdout)
            stream._tg_ws_proxy_runtime_handler = True
            root.addHandler(stream)

    def _emit_error(self, message):
        if self.on_error:
            self.on_error(message)

    def _run_proxy_thread(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop_event = asyncio.Event()
        self._async_stop = (loop, stop_event)
        try:
            loop.run_until_complete(self.run_proxy(stop_event=stop_event))
        except Exception as exc:
            self.log.error('Proxy thread crashed: %s', exc)
            if 'address already in use' in str(exc).lower() or '10048' in str(exc):
                self._emit_error('Не удалось запустить прокси:\n'
                                 'Порт уже используется другим приложением.')
            else:
                self._emit_error(str(exc) or exc.__class__.__name__)
        finally:
            self._async_stop = None
            loop.close()

    def start_proxy(self, cfg=None):
        if self.is_proxy_running():
            return True
        active = dict(self.default_config)
        active.update(cfg if cfg is not None else self.config)
        try:
            entries = active['dc_ip']
            if not isinstance(entries, (list, tuple)) or not entries:
                raise ValueError('dc_ip must contain at least one DC:IP entry')
            dc_redirects = self.parse_dc_ip_list(entries)
            if not dc_redirects:
                raise ValueError('dc_ip must contain at least one valid entry')
            user_domains = core_config.coerce_domain_list(
                active.get('cfproxy_user_domains', active.get('cfproxy_user_domain', '')))
            worker_domains = core_config.coerce_domain_list(
                active.get('cfproxy_worker_domain', []))
            for domain in user_domains + worker_domains:
                if not core_config._is_valid_domain(domain):
                    raise ValueError('Invalid CF domain: %s' % domain)
            secret = str(active.get('secret') or '').strip() or os.urandom(16).hex()
            active['secret'] = secret
            next_config = core_config.ProxyConfig(
                port=int(active['port']), host=str(active['host']), secret=secret,
                dc_redirects=dc_redirects,
                buffer_size=max(4, int(active['buf_kb'])) * 1024,
                pool_size=max(0, int(active['pool_size'])),
                fallback_cfproxy=bool(active['cfproxy']),
                cfproxy_user_domains=user_domains if active.get('cfproxy_user_domain_enabled', True) else [],
                cfproxy_worker_domains=worker_domains if active.get('cfproxy_worker_enabled', False) else [],
                disable_secure=bool(active.get('no_secure', False)),
                force_test_dc=bool(active.get('force_test_dc', False)),
                fake_tls_domain=str(active.get('fake_tls_domain') or ''),
                proxy_protocol=bool(active.get('proxy_protocol', False)),
            )
        except (ValueError, TypeError, KeyError) as exc:
            self._emit_error('Ошибка конфигурации:\n%s' % exc)
            return False
        self.save_config(active)
        core_config.proxy_config.__dict__.update(next_config.__dict__)
        self._proxy_thread = self.thread_factory(target=self._run_proxy_thread,
                                                 daemon=True, name='proxy')
        self._proxy_thread.start()
        return True

    def stop_proxy(self):
        if self._async_stop:
            loop, event = self._async_stop
            loop.call_soon_threadsafe(event.set)
        if self._proxy_thread:
            self._proxy_thread.join(timeout=2)
            if self._proxy_thread.is_alive():
                return
        self._proxy_thread = None

    def restart_proxy(self, delay_seconds=0.3):
        self.stop_proxy()
        if self.is_proxy_running():
            return False
        time.sleep(delay_seconds)
        return self.start_proxy()

    def is_proxy_running(self):
        return bool(self._proxy_thread and self._proxy_thread.is_alive())
