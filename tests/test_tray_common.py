import unittest
from unittest import mock

from utils import tray_common


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


if __name__ == '__main__':
    unittest.main()
