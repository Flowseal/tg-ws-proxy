import asyncio
import threading
import unittest
from unittest import mock

from utils import tray_common

# Captured before tests patch threading.Thread for the proxy thread
_RealThread = threading.Thread


class CheckUpdateAsyncTest(unittest.TestCase):
    def test_disabled_check_does_not_start_a_thread(self):
        called = []
        with mock.patch.object(tray_common.threading, 'Thread') as thread:
            tray_common.check_update_async(
                {'check_updates': False}, lambda: False, lambda v, u: called.append(v),
            )
        thread.assert_not_called()
        self.assertEqual(called, [])

    def test_update_is_reported_to_the_handler(self):
        status = {'has_update': True, 'latest': '2.0.0', 'html_url': 'https://x.invalid/r'}
        seen = []
        with mock.patch.object(tray_common.threading, 'Thread') as thread, \
                mock.patch.object(tray_common.time, 'sleep'), \
                mock.patch('utils.update_check.run_check'), \
                mock.patch('utils.update_check.get_status', return_value=status):
            tray_common.check_update_async(
                {'check_updates': True}, lambda: False,
                lambda version, url: seen.append((version, url)),
            )
            thread.call_args.kwargs['target']()

        self.assertEqual(seen, [('2.0.0', 'https://x.invalid/r')])

    def test_no_update_means_no_handler_call(self):
        seen = []
        with mock.patch.object(tray_common.threading, 'Thread') as thread, \
                mock.patch.object(tray_common.time, 'sleep'), \
                mock.patch('utils.update_check.run_check'), \
                mock.patch('utils.update_check.get_status',
                           return_value={'has_update': False}):
            tray_common.check_update_async(
                {'check_updates': True}, lambda: False,
                lambda version, url: seen.append(version),
            )
            thread.call_args.kwargs['target']()

        self.assertEqual(seen, [])


class _StoppingThread:
    """A proxy thread whose cleanup outlives stop_proxy()'s join timeout."""

    def __init__(self, finishes_on_join):
        self.alive = True
        self.finishes_on_join = finishes_on_join
        self.joins = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.joins.append(timeout)
        if self.finishes_on_join:
            self.alive = False


class _FakeLoop:
    def __init__(self):
        self.stop_requests = 0

    def call_soon_threadsafe(self, callback):
        self.stop_requests += 1


class ProxyStartupTest(unittest.TestCase):
    def test_restart_before_loop_is_ready_does_not_lose_stop(self):
        entering_loop = threading.Event()
        release_loop = threading.Event()
        restarted = threading.Event()
        new_event_loop = asyncio.new_event_loop
        runs = []
        errors = []

        def delayed_loop():
            entering_loop.set()
            if not release_loop.wait(2):
                raise AssertionError('proxy startup was not released')
            return new_event_loop()

        async def run(stop_event):
            runs.append(stop_event.is_set())
            if len(runs) == 2:
                restarted.set()
            await stop_event.wait()

        with mock.patch.object(tray_common, '_proxy_thread', None), \
                mock.patch.object(tray_common, '_async_stop', None), \
                mock.patch.object(tray_common, '_stop_requested', threading.Event()), \
                mock.patch.object(tray_common, '_lifecycle_version', 0), \
                mock.patch.object(tray_common, 'apply_proxy_config', return_value=True), \
                mock.patch.object(tray_common.asyncio, 'new_event_loop', side_effect=delayed_loop), \
                mock.patch.object(tray_common, '_run', side_effect=run), \
                mock.patch.object(tray_common.time, 'sleep'):
            click = _RealThread(target=tray_common.restart_proxy, args=({}, errors.append))
            try:
                tray_common.start_proxy({}, errors.append)
                self.assertTrue(entering_loop.wait(1))
                click.start()
                self.assertTrue(tray_common._stop_requested.wait(1))
                release_loop.set()
                self.assertTrue(restarted.wait(2))
                click.join(2)
                self.assertFalse(click.is_alive())
                self.assertEqual(runs, [True, False])
                self.assertEqual(errors, [])
            finally:
                release_loop.set()
                tray_common.stop_proxy()
                if click.ident is not None:
                    click.join(3)


class ProxyLifecycleTest(unittest.TestCase):
    """Real start/stop/restart with a fake proxy thread: nothing binds a port."""

    def setUp(self):
        self.created = []
        self.started_beside_alive = 0

        def new_thread(*args, **kwargs):
            current = tray_common._proxy_thread
            if any(t.is_alive() for t in self.created) or (current and current.is_alive()):
                self.started_beside_alive += 1
            thread = _StoppingThread(finishes_on_join=True)
            thread.start = lambda: None
            self.created.append(thread)
            return thread

        for patcher in (
                mock.patch.object(tray_common, '_proxy_thread', None),
                mock.patch.object(tray_common, '_stop_requested', threading.Event()),
                mock.patch.object(tray_common, '_lifecycle_version', 0),
                mock.patch.object(tray_common, '_async_stop', (_FakeLoop(), threading.Event())),
                mock.patch.object(tray_common, 'apply_proxy_config', return_value=True),
                mock.patch.object(tray_common.threading, 'Thread', side_effect=new_thread),
                mock.patch.object(tray_common.time, 'sleep')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_start_does_not_replace_a_running_proxy(self):
        running = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = running
        tray_common.start_proxy({}, lambda message: None)
        self.assertIs(tray_common._proxy_thread, running)
        self.assertEqual(self.created, [])

    def test_restart_waits_for_cleanup_and_starts_automatically(self):
        stuck = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = stuck
        waiting = threading.Event()
        finished = threading.Event()

        def join(timeout=None):
            stuck.joins.append(timeout)
            if timeout is None:
                waiting.set()
                if not finished.wait(2):
                    raise AssertionError('old proxy was not released')
                stuck.alive = False

        stuck.join = join
        click = _RealThread(target=tray_common.restart_proxy,
                            args=({}, lambda message: None))
        click.start()
        try:
            self.assertTrue(waiting.wait(1), 'restart must wait for the old proxy')
            self.assertEqual(self.created, [])
            self.assertTrue(stuck.is_alive())
        finally:
            finished.set()
            click.join(3)

        self.assertFalse(click.is_alive())
        self.assertEqual(stuck.joins, [None])
        self.assertFalse(stuck.is_alive())
        self.assertEqual(len(self.created), 1)
        self.assertIs(tray_common._proxy_thread, self.created[0])

    def test_start_after_stop_timeout_waits_for_cleanup(self):
        running = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = running
        tray_common.stop_proxy()
        self.assertIs(tray_common._proxy_thread, running)

        running.finishes_on_join = True
        tray_common.start_proxy({}, lambda message: None)

        self.assertEqual(running.joins, [5, None])
        self.assertFalse(running.is_alive())
        self.assertEqual(len(self.created), 1)

    def test_stop_before_loop_is_ready_still_joins_thread(self):
        running = _StoppingThread(finishes_on_join=True)
        tray_common._proxy_thread = running
        with mock.patch.object(tray_common, '_async_stop', None):
            tray_common.stop_proxy()
        self.assertTrue(tray_common._stop_requested.is_set())
        self.assertEqual(running.joins, [5])
        self.assertIsNone(tray_common._proxy_thread)

    def test_stop_handles_loop_closed_during_signal(self):
        loop = mock.Mock()
        loop.call_soon_threadsafe.side_effect = RuntimeError('Event loop is closed')
        loop.is_closed.return_value = True
        running = _StoppingThread(finishes_on_join=True)
        tray_common._proxy_thread = running
        with mock.patch.object(tray_common, '_async_stop', (loop, threading.Event())):
            tray_common.restart_proxy({}, lambda message: None)
        self.assertFalse(running.is_alive())
        self.assertEqual(len(self.created), 1)

    def test_stop_supersedes_pending_restart(self):
        self.check_stop_supersedes(tray_common.restart_proxy)

    def test_stop_supersedes_pending_start_after_stop_timeout(self):
        tray_common._stop_requested.set()
        self.check_stop_supersedes(tray_common.start_proxy)

    def check_stop_supersedes(self, action):
        running = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = running
        waiting = threading.Event()
        finished = threading.Event()

        def join(timeout=None):
            if timeout is None:
                waiting.set()
                if not finished.wait(2):
                    raise AssertionError('old proxy was not released')
                running.alive = False

        running.join = join
        click = _RealThread(target=action,
                            args=({}, lambda message: None))
        click.start()
        try:
            self.assertTrue(waiting.wait(1))
            # stop_proxy must return even while restart is waiting on cleanup.
            stopped = threading.Event()
            stop = _RealThread(target=lambda: (tray_common.stop_proxy(), stopped.set()))
            stop.start()
            try:
                self.assertTrue(stopped.wait(1))
            finally:
                finished.set()
                stop.join(3)
        finally:
            finished.set()
            click.join(3)
        self.assertFalse(click.is_alive())
        self.assertEqual(self.created, [])

    def test_latest_restart_uses_latest_config(self):
        running = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = running
        first_waiting = threading.Event()
        both_waiting = threading.Event()
        finished = threading.Event()
        joins = []

        def join(timeout=None):
            joins.append(timeout)
            first_waiting.set()
            if len(joins) == 2:
                both_waiting.set()
            if not finished.wait(2):
                raise AssertionError('old proxy was not released')
            running.alive = False

        running.join = join
        clicks = [_RealThread(target=tray_common.restart_proxy,
                              args=({'port': port}, lambda message: None))
                  for port in (1080, 1081)]
        clicks[0].start()
        try:
            self.assertTrue(first_waiting.wait(1))
            clicks[1].start()
            self.assertTrue(both_waiting.wait(1))
        finally:
            finished.set()
            for click in clicks:
                if click.ident is not None:
                    click.join(3)
        self.assertTrue(all(not click.is_alive() for click in clicks))
        tray_common.apply_proxy_config.assert_called_once_with({'port': 1081})
        self.assertEqual(len(self.created), 1)
        self.assertEqual(self.started_beside_alive, 0)

    def test_normal_stop_then_start(self):
        running = _StoppingThread(finishes_on_join=True)
        tray_common._proxy_thread = running

        tray_common.stop_proxy()
        tray_common.start_proxy({}, lambda message: None)

        self.assertFalse(running.is_alive())
        self.assertEqual(len(self.created), 1)
        self.assertIs(tray_common._proxy_thread, self.created[0])

    def test_parallel_restarts_never_run_two_proxies_at_once(self):
        tray_common._proxy_thread = _StoppingThread(finishes_on_join=True)
        both_checked = threading.Barrier(2, timeout=1)

        def apply_after_both_checked(cfg):
            # Without serialisation both restarts get here before either sets _proxy_thread
            try:
                both_checked.wait()
            except threading.BrokenBarrierError:
                pass
            return True

        tray_common.apply_proxy_config.side_effect = apply_after_both_checked
        clicks = [_RealThread(target=tray_common.restart_proxy, args=({}, lambda message: None))
                  for _ in range(2)]
        for click in clicks:
            click.start()
        for click in clicks:
            click.join(5)

        # Concurrent requests may coalesce, but never start beside a live proxy.
        self.assertEqual(self.started_beside_alive, 0)
        self.assertEqual(sum(t.is_alive() for t in self.created), 1)
        self.assertIs(tray_common._proxy_thread, self.created[-1])


if __name__ == '__main__':
    unittest.main()
