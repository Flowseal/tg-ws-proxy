import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import linux_update


class _Proc:
    def __init__(self, returncode, stdout=b'', stderr=b''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AssetNameTest(unittest.TestCase):
    def test_names_match_release_assets(self):
        self.assertEqual(linux_update.asset_name('binary'), 'TgWsProxy_linux_amd64')
        self.assertEqual(linux_update.asset_name('deb'), 'TgWsProxy_linux_amd64.deb')
        self.assertEqual(linux_update.asset_name('rpm'), 'TgWsProxy_linux_amd64.rpm')


class DetectInstallKindTest(unittest.TestCase):
    def _detect(self, *, machine='x86_64', tools=(), owned_by=None, writable=True):
        exe = Path('/usr/bin/tg-ws-proxy')

        def _which(name):
            return '/usr/bin/' + name if name in tools else None

        def _run_quiet(cmd):
            return cmd[0] == owned_by

        with mock.patch.object(linux_update.platform, 'machine', return_value=machine), \
                mock.patch.object(linux_update.shutil, 'which', side_effect=_which), \
                mock.patch.object(linux_update, '_run_quiet', side_effect=_run_quiet), \
                mock.patch.object(linux_update.os, 'access', return_value=writable), \
                mock.patch.object(linux_update, '_is_root', return_value=False):
            return linux_update.detect_install_kind(exe)

    def test_deb_package_with_pkexec(self):
        self.assertEqual(
            self._detect(tools=('dpkg-query', 'pkexec', 'apt-get'), owned_by='dpkg-query'),
            'deb',
        )

    def test_rpm_package_with_pkexec(self):
        self.assertEqual(
            self._detect(tools=('rpm', 'pkexec'), owned_by='rpm'), 'rpm',
        )

    def test_package_without_pkexec_is_unsupported(self):
        self.assertIsNone(self._detect(tools=('dpkg-query',), owned_by='dpkg-query'))

    def test_aur_install_is_unsupported(self):
        self.assertIsNone(
            self._detect(tools=('pacman', 'pkexec'), owned_by='pacman'),
        )

    def test_standalone_writable_binary(self):
        self.assertEqual(self._detect(tools=()), 'binary')

    def test_read_only_dir_is_supported_via_pkexec(self):
        self.assertEqual(self._detect(tools=('pkexec',), writable=False), 'binary')

    def test_read_only_dir_without_pkexec_is_unsupported(self):
        self.assertIsNone(self._detect(tools=(), writable=False))

    def test_non_amd64_is_unsupported(self):
        self.assertIsNone(self._detect(machine='aarch64'))


class InstallCmdTest(unittest.TestCase):
    def _cmd(self, kind, tools):
        with mock.patch.object(
            linux_update.shutil, 'which',
            side_effect=lambda n: '/usr/bin/' + n if n in tools else None,
        ), mock.patch.object(linux_update, '_is_root', return_value=False):
            return linux_update.install_cmd(kind, Path('/tmp/pkg'))

    def test_deb_prefers_apt_get_and_elevates(self):
        cmd = self._cmd('deb', ('apt-get',))
        self.assertEqual(cmd[:3], ['pkexec', 'apt-get', 'install'])
        self.assertEqual(cmd[-1], '/tmp/pkg')

    def test_deb_falls_back_to_dpkg(self):
        self.assertEqual(self._cmd('deb', ()), ['pkexec', 'dpkg', '-i', '/tmp/pkg'])

    def test_rpm_prefers_dnf_without_removing_packages(self):
        cmd = self._cmd('rpm', ('dnf',))
        self.assertEqual(cmd, ['pkexec', 'dnf', 'install', '-y', '/tmp/pkg'])
        self.assertNotIn('--allowerasing', cmd)

    def test_rpm_falls_back_to_rpm_binary(self):
        self.assertEqual(
            self._cmd('rpm', ()), ['pkexec', 'rpm', '-U', '--force', '/tmp/pkg'],
        )

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self._cmd('binary', ('apt-get',))

    def test_root_does_not_use_pkexec(self):
        with mock.patch.object(linux_update.shutil, 'which', return_value=None), \
                mock.patch.object(linux_update, '_is_root', return_value=True):
            self.assertEqual(
                linux_update.install_cmd('deb', Path('/tmp/pkg')),
                ['dpkg', '-i', '/tmp/pkg'],
            )


class ApplyUpdateTest(unittest.TestCase):
    def _download(self, payload=b'new'):
        def _fn(url, dest_dir, suffix='.tmp', digest=''):
            path = Path(dest_dir) / ('new' + suffix)
            path.write_bytes(payload)
            return path
        return _fn

    def test_writable_binary_is_replaced_in_place(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'TgWsProxy'
            exe.write_bytes(b'old')

            with mock.patch.object(linux_update, 'exe_path', return_value=exe), \
                    mock.patch('utils.update_check.download_asset', self._download()):
                self.assertIsNone(linux_update.apply_update('binary', 'https://x.invalid/a'))

            self.assertEqual(exe.read_bytes(), b'new')
            self.assertTrue(os.access(str(exe), os.X_OK))
            self.assertEqual(list(Path(tmp).iterdir()), [exe])

    def test_read_only_dir_binary_is_installed_with_elevation(self):
        exe = Path('/usr/local/bin/TgWsProxy')
        with tempfile.TemporaryDirectory() as tmp:
            def _download(url, dest_dir, suffix='.tmp', digest=''):
                path = Path(tmp) / ('new' + suffix)
                path.write_bytes(b'new')
                return path

            run = mock.Mock(return_value=_Proc(0))
            with mock.patch.object(linux_update, 'exe_path', return_value=exe), \
                    mock.patch.object(linux_update.os, 'access', return_value=False), \
                    mock.patch.object(linux_update, '_is_root', return_value=False), \
                    mock.patch('utils.update_check.download_asset', _download), \
                    mock.patch.object(linux_update.subprocess, 'run', run):
                self.assertIsNone(linux_update.apply_update('binary', 'https://x.invalid/a'))

            cmd = run.call_args[0][0]
            self.assertEqual(cmd[:4], ['pkexec', 'install', '-m', '755'])
            self.assertEqual(cmd[-1], str(exe))

    def test_deb_install_failure_returns_stderr(self):
        run = mock.Mock(return_value=_Proc(1, stderr=b'dpkg: error'))
        with mock.patch.object(linux_update, 'exe_path', return_value=Path('/usr/bin/x')), \
                mock.patch('utils.update_check.download_asset', self._download()), \
                mock.patch.object(linux_update.subprocess, 'run', run), \
                mock.patch.object(linux_update.shutil, 'which', return_value=None), \
                mock.patch.object(linux_update, '_is_root', return_value=True):
            err = linux_update.apply_update('deb', 'https://x.invalid/a.deb')

        self.assertEqual(err, 'dpkg: error')

    def test_deb_install_success_removes_downloaded_package(self):
        holder = {}

        def _download(url, dest_dir, suffix='.tmp', digest=''):
            path = Path(dest_dir) / ('pkg' + suffix)
            path.write_bytes(b'deb')
            holder['path'] = path
            return path

        with mock.patch.object(linux_update, 'exe_path', return_value=Path('/usr/bin/x')), \
                mock.patch('utils.update_check.download_asset', _download), \
                mock.patch.object(linux_update.subprocess, 'run',
                                  return_value=_Proc(0)), \
                mock.patch.object(linux_update.shutil, 'which', return_value=None), \
                mock.patch.object(linux_update, '_is_root', return_value=True):
            self.assertIsNone(linux_update.apply_update('deb', 'https://x.invalid/a.deb'))

        self.assertFalse(holder['path'].exists())

    def test_digest_is_passed_to_the_downloader(self):
        seen = {}

        def _download(url, dest_dir, suffix='.tmp', digest=''):
            seen['digest'] = digest
            path = Path(dest_dir) / ('new' + suffix)
            path.write_bytes(b'new')
            return path

        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'TgWsProxy'
            exe.write_bytes(b'old')
            with mock.patch.object(linux_update, 'exe_path', return_value=exe), \
                    mock.patch('utils.update_check.download_asset', _download):
                linux_update.apply_update('binary', 'https://x.invalid/a', digest='sha256:ab')

        self.assertEqual(seen['digest'], 'sha256:ab')

    def test_download_failure_is_reported(self):
        with mock.patch.object(linux_update, 'exe_path', return_value=Path('/tmp/x')), \
                mock.patch('utils.update_check.download_asset',
                           side_effect=OSError('boom')):
            self.assertEqual(linux_update.apply_update('binary', 'https://x.invalid/a'), 'boom')


class RelaunchEnvTest(unittest.TestCase):
    def _env(self, env, meipass='/tmp/_MEI1'):
        with mock.patch.dict(linux_update.os.environ, env, clear=True), \
                mock.patch.object(linux_update.sys, '_MEIPASS', meipass, create=True):
            return linux_update.relaunch_env()

    def test_bundle_paths_are_dropped_and_originals_restored(self):
        out = self._env({
            '_PYI_APPLICATION_HOME_DIR': '/tmp/_MEI1',
            '_MEIPASS': '/tmp/_MEI1',
            'LD_LIBRARY_PATH': '/tmp/_MEI1',
            'LD_LIBRARY_PATH_ORIG': '/usr/lib',
            'GI_TYPELIB_PATH': '/tmp/_MEI1',
            'PATH': '/tmp/_MEI1:/usr/bin',
            'HOME': '/home/u',
        })

        self.assertEqual(out['LD_LIBRARY_PATH'], '/usr/lib')
        self.assertNotIn('GI_TYPELIB_PATH', out)
        self.assertEqual(out['PATH'], '/usr/bin')
        self.assertNotIn('_MEIPASS', out)
        self.assertNotIn('_PYI_APPLICATION_HOME_DIR', out)
        self.assertEqual(out['HOME'], '/home/u')

    def test_environment_without_bundle_is_unchanged(self):
        env = {'PATH': '/usr/bin', 'HOME': '/home/u'}
        with mock.patch.dict(linux_update.os.environ, env, clear=True):
            with mock.patch.object(linux_update.sys, '_MEIPASS', None, create=True):
                out = linux_update.relaunch_env()
        self.assertEqual(out, env)


if __name__ == '__main__':
    unittest.main()
