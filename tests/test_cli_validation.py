import unittest
from unittest.mock import patch

from proxy import tg_ws_proxy as proxy


class CliSecretTest(unittest.TestCase):
    def test_secret_with_internal_spaces_exits_before_starting(self):
        with patch('sys.argv', ['proxy', '--secret', 'aa ' * 10 + 'aa']):
            with patch.object(proxy.asyncio, 'run') as run:
                with self.assertRaises(SystemExit) as caught:
                    proxy.main()
                self.assertEqual(caught.exception.code, 1)
                run.assert_not_called()
