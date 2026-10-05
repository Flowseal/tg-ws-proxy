import asyncio
import time
import unittest

from collections import deque
from types import SimpleNamespace
from unittest import mock

from proxy.config import proxy_config
from proxy.pool import _WsPool
from proxy.raw_websocket import RawWebSocket, WsHandshakeError
from proxy.utils import WS_DC_NAMES, ws_domains


class _StopRotation(Exception):
    pass


def _open_ws():
    transport = SimpleNamespace(is_closing=lambda: False)
    writer = mock.Mock(transport=transport, drain=mock.AsyncMock(),
                       wait_closed=mock.AsyncMock())
    return RawWebSocket(asyncio.StreamReader(), writer)


class WsPoolRotationTest(unittest.IsolatedAsyncioTestCase):
    async def test_warmup_preserves_media_ws_with_or_without_h2(self):
        for cf, secure, opted_out, media in [(True, True, False, True),
                                            (False, True, False, True),
                                            (True, False, False, True),
                                            (True, True, True, True)]:
            with self.subTest(cf=cf, secure=secure, opted_out=opted_out), \
                    mock.patch.object(proxy_config, 'dc_redirects', {2: '149.154.167.220'}), \
                    mock.patch.object(proxy_config, 'fallback_cfproxy', cf), \
                    mock.patch.object(proxy_config, 'disable_secure', not secure), \
                    mock.patch.object(proxy_config, 'cfproxy_h2_media', not opted_out), \
                    mock.patch.object(proxy_config, 'force_test_dc', False):
                pool = _WsPool()
                with mock.patch.object(pool, '_schedule_refill') as refill:
                    await pool.warmup()
                keys = [call.args[0] for call in refill.call_args_list]
                self.assertIn((2, False, False), keys)
                self.assertEqual((2, True, False) in keys, media)

    async def test_refills_partially_populated_bucket(self):
        pool = _WsPool()
        key = (2, False, False)
        pool._idle[key] = deque([
            (_open_ws(), time.monotonic()),
            (_open_ws(), time.monotonic()),
        ])
        async def stop_after_one_iteration(_delay):
            raise _StopRotation

        with mock.patch.object(proxy_config, 'pool_size', 4):
            with mock.patch(
                    'proxy.pool.asyncio.sleep',
                    side_effect=stop_after_one_iteration):
                with mock.patch.object(
                        pool, '_schedule_refill') as schedule_refill:
                    with self.assertRaises(_StopRotation):
                        await pool._rotate(
                            key, '149.154.167.220', ['example.com'])

        schedule_refill.assert_called_once_with(
            key, '149.154.167.220', ['example.com'])


class WsPoolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = _WsPool()
        self.key = (2, False, False)
        self.pool.WS_POOL_CHECK_INTERVAL = .01
        for name, value in [('dc_redirects', {2: '192.0.2.1'}),
                            ('pool_size', 1), ('force_test_dc', False)]:
            patcher = mock.patch.object(proxy_config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addAsyncCleanup(self.pool.close)

    async def wait_for(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(.001)
        await asyncio.wait_for(wait(), 1)

    async def test_regular_sessions_never_use_media_domain(self):
        for dc in (2, 4):
            with self.subTest(dc=dc), mock.patch(
                    'proxy.pool.RawWebSocket.connect',
                    side_effect=WsHandshakeError(302, 'redirect')) as connect:
                self.assertIsNone(await self.pool._connect_one('192.0.2.1', ws_domains(dc, False)))
                self.assertEqual([call.args[1] for call in connect.call_args_list],
                                 [f'kws{dc}.web.telegram.org'] * 2
                                 + [f'{WS_DC_NAMES[dc]}.web.telegram.org'] * 2)

    async def test_media_tries_regular_domain_after_both_modes_fail(self):
        for error in (asyncio.TimeoutError(), ConnectionResetError(),
                      WsHandshakeError(302, 'redirect'), WsHandshakeError(500, 'error')):
            with self.subTest(error=type(error)), mock.patch(
                    'proxy.pool.RawWebSocket.connect',
                    side_effect=[error, error, _open_ws()]) as connect:
                self.assertIsNotNone(await self.pool._connect_one('192.0.2.1', ws_domains(2, True)))
                self.assertEqual([call.args[1] for call in connect.call_args_list],
                                 ['kws2-1.web.telegram.org'] * 2 + ['kws2.web.telegram.org'])

    async def test_concurrent_preference_change_does_not_skip_fronting(self):
        ws = _open_ws()

        async def connect(*args, **kwargs):
            if kwargs['sni'] is None:
                self.pool.try_fronting_first = True
                raise asyncio.TimeoutError()
            return ws

        with mock.patch('proxy.pool.RawWebSocket.connect', side_effect=connect) as dial:
            self.assertIs(await self.pool._connect_one('192.0.2.1', ws_domains(2, False)), ws)
        self.assertEqual(dial.await_count, 2)

    async def test_miss_returns_without_waiting_and_starts_only_one_refill(self):
        started = asyncio.Event()

        async def connect(*args):
            started.set()
            await asyncio.Future()

        with mock.patch.object(self.pool, '_connect_one', side_effect=connect) as dial:
            for _ in range(10):
                self.assertIsNone(await self.pool.get(2, False))
            await asyncio.wait_for(started.wait(), 1)
            self.assertEqual(dial.await_count, 1)
            await self.pool.close()

    async def test_empty_pool_recovers_in_background_after_backoff(self):
        ws = _open_ws()
        self.pool.REFILL_BACKOFF_INITIAL = .02
        with mock.patch.object(self.pool, '_connect_one',
                               side_effect=[None, ws]) as dial:
            self.assertIsNone(await self.pool.get(2, False))
            await self.wait_for(lambda: self.key in self.pool._refill_after)
            self.assertEqual(dial.await_count, 1)
            # No new client requests are needed to resume refilling.
            await self.wait_for(lambda: bool(self.pool._idle[self.key]))
            self.assertEqual(dial.await_count, 2)
            self.assertNotIn(self.key, self.pool._refill_failures)
            await self.pool.close()

    async def test_ready_connection_is_available_before_slower_attempt(self):
        ready = _open_ws()
        slow_started = asyncio.Event()
        calls = 0

        async def connect(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                slow_started.set()
                await asyncio.Future()
            return ready

        with mock.patch.object(proxy_config, 'pool_size', 2), \
                mock.patch.object(self.pool, '_connect_one', side_effect=connect):
            await self.pool.get(2, False)
            await asyncio.wait_for(slow_started.wait(), 1)
            await self.wait_for(lambda: bool(self.pool._idle[self.key]))
            self.assertIs(await self.pool.get(2, False), ready)
            await self.pool.close()

    async def test_stale_connections_are_skipped(self):
        for state in ('expired', 'eof', 'exception', 'closed', 'closing'):
            with self.subTest(state=state):
                stale, good = _open_ws(), _open_ws()
                created = time.monotonic()
                if state == 'expired':
                    created -= self.pool.WS_POOL_MAX_AGE
                elif state == 'eof':
                    stale.reader.feed_eof()
                elif state == 'exception':
                    stale.reader.set_exception(ConnectionResetError())
                elif state == 'closed':
                    stale._closed = True
                else:
                    stale.writer.transport.is_closing = lambda: True
                self.pool._idle[self.key] = deque([(stale, created), (good, time.monotonic())])
                with mock.patch.object(self.pool, '_schedule_refill'):
                    self.assertIs(await self.pool.get(2, False), good)
                await asyncio.sleep(0)

    async def test_hit_does_not_reset_failed_refill_backoff(self):
        self.pool._idle[self.key] = deque([(_open_ws(), time.monotonic())])
        self.pool._refill_failures[self.key] = 3
        self.pool._refill_after[self.key] = time.monotonic() + 60
        with mock.patch.object(self.pool, '_schedule_rotation'):
            self.assertIsNotNone(await self.pool.get(2, False))
        self.assertEqual(self.pool._refill_failures[self.key], 3)
        self.assertFalse(self.pool._refilling)

    async def test_zero_size_and_unconfigured_dc_do_not_connect(self):
        with mock.patch.object(self.pool, '_connect_one') as dial:
            self.assertIsNone(await self.pool.get(4, False))
            with mock.patch.object(proxy_config, 'pool_size', 0):
                await self.pool.warmup()
                self.assertIsNone(await self.pool.get(2, False))
            self.assertFalse(self.pool._rotating)
            self.assertFalse(self.pool._refilling)
            dial.assert_not_called()

    async def test_test_dc_has_separate_pool_and_path(self):
        prod = _open_ws()
        self.pool._idle[self.key] = deque([(prod, time.monotonic())])
        with mock.patch.object(self.pool, '_connect_one', return_value=_open_ws()) as dial:
            self.assertIsNone(await self.pool.get(2, False, is_test_dc=True))
            test_key = (2, False, True)
            await self.wait_for(lambda: bool(self.pool._idle.get(test_key)))
            dial.assert_awaited_once_with('192.0.2.1', ws_domains(2, False), '/apiws_test')
            self.assertIs(self.pool._idle[self.key][0][0], prod)
            await self.pool.close()

    async def test_close_cancels_refill_and_closes_unclaimed_connections(self):
        ready = _open_ws()
        idle = _open_ws()
        started = asyncio.Event()
        self.pool._idle[self.key] = deque([(idle, time.monotonic())])

        async def connect(*args):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                # Completion raced with shutdown; it must not leak into the pool.
                return ready

        with mock.patch.object(proxy_config, 'pool_size', 2), \
                mock.patch.object(self.pool, '_connect_one', side_effect=connect):
            self.pool._schedule_refill(self.key, '192.0.2.1', ws_domains(2, False))
            await asyncio.wait_for(started.wait(), 1)
            await self.pool.close()
        self.assertFalse(self.pool._idle)
        self.assertFalse(self.pool._rotating)
        self.assertFalse(self.pool._refilling)
        ready.writer.close.assert_called_once()
        idle.writer.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
