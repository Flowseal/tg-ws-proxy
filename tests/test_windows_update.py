import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class WindowsUpdateTest(unittest.TestCase):
    def setUp(self):
        import windows
        self.windows = windows
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.exe = Path(directory) / 'TgWsProxy.exe'
        self.original = b'original test executable - never executed'
        self.exe.write_bytes(self.original)
        self.stack.enter_context(patch.object(windows.sys, 'executable', str(self.exe)))
        self.stack.enter_context(patch.object(windows.time, 'sleep'))
        self.stop = self.stack.enter_context(patch.object(windows, 'stop_proxy'))
        self.release = self.stack.enter_context(patch.object(windows, '_release_win_mutex'))
        self.launch = self.stack.enter_context(patch.object(windows.subprocess, 'Popen'))
        self.exit = self.stack.enter_context(patch.object(windows.os, '_exit'))
        self.messages = []

    def assert_original_kept(self):
        self.assertEqual(self.exe.read_bytes(), self.original)
        self.assertEqual(list(self.exe.parent.glob('*.tmp')), [])
        self.stop.assert_not_called()
        self.release.assert_not_called()
        self.launch.assert_not_called()
        self.exit.assert_not_called()

    def run_http_download(self, body, length):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if length is not None:
                    self.send_header('Content-Length', str(length))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.windows._perform_update(
                f'http://127.0.0.1:{server.server_port}/update', self.messages.append,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_truncated_http_download_keeps_original(self):
        self.run_http_download(b'MZpartial', 1024)
        self.assert_original_kept()
        self.assertIn('1024', self.messages[-1])

    def test_empty_download_keeps_original(self):
        self.run_http_download(b'', 0)
        self.assert_original_kept()

    def test_invalid_content_length_keeps_original(self):
        self.run_http_download(b'MZpartial', 'invalid')
        self.assert_original_kept()

    def test_empty_download_without_length_keeps_original(self):
        self.run_http_download(b'', None)
        self.assert_original_kept()

    def test_rename_denied_keeps_original(self):
        with patch.object(Path, 'rename', side_effect=PermissionError('test rename denied')):
            self.run_http_download(b'MZupdate', 8)
        self.assert_original_kept()

    def test_install_failure_restores_original(self):
        rename = Path.rename

        def fail_install(source, target):
            if source.suffix == '.tmp':
                raise PermissionError('test install denied')
            return rename(source, target)

        with patch.object(Path, 'rename', fail_install):
            self.run_http_download(b'MZupdate', 8)
        self.assert_original_kept()

    def test_complete_download_replaces_and_restarts(self):
        payload = b'MZsynthetic update'
        self.run_http_download(payload, len(payload))
        self.assertEqual(self.exe.read_bytes(), payload)
        self.assertEqual(self.exe.with_name('TgWsProxy_oldtgws.exe').read_bytes(), self.original)
        self.launch.assert_called_once()
        self.exit.assert_called_once_with(0)

    def test_nonempty_download_without_length_still_works(self):
        payload = b'MZsynthetic update without content length'
        self.run_http_download(payload, None)
        self.assertEqual(self.exe.read_bytes(), payload)
        self.launch.assert_called_once()

    def test_timeout_keeps_original_and_is_bounded(self):
        opener = Mock()
        opener.open.side_effect = TimeoutError('test stalled download')
        with patch.object(self.windows, 'build_github_opener', return_value=opener):
            self.windows._perform_update('https://example.invalid/update', self.messages.append)
        timeout = opener.open.call_args.kwargs.get('timeout')
        self.assertIsNotNone(timeout)
        self.assertGreater(timeout, 0)
        self.assertLessEqual(timeout, 60)
        self.assert_original_kept()

    def test_read_error_keeps_original(self):
        response = Mock()
        response.headers = {'Content-Length': '1024'}
        response.read.side_effect = [b'MZpartial', TimeoutError('test read timeout')]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch.object(self.windows, 'build_github_opener', return_value=opener):
            self.windows._perform_update('https://example.invalid/update', self.messages.append)
        self.assert_original_kept()

    def test_unwritable_directory_reports_error_without_stopping(self):
        with patch.object(self.windows.tempfile, 'mkstemp', side_effect=PermissionError('test denied')):
            self.windows._perform_update('https://example.invalid/update', self.messages.append)
        self.assert_original_kept()
        self.assertIn('test denied', self.messages[-1])
