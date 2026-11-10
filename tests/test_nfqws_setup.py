import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import setup_masking as setup
from deploy.linux import nfqws


class NfqwsInstallTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.unit = self.root / 'nfqws.service'
        self.rules = self.root / 'dpi.service'
        self.calls = []
        for name, value in (('UNIT', self.unit), ('DPI_UNIT', self.rules)):
            patcher = patch.object(nfqws, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(setup, 'run', side_effect=self.record)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, args):
        self.calls.append([str(arg) for arg in args])

    def test_reinstall_builds_before_stopping_and_cleans_old_port(self):
        setup.write_file(self.unit, setup.MARKER)
        setup.write_file(self.rules, setup.MARKER)
        with patch.object(nfqws, 'build', side_effect=lambda: self.calls.append(['build'])), \
                patch.object(nfqws, 'queue_ready', return_value=True):
            setup.install_nfqws(self.root, 2443, 8, True, {'port': 443, 'nfqws': True})
        self.assertEqual(self.calls[0], ['build'])
        removals = [cmd for cmd in self.calls if 'remove' in cmd]
        self.assertEqual({cmd[cmd.index('--port') + 1] for cmd in removals}, {'443', '2443'})
        self.assertIn('--dpi-desync-ttl=8', self.unit.read_text())
        self.assertIn('--port 2443 --mss 0 --nfqueue', self.rules.read_text())
        self.assertIn('status', self.calls[-1])

    def test_failed_build_retains_running_services_and_rules(self):
        setup.write_file(self.unit, 'existing')
        with patch.object(nfqws, 'build', side_effect=subprocess.CalledProcessError(1, 'make')), \
                self.assertRaises(subprocess.CalledProcessError):
            setup.install_nfqws(self.root, 443, 6, True)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.unit.read_text(), 'existing')

    def test_opt_out_removes_units_and_rules_without_building(self):
        for unit in (self.unit, self.rules):
            setup.write_file(unit, setup.MARKER)
        with patch.object(nfqws, 'build') as build:
            setup.install_nfqws(self.root, 443, 6, False)
        build.assert_not_called()
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.rules.exists())
        self.assertTrue(any('remove' in cmd for cmd in self.calls))
        self.assertFalse(any('restart' in cmd for cmd in self.calls))

    def test_queue_health_requires_attached_handler(self):
        with patch.object(Path, 'read_text', return_value='  201 123 0\n'):
            self.assertFalse(nfqws.queue_ready())
        with patch.object(Path, 'read_text', return_value='  200 123 0\n'):
            self.assertTrue(nfqws.queue_ready())
        with patch.object(Path, 'read_text', side_effect=FileNotFoundError):
            self.assertFalse(nfqws.queue_ready())

    def test_cached_build_does_not_download_or_install_packages(self):
        binary = self.root / 'nfqws'
        binary.write_text('binary')
        binary.with_suffix('.version').write_text(nfqws.ZAPRET_TAG + '\n')
        with patch.object(nfqws, 'BINARY', binary), patch.object(subprocess, 'run') as run:
            nfqws.build()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
