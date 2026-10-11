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


if __name__ == '__main__':
    unittest.main()
