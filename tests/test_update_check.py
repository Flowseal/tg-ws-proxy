import unittest

from utils import update_check


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        self._orig_state = dict(update_check._state)

    def tearDown(self):
        update_check._state.clear()
        update_check._state.update(self._orig_state)

    def test_apply_release_tag_marks_update_available(self):
        update_check._apply_release_tag(
            tag="v1.3.1",
            html_url="https://example.com/release",
            current_version="1.3.0",
        )

        status = update_check.get_status()
        self.assertTrue(status["has_update"])
        self.assertFalse(status["ahead_of_release"])
        self.assertEqual(status["latest"], "1.3.1")
        self.assertEqual(status["html_url"], "https://example.com/release")

    def test_apply_release_tag_marks_ahead_of_release(self):
        update_check._apply_release_tag(
            tag="v1.1.2-relay",
            html_url="https://example.com/release",
            current_version="1.3.0",
        )

        status = update_check.get_status()
        self.assertFalse(status["has_update"])
        self.assertTrue(status["ahead_of_release"])
        self.assertEqual(status["latest"], "1.1.2-relay")

    def test_apply_release_tag_marks_latest_when_versions_match(self):
        update_check._apply_release_tag(
            tag="v1.3.0",
            html_url="https://example.com/release",
            current_version="1.3.0",
        )

        status = update_check.get_status()
        self.assertFalse(status["has_update"])
        self.assertFalse(status["ahead_of_release"])
        self.assertEqual(status["latest"], "1.3.0")


if __name__ == "__main__":
    unittest.main()
