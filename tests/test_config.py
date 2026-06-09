import unittest

from proxy.config import (
    CFPROXY_DEFAULT_DOMAINS,
    _CFPROXY_ENC,
    _normalize_domain_pool,
)


class CfProxyDefaultsTest(unittest.TestCase):
    def test_default_domains_are_valid_and_not_lost(self):
        normalized = _normalize_domain_pool(CFPROXY_DEFAULT_DOMAINS)

        self.assertEqual(len(CFPROXY_DEFAULT_DOMAINS), len(_CFPROXY_ENC))
        self.assertEqual(CFPROXY_DEFAULT_DOMAINS, normalized)


if __name__ == "__main__":
    unittest.main()
