import unittest

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


if __name__ == '__main__':
    unittest.main()
