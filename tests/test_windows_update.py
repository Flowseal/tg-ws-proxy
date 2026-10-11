import hashlib
import io
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from utils import update_check, windows_update


class AssetNameTest(unittest.TestCase):
    def test_keeps_the_running_build_even_when_renamed_or_emulated(self):
        builds = (
            ('win-amd64', (3, 11), 'TgWsProxy_windows.exe'),
            ('win-amd64', (3, 8), 'TgWsProxy_windows_7_64bit.exe'),
            ('win32', (3, 8), 'TgWsProxy_windows_7_32bit.exe'),
            ('win-arm64', (3, 11), 'TgWsProxy_windows_arm64.exe'),
            ('unknown', (3, 11), None),
        )
        for platform, version, expected in builds:
            with self.subTest(platform=platform, version=version), \
                    mock.patch.object(windows_update.sysconfig, 'get_platform', return_value=platform), \
                    mock.patch.object(sys, 'version_info', version), \
                    mock.patch.object(sys, 'executable', 'renamed.exe'):
                self.assertEqual(windows_update.asset_name(), expected)


class ApplyUpdateTest(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        self.exe = Path(directory) / 'renamed app.exe'
        self.exe.write_bytes(b'old')
        self.backup = self.exe.with_name('renamed app_oldtgws.exe')
        self.opener = mock.Mock()
        self.opener.open.return_value = io.BytesIO(b'new')
        self.asset = {'url': 'https://example.invalid/new.exe',
                      'digest': 'sha256:' + hashlib.sha256(b'new').hexdigest()}
        stack.enter_context(mock.patch.object(sys, 'executable', str(self.exe)))
        stack.enter_context(mock.patch.object(sys, 'argv', [str(self.exe), '--portable']))
        stack.enter_context(mock.patch.object(sys, '_MEIPASS', None, create=True))
        stack.enter_context(mock.patch.object(update_check, 'build_github_opener', return_value=self.opener))
        stack.enter_context(mock.patch.object(windows_update.subprocess, 'DETACHED_PROCESS', 8, create=True))
        stack.enter_context(mock.patch.object(windows_update.subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, create=True))
        self.launch = stack.enter_context(mock.patch.object(windows_update.subprocess, 'Popen'))
        self.prepare = mock.Mock()

    def test_verified_update_keeps_legacy_backup_and_launch_arguments(self):
        def prepare():
            self.assertEqual(self.exe.read_bytes(), b'new')
            self.assertEqual(self.backup.read_bytes(), b'old')
            self.launch.assert_not_called()

        windows_update.apply_update(self.asset, prepare)
        self.assertEqual(self.exe.read_bytes(), b'new')
        self.assertEqual(self.backup.read_bytes(), b'old')
        self.assertEqual(set(self.exe.parent.iterdir()), {self.exe, self.backup})
        self.assertEqual(self.launch.call_args[0][0], [str(self.exe), '--portable'])
        self.assertEqual(self.launch.call_args[1]['creationflags'], 520)

    def test_missing_or_corrupt_checksum_does_not_replace_or_launch(self):
        for digest in ('', 'sha256:' + '0' * 64):
            with self.subTest(digest=digest):
                self.asset['digest'] = digest
                with self.assertRaises((OSError, ValueError)):
                    windows_update.apply_update(self.asset, self.prepare)
                self.assertEqual(self.exe.read_bytes(), b'old')
                self.assertEqual(list(self.exe.parent.iterdir()), [self.exe])
                self.prepare.assert_not_called()
                self.launch.assert_not_called()

    def test_launch_failure_restores_the_old_executable(self):
        self.launch.side_effect = OSError('launch failed')
        with self.assertRaisesRegex(OSError, 'launch failed'):
            windows_update.apply_update(self.asset, self.prepare)
        self.prepare.assert_called_once_with()
        self.assertEqual(self.exe.read_bytes(), b'old')
        self.assertEqual(list(self.exe.parent.iterdir()), [self.exe])

    def test_rename_failure_leaves_current_executable_and_cleans_download(self):
        with mock.patch.object(Path, 'rename', side_effect=PermissionError('in use')):
            with self.assertRaises(PermissionError):
                windows_update.apply_update(self.asset, self.prepare)
        self.assertEqual(self.exe.read_bytes(), b'old')
        self.assertEqual(list(self.exe.parent.iterdir()), [self.exe])
        self.prepare.assert_not_called()
        self.launch.assert_not_called()

    def test_replacement_failure_rolls_back_and_cleans_download(self):
        replace = Path.replace

        def fail_download_move(path, target):
            if path.suffix == '.tmp':
                raise PermissionError('move failed')
            return replace(path, target)

        with mock.patch.object(Path, 'replace', fail_download_move):
            with self.assertRaisesRegex(PermissionError, 'move failed'):
                windows_update.apply_update(self.asset, self.prepare)
        self.assertEqual(self.exe.read_bytes(), b'old')
        self.assertEqual(list(self.exe.parent.iterdir()), [self.exe])
        self.prepare.assert_not_called()
        self.launch.assert_not_called()

    def test_restart_uses_a_fresh_bundle_and_resets_dll_directory(self):
        dll = mock.Mock()
        with mock.patch.object(sys, '_MEIPASS', 'old-bundle'), \
                mock.patch.object(windows_update.ctypes, 'windll', dll, create=True):
            windows_update.apply_update(self.asset, self.prepare)
        self.assertEqual(self.launch.call_args[1]['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        calls = dll.kernel32.SetDllDirectoryW.call_args_list
        self.assertEqual(calls[0], mock.call(None))
        self.assertEqual(calls[1].args[0].value, 'old-bundle')


@unittest.skipUnless(sys.platform == 'win32', 'Windows tray integration')
class UpdateFlowTest(unittest.TestCase):
    def setUp(self):
        import windows

        self.windows = windows

    def test_fallback_dialog_actually_installs_the_selected_asset(self):
        app = self.windows
        asset = {'url': 'https://example.invalid/new.exe', 'digest': 'sha256:' + '0' * 64}
        with mock.patch.object(app, 'ctk', None), \
                mock.patch.object(app._u32, 'MessageBoxW', return_value=app._IDYES), \
                mock.patch.object(app, '_perform_update') as update:
            app.update_ctk_form('update', asset=asset)
        update.assert_called_once_with(asset)

    def test_failed_launch_recovers_proxy_and_lock_instead_of_exiting(self):
        app = self.windows

        def fail(asset, before_restart):
            before_restart()
            raise OSError('launch failed')

        with ExitStack() as stack:
            mocks = {name: stack.enter_context(mock.patch.object(app, name)) for name in (
                'apply_update', 'stop_proxy', 'release_lock', '_release_win_mutex',
                '_acquire_win_mutex', 'acquire_lock', 'start_proxy', '_show_error',
            )}
            exit_process = stack.enter_context(mock.patch.object(app.os, '_exit'))
            mocks['apply_update'].side_effect = fail
            mocks['_acquire_win_mutex'].return_value = None
            app._perform_update({'url': 'asset', 'digest': 'digest'})
        mocks['stop_proxy'].assert_called_once_with()
        mocks['release_lock'].assert_called_once_with()
        mocks['_release_win_mutex'].assert_called_once_with()
        mocks['acquire_lock'].assert_called_once_with()
        mocks['start_proxy'].assert_called_once_with(app._config, mocks['_show_error'])
        mocks['_show_error'].assert_called_once()
        exit_process.assert_not_called()

    def test_download_failure_leaves_proxy_and_locks_running(self):
        app = self.windows
        with mock.patch.object(app, 'apply_update', side_effect=OSError('checksum mismatch')), \
                mock.patch.object(app, 'stop_proxy') as stop, \
                mock.patch.object(app, 'release_lock') as unlock, \
                mock.patch.object(app, '_show_error'), \
                mock.patch.object(app.os, '_exit') as exit_process:
            app._perform_update({})
        stop.assert_not_called()
        unlock.assert_not_called()
        exit_process.assert_not_called()
