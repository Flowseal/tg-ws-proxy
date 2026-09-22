import threading
import unittest
from unittest.mock import Mock, patch

from utils import tray_common


class CtkInitializationTest(unittest.TestCase):
    def setUp(self):
        self.threads = []
        real_thread = threading.Thread

        def make_thread(**kwargs):
            thread = real_thread(**kwargs)
            self.threads.append(thread)
            return thread

        for patcher in (
            patch.object(tray_common, '_ctk_root', None),
            patch.object(tray_common, '_ctk_root_ready', threading.Event()),
            patch.dict('sys.modules', {'ui.ctk_theme': Mock()}),
            patch.object(tray_common.threading, 'Thread', side_effect=make_thread),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.join_threads)
        self.ctk = Mock()

    def join_threads(self):
        for thread in self.threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def test_missing_module_does_not_start_thread(self):
        self.assertFalse(tray_common.ensure_ctk_thread(None))
        self.assertEqual(self.threads, [])

    def test_success_reuses_initialized_root(self):
        self.assertTrue(tray_common.ensure_ctk_thread(self.ctk))
        self.assertTrue(tray_common.ensure_ctk_thread(self.ctk))
        self.assertIs(tray_common._ctk_root, self.ctk.CTk.return_value)
        self.ctk.CTk.assert_called_once_with()
        self.ctk.CTk.return_value.withdraw.assert_called_once_with()
        self.join_threads()
        self.ctk.CTk.return_value.mainloop.assert_called_once_with()

    def test_constructor_failure_logs_traceback_and_allows_retry(self):
        self.ctk.CTk.side_effect = RuntimeError('Cannot find a usable init.tcl')
        with self.assertLogs('tg-ws-tray', level='ERROR') as captured:
            self.assertFalse(tray_common.ensure_ctk_thread(self.ctk))
        self.assertIn('CTk root initialization failed', captured.output[0])
        self.assertIn('Cannot find a usable init.tcl', captured.output[0])
        self.assertIsNotNone(captured.records[0].exc_info)
        self.assertIsNone(tray_common._ctk_root)
        self.assertFalse(tray_common._ctk_root_ready.is_set())
        self.join_threads()
        self.ctk.CTk.side_effect = None
        self.assertTrue(tray_common.ensure_ctk_thread(self.ctk))

    def test_withdraw_failure_does_not_publish_partial_root(self):
        root = self.ctk.CTk.return_value
        root.withdraw.side_effect = RuntimeError('withdraw failed')
        with self.assertLogs('tg-ws-tray', level='ERROR'):
            self.assertFalse(tray_common.ensure_ctk_thread(self.ctk))
        self.assertIsNone(tray_common._ctk_root)
        self.assertFalse(tray_common._ctk_root_ready.is_set())
        root.destroy.assert_called_once_with()
        root.mainloop.assert_not_called()

    def test_failure_signals_completion_without_waiting_for_timeout(self):
        completed = threading.Event()
        self.ctk.CTk.side_effect = RuntimeError('initialization failed')
        with patch.object(tray_common.threading, 'Event', return_value=completed):
            with self.assertLogs('tg-ws-tray', level='ERROR'):
                self.assertFalse(tray_common.ensure_ctk_thread(self.ctk))
        self.assertTrue(completed.is_set())

    def test_timeout_is_logged_and_does_not_report_success(self):
        completed = Mock()
        completed.wait.return_value = False
        with patch.object(tray_common.threading, 'Thread'), \
                patch.object(tray_common.threading, 'Event', return_value=completed):
            with self.assertLogs('tg-ws-tray', level='ERROR') as captured:
                self.assertFalse(tray_common.ensure_ctk_thread(self.ctk))
        self.assertIn('timed out', captured.output[0])
        self.assertIsNone(tray_common._ctk_root)


if __name__ == '__main__':
    unittest.main()
