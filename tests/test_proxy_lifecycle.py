import asyncio
import threading
import unittest
from unittest.mock import patch

from utils import tray_common as tray


class ProxyLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(tray.DEFAULT_CONFIG, pool_size=0, cfproxy=False)
        self.addCleanup(tray.stop_proxy)

    def test_stop_during_loop_initialization_waits_for_worker(self):
        entered = threading.Event()
        release = threading.Event()
        make_loop = asyncio.new_event_loop

        def delayed_loop():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test did not release worker')
            return make_loop()

        async def run(stop_event):
            await stop_event.wait()

        with patch.object(tray.asyncio, 'new_event_loop', delayed_loop), patch.object(tray, '_run', run):
            tray.start_proxy(self.cfg, self.fail)
            worker = tray._proxy_thread
            self.assertTrue(entered.wait(1))
            timer = threading.Timer(0.05, release.set)
            timer.start()
            try:
                tray.stop_proxy()
                self.assertFalse(worker.is_alive())
                self.assertIsNone(tray._proxy_thread)
            finally:
                release.set()
                timer.join()

    def test_repeated_start_stop_clears_stop_request(self):
        started = threading.Event()

        async def run(stop_event):
            self.assertFalse(stop_event.is_set())
            started.set()
            await stop_event.wait()

        with patch.object(tray, '_run', run):
            for _ in range(3):
                started.clear()
                tray.start_proxy(self.cfg, self.fail)
                self.assertTrue(started.wait(1))
                worker = tray._proxy_thread
                tray.start_proxy(self.cfg, self.fail)
                self.assertIs(tray._proxy_thread, worker)
                tray.stop_proxy()
                self.assertFalse(worker.is_alive())

    def test_timed_out_worker_is_kept_and_cannot_be_replaced(self):
        started = threading.Event()
        release = threading.Event()

        async def run(stop_event):
            started.set()
            await asyncio.get_running_loop().run_in_executor(None, release.wait)

        with patch.object(tray, '_run', run):
            tray.start_proxy(self.cfg, self.fail)
            self.assertTrue(started.wait(1))
            worker = tray._proxy_thread
            try:
                with patch.object(worker, 'join'):
                    tray.stop_proxy()
                self.assertIs(tray._proxy_thread, worker)
                tray.start_proxy(self.cfg, self.fail)
                self.assertIs(tray._proxy_thread, worker)
            finally:
                release.set()
                worker.join(2)
            tray.stop_proxy()
            self.assertIsNone(tray._proxy_thread)
