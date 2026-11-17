import asyncio
import threading
import unittest
from unittest import mock

from utils import tray_common


class ProxyLifecycleTest(unittest.TestCase):
    def test_restart_retains_live_worker_until_a_later_request(self):
        for state in ('running', 'starting', 'loop_closed'):
            with self.subTest(state=state):
                worker = mock.Mock()
                worker.is_alive.return_value = True
                loop, stop_event = mock.Mock(), mock.Mock()
                if state == 'loop_closed':
                    loop.call_soon_threadsafe.side_effect = RuntimeError('Event loop is closed')
                stop = None if state == 'starting' else (loop, stop_event)
                cfg = {'port': 1234}
                with mock.patch.object(tray_common, '_proxy_thread', worker), \
                        mock.patch.object(tray_common, '_async_stop', stop), \
                        mock.patch.object(tray_common, 'apply_proxy_config', return_value=True) as apply, \
                        mock.patch.object(tray_common.threading, 'Thread') as thread, \
                        mock.patch.object(tray_common, 'log') as log:
                    tray_common.restart_proxy(cfg, mock.Mock())
                    self.assertIs(tray_common._proxy_thread, worker)
                    worker.join.assert_called_once_with(timeout=5)
                    tray_common.start_proxy(cfg, mock.Mock())
                    apply.assert_not_called()
                    thread.assert_not_called()
                    self.assertNotIn(mock.call('Proxy stopped'), log.info.call_args_list)

                    # Publication after an early stop still allows a subsequent signal.
                    loop.call_soon_threadsafe.side_effect = None
                    tray_common._async_stop = (loop, stop_event)
                    self.assertFalse(tray_common.stop_proxy())
                    loop.call_soon_threadsafe.assert_called_with(stop_event.set)
                    self.assertIs(tray_common._proxy_thread, worker)

                    worker.is_alive.return_value = False
                    tray_common.restart_proxy(cfg, mock.Mock())
                    apply.assert_called_once_with(cfg)
                    thread.return_value.start.assert_called_once_with()
                    self.assertIs(tray_common._proxy_thread, thread.return_value)
                    log.info.assert_any_call('Proxy stopped')

    def test_concurrent_restarts_join_each_worker_before_applying_config(self):
        real_thread = threading.Thread
        workers, stop_events, applied, failures = [], [], [], []
        stopping, release, second_request = (threading.Event() for _ in range(3))

        def make_worker(**kwargs):
            stop_event = threading.Event()
            stop_events.append(stop_event)
            loop = mock.Mock()
            loop.call_soon_threadsafe.side_effect = lambda callback: callback()
            tray_common._async_stop = (loop, stop_event)
            first = not workers

            def run():
                if not stop_event.wait(timeout=5):
                    failures.append('Worker was not stopped')
                if first:
                    stopping.set()
                    if not release.wait(timeout=5):
                        failures.append('Worker was not released')
                tray_common._async_stop = None

            worker = real_thread(target=run, daemon=True)
            workers.append(worker)
            return worker

        def apply(cfg):
            applied.append((cfg['port'], any(worker.is_alive() for worker in workers)))
            return True

        def restart(port):
            try:
                if port == 3:
                    second_request.set()
                tray_common.restart_proxy({'port': port}, failures.append)
            except Exception as exc:
                failures.append(exc)

        callers = [real_thread(target=restart, args=(port,), daemon=True) for port in (2, 3)]
        with mock.patch.object(tray_common, '_proxy_thread', None), \
                mock.patch.object(tray_common, '_async_stop', None), \
                mock.patch.object(tray_common, 'apply_proxy_config', side_effect=apply), \
                mock.patch.object(tray_common.threading, 'Thread', side_effect=make_worker):
            try:
                tray_common.start_proxy({'port': 1}, failures.append)
                callers[0].start()
                self.assertTrue(stopping.wait(timeout=2))
                callers[1].start()
                self.assertTrue(second_request.wait(timeout=2))
                release.set()
                for caller in callers:
                    caller.join(timeout=2)
                    self.assertFalse(caller.is_alive())
                self.assertEqual(applied, [(1, False), (2, False), (3, False)])
                self.assertEqual(len(workers), 3)
                self.assertTrue(tray_common.stop_proxy())
                self.assertTrue(tray_common.stop_proxy())
                self.assertIsNone(tray_common._proxy_thread)
                self.assertIsNone(tray_common._async_stop)
                self.assertEqual(failures, [])
            finally:
                release.set()
                for stop_event in stop_events:
                    stop_event.set()
                for caller in callers:
                    if caller.ident is not None:
                        caller.join(timeout=6)
                # A caller may have started its replacement during cleanup.
                for stop_event in stop_events:
                    stop_event.set()
                for worker in workers:
                    worker.join(timeout=2)

    def test_crashed_worker_cleans_up_before_stop(self):
        loops, errors = [], []

        async def run(stop_event):
            loops.append(asyncio.get_running_loop())
            raise RuntimeError('Worker failed')

        with mock.patch.object(tray_common, '_proxy_thread', None), \
                mock.patch.object(tray_common, '_async_stop', None), \
                mock.patch.object(tray_common, 'apply_proxy_config', return_value=True), \
                mock.patch.object(tray_common, '_run', side_effect=run), \
                mock.patch.object(tray_common, 'diagnose_listen_error', return_value=('Failed', None)), \
                self.assertLogs(tray_common.log, 'ERROR'):
            try:
                tray_common.start_proxy({}, errors.append)
                worker = tray_common._proxy_thread
                worker.join(timeout=2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, ['Failed'])
                self.assertTrue(loops[0].is_closed())
                self.assertIsNone(tray_common._async_stop)
                self.assertTrue(tray_common.stop_proxy())
                self.assertTrue(tray_common.stop_proxy())
                self.assertIsNone(tray_common._proxy_thread)
            finally:
                if tray_common._proxy_thread is not None:
                    tray_common._proxy_thread.join(timeout=2)

    def test_config_error_callback_runs_outside_lifecycle_lock(self):
        for action in (tray_common.start_proxy, tray_common.restart_proxy):
            with self.subTest(action=action.__name__):
                callers = []

                def on_error(message):
                    caller = threading.Thread(target=tray_common.stop_proxy, daemon=True)
                    callers.append(caller)
                    caller.start()
                    caller.join(timeout=2)
                    self.assertFalse(caller.is_alive())

                with mock.patch.object(tray_common, '_proxy_thread', None), \
                        mock.patch.object(tray_common, '_async_stop', None), \
                        mock.patch.object(tray_common, 'apply_proxy_config', return_value=False):
                    try:
                        action({}, on_error)
                        self.assertEqual(len(callers), 1)
                    finally:
                        for caller in callers:
                            caller.join(timeout=2)


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
