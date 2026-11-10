import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from configuration import apply_sources, read_settings
from deploy import run_proxy, setup_masking


class TomlConfigTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.path = self.root / 'config.toml'

    def write(self, text):
        self.path.write_text(text)
        return str(self.path)

    def test_root_example_is_valid_and_documents_supported_settings(self):
        values = read_settings(Path(__file__).resolve().parents[1] / 'config.toml.example')
        self.assertEqual(values['fake_tls.domain'], 'proxy.example.com')
        self.assertEqual(values['deployment.tcpmss'], 88)

    def test_relative_paths_are_resolved_against_the_config_directory(self):
        self.write('[masking]\ncertificate="tls/full.pem"\nprivate_key="tls/key.pem"\n')
        self.assertEqual(read_settings(self.path)['masking.certificate'],
                         str((self.root / 'tls/full.pem').resolve()))

    def test_unknown_keys_bad_types_and_ranges_are_rejected(self):
        for text in ('[server]\nprot=443', '[server]\nport=true',
                     '[server]\nport=65536', '[fake_tls]\ndrs="false"',
                     '[unknown]\nx=1', '[deployment]\ntcpmss=12',
                     '[upstream]\ndc_ips=[2]', '[masking]\nmode="typo"'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                read_settings(self.write(text))

    def test_cli_environment_toml_default_priority_and_positive_overrides(self):
        self.write('[server]\nport=2443\n[fake_tls]\ndrs=false\n')
        parser = argparse.ArgumentParser()
        parser.add_argument('--config')
        parser.add_argument('--port', type=int, default=1443)
        parser.add_argument('--fake-tls-drs', dest='no_fake_tls_drs', action='store_false')
        argv = ['--config', str(self.path), '--port', '4443', '--fake-tls-drs']
        apply_sources(parser, argv, env={'TG_WS_PROXY_PORT': '3443'})
        args = parser.parse_args(argv)
        self.assertEqual(args.port, 4443)
        self.assertFalse(args.no_fake_tls_drs)
        self.assertEqual(parser.parse_args(['--config', str(self.path)]).port, 3443)

    def test_explicit_cli_lists_replace_toml_lists(self):
        self.write('[upstream]\ndc_ips=["2:1.2.3.4"]\n')
        parser = argparse.ArgumentParser()
        parser.add_argument('--config')
        parser.add_argument('--dc-ip', action='append')
        argv = ['--config', str(self.path), '--dc-ip', '4:5.6.7.8']
        apply_sources(parser, argv, env={})
        self.assertEqual(parser.parse_args(argv).dc_ip, ['4:5.6.7.8'])

    def test_environment_can_enable_tcpmss_disabled_in_file(self):
        self.write('[deployment]\ntcpmss=0\n')
        parser = argparse.ArgumentParser()
        parser.add_argument('--config')
        parser.add_argument('--tcpmss', type=int, default=88)
        argv = ['--config', str(self.path)]
        apply_sources(parser, argv, setup=True, env={'TG_WS_PROXY_TCPMSS': 'true'})
        self.assertEqual(parser.parse_args(argv).tcpmss, 88)

    def test_bad_config_is_rejected_before_state_or_system_changes(self):
        self.write('[fake_tls]\ndomian="cover.example"')
        state = self.root / 'state'
        with patch.object(setup_masking, 'install_host_packages') as install, \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            setup_masking.main(['--config', str(self.path), '--state-dir', str(state)])
        install.assert_not_called()
        self.assertFalse(state.exists())

    def test_external_docker_setup_needs_no_nginx_or_certificate(self):
        self.write('[server]\npublic_host="198.51.100.1"\npublic_port=443\n'
                   '[fake_tls]\ndomain="cover.example"\ndrs=false\n'
                   '[masking]\nmode="external"\nbackend_host="origin.example"\n'
                   '[deployment]\nmode="docker"\nstate_dir="state"\ntcpmss=0\nnfqws=false\n')
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(setup_masking, 'run') as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup_masking.main(['--config', str(self.path), '--prepare-only']), 0)
        run.assert_not_called()
        state_dir = self.root / 'state'
        compose = json.loads((state_dir / 'compose.yaml').read_text())
        self.assertEqual(set(compose['services']), {'proxy'})
        self.assertNotIn('cap_add', compose['services']['proxy'])
        self.assertFalse((state_dir / 'tls').exists())
        env = (state_dir / 'proxy.env').read_text()
        self.assertIn('TG_WS_PROXY_FAKE_TLS_MASK_HOST=origin.example', env)
        self.assertIn('TG_WS_PROXY_FAKE_TLS_MASK_PORT=443', env)
        self.assertIn('TG_WS_PROXY_FAKE_TLS_DRS=false', env)
        self.assertIn('TG_WS_PROXY_PUBLIC_HOST=198.51.100.1', env)

    def test_domain_alias_and_cli_can_override_config(self):
        self.write('[fake_tls]\ndomain="cover.example"\n[deployment]\nmode="docker"\n')
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup_masking.main([
                '--config', str(self.path), '--domain', 'new.example',
                '--state-dir', str(self.root / 'state'), '--masking-mode', 'external',
                '--prepare-only']), 0)
        state = json.loads((self.root / 'state/state.json').read_text())
        self.assertEqual(state['domain'], 'new.example')

    def test_installer_can_run_as_a_script_outside_the_repository(self):
        self.write('[fake_tls]\ndomain="cover.example"\n[masking]\nmode="external"\n'
                   '[deployment]\nmode="docker"\nstate_dir="state"\n')
        script = Path(__file__).resolve().parents[1] / 'deploy/setup_masking.py'
        result = subprocess.run([sys.executable, str(script), '--config', str(self.path),
                                 '--prepare-only'], cwd=str(self.root),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'state/compose.yaml').exists())

    def test_entrypoint_does_not_override_config_with_implicit_defaults(self):
        args = run_proxy.build_args({}, ['--config', '/etc/proxy.toml'])
        self.assertEqual(args, ['--config', '/etc/proxy.toml'])

    def test_runtime_applies_config_and_cli_before_starting(self):
        from proxy import tg_ws_proxy
        from proxy.config import ProxyConfig

        self.write('[server]\nport=2443\npublic_host="198.51.100.2"\n'
                   '[fake_tls]\ndomain="cover.example"\ndrs=false\n'
                   '[masking]\nmode="external"\nbackend_host="origin.example"\n')
        config = ProxyConfig()
        observed = []

        async def run():
            observed.append(config)

        import logging
        root = logging.getLogger()
        handlers, level = root.handlers[:], root.level
        try:
            with patch.dict(os.environ, {}, clear=True), \
                    patch.object(tg_ws_proxy, 'proxy_config', config), \
                    patch.object(tg_ws_proxy, '_run', run), \
                    patch('sys.argv', ['proxy', '--config', str(self.path),
                                       '--port', '3443', '--fake-tls-drs', '--secret', 'ab' * 16]):
                tg_ws_proxy.main()
            self.assertEqual(observed, [config])
            self.assertEqual(config.port, 3443)
            self.assertEqual(config.public_host, '198.51.100.2')
            self.assertEqual(config.fake_tls_mask_host, 'origin.example')
            self.assertTrue(config.fake_tls_drs)
        finally:
            for handler in root.handlers[:]:
                if handler not in handlers:
                    root.removeHandler(handler)
                    handler.close()
            root.setLevel(level)


if __name__ == '__main__':
    unittest.main()
