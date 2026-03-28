import sys
import unittest
import json
from pathlib import Path


sys.path.insert(0, str(
    Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "python"
))

import android_proxy_bridge  # noqa: E402
import proxy.tg_ws_proxy as tg_ws_proxy  # noqa: E402


class FakeJavaArrayList:
    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get(self, index):
        return self._items[index]


class AndroidProxyBridgeTests(unittest.TestCase):
    def tearDown(self):
        tg_ws_proxy.reset_stats()
        android_proxy_bridge._LAST_ERROR = None

    def test_normalize_dc_ip_list_with_python_iterable(self):
        result = android_proxy_bridge._normalize_dc_ip_list([
            "2:149.154.167.220",
            "  ",
            "4:149.154.167.220 ",
        ])

        self.assertEqual(result, [
            "2:149.154.167.220",
            "4:149.154.167.220",
        ])

    def test_get_runtime_stats_json_reports_proxy_counters(self):
        tg_ws_proxy.reset_stats()
        snapshot = tg_ws_proxy.get_stats_snapshot()
        snapshot["bytes_up"] = 1536
        snapshot["bytes_down"] = 4096
        tg_ws_proxy._stats.bytes_up = snapshot["bytes_up"]
        tg_ws_proxy._stats.bytes_down = snapshot["bytes_down"]

        result = json.loads(android_proxy_bridge.get_runtime_stats_json())

        self.assertEqual(result["bytes_up"], 1536)
        self.assertEqual(result["bytes_down"], 4096)
        self.assertFalse(result["running"])
        self.assertIsNone(result["last_error"])

    def test_get_runtime_stats_json_includes_last_error(self):
        android_proxy_bridge._LAST_ERROR = "boom"

        result = json.loads(android_proxy_bridge.get_runtime_stats_json())

        self.assertEqual(result["last_error"], "boom")

    def test_normalize_dc_ip_list_with_java_array_list_shape(self):
        result = android_proxy_bridge._normalize_dc_ip_list(FakeJavaArrayList([
            "2:149.154.167.220",
            "4:149.154.167.220",
        ]))

        self.assertEqual(result, [
            "2:149.154.167.220",
            "4:149.154.167.220",
        ])

    def test_start_proxy_saves_advanced_runtime_config(self):
        captured = {}

        class FakeRuntime:
            def __init__(self, *args, **kwargs):
                captured["runtime_init"] = kwargs
                self.log_file = Path("/tmp/proxy.log")

            def reset_log_file(self):
                captured["reset_log_file"] = True

            def setup_logging(self, verbose=False, log_max_mb=5):
                captured["verbose"] = verbose
                captured["log_max_mb"] = log_max_mb

            def save_config(self, config):
                captured["config"] = dict(config)

            def start_proxy(self, config):
                captured["start_proxy"] = dict(config)
                return True

            def is_proxy_running(self):
                return True

            def stop_proxy(self):
                captured["stop_proxy"] = True

        original_runtime = android_proxy_bridge.ProxyAppRuntime
        try:
            android_proxy_bridge.ProxyAppRuntime = FakeRuntime
            log_path = android_proxy_bridge.start_proxy(
                "/tmp/app",
                "127.0.0.1",
                1080,
                ["2:149.154.167.220"],
                7.0,
                512,
                6,
                True,
            )
        finally:
            android_proxy_bridge.ProxyAppRuntime = original_runtime

        self.assertEqual(log_path, "/tmp/proxy.log")
        self.assertEqual(captured["config"]["log_max_mb"], 7.0)
        self.assertEqual(captured["config"]["buf_kb"], 512)
        self.assertEqual(captured["config"]["pool_size"], 6)
        self.assertEqual(captured["log_max_mb"], 7.0)
        self.assertTrue(captured["verbose"])

    def test_get_update_status_json_merges_python_update_state(self):
        original_run_check = android_proxy_bridge.run_check
        original_get_status = android_proxy_bridge.get_status
        try:
            captured = {}

            def fake_run_check(version):
                captured["run_check_version"] = version

            def fake_get_status():
                return {
                    "latest": "1.3.1",
                    "has_update": True,
                    "ahead_of_release": False,
                    "html_url": "https://example.com/release",
                    "error": "",
                }

            android_proxy_bridge.run_check = fake_run_check
            android_proxy_bridge.get_status = fake_get_status

            result = json.loads(android_proxy_bridge.get_update_status_json(True))
        finally:
            android_proxy_bridge.run_check = original_run_check
            android_proxy_bridge.get_status = original_get_status

        self.assertEqual(captured["run_check_version"], android_proxy_bridge.__version__)
        self.assertEqual(result["current_version"], android_proxy_bridge.__version__)
        self.assertEqual(result["latest"], "1.3.1")
        self.assertTrue(result["has_update"])
        self.assertEqual(result["html_url"], "https://example.com/release")


if __name__ == "__main__":
    unittest.main()
