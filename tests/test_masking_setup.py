import contextlib
import io
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deploy import run_proxy, setup_masking as setup


class EnvironmentEntrypointTest(unittest.TestCase):
    def test_container_tcpmss_is_applied_before_dropping_root(self):
        calls = []
        env = {'TG_WS_PROXY_TCPMSS': 'true', 'TG_WS_PROXY_TCPMSS_MSS': '92'}
        pwd = SimpleNamespace(getpwnam=lambda name: SimpleNamespace(pw_uid=123, pw_gid=456))
        with patch.object(run_proxy.sys, 'platform', 'linux'), \
                patch.object(run_proxy.os, 'geteuid', return_value=0, create=True), \
                patch.dict('sys.modules', {'pwd': pwd}), \
                patch.object(run_proxy.subprocess, 'run', side_effect=lambda *a, **k: calls.append(a[0])), \
                patch.object(run_proxy.os, 'setgroups', side_effect=lambda v: calls.append(('groups', v)), create=True), \
                patch.object(run_proxy.os, 'setgid', side_effect=lambda v: calls.append(('gid', v)), create=True), \
                patch.object(run_proxy.os, 'setuid', side_effect=lambda v: calls.append(('uid', v)), create=True):
            run_proxy.configure_docker_tcpmss(env, ['--port', '1443', '--port=2443'])
        self.assertIn('--tcpmss-only', calls[0])
        self.assertIn('2443', calls[0])
        self.assertIn('92', calls[0])
        self.assertEqual(calls[1:], [('groups', []), ('gid', 456), ('uid', 123)])

    def test_disabled_container_tcpmss_does_not_need_root_or_change_network(self):
        with patch.object(run_proxy.subprocess, 'run') as run:
            run_proxy.configure_docker_tcpmss({}, [])
            run_proxy.configure_docker_tcpmss({'TG_WS_PROXY_TCPMSS': 'false'}, [])
        run.assert_not_called()

    def test_existing_docker_environment_and_cli_arguments_are_preserved(self):
        env = {'TG_WS_PROXY_SECRET': 'ab' * 16,
               'TG_WS_PROXY_DC_IPS': '2:1.2.3.4 4:5.6.7.8',
               'TG_WS_PROXY_CF_WORKER': 'one.example two.example',
               'TG_WS_PROXY_NO_SECURE': 'True'}
        args = run_proxy.build_args(env, ['--verbose', '--port', '2443'])
        self.assertEqual(args[:4], ['--host', '0.0.0.0', '--port', '1443'])
        self.assertIn('one.example two.example', args)
        self.assertEqual(args.count('--dc-ip'), 2)
        self.assertIn('--no-secure', args)
        self.assertEqual(args[-3:], ['--verbose', '--port', '2443'])

    def test_masking_and_shaping_environment_is_translated_without_shell_expansion(self):
        env = {'TG_WS_PROXY_FAKE_TLS_DOMAIN': 'cover.example',
               'TG_WS_PROXY_FAKE_TLS_MASK_HOST': 'cover',
               'TG_WS_PROXY_FAKE_TLS_MASK_PORT': '8443',
               'TG_WS_PROXY_FAKE_TLS_DRS': 'false',
               'TG_WS_PROXY_FAKE_TLS_SPLIT': '0',
               'TG_WS_PROXY_DC_IPS': ''}
        args = run_proxy.build_args(env)
        self.assertIn('--no-fake-tls-drs', args)
        self.assertIn('--no-fake-tls-split', args)
        self.assertIn('--dc-ip', args)
        self.assertEqual(args[args.index('--fake-tls-mask-host') + 1], 'cover')
        with self.assertRaises(ValueError):
            run_proxy.build_args({'TG_WS_PROXY_NO_SECURE': 'typo'})


@unittest.skipUnless(shutil.which('openssl'), 'certificate generation requires openssl')
class PrepareMaskingTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = Path(self.folder.name) / 'deployment'

    def prepare(self, *extra, mode='docker'):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return setup.main(['--mode', mode, '--domain', 'cover.example',
                               '--state-dir', str(self.directory), '--prepare-only'] + list(extra))

    def test_rerun_preserves_secret_certificate_and_operator_website(self):
        self.assertEqual(self.prepare(), 0)
        state = (self.directory / 'state.json').read_bytes()
        certificate = (self.directory / 'tls/cert.pem').read_bytes()
        setup.write_file(self.directory / 'site/index.html', '<h1>My website</h1>')
        self.assertEqual(self.prepare(), 0)
        self.assertEqual((self.directory / 'state.json').read_bytes(), state)
        self.assertEqual((self.directory / 'tls/cert.pem').read_bytes(), certificate)
        self.assertEqual((self.directory / 'site/index.html').read_text(), '<h1>My website</h1>')
        self.assertEqual((self.directory / 'proxy.env').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / 'tls/key.pem').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / 'state.json').stat().st_mode & 0o777, 0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.directory / 'tls/cert.pem'),
                                str(self.directory / 'tls/key.pem'))

    def test_domain_change_requires_explicit_flag_and_renews_generated_certificate(self):
        self.assertEqual(self.prepare(), 0)
        original = json.loads((self.directory / 'state.json').read_text())
        self.assertEqual(self.prepare('--domain', 'new.example'), 1)
        self.assertEqual(json.loads((self.directory / 'state.json').read_text()), original)
        self.assertEqual(self.prepare('--domain', 'new.example', '--force-domain-change'), 0)
        updated = json.loads((self.directory / 'state.json').read_text())
        self.assertEqual(updated['secret'], original['secret'])
        self.assertTrue(setup.certificate_matches(self.directory / 'tls/cert.pem', 'new.example'))

    def test_supplied_certificate_is_reused_and_wrong_hostname_cannot_replace_it(self):
        self.assertEqual(self.prepare(), 0)
        cert, key = self.directory / 'tls/cert.pem', self.directory / 'tls/key.pem'
        self.assertEqual(self.prepare('--cert', str(cert), '--key', str(key)), 0)
        self.assertEqual(json.loads((self.directory / 'state.json').read_text())['certificate'],
                         'provided')
        before = cert.read_bytes()
        self.assertEqual(self.prepare('--domain', 'wrong.example', '--force-domain-change'), 1)
        self.assertEqual(cert.read_bytes(), before)
        self.assertEqual(json.loads((self.directory / 'state.json').read_text())['domain'],
                         'cover.example')

    def test_compose_publishes_only_proxy_and_health_checks_real_masking(self):
        self.assertEqual(self.prepare('--bind-address', '127.0.0.1', '--port', '19443'), 0)
        compose = json.loads((self.directory / 'compose.yaml').read_text())
        cover, proxy = compose['services']['cover'], compose['services']['proxy']
        self.assertNotIn('ports', cover)
        self.assertTrue(compose['networks']['cover']['internal'])
        processor = compose['services']['nfqws']
        self.assertEqual(processor['ports'][0]['host_ip'], '127.0.0.1')
        self.assertNotIn('ports', proxy)
        self.assertNotIn('networks', proxy)
        self.assertEqual(proxy['network_mode'], 'service:nfqws')
        self.assertEqual(proxy['depends_on']['nfqws']['condition'], 'service_healthy')
        self.assertEqual(processor['cap_drop'], ['ALL'])
        self.assertEqual(processor['cap_add'], ['NET_ADMIN', 'NET_RAW'])
        self.assertNotIn('volumes', proxy)
        self.assertNotIn('volumes', compose)
        self.assertEqual(proxy['depends_on']['cover']['condition'], 'service_healthy')
        self.assertIn("('127.0.0.1',1443)", proxy['healthcheck']['test'][-1])
        self.assertIn("server_hostname='cover.example'", proxy['healthcheck']['test'][-1])
        self.assertFalse(any('tls' in mount for mount in proxy.get('volumes', [])))
        self.assertEqual(proxy['cap_add'], ['NET_ADMIN'])
        self.assertEqual(proxy['environment']['TG_WS_PROXY_TCPMSS'], 'true')
        self.assertIn('TG_WS_PROXY_FAKE_TLS_MASK_HOST=cover',
                      (self.directory / 'proxy.env').read_text())

    def test_public_link_address_is_independent_of_the_tls_domain(self):
        self.assertEqual(self.prepare('--public-host', '198.51.100.10'), 0)
        state = json.loads((self.directory / 'state.json').read_text())
        self.assertEqual(state['public_host'], '198.51.100.10')
        self.assertIn('TG_WS_PROXY_FAKE_TLS_DOMAIN=cover.example',
                      (self.directory / 'proxy.env').read_text())

    def test_encrypted_key_is_rejected_without_an_interactive_prompt(self):
        self.assertEqual(self.prepare(), 0)
        key = self.directory / 'encrypted-key.pem'
        setup.run(['openssl', 'pkey', '-in', self.directory / 'tls/key.pem',
                   '-aes-256-cbc', '-passout', 'pass:test-password', '-out', key])
        self.assertEqual(self.prepare('--cert', str(self.directory / 'tls/cert.pem'),
                                      '--key', str(key)), 1)

    def test_host_preview_generates_unit_without_touching_system_or_running_docker(self):
        original = setup.run

        def only_openssl(args, **kwargs):
            self.assertEqual(str(args[0]), 'openssl')
            return original(args, **kwargs)

        with patch.object(setup, 'run', side_effect=only_openssl), \
                patch.object(setup, 'install_host') as install, \
                patch.object(setup, 'check_cover') as check:
            self.assertEqual(self.prepare(mode='host'), 0)
        install.assert_not_called()
        check.assert_not_called()
        self.assertIn('127.0.0.1:8443 ssl', (self.directory / 'nginx.conf').read_text())
        self.assertIn('DynamicUser=yes', (self.directory / 'tg-ws-proxy.service').read_text())
        self.assertNotIn('StateDirectory=',
                         (self.directory / 'tg-ws-proxy.service').read_text())
        self.assertNotIn('REPLAY_DB', (self.directory / 'proxy.env').read_text())
        self.assertTrue((self.directory / 'tg-ws-proxy-tcpmss.service').exists())

    def test_no_tcpmss_removes_preview_unit_and_container_capabilities(self):
        self.assertEqual(self.prepare(mode='host'), 0)
        self.assertEqual(self.prepare('--no-tcpmss', mode='host'), 0)
        self.assertFalse((self.directory / 'tg-ws-proxy-tcpmss.service').exists())
        self.assertNotIn('tcpmss', (self.directory / 'tg-ws-proxy.service').read_text())
        other = self.directory / 'docker'
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(setup.main(['--mode', 'docker', '--domain', 'cover.example',
                                         '--state-dir', str(other), '--prepare-only',
                                         '--no-tcpmss', '--no-nfqws']), 0)
        proxy = json.loads((other / 'compose.yaml').read_text())['services']['proxy']
        self.assertNotIn('cap_add', proxy)
        self.assertNotIn('user', proxy)

    def test_invalid_arguments_do_not_install_packages(self):
        for flags in (['--domain', 'bad;example.com'], ['--secret', 'short'],
                      ['--cert', '/missing/cert'], ['--port', '0'], ['--tcpmss', '1'],
                      ['--nfqws-ttl', '0'], ['--nfqws-ttl', '256']):
            with self.subTest(flags=flags), self.assertRaises(SystemExit), \
                    patch.object(setup, 'install_host_packages') as packages, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.prepare(*flags)
            packages.assert_not_called()

    def test_nfqws_can_be_disabled_independently_and_preview_units_are_removed(self):
        self.assertEqual(self.prepare('--nfqws-ttl', '8', mode='host'), 0)
        unit = self.directory / 'tg-ws-proxy-nfqws.service'
        self.assertIn('--dpi-desync-ttl=8', unit.read_text())
        self.assertEqual(self.prepare('--no-nfqws', mode='host'), 0)
        self.assertFalse(unit.exists())
        self.assertFalse((self.directory / 'tg-ws-proxy-dpi.service').exists())
        self.assertTrue((self.directory / 'tg-ws-proxy-tcpmss.service').exists())

    def test_docker_nfqws_without_tcpmss_keeps_proxy_unprivileged(self):
        self.assertEqual(self.prepare('--no-tcpmss'), 0)
        compose = json.loads((self.directory / 'compose.yaml').read_text())
        proxy = compose['services']['proxy']
        self.assertNotIn('user', proxy)
        self.assertNotIn('cap_add', proxy)
        self.assertEqual(proxy['network_mode'], 'service:nfqws')
        self.assertIn('nfqws', compose['services'])
        self.assertEqual(self.prepare('--no-nfqws'), 0)
        compose = json.loads((self.directory / 'compose.yaml').read_text())
        self.assertNotIn('nfqws', compose['services'])
        self.assertIn('ports', compose['services']['proxy'])
        self.assertNotIn('network_mode', compose['services']['proxy'])


class HostInstallTest(unittest.TestCase):
    def test_tcpmss_reinstall_cleans_old_port_and_verifies_the_new_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            unit = root / 'installed.service'
            setup.write_file(unit, setup.MARKER + '\nold unit')
            calls = []
            with patch.object(setup, 'HOST_TCPMSS_UNIT', unit), \
                    patch.object(setup, 'run', side_effect=lambda args: calls.append([str(a) for a in args])):
                setup.install_tcpmss(root, 2443, 92, True, {'port': 443, 'tcpmss': True})
            self.assertIn(['systemctl', 'stop', 'tg-ws-proxy-tcpmss.service'], calls)
            removals = [cmd for cmd in calls if 'remove' in cmd]
            self.assertEqual({cmd[cmd.index('--port') + 1] for cmd in removals}, {'443', '2443'})
            self.assertIn('status', calls[-1])
            self.assertIn('92', calls[-1])
            self.assertIn('--mss 92', unit.read_text())

    def test_no_tcpmss_stops_disables_and_removes_owned_service_and_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            unit = root / 'installed.service'
            setup.write_file(unit, setup.MARKER + '\nold unit')
            calls = []
            with patch.object(setup, 'HOST_TCPMSS_UNIT', unit), \
                    patch.object(setup, 'run', side_effect=lambda args: calls.append([str(a) for a in args])):
                setup.install_tcpmss(root, 443, 88, False)
            self.assertFalse(unit.exists())
            self.assertIn(['systemctl', 'disable', 'tg-ws-proxy-tcpmss.service'], calls)
            self.assertTrue(any('remove' in cmd for cmd in calls))
            self.assertFalse(any('enable' in cmd or 'restart' in cmd for cmd in calls))

    def test_bad_nginx_config_restores_previous_file_and_does_not_start_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            nginx, unit = root / 'system-nginx.conf', root / 'system.service'
            previous = setup.MARKER + '\n# previous configuration\n'
            setup.write_file(nginx, previous)
            setup.write_file(root / 'nginx.conf', 'bad configuration')
            calls = []

            def run(args, **kwargs):
                calls.append([str(arg) for arg in args])
                if args[-1] == '-t' and nginx.read_text() == 'bad configuration':
                    raise subprocess.CalledProcessError(1, args)

            with patch.object(setup, 'HOST_NGINX', nginx), \
                    patch.object(setup, 'HOST_UNIT', unit), \
                    patch.object(setup, 'run', side_effect=run), \
                    self.assertRaises(subprocess.CalledProcessError):
                setup.install_host(root)
            self.assertEqual(nginx.read_text(), previous)
            self.assertFalse(unit.exists())
            self.assertFalse(any('systemctl' in cmd for cmd in calls))

    def test_unmanaged_nginx_configuration_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            setup.write_file(root / 'nginx.conf', '# operator configuration')
            with patch.object(setup, 'HOST_NGINX', root / 'nginx.conf'), \
                    patch.object(setup, 'HOST_UNIT', root / 'unit'), \
                    patch.object(setup, 'run') as run, self.assertRaises(ValueError):
                setup.install_host(root)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
