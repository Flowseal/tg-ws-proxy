"""Chaquopy entry points for the Android service."""
import json
import os
import ssl
import threading
import certifi
from pathlib import Path
from itertools import zip_longest
from urllib.request import Request, urlopen

from android_cfproxy_diagnostics import run_diagnostics

# Chaquopy cannot rely on desktop cryptography or system libcrypto.
os.environ['TG_WS_PROXY_CRYPTO_BACKEND'] = 'python'

from proxy.app_runtime import ProxyAppRuntime
from proxy.stats import stats


_LOCK = threading.RLock()
_RUNTIME = None
_LAST_ERROR = None
_RELEASES_API = 'https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest'
_RELEASES_URL = 'https://github.com/Flowseal/tg-ws-proxy/releases/latest'


def _fetch_latest_release(timeout):
    request = Request(_RELEASES_API, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'tg-ws-proxy-android',
    })
    context = ssl.create_default_context(cafile=certifi.where())
    with urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Release response is too large')
    return json.loads(raw)


def _version_parts(value):
    value = str(value or '').strip().lstrip('vV')
    if value.endswith('-legacy32'):
        value = value[:-len('-legacy32')]
    parts = value.split('.')
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError('Invalid release version')
    return tuple(int(part) for part in parts)


def get_update_status_json(current_version, check_now=False):
    status = {'checked': False, 'current': current_version,
              'html_url': _RELEASES_URL}
    if not check_now:
        return json.dumps(status)
    status['checked'] = True
    try:
        release = _fetch_latest_release(timeout=5)
        latest = str(release['tag_name']).strip().lstrip('vV')
        latest_parts = _version_parts(latest)
        current_parts = _version_parts(current_version)
        comparison = next((a - b for a, b in zip_longest(
            latest_parts, current_parts, fillvalue=0) if a != b), 0)
        status.update(latest=latest, has_update=comparison > 0,
                      ahead_of_release=comparison < 0)
    except (OSError, TimeoutError, ValueError, KeyError, TypeError) as exc:
        status['error'] = str(exc) or type(exc).__name__
    return json.dumps(status)


def _remember_error(message):
    global _LAST_ERROR
    _LAST_ERROR = message


def _normalize_dc_ip_list(values):
    if values is None:
        return []
    try:
        items = list(values)
    except TypeError:
        if hasattr(values, 'toArray'):
            items = list(values.toArray())
        elif hasattr(values, 'size') and hasattr(values, 'get'):
            items = [values.get(i) for i in range(int(values.size()))]
        else:
            items = [values]
    return [str(item).strip() for item in items if str(item).strip()]


def _normalize_domain_list(values):
    if isinstance(values, str):
        return [values] if values.strip() else []
    return _normalize_dc_ip_list(values)


def start_proxy(app_dir, host, port, secret, dc_ip_list, log_max_mb=5.0,
                buf_kb=256, pool_size=4, verbose=False, cfproxy=True,
                cfproxy_user_domain='', cfproxy_user_domain_enabled=True,
                cfproxy_worker_domain=None, cfproxy_worker_enabled=False,
                no_secure=False, force_test_dc=False, fake_tls_domain='',
                proxy_protocol=False):
    global _RUNTIME, _LAST_ERROR
    with _LOCK:
        if _RUNTIME is not None:
            _RUNTIME.stop_proxy()
            if _RUNTIME.is_proxy_running():
                raise RuntimeError('Previous proxy did not stop')
            _RUNTIME = None
        _LAST_ERROR = None
        runtime = ProxyAppRuntime(Path(app_dir), logger_name='tg-ws-android',
                                  on_error=_remember_error)
        runtime.setup_logging(verbose=verbose, log_max_mb=float(log_max_mb))
        cfg = {
            'host': host, 'port': int(port), 'secret': str(secret).strip(),
            'dc_ip': _normalize_dc_ip_list(dc_ip_list),
            'log_max_mb': float(log_max_mb), 'buf_kb': int(buf_kb),
            'pool_size': int(pool_size), 'verbose': bool(verbose),
            'cfproxy': bool(cfproxy),
            'cfproxy_user_domain': _normalize_domain_list(cfproxy_user_domain),
            'cfproxy_user_domain_enabled': bool(cfproxy_user_domain_enabled),
            'cfproxy_worker_domain': _normalize_domain_list(cfproxy_worker_domain),
            'cfproxy_worker_enabled': bool(cfproxy_worker_enabled),
            'no_secure': bool(no_secure), 'force_test_dc': bool(force_test_dc),
            'fake_tls_domain': fake_tls_domain,
            'proxy_protocol': bool(proxy_protocol),
        }
        if not runtime.start_proxy(cfg):
            raise RuntimeError(_LAST_ERROR or 'Failed to start proxy runtime.')
        _RUNTIME = runtime
    ready = runtime.wait_until_ready(timeout=10)
    with _LOCK:
        if _LAST_ERROR or not ready or not runtime.is_proxy_running():
            runtime.stop_proxy()
            _RUNTIME = None
            raise RuntimeError(_LAST_ERROR or 'Proxy runtime did not bind before timeout.')
        return str(runtime.log_file)


def stop_proxy():
    global _RUNTIME, _LAST_ERROR
    with _LOCK:
        if _RUNTIME is not None:
            _RUNTIME.stop_proxy()
            if _RUNTIME.is_proxy_running():
                raise RuntimeError('Proxy did not stop')
            _RUNTIME = None
        _LAST_ERROR = None


def is_running():
    with _LOCK:
        return bool(_RUNTIME and _RUNTIME.is_proxy_running())


def get_last_error():
    return _LAST_ERROR


def get_runtime_stats_json():
    with _LOCK:
        payload = {key: value for key, value in vars(stats).items()
                   if isinstance(value, (int, float, str, bool))}
        payload['running'] = is_running()
        payload['last_error'] = _LAST_ERROR
    return json.dumps(payload)


def run_cfproxy_test_json(mode, domains, no_secure=False, probe=None):
    return json.dumps(run_diagnostics(mode, _normalize_domain_list(domains),
                                      no_secure=no_secure, probe=probe))
