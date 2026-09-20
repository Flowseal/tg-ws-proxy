import importlib.util
import json
from pathlib import Path


BRIDGE = Path(__file__).resolve().parents[1] / 'android/app/src/main/python/android_proxy_bridge.py'


def load_bridge():
    spec = importlib.util.spec_from_file_location('android_proxy_bridge', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
