import asyncio

import pytest

from proxy.config import proxy_config
from proxy.stats import stats
from proxy import tg_ws_proxy


@pytest.mark.asyncio
async def test_warmup_failure_releases_listener_and_allows_restart(monkeypatch):
    original = dict(proxy_config.__dict__)
    original_route = stats.last_transport_route
    servers = []
    class Server:
        sockets = []
        closed = False
        def close(self):
            self.closed = True
        async def wait_closed(self):
            pass
        async def serve_forever(self):
            await asyncio.Future()
    async def start_server(*args):
        if servers and not servers[-1].closed:
            raise OSError('previous listener open')
        server = Server()
        servers.append(server)
        return server
    monkeypatch.setattr(tg_ws_proxy.asyncio, 'start_server', start_server)
    proxy_config.host = '127.0.0.1'
    proxy_config.port = 1443
    proxy_config.secret = '00' * 16
    proxy_config.cfproxy_user_domains = ['example.com']
    proxy_config.dc_redirects = {}
    ready = []
    stats.last_transport_route = 'tcp_fallback'
    async def fail():
        raise RuntimeError('warmup failed')
    monkeypatch.setattr(tg_ws_proxy.ws_pool, 'warmup', fail)
    try:
        with pytest.raises(RuntimeError, match='warmup failed'):
            await tg_ws_proxy._run(on_ready=lambda: ready.append(True))
        assert ready == []
        assert tg_ws_proxy._server_instance is None
        stop = asyncio.Event()
        assert servers[0].closed
        monkeypatch.setattr(tg_ws_proxy.ws_pool, 'warmup', lambda: asyncio.sleep(0))
        def mark_ready():
            assert stats.last_transport_route is None
            ready.append(True)
            stop.set()
        await tg_ws_proxy._run(stop, on_ready=mark_ready)
        assert ready == [True]
    finally:
        stats.last_transport_route = original_route
        proxy_config.__dict__.update(original)


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_second', [False, True])
async def test_warmup_stop_or_second_failure_suppresses_ready_and_shuts_pools(
        monkeypatch, fail_second):
    original = dict(proxy_config.__dict__)
    stop = asyncio.Event()
    closed = []
    ready = []
    class Server:
        sockets = []
        def close(self):
            closed.append(True)
        async def wait_closed(self):
            pass
    monkeypatch.setattr(tg_ws_proxy.asyncio, 'start_server',
                        lambda *_args: asyncio.sleep(0, result=Server()))
    proxy_config.secret = '00' * 16
    proxy_config.cfproxy_user_domains = ['example.com']
    async def first():
        if not fail_second:
            stop.set()
    async def second():
        if fail_second:
            raise RuntimeError('second warmup failed')
    monkeypatch.setattr(tg_ws_proxy.ws_pool, 'warmup', first)
    monkeypatch.setattr(tg_ws_proxy.cf_worker_pool, 'warmup', second)
    shutdowns = []
    original_shutdown = tg_ws_proxy.ws_pool.shutdown
    async def shutdown():
        shutdowns.append(True)
        await original_shutdown()
    monkeypatch.setattr(tg_ws_proxy.ws_pool, 'shutdown', shutdown)
    try:
        if fail_second:
            with pytest.raises(RuntimeError, match='second warmup failed'):
                await tg_ws_proxy._run(stop, on_ready=lambda: ready.append(True))
        else:
            await tg_ws_proxy._run(stop, on_ready=lambda: ready.append(True))
        assert ready == []
        assert closed
        assert len(shutdowns) == 2
    finally:
        proxy_config.__dict__.update(original)


def test_transport_counters_do_not_choose_route():
    old = dict(stats.__dict__)
    try:
        stats.last_transport_route = None
        stats.connections_ws += 1
        assert stats.last_transport_route is None
        stats.connections_cfproxy += 1
        assert stats.last_transport_route is None
        stats.connections_tcp_fallback += 1
        assert stats.last_transport_route is None
    finally:
        stats.__dict__.clear()
        stats.__dict__.update(old)
