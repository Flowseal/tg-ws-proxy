import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy import bridge


class TcpFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        bridge.reset_tcp_backoff()
        self.addCleanup(bridge.reset_tcp_backoff)
        self.key = ('192.0.2.1', 443)
        self.remote = Mock(drain=AsyncMock(), wait_closed=AsyncMock())

    async def fallback(self, dst='192.0.2.1', port=443):
        writer = Mock()
        writer.is_closing.return_value = False
        return await bridge._tcp_fallback(
            asyncio.StreamReader(), writer, dst, port, b'init', 'test', Mock())

    async def test_failure_backoff_doubles_caps_and_skips_new_connections(self):
        with patch.object(bridge.asyncio, 'open_connection',
                          AsyncMock(side_effect=asyncio.TimeoutError())) as connect:
            for delay in (30, 60, 120, 240, 480, 960, 1920, 3600, 3600):
                bridge._tcp_retry_after[self.key] = 0
                start = time.monotonic()
                self.assertFalse(await self.fallback())
                remaining = bridge._tcp_retry_after[self.key] - start
                self.assertGreaterEqual(remaining, delay - .001)
                self.assertLess(remaining, delay + 1)
                count = connect.await_count
                self.assertFalse(await self.fallback())
                self.assertEqual(connect.await_count, count)
        self.assertFalse(bridge._tcp_connecting)

    async def test_other_ips_and_ports_are_not_blocked(self):
        bridge._tcp_retry_after[self.key] = time.monotonic() + 60
        with patch.object(bridge.asyncio, 'open_connection', AsyncMock(
                return_value=(Mock(), self.remote))) as connect, \
                patch.object(bridge, '_bridge_tcp_reencrypt', AsyncMock()):
            self.assertTrue(await self.fallback('192.0.2.2'))
            self.assertTrue(await self.fallback(port=80))
        self.assertEqual(connect.await_count, 2)

    async def test_success_clears_backoff_and_allows_following_sessions(self):
        bridge._tcp_failures[self.key] = 5
        bridge._tcp_retry_after[self.key] = 0
        with patch.object(bridge.asyncio, 'open_connection', AsyncMock(
                return_value=(Mock(), self.remote))) as connect, \
                patch.object(bridge, '_bridge_tcp_reencrypt', AsyncMock()):
            self.assertTrue(await self.fallback())
            self.assertNotIn(self.key, bridge._tcp_failures)
            self.assertNotIn(self.key, bridge._tcp_retry_after)
            self.assertTrue(await self.fallback())
        self.assertEqual(connect.await_count, 2)

    async def test_concurrent_requests_only_start_one_connection_attempt(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def connect(*args):
            started.set()
            await release.wait()
            raise ConnectionRefusedError()

        with patch.object(bridge.asyncio, 'open_connection', side_effect=connect) as dial:
            first = asyncio.create_task(self.fallback())
            try:
                await asyncio.wait_for(started.wait(), 1)
                results = await asyncio.gather(*(self.fallback() for _ in range(8)))
                self.assertEqual(results, [False] * 8)
                self.assertEqual(dial.await_count, 1)
                release.set()
                self.assertFalse(await first)
            finally:
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)

    async def test_init_failure_closes_socket_and_enters_backoff(self):
        self.remote.drain.side_effect = ConnectionResetError()
        with patch.object(bridge.asyncio, 'open_connection', AsyncMock(
                return_value=(Mock(), self.remote))), \
                patch.object(bridge, '_bridge_tcp_reencrypt', AsyncMock()) as forward:
            self.assertFalse(await self.fallback())
        self.remote.close.assert_called_once()
        forward.assert_not_awaited()
        self.assertIn(self.key, bridge._tcp_retry_after)

    async def test_cancellation_releases_attempt_without_marking_ip_blocked(self):
        started = asyncio.Event()

        async def drain():
            started.set()
            await asyncio.Future()

        self.remote.drain.side_effect = drain
        with patch.object(bridge.asyncio, 'open_connection', AsyncMock(
                return_value=(Mock(), self.remote))):
            task = asyncio.create_task(self.fallback())
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.remote.close.assert_called_once()
        self.assertFalse(bridge._tcp_connecting)
        self.assertFalse(bridge._tcp_failures)


if __name__ == '__main__':
    unittest.main()
