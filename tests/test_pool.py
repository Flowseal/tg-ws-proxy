import time
import unittest

from collections import deque
from types import SimpleNamespace
from unittest import mock

from proxy.config import proxy_config
from proxy.pool import _WsPool, _CfWorkerPool


class _StopRotation(Exception):
    pass


def _open_ws():
    transport = SimpleNamespace(is_closing=lambda: False)
    writer = SimpleNamespace(transport=transport)
    return SimpleNamespace(_closed=False, writer=writer)


class WsPoolRotationTest(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_refill_and_closes_idle(self):
        for pool, key, schedule_args, idle_entry in (
                (_WsPool(), (2, False), ((2, False), 'ip', ['domain']),
                 lambda ws: (ws, time.monotonic())),
                (_CfWorkerPool(), 2, (2, 'ip', ['domain']),
                 lambda ws: (ws, time.monotonic(), 'domain'))):
            closed = []
            ws = _open_ws()
            ws.close = lambda: _close(closed)
            pool._idle[key] = deque([idle_entry(ws)])
            started = __import__('asyncio').Event()
            async def blocked(*_args):
                started.set()
                await __import__('asyncio').Future()
            with mock.patch.object(pool, '_refill', blocked):
                pool._schedule_refill(*schedule_args)
                await started.wait()
                await pool.shutdown()
            self.assertEqual(closed, [True])
            self.assertFalse(pool._idle)
            self.assertFalse(pool._refilling)

    async def test_refills_partially_populated_bucket(self):
        pool = _WsPool()
        key = (2, False)
        pool._idle[key] = deque([
            (_open_ws(), time.monotonic()),
            (_open_ws(), time.monotonic()),
        ])
        sleep_calls = 0

        async def stop_after_one_iteration(_delay):
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls > 1:
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


if __name__ == '__main__':
    unittest.main()


async def _close(closed):
    closed.append(True)
