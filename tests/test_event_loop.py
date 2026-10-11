import asyncio
import signal
import socket
import sys
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from proxy import event_loop


async def _wait_for_replacement(loop, old_writer, timeout=1):
    async def wait():
        while loop._csock is old_writer:
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), timeout)


def _wake_later(loop, callback):
    def send():
        time.sleep(.03)
        loop.call_soon_threadsafe(callback)
    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    return thread


@unittest.skipUnless(sys.platform == 'win32', 'Windows proactor self-pipe')
class WindowsSelfPipeTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.ProactorEventLoop()
        self.addCleanup(self.loop.close)

    def test_eof_replaces_sockets_and_stops_idle_callbacks(self):
        loop = self.loop
        read = Mock(wraps=loop._loop_self_reading)
        loop._loop_self_reading = read

        async def run():
            # Install after the original callback has armed a read, as can
            # happen when the proxy starts in an already running event loop.
            await asyncio.sleep(.01)
            event_loop.install_self_pipe_guard(loop)
            callback = loop._loop_self_reading
            event_loop.install_self_pipe_guard(loop)
            self.assertIs(loop._loop_self_reading, callback)
            old_reader, old_writer = loop._ssock, loop._csock
            internal_fds = loop._internal_fds
            with patch.object(event_loop.socket, 'socketpair', wraps=socket.socketpair) as pair:
                old_writer.shutdown(socket.SHUT_WR)
                await _wait_for_replacement(loop, old_writer)
                self.assertEqual(pair.call_count, 1)
            self.assertEqual(old_reader.fileno(), -1)
            self.assertEqual(old_writer.fileno(), -1)
            self.assertEqual(loop._internal_fds, internal_fds)
            self.assertFalse(loop._self_reading_future.done())
            read.reset_mock()
            await asyncio.sleep(.05)
            read.assert_not_called()

            completed = loop.create_future()
            thread = _wake_later(loop, lambda: completed.set_result(True))
            started = time.monotonic()
            try:
                self.assertTrue(await asyncio.wait_for(completed, 1))
                self.assertLess(time.monotonic() - started, .5)
            finally:
                thread.join(timeout=1)

        with self.assertLogs(event_loop.log, 'WARNING') as messages:
            loop.run_until_complete(run())
        self.assertEqual(len(messages.output), 1)
        self.assertIn('EOF', messages.output[0])

    def test_repeated_eof_preserves_existing_tcp_connection(self):
        async def run():
            loop = self.loop
            event_loop.install_self_pipe_guard(loop)
            finished = loop.create_future()

            async def echo(reader, writer):
                try:
                    while True:
                        data = await reader.read(128)
                        if not data:
                            break
                        writer.write(data)
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
                    finished.set_result(None)

            server = await asyncio.start_server(echo, '127.0.0.1', 0)
            writer = None
            try:
                port = server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                for index in range(5):
                    old_writer = loop._csock
                    old_writer.shutdown(socket.SHUT_WR)
                    await _wait_for_replacement(loop, old_writer)
                    payload = f'packet {index}'.encode()
                    writer.write(payload)
                    await writer.drain()
                    data = await asyncio.wait_for(reader.readexactly(len(payload)), 1)
                    self.assertEqual(data, payload)
            finally:
                if writer is not None:
                    writer.close()
                    await writer.wait_closed()
                    await asyncio.wait_for(finished, 1)
                server.close()
                await server.wait_closed()

        with self.assertLogs(event_loop.log, 'WARNING'):
            self.loop.run_until_complete(run())

    def test_reset_rebuilds_but_cancelled_and_stale_callbacks_do_not(self):
        async def run():
            loop = self.loop
            event_loop.install_self_pipe_guard(loop)
            await asyncio.sleep(0)
            old_writer = loop._csock
            pending = loop._self_reading_future
            pending.cancel()
            failed = loop.create_future()
            failed.set_exception(ConnectionResetError('self-pipe reset'))
            loop._self_reading_future = failed
            loop._loop_self_reading(failed)
            self.assertIsNot(loop._csock, old_writer)
            replacement = loop._csock
            with patch.object(loop, 'call_exception_handler') as report:
                loop._loop_self_reading(failed)  # queued callback from the old pipe
                loop._loop_self_reading(pending)  # cancellation during shutdown
                report.assert_not_called()
            self.assertIs(loop._csock, replacement)
            await asyncio.sleep(.01)

        with self.assertLogs(event_loop.log, 'WARNING') as messages:
            self.loop.run_until_complete(run())
        self.assertEqual(len(messages.output), 1)

    def test_reset_on_read_armed_before_installation_is_recovered(self):
        async def run():
            loop = self.loop
            await asyncio.sleep(.01)
            event_loop.install_self_pipe_guard(loop)
            old_writer = loop._csock
            loop._self_reading_future.set_exception(ConnectionResetError('self-pipe reset'))
            await _wait_for_replacement(loop, old_writer)
            self.assertFalse(loop._self_reading_future.done())

        with self.assertLogs(event_loop.log, 'WARNING') as messages:
            self.loop.run_until_complete(run())
        self.assertEqual(len(messages.output), 1)

    def test_recovery_in_background_proxy_thread(self):
        errors = []

        async def run():
            loop = asyncio.get_running_loop()
            event_loop.install_self_pipe_guard(loop)
            await asyncio.sleep(0)
            old_writer = loop._csock
            old_writer.shutdown(socket.SHUT_WR)
            await _wait_for_replacement(loop, old_writer)
            self.assertFalse(loop._self_reading_future.done())

        def worker():
            try:
                asyncio.run(run())
            except BaseException as exc:
                errors.append(exc)

        with self.assertLogs(event_loop.log, 'WARNING'):
            thread = threading.Thread(target=worker, daemon=True)
            thread.start()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)

    def test_allocation_failure_retries_without_spinning(self):
        async def run():
            loop = self.loop
            event_loop.install_self_pipe_guard(loop)
            await asyncio.sleep(0)
            original_pair = socket.socketpair
            attempts = 0

            def allocate():
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError('temporarily out of sockets')
                return original_pair()

            with patch.object(event_loop.socket, 'socketpair', side_effect=allocate):
                old_reader, old_writer = loop._ssock, loop._csock
                old_writer.shutdown(socket.SHUT_WR)
                await asyncio.sleep(.05)
                self.assertEqual(attempts, 1)
                self.assertIs(loop._csock, old_writer)
                self.assertNotEqual(old_reader.fileno(), -1)
                self.assertIsNone(loop._self_reading_future)
                await _wait_for_replacement(loop, old_writer, timeout=2)
                self.assertEqual(attempts, 2)

        with self.assertLogs(event_loop.log, 'WARNING'):
            self.loop.run_until_complete(run())

    def test_signal_wakeup_fd_is_moved_only_when_owned_by_the_loop(self):
        async def run():
            loop = self.loop
            event_loop.install_self_pipe_guard(loop)
            await asyncio.sleep(0)
            external_reader, external_writer = socket.socketpair()
            external_writer.setblocking(False)
            try:
                for external in (False, True):
                    with self.subTest(external=external):
                        if external:
                            signal.set_wakeup_fd(external_writer.fileno())
                        old_writer = loop._csock
                        old_writer.shutdown(socket.SHUT_WR)
                        await _wait_for_replacement(loop, old_writer)
                        fd = signal.set_wakeup_fd(-1)
                        expected = external_writer if external else loop._csock
                        self.assertEqual(fd, expected.fileno())
                        signal.set_wakeup_fd(loop._csock.fileno())
            finally:
                external_reader.close()
                external_writer.close()

        with self.assertLogs(event_loop.log, 'WARNING'):
            self.loop.run_until_complete(run())


@unittest.skipUnless(sys.platform == 'win32', 'Windows proactor self-pipe')
class ProxySelfPipeTest(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_without_h2_can_stop_from_thread_after_eof(self):
        from proxy import tg_ws_proxy
        from proxy.config import ProxyConfig

        config = ProxyConfig(port=0, dc_redirects={}, pool_size=0, cfproxy_h2_media=False)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        with patch.object(tg_ws_proxy, 'proxy_config', config), \
                patch.object(tg_ws_proxy, 'start_cfproxy_domain_refresh'), \
                patch.object(tg_ws_proxy.ws_pool, 'warmup', AsyncMock()), \
                patch.object(tg_ws_proxy.cf_worker_pool, 'warmup', AsyncMock()), \
                self.assertLogs(event_loop.log, 'WARNING'):
            task = asyncio.create_task(tg_ws_proxy._run(stop))
            thread = None
            try:
                async def listening():
                    while tg_ws_proxy._server_instance is None:
                        await asyncio.sleep(.005)
                await asyncio.wait_for(listening(), 1)
                self.assertIsNone(tg_ws_proxy.cf_h2_pool)
                old_writer = loop._csock
                old_writer.shutdown(socket.SHUT_WR)
                await _wait_for_replacement(loop, old_writer)
                thread = _wake_later(loop, stop.set)
                started = time.monotonic()
                await asyncio.wait_for(task, 1)
                self.assertLess(time.monotonic() - started, .5)
                self.assertIsNone(tg_ws_proxy._server_instance)
            finally:
                if thread is not None:
                    thread.join(timeout=1)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
