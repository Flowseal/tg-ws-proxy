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


class RestartProxyTest(unittest.TestCase):
    def restart(self, thread):
        events = []

        def stop():
            events.append('stop')
            tray_common._proxy_thread = None  # stop_proxy() forgets the thread even if it is alive

        with mock.patch.object(tray_common, '_proxy_thread', thread), \
                mock.patch.object(tray_common, 'stop_proxy', side_effect=stop), \
                mock.patch.object(tray_common, 'start_proxy',
                                  side_effect=lambda cfg, on_error: events.append('start')), \
                mock.patch.object(tray_common.time, 'sleep'):
            tray_common.restart_proxy({}, lambda message: events.append(message))
        return events

    def test_waits_for_slow_cleanup_before_starting_new_proxy(self):
        thread = _StoppingThread(finishes_on_join=True)

        self.assertEqual(self.restart(thread), ['stop', 'start'])
        self.assertEqual(thread.joins, [tray_common._RESTART_STOP_TIMEOUT])

    def test_never_starts_second_proxy_while_first_is_alive(self):
        thread = _StoppingThread(finishes_on_join=False)

        self.assertEqual(self.restart(thread), ['stop'])

    def test_stopped_proxy_restarts_without_extra_wait(self):
        thread = _StoppingThread(finishes_on_join=True)
        thread.alive = False

        self.assertEqual(self.restart(thread), ['stop', 'start'])
        self.assertEqual(thread.joins, [])


class _FakeLoop:
    def __init__(self):
        self.stop_requests = 0

    def call_soon_threadsafe(self, callback):
        self.stop_requests += 1


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
                mock.patch.object(tray_common, '_async_stop', (_FakeLoop(), threading.Event())),
                mock.patch.object(tray_common, 'apply_proxy_config', return_value=True),
                mock.patch.object(tray_common.threading, 'Thread', side_effect=new_thread),
                mock.patch.object(tray_common.time, 'sleep')):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stop_then_start_keeps_the_stuck_thread_and_starts_nothing(self):
        stuck = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = stuck

        tray_common.stop_proxy()
        tray_common.start_proxy({}, lambda message: None)

        self.assertIs(tray_common._proxy_thread, stuck)
        self.assertEqual(self.created, [])

    def test_restart_after_failed_stop_waits_for_the_same_thread(self):
        stuck = _StoppingThread(finishes_on_join=False)
        tray_common._proxy_thread = stuck

        tray_common.restart_proxy({}, lambda message: None)
        self.assertEqual(self.created, [])

        stuck.finishes_on_join = True
        tray_common.restart_proxy({}, lambda message: None)
        self.assertFalse(stuck.is_alive(), 'the second restart must wait for the stuck thread')
        self.assertEqual(len(self.created), 1)
        self.assertIs(tray_common._proxy_thread, self.created[0])

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

        # Two clicks are two restarts in a row, but a proxy never starts beside a live one
        self.assertEqual(self.started_beside_alive, 0)
        self.assertEqual(sum(t.is_alive() for t in self.created), 1)
        self.assertIs(tray_common._proxy_thread, self.created[-1])


if __name__ == '__main__':
    unittest.main()
