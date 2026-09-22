import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'android/app/src/main/python/android_proxy_bridge.py'


def load_bridge():
    if str(BRIDGE.parent) not in sys.path:
        sys.path.insert(0, str(BRIDGE.parent))
    spec = importlib.util.spec_from_file_location('android_proxy_bridge_update_test', BRIDGE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_check_compares_release_and_uses_fixed_url(monkeypatch):
    bridge = load_bridge()
    calls = []

    def fetch(timeout):
        calls.append(timeout)
        return {'tag_name': 'v1.10.5', 'html_url': 'https://example.com/unsafe.exe'}

    monkeypatch.setattr(bridge, '_fetch_latest_release', fetch)
    idle = json.loads(bridge.get_update_status_json('1.10.4', False))
    assert idle['checked'] is False
    assert calls == []
    status = json.loads(bridge.get_update_status_json('1.10.4', True))
    assert status['checked'] is True
    assert status['has_update'] is True
    assert status['latest'] == '1.10.5'
    assert status['html_url'] == 'https://github.com/Flowseal/tg-ws-proxy/releases/latest'
    assert calls and 0 < calls[0] <= 5


def test_update_check_reports_network_failure_with_safe_link(monkeypatch):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, '_fetch_latest_release',
                        lambda timeout: (_ for _ in ()).throw(TimeoutError('offline')))
    status = json.loads(bridge.get_update_status_json('1.10.4', True))
    assert status['checked'] is True
    assert status['error']
    assert status['html_url'].endswith('/Flowseal/tg-ws-proxy/releases/latest')


@pytest.mark.parametrize(('current', 'latest', 'has_update', 'ahead'), [
    ('1.10.4', 'v1.10.4', False, False),
    ('1.10.5', 'v1.10.4', False, True),
    ('1.10.4', 'v1.10.4.1', True, False),
    ('1.10.4-legacy32', 'v1.10.5', True, False),
])
def test_update_check_version_order(monkeypatch, current, latest, has_update, ahead):
    bridge = load_bridge()
    monkeypatch.setattr(bridge, '_fetch_latest_release',
                        lambda timeout: {'tag_name': latest})
    status = json.loads(bridge.get_update_status_json(current, True))
    assert status['has_update'] is has_update
    assert status['ahead_of_release'] is ahead


def test_release_fetch_bounds_request_and_response(monkeypatch):
    bridge = load_bridge()
    calls = []
    tls_context = object()
    monkeypatch.setattr(bridge.certifi, 'where', lambda: '/bundled/cacert.pem')
    monkeypatch.setattr(bridge.ssl, 'create_default_context',
                        lambda cafile: (calls.append(('cafile', cafile)) or tls_context))

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            calls.append(size)
            return b'{' + b' ' * 65536

    def urlopen(request, timeout, context):
        calls.append((request.full_url, timeout, context))
        return Response()

    monkeypatch.setattr(bridge, 'urlopen', urlopen)
    with pytest.raises(ValueError, match='too large'):
        bridge._fetch_latest_release(5)
    assert calls == [
        ('cafile', '/bundled/cacert.pem'),
        ('https://api.github.com/repos/Flowseal/tg-ws-proxy/releases/latest',
         5, tls_context), 65537]


def test_failed_start_keeps_previous_log(tmp_path, monkeypatch):
    bridge = load_bridge()
    log = tmp_path / 'proxy.log'
    log.write_text('prior evidence\n')

    class Runtime:
        def __init__(self, app_dir, **kwargs):
            self.log_file = log

        def reset_log_file(self):
            log.unlink()

        def setup_logging(self, **kwargs):
            pass

        def start_proxy(self, cfg):
            return False

    monkeypatch.setattr(bridge, 'ProxyAppRuntime', Runtime)
    with pytest.raises(RuntimeError):
        bridge.start_proxy(str(tmp_path), '127.0.0.1', 1443, '00' * 16, [])
    assert log.read_text() == 'prior evidence\n'
