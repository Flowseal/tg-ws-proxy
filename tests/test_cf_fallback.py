import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy import bridge
from proxy.balancer import balancer
from proxy.config import proxy_config

DOMAINS = [f'd{i}.example' for i in range(20)]


class _Connects:
    """Fake RawWebSocket.connect that records load on the network."""

    def __init__(self, ok_hosts=(), delay=0.02):
        self.ok_hosts = set(ok_hosts)
        self.delay = delay
        self.total = 0
        self.in_flight = 0
        self.peak = 0
        self.hosts = []

    async def __call__(self, host, *args, **kwargs):
        self.total += 1
        self.hosts.append(host)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        if host in self.ok_hosts:
            return Mock(send=AsyncMock())
        raise asyncio.TimeoutError()


class CfFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge.reset_tcp_backoff()
        self.addCleanup(bridge.reset_tcp_backoff)
        saved = (list(balancer.domains), dict(balancer._dc_to_domain))
        self.addCleanup(self._restore_balancer, saved)
        balancer.domains = []
        balancer.update_domains_list(DOMAINS)
        patcher = patch.object(bridge, 'bridge_ws_reencrypt', AsyncMock())
        self.bridged = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _restore_balancer(saved):
        balancer.domains, balancer._dc_to_domain = saved

    async def cf(self, dc=2):
        return await bridge._cfproxy_fallback(
            Mock(), Mock(), b'init', 'test', Mock(), dc=dc, is_media=False)

    async def clients(self, n, dc=2, gap=0.002):
        tasks = []
        for _ in range(n):
            tasks.append(asyncio.create_task(self.cf(dc)))
            await asyncio.sleep(gap)
        return await asyncio.gather(*tasks)

    async def test_dead_cf_does_not_storm(self):
        connects = _Connects()
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            results = await self.clients(40)
        self.assertFalse(any(results))
        # one full sweep of the domain list plus a bounded first burst
        self.assertLessEqual(connects.peak, bridge.CF_CONNECT_LIMIT)
        self.assertLessEqual(
            connects.total, len(DOMAINS) + bridge.CF_CONNECT_LIMIT)

    async def test_backoff_skips_connects_and_doubles(self):
        connects = _Connects(delay=0)
        key = ('cf', 2)
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            for delay in (30, 60, 120, 240, 480, 960, 1920, 3600, 3600):
                bridge._cf_retry_after[key] = 0
                start = time.monotonic()
                self.assertFalse(await self.cf())
                remaining = bridge._cf_retry_after[key] - start
                self.assertGreaterEqual(remaining, delay - .001)
                self.assertLess(remaining, delay + 1)
                count = connects.total
                self.assertFalse(await self.cf())
                self.assertEqual(connects.total, count)
        self.assertFalse(bridge._cf_probing)

    async def test_retry_after_backoff_is_single_flight(self):
        connects = _Connects()
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            self.assertFalse(await self.cf())
            bridge._cf_retry_after[('cf', 2)] = 0
            connects.total = connects.peak = 0
            await self.clients(40)
        self.assertEqual(connects.peak, 1)
        self.assertLessEqual(connects.total, len(DOMAINS))

    async def test_healthy_cf_serves_all_clients(self):
        current = balancer._dc_to_domain[2]
        connects = _Connects(ok_hosts={f'kws2.{current}'})
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            results = await self.clients(40)
        self.assertTrue(all(results))
        self.assertEqual(connects.total, 40)
        self.assertEqual(self.bridged.await_count, 40)
        self.assertFalse(bridge._cf_failures)

    async def test_sweep_finds_working_domain_and_recovers(self):
        current = balancer._dc_to_domain[2]
        good = next(d for d in DOMAINS if d != current)
        connects = _Connects(ok_hosts={f'kws2.{good}'}, delay=0)
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            self.assertTrue(await self.cf())
            self.assertEqual(balancer._dc_to_domain[2], good)
            self.assertNotIn(('cf', 2), bridge._cf_retry_after)
            connects.total = 0
            self.assertTrue(await self.cf())
        self.assertEqual(connects.total, 1)

    async def test_success_clears_backoff(self):
        key = ('cf', 2)
        bridge._cf_failures[key] = 5
        bridge._cf_retry_after[key] = 0
        current = balancer._dc_to_domain[2]
        connects = _Connects(ok_hosts={f'kws2.{current}'}, delay=0)
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            self.assertTrue(await self.cf())
        self.assertNotIn(key, bridge._cf_failures)
        self.assertNotIn(key, bridge._cf_retry_after)

    async def test_other_dc_is_not_blocked(self):
        bridge._cf_retry_after[('cf', 2)] = time.monotonic() + 60
        current = balancer._dc_to_domain[4]
        connects = _Connects(ok_hosts={f'kws4.{current}'}, delay=0)
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            self.assertFalse(await self.cf(2))
            self.assertTrue(await self.cf(4))
        self.assertEqual(connects.total, 1)

    async def test_clients_stuck_on_old_domain_do_not_sweep_again(self):
        current = balancer._dc_to_domain[2]
        good = {f'kws2.{d}' for d in DOMAINS if d != current}
        connects = _Connects(ok_hosts=good, delay=0.05)
        results = []

        async def client():
            started = time.monotonic()
            results.append((started, await self.cf()))

        with patch.object(bridge.RawWebSocket, 'connect', connects):
            tasks = []
            for _ in range(60):
                tasks.append(asyncio.create_task(client()))
                await asyncio.sleep(0.005)
            await asyncio.gather(*tasks)

        # dead preferred domain: one burst of CF_CONNECT_LIMIT, then one sweep
        dead = connects.hosts.count(f'kws2.{current}')
        self.assertLessEqual(dead, bridge.CF_CONNECT_LIMIT)
        # the sweep needs two connects (0.1 s); nobody arriving later may fail
        first = min(started for started, _ in results)
        late = [ok for started, ok in results if started > first + 0.15]
        self.assertTrue(late)
        self.assertTrue(all(late))

    async def test_cancelled_sweep_releases_probe_slot(self):
        connects = _Connects(delay=0.05)
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            task = asyncio.create_task(self.cf())
            await asyncio.sleep(0.12)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(bridge._cf_probing)
        self.assertEqual(connects.in_flight, 0)


class CfWorkerFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge.reset_tcp_backoff()
        self.addCleanup(bridge.reset_tcp_backoff)
        saved = (proxy_config.cfproxy_worker_domains, proxy_config.pool_size)
        self.addCleanup(self._restore, saved)
        proxy_config.cfproxy_worker_domains = ['w1.example', 'w2.example']
        proxy_config.pool_size = 0  # no worker pool, direct connects only

    @staticmethod
    def _restore(saved):
        proxy_config.cfproxy_worker_domains, proxy_config.pool_size = saved

    async def worker(self):
        return await bridge._cfproxy_worker_fallback(
            Mock(), Mock(), b'init', 'test', Mock(),
            dc=2, is_test_dc=False, is_media=False, fallback_dst='192.0.2.1')

    async def test_dead_workers_do_not_storm(self):
        connects = _Connects()
        with patch.object(bridge.RawWebSocket, 'connect', connects):
            tasks = []
            for _ in range(40):
                tasks.append(asyncio.create_task(self.worker()))
                await asyncio.sleep(0.002)
            results = await asyncio.gather(*tasks)
            count = connects.total
            self.assertFalse(await self.worker())
        self.assertFalse(any(results))
        self.assertEqual(connects.total, count)
        self.assertLessEqual(connects.peak, bridge.CF_CONNECT_LIMIT)
        self.assertLessEqual(count, 2 + bridge.CF_CONNECT_LIMIT)

    async def test_half_dead_workers_settle_on_the_working_one(self):
        # available_domains() shuffles on every call, so the working domain
        # has to be remembered or half of the clients keep hitting the dead one
        connects = _Connects(ok_hosts={'w2.example'})
        bursts = []
        with patch.object(bridge.RawWebSocket, 'connect', connects), \
                patch.object(bridge, 'bridge_ws_reencrypt', AsyncMock()):
            for _ in range(4):
                tasks = []
                for _ in range(20):
                    tasks.append(asyncio.create_task(self.worker()))
                    await asyncio.sleep(0.002)
                bursts.append(sum(await asyncio.gather(*tasks)))
        self.assertEqual(bursts[1:], [20, 20, 20])


if __name__ == '__main__':
    unittest.main()
