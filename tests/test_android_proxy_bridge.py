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


if __name__ == "__main__":
    unittest.main()
