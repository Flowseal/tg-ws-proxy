import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from utils import update_check
from utils.update_check import _extract_assets, _parse_version_tuple, _version_gt


class ParseVersionTupleTest(unittest.TestCase):
    def test_plain_and_prefixed_versions(self):
        self.assertEqual(_parse_version_tuple('1.9.1'), (1, 9, 1))
        self.assertEqual(_parse_version_tuple('v1.9.1'), (1, 9, 1))
        self.assertEqual(_parse_version_tuple(' V2.0 '), (2, 0))

    def test_trailing_suffixes_are_truncated_to_digits(self):
        self.assertEqual(_parse_version_tuple('1.9.1rc2'), (1, 9, 1))
        self.assertEqual(_parse_version_tuple('1.9.1-beta'), (1, 9, 1))

    def test_empty_and_non_numeric_segments(self):
        self.assertEqual(_parse_version_tuple(''), (0,))
        self.assertEqual(_parse_version_tuple(None), (0,))
        self.assertEqual(_parse_version_tuple('1.x.3'), (1, 0, 3))


class VersionGtTest(unittest.TestCase):
    def test_newer_versions(self):
        self.assertTrue(_version_gt('1.9.2', '1.9.1'))
        self.assertTrue(_version_gt('1.10.0', '1.9.9'))
        self.assertTrue(_version_gt('2.0', '1.9.9'))

    def test_equal_and_older_versions(self):
        self.assertFalse(_version_gt('1.9.1', '1.9.1'))
        self.assertFalse(_version_gt('1.9.1', '1.9.2'))
        self.assertFalse(_version_gt('1.9', '1.9.0'))

    def test_shorter_version_is_padded_with_zeros(self):
        self.assertTrue(_version_gt('1.9.1', '1.9'))
        self.assertFalse(_version_gt('1.9', '1.9.1'))


class ExtractAssetsTest(unittest.TestCase):
    def test_keeps_name_url_and_digest(self):
        data = {'assets': [{
            'name': 'TgWsProxy_windows.exe',
            'browser_download_url': 'https://example.invalid/a.exe',
            'digest': 'sha256:abc',
        }]}
        self.assertEqual(_extract_assets(data), [{
            'name': 'TgWsProxy_windows.exe',
            'url': 'https://example.invalid/a.exe',
            'digest': 'sha256:abc',
        }])

    def test_drops_entries_without_name_or_url(self):
        data = {'assets': [
            {'name': 'a.exe'},
            {'browser_download_url': 'https://example.invalid/b.exe'},
        ]}
        self.assertEqual(_extract_assets(data), [])

    def test_missing_digest_becomes_empty_string(self):
        data = {'assets': [{
            'name': 'a.exe', 'browser_download_url': 'https://example.invalid/a.exe',
        }]}
        self.assertEqual(_extract_assets(data)[0]['digest'], '')

    def test_empty_input(self):
        self.assertEqual(_extract_assets(None), [])
        self.assertEqual(_extract_assets({}), [])


class _Response(io.BytesIO):
    def __init__(self, body, content_length='auto'):
        super().__init__(body)
        if content_length == 'auto':
            content_length = len(body)
        self.headers = {} if content_length is None else {
            'Content-Length': str(content_length),
        }

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _opener(response):
    opener = mock.Mock()
    opener.open.return_value = response
    return mock.Mock(return_value=opener)


class DownloadAssetTest(unittest.TestCase):
    def _download(self, response, digest=''):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(update_check, 'build_github_opener', _opener(response)):
                path = update_check.download_asset(
                    'https://example.invalid/a', Path(tmp), suffix='.bin', digest=digest,
                )
            try:
                return path.read_bytes(), sorted(p.name for p in Path(tmp).iterdir())
            finally:
                path.unlink(missing_ok=True)

    def test_complete_download_is_kept(self):
        body, files = self._download(_Response(b'payload'))
        self.assertEqual(body, b'payload')
        self.assertEqual(len(files), 1)

    def test_matching_digest_is_accepted(self):
        digest = 'sha256:' + hashlib.sha256(b'payload').hexdigest()
        body, _ = self._download(_Response(b'payload'), digest=digest)
        self.assertEqual(body, b'payload')

    def test_unknown_digest_format_is_ignored(self):
        body, _ = self._download(_Response(b'payload'), digest='md5:whatever')
        self.assertEqual(body, b'payload')

    def test_truncated_download_raises_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = _Response(b'half', content_length=99)
            with mock.patch.object(update_check, 'build_github_opener', _opener(resp)):
                with self.assertRaises(OSError):
                    update_check.download_asset(
                        'https://example.invalid/a', Path(tmp), suffix='.bin',
                    )
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_digest_mismatch_raises_and_leaves_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp = _Response(b'payload')
            with mock.patch.object(update_check, 'build_github_opener', _opener(resp)):
                with self.assertRaises(OSError):
                    update_check.download_asset(
                        'https://example.invalid/a', Path(tmp),
                        suffix='.bin', digest='sha256:' + '0' * 64,
                    )
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_missing_content_length_is_tolerated(self):
        body, _ = self._download(_Response(b'payload', content_length=None))
        self.assertEqual(body, b'payload')


class FindAssetTest(unittest.TestCase):
    def test_returns_asset_with_digest(self):
        assets = [{'name': 'a.deb', 'url': 'https://example.invalid/a.deb', 'digest': 'sha256:ab'}]
        with mock.patch.dict(update_check._state, {'assets': assets}):
            self.assertEqual(update_check.find_asset('a.deb'), assets[0])
            self.assertIsNone(update_check.find_asset('b.deb'))

    def test_ignores_asset_without_url(self):
        with mock.patch.dict(update_check._state, {'assets': [{'name': 'a.deb', 'url': ''}]}):
            self.assertIsNone(update_check.find_asset('a.deb'))


if __name__ == '__main__':
    unittest.main()
