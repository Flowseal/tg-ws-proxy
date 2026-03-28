import json
import tempfile
import unittest
from pathlib import Path

from proxy.app_runtime import DEFAULT_CONFIG, ProxyAppRuntime


class _FakeThread:
    def __init__(self, target=None, args=(), daemon=None, name=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.name = name
        self.started = False
        self.join_timeout = None
        self._alive = False

    def start(self):
        self.started = True
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.join_timeout = timeout
        self._alive = False


class ProxyAppRuntimeTests(unittest.TestCase):
    def test_load_config_returns_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = ProxyAppRuntime(Path(tmpdir))

            cfg = runtime.load_config()

            self.assertEqual(cfg, DEFAULT_CONFIG)

    def test_load_config_merges_defaults_into_saved_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = Path(tmpdir)
            config_path = app_dir / "config.json"
            app_dir.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps({"port": 9050, "host": "127.0.0.2"}),
                encoding="utf-8")
            runtime = ProxyAppRuntime(app_dir)

            cfg = runtime.load_config()

            self.assertEqual(cfg["port"], 9050)
            self.assertEqual(cfg["host"], "127.0.0.2")
            self.assertEqual(cfg["dc_ip"], DEFAULT_CONFIG["dc_ip"])
            self.assertEqual(cfg["verbose"], DEFAULT_CONFIG["verbose"])

    def test_invalid_config_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = Path(tmpdir)
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / "config.json").write_text("{broken", encoding="utf-8")
            runtime = ProxyAppRuntime(app_dir)

            cfg = runtime.load_config()

            self.assertEqual(cfg, DEFAULT_CONFIG)

    def test_start_proxy_starts_thread_with_parsed_dc_options(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            captured = {}
            thread_holder = {}

            def fake_parse(entries):
                captured["dc_ip"] = list(entries)
                return {2: "149.154.167.220"}

            def fake_thread_factory(**kwargs):
                thread = _FakeThread(**kwargs)
                thread_holder["thread"] = thread
                return thread

            runtime = ProxyAppRuntime(
                Path(tmpdir),
                parse_dc_ip_list=fake_parse,
                thread_factory=fake_thread_factory)

            started = runtime.start_proxy(dict(DEFAULT_CONFIG))

            self.assertTrue(started)
            self.assertEqual(captured["dc_ip"], DEFAULT_CONFIG["dc_ip"])
            self.assertTrue(thread_holder["thread"].started)
            self.assertEqual(
                thread_holder["thread"].args,
                (DEFAULT_CONFIG["port"], {2: "149.154.167.220"},
                 DEFAULT_CONFIG["host"]))

    def test_start_proxy_reports_bad_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            errors = []

            def fake_parse(entries):
                raise ValueError("bad dc mapping")

            runtime = ProxyAppRuntime(
                Path(tmpdir),
                parse_dc_ip_list=fake_parse,
                on_error=errors.append)

            started = runtime.start_proxy({
                "host": "127.0.0.1",
                "port": 1080,
                "dc_ip": ["broken"],
                "verbose": False,
            })

            self.assertFalse(started)
            self.assertEqual(errors, ["Ошибка конфигурации:\nbad dc mapping"])


if __name__ == "__main__":
    unittest.main()
