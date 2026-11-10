import contextlib
import io
import logging
import unittest
from unittest.mock import patch

from proxy import tg_ws_proxy
from proxy.config import ProxyConfig


class FakeTlsSettingsTest(unittest.TestCase):
    def test_cli_maps_backend_and_transport_options(self):
        config = ProxyConfig()
        observed = []

        async def run():
            observed.append(config)

        root = logging.getLogger()
        handlers, level = root.handlers[:], root.level
        try:
            with patch.object(tg_ws_proxy, 'proxy_config', config), \
                    patch.object(tg_ws_proxy, '_run', run), \
                    patch('sys.argv', ['proxy', '--secret', 'ab' * 16,
                                       '--fake-tls-domain', 'Cover.Example',
                                       '--fake-tls-mask-host', '127.0.0.1',
                                       '--fake-tls-mask-port', '8443',
                                       '--no-fake-tls-drs', '--no-fake-tls-split',
                                       '--fake-tls-split-delay-ms', '7',
                                       '--fake-tls-split-jitter-ms', '11']):
                tg_ws_proxy.main()
            self.assertEqual(observed, [config])
            self.assertEqual(config.fake_tls_domain, 'cover.example')
            self.assertEqual(config.fake_tls_mask_host, '127.0.0.1')
            self.assertEqual(config.fake_tls_mask_port, 8443)
            self.assertEqual(config.fake_tls_split_delay_ms, 7)
            self.assertEqual(config.fake_tls_split_jitter_ms, 11)
            self.assertFalse(config.fake_tls_drs)
            self.assertFalse(config.fake_tls_split)
        finally:
            for handler in root.handlers[:]:
                if handler not in handlers:
                    root.removeHandler(handler)
                    handler.close()
            root.setLevel(level)

    def test_invalid_cli_options_do_not_start_listener(self):
        for flags in (['--fake-tls-mask-port', '0'],
                      ['--fake-tls-mask-host', '127.0.0.1'],
                      ['--fake-tls-split-delay-ms', '-1'],
                      ['--fake-tls-split-jitter-ms', '1001'],
                      ['--fake-tls-domain', 'bad/domain'],
                      ['--fake-tls-domain', 'пример.рф']):
            with self.subTest(flags=flags), patch('sys.argv', ['proxy'] + flags), \
                    patch.object(tg_ws_proxy, '_run') as run, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                tg_ws_proxy.main()
            run.assert_not_called()

    def test_shaping_defaults_are_enabled_but_faketls_remains_opt_in(self):
        config = ProxyConfig()
        self.assertTrue(config.fake_tls_drs)
        self.assertTrue(config.fake_tls_split)
        self.assertEqual(config.fake_tls_domain, '')


if __name__ == '__main__':
    unittest.main()
