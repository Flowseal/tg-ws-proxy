import asyncio

import proxy.config as core_config
from proxy.app_runtime import ProxyAppRuntime


class FakeThread:
    def __init__(self, target, args=(), **kwargs):
        self.alive = False

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.alive = False


def test_mapping_and_singleton(tmp_path):
    original = dict(core_config.proxy_config.__dict__)
    runtime = ProxyAppRuntime(tmp_path, thread_factory=FakeThread)
    singleton = core_config.proxy_config
    try:
        assert runtime.start_proxy({
            'dc_ip': ['2:149.154.167.220'], 'buf_kb': 64, 'pool_size': 3,
            'cfproxy': True, 'cfproxy_user_domain': 'a.example.com, b.example.com',
            'cfproxy_user_domain_enabled': False,
            'cfproxy_worker_domain': ['worker.example.com'],
            'cfproxy_worker_enabled': True, 'no_secure': True,
            'force_test_dc': True, 'fake_tls_domain': 'tls.example.com',
            'proxy_protocol': True,
        })
        assert core_config.proxy_config is singleton
        assert singleton.buffer_size == 64 * 1024
        assert singleton.pool_size == 3
        assert runtime.config['cfproxy_user_domain'] == 'a.example.com, b.example.com'
        assert singleton.cfproxy_user_domains == []
        assert singleton.cfproxy_worker_domains == ['worker.example.com']
        assert singleton.disable_secure and singleton.force_test_dc
        assert singleton.fake_tls_domain == 'tls.example.com'
        assert singleton.proxy_protocol
        assert not hasattr(singleton, 'fallback_cfproxy_priority')
    finally:
        runtime.stop_proxy()
        singleton.__dict__.update(original)


def test_enabled_domains_and_repeat_start_stop(tmp_path):
    original = dict(core_config.proxy_config.__dict__)
    runtime = ProxyAppRuntime(tmp_path, thread_factory=FakeThread)
    try:
        cfg = {'dc_ip': ['2:149.154.167.220'],
               'cfproxy_user_domain': 'a.example.com; a.example.com',
               'cfproxy_user_domain_enabled': True,
               'cfproxy_worker_domain': ['worker.example.com'],
               'cfproxy_worker_enabled': False}
        assert runtime.start_proxy(cfg)
        assert runtime.start_proxy(cfg)
        assert runtime.is_proxy_running()
        assert core_config.proxy_config.cfproxy_user_domains == ['a.example.com']
        assert core_config.proxy_config.cfproxy_worker_domains == []
        assert runtime.config['cfproxy_worker_domain'] == ['worker.example.com']
        runtime.stop_proxy()
        runtime.stop_proxy()
        assert not runtime.is_proxy_running()
    finally:
        core_config.proxy_config.__dict__.update(original)


def test_invalid_dc_does_not_mutate_core(tmp_path):
    original = dict(core_config.proxy_config.__dict__)
    errors = []
    runtime = ProxyAppRuntime(tmp_path, thread_factory=FakeThread,
                              on_error=errors.append)
    for entries in ([], ['broken'], ['2:not-an-ip']):
        assert not runtime.start_proxy({'dc_ip': entries})
        assert core_config.proxy_config.__dict__ == original
    assert len(errors) == 3


def test_bind_failure_and_stop_event(tmp_path):
    errors = []

    async def fail(stop_event):
        assert isinstance(stop_event, asyncio.Event)
        raise OSError('address already in use')

    runtime = ProxyAppRuntime(tmp_path, run_proxy=fail, on_error=errors.append)
    runtime._run_proxy_thread()
    assert 'Порт уже используется' in errors[0]
    assert not runtime.is_proxy_running()
    runtime.stop_proxy()
