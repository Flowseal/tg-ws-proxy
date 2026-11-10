from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.docker import entrypoint as release


class ReleaseComposeTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = self.root / 'config.toml'
        self.state = self.root / 'state'
        for name, value in (('STATE', self.state), ('READY', self.root / 'ready')):
            patcher = patch.object(release, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(release.os, 'chown')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.source.write_text('[server]\npublic_host="198.51.100.1"\n'
                               '[fake_tls]\ndomain="cover.example"\n'
                               '[masking]\nmode="external"\n')

    def test_first_boot_generates_secret_and_recreation_preserves_it(self):
        values = release.load_source(self.source)
        release.prepare_cover(values)
        secret = (self.state / 'secret').read_text()
        self.assertEqual(len(secret.strip()), 32)
        self.assertEqual((self.state / 'secret').stat().st_mode & 0o777, 0o600)
        release.prepare_cover(values)
        self.assertEqual((self.state / 'secret').read_text(), secret)

    def test_explicit_secret_is_private_and_missing_secret_does_not_regenerate_it(self):
        values = release.load_source(self.source)
        values['access.secret'] = 'ab' * 16
        release.prepare_cover(values)
        stored = json.loads((self.state / 'settings.json').read_text())
        self.assertNotIn('access.secret', stored)
        self.assertNotIn('ab' * 16, (self.state / 'settings.json').read_text())
        del values['access.secret']
        release.prepare_cover(values)
        self.assertEqual((self.state / 'secret').read_text().strip(), 'ab' * 16)

    def test_corrupt_persistent_secret_is_not_silently_replaced(self):
        values = release.load_source(self.source)
        release.prepare_cover(values)
        (self.state / 'secret').write_text('broken')
        with self.assertRaises(ValueError):
            release.prepare_cover(values)
        self.assertEqual((self.state / 'secret').read_text(), 'broken')

    def test_invalid_source_fails_before_state_creation(self):
        for text in ('[fake_tls]\ndomain="bad;example.org"',
                     '[fake_tls]\ndomain="cover.example"\n[deployment]\nnfqws="false"',
                     '[fake_tls]\ndomain="cover.example"\n[access]\nsecret="short"'):
            self.source.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                release.load_source(self.source)
        self.assertFalse(self.state.exists())

    def test_proxy_uses_fixed_internal_topology_and_published_port_in_links(self):
        values = release.load_source(self.source)
        values['server.port'] = 2443
        values['masking.mode'] = 'local'
        values['masking.port'] = 8443
        self.state.mkdir()
        (self.state / 'secret').write_text('ab' * 16)
        with patch.dict(release.os.environ, {'TG_WS_PROXY_PUBLISHED_PORT': '3443'}):
            args = release.proxy_args(values)
            self.assertEqual(args[args.index('--port') + 1], '1443')
            self.assertEqual(args[args.index('--fake-tls-mask-host') + 1], 'cover')
            self.assertIn('port=3443', release.link(values))
            values['server.public_port'] = 4443
            self.assertIn('port=4443', release.link(values))

    def test_disabled_nfqueue_keeps_namespace_and_tcpmss_service(self):
        values = release.load_source(self.source)
        values['deployment.nfqws'] = False
        values['deployment.tcpmss'] = 92
        with patch.object(release, 'firewall') as firewall, \
                patch.object(release.os, 'execlp') as idle:
            release.start_network(values)
        self.assertIn(unittest.mock.call('apply', '--tcpmss-only', '--mss', '92'),
                      firewall.call_args_list)
        self.assertIn(unittest.mock.call('remove'), firewall.call_args_list)
        idle.assert_called_once_with('sleep', 'sleep', 'infinity')

    def test_proxy_preserves_configured_upstream_and_shaping(self):
        values = release.load_source(self.source)
        release.prepare_cover(values)
        values.update({'upstream.dc_ips': [], 'upstream.h2': False,
                       'upstream.workers': ['worker.example'], 'fake_tls.drs': False})
        previous = deepcopy(values)
        args = release.proxy_args(values)
        self.assertIn('--dc-ip', args)
        self.assertIn('--no-h2', args)
        self.assertIn('--no-fake-tls-drs', args)
        self.assertIn('worker.example', args)
        self.assertEqual(values, previous)


if __name__ == '__main__':
    unittest.main()
