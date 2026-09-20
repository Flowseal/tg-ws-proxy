import importlib.util
import json
import threading
from pathlib import Path
import pytest


BRIDGE = Path(__file__).resolve().parents[1] / 'android/app/src/main/python/android_proxy_bridge.py'


def load_bridge():
    spec = importlib.util.spec_from_file_location('android_proxy_bridge', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('user_enabled,worker_enabled', [(True, True), (False, False)])
def test_bridge_forwards_domain_lists_flags_and_upstream_options(
        tmp_path, monkeypatch, user_enabled, worker_enabled):
    bridge = load_bridge()
    captured = []

    class Runtime:
        def __init__(self, app_dir, **kwargs):
            self.log_file = Path(app_dir) / 'proxy.log'
            self.running = False

        def reset_log_file(self): pass
        def setup_logging(self, **kwargs): pass
        def start_proxy(self, cfg):
            captured.append(cfg)
            self.running = True
            return True
        def wait_until_ready(self, timeout): return True
        def is_proxy_running(self): return self.running
        def stop_proxy(self): self.running = False

    monkeypatch.setattr(bridge, 'ProxyAppRuntime', Runtime)
    bridge.start_proxy(
        str(tmp_path), '127.0.0.1', 1443, '00' * 16,
        ['2:149.154.167.220'], 5.0, 256, 4, False, True,
        ['one.example', 'two.example'], user_enabled,
        ['worker-one.example', 'worker-two.example'], worker_enabled,
        True, True, 'tls.example', True,
    )
    assert captured[0]['cfproxy_user_domain'] == ['one.example', 'two.example']
    assert captured[0]['cfproxy_user_domain_enabled'] is user_enabled
    assert captured[0]['cfproxy_worker_domain'] == ['worker-one.example', 'worker-two.example']
    assert captured[0]['cfproxy_worker_enabled'] is worker_enabled
    assert captured[0]['no_secure'] is True
    assert captured[0]['force_test_dc'] is True
    assert captured[0]['fake_tls_domain'] == 'tls.example'
    assert captured[0]['proxy_protocol'] is True
    bridge.stop_proxy()


def test_bridge_converts_chaquopy_style_domain_lists(tmp_path, monkeypatch):
    bridge = load_bridge()
    captured = []

    class JavaList:
        def __init__(self, values):
            self.values = values
        def size(self): return len(self.values)
        def get(self, index): return self.values[index]

    class Runtime:
        def __init__(self, app_dir, **kwargs):
            self.log_file = Path(app_dir) / 'proxy.log'
            self.running = False
        def reset_log_file(self): pass
        def setup_logging(self, **kwargs): pass
        def start_proxy(self, cfg):
            captured.append(cfg)
            self.running = True
            return True
        def wait_until_ready(self, timeout): return True
        def is_proxy_running(self): return self.running
        def stop_proxy(self): self.running = False

    monkeypatch.setattr(bridge, 'ProxyAppRuntime', Runtime)
    bridge.start_proxy(
        str(tmp_path), '127.0.0.1', 1443, '00' * 16,
        ['2:149.154.167.220'], cfproxy_user_domain=JavaList(['one.example']),
        cfproxy_worker_domain=JavaList(['worker.example']),
    )
    assert captured[0]['cfproxy_user_domain'] == ['one.example']
    assert captured[0]['cfproxy_worker_domain'] == ['worker.example']
    bridge.stop_proxy()


def test_bridge_start_stats_stop(tmp_path, monkeypatch):
    bridge = load_bridge()
    class Runtime:
        def __init__(self, app_dir, **kwargs):
            self.log_file = Path(app_dir) / 'proxy.log'
            self.config = None
            self.running = False
        def reset_log_file(self): pass
        def setup_logging(self, **kwargs): pass
        def start_proxy(self, cfg):
            self.config = cfg
            self.running = True
            return True
        def wait_until_ready(self, timeout): return True
        def is_proxy_running(self): return self.running
        def stop_proxy(self): self.running = False
    monkeypatch.setattr(bridge, 'ProxyAppRuntime', Runtime)
    assert bridge.start_proxy(str(tmp_path), '127.0.0.1', 1443, '00' * 16,
                              ['2:149.154.167.220'], cfproxy_user_domain='a.example.com')
    assert json.loads(bridge.get_runtime_stats_json())['running']
    bridge.stop_proxy()
    assert not json.loads(bridge.get_runtime_stats_json())['running']


def test_bridge_reports_immediate_failure(tmp_path, monkeypatch):
    bridge = load_bridge()
    class Runtime:
        def __init__(self, *args, **kwargs): pass
        def reset_log_file(self): pass
        def setup_logging(self, **kwargs): pass
        def start_proxy(self, cfg): return False
    monkeypatch.setattr(bridge, 'ProxyAppRuntime', Runtime)
    import pytest
    with pytest.raises(RuntimeError):
        bridge.start_proxy(str(tmp_path), '127.0.0.1', 1443, '00' * 16, [])


def test_bridge_selects_android_crypto_before_core_import(monkeypatch):
    monkeypatch.delenv('TG_WS_PROXY_CRYPTO_BACKEND', raising=False)
    load_bridge()
    import os
    assert os.environ['TG_WS_PROXY_CRYPTO_BACKEND'] == 'python'


def test_bridge_does_not_report_alive_thread_before_bind(tmp_path, monkeypatch):
    bridge = load_bridge()
    ready = threading.Event()
    entered = threading.Event()
    class SlowRuntime:
        def __init__(self, app_dir, **kwargs):
            self.log_file = Path(app_dir) / 'proxy.log'
            self.running = True
        def reset_log_file(self): pass
        def setup_logging(self, **kwargs): pass
        def start_proxy(self, cfg): return True
        def is_proxy_running(self): return self.running
        def wait_until_ready(self, timeout):
            entered.set()
            return ready.wait(timeout)
        def stop_proxy(self): self.running = False
    monkeypatch.setattr(bridge, 'ProxyAppRuntime', SlowRuntime)
    result = []
    thread = threading.Thread(target=lambda: result.append(bridge.start_proxy(
        str(tmp_path), '127.0.0.1', 1443, '00' * 16, ['2:149.154.167.220'])))
    thread.start()
    assert entered.wait(3)
    assert thread.is_alive()
    assert not result
    ready.set()
    thread.join(3)
    assert result
    bridge.stop_proxy()
