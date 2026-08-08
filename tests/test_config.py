import unittest

from proxy.config import (
    CFPROXY_DEFAULT_DOMAINS,
    _is_valid_domain,
    _normalize_domain_pool,
    coerce_domain_list,
    parse_dc_ip_list,
)


class ParseDcIpListTest(unittest.TestCase):
    def test_parses_multiple_entries(self):
        self.assertEqual(
            parse_dc_ip_list(['2:149.154.167.220', '4:1.2.3.4']),
            {2: '149.154.167.220', 4: '1.2.3.4'},
        )

    def test_last_entry_wins_for_duplicate_dc(self):
        self.assertEqual(parse_dc_ip_list(['2:1.1.1.1', '2:2.2.2.2']), {2: '2.2.2.2'})

    def test_rejects_missing_separator(self):
        with self.assertRaises(ValueError) as ctx:
            parse_dc_ip_list(['2-1.2.3.4'])
        self.assertEqual(ctx.exception.kind, 'format')

    def test_rejects_short_form_ipv4(self):
        for entry in ('2:149.154', '2:1.2.3.4.5', '2:999.1.1.1', '2:abc'):
            with self.subTest(entry=entry), self.assertRaises(ValueError) as ctx:
                parse_dc_ip_list([entry])
            self.assertEqual(ctx.exception.kind, 'invalid')

    def test_rejects_non_numeric_dc(self):
        with self.assertRaises(ValueError):
            parse_dc_ip_list(['x:1.2.3.4'])


class CoerceDomainListTest(unittest.TestCase):
    def test_splits_on_common_separators(self):
        self.assertEqual(
            coerce_domain_list('a.com, b.com; c.com d.com'),
            ['a.com', 'b.com', 'c.com', 'd.com'],
        )

    def test_deduplicates_case_insensitively_keeping_first(self):
        self.assertEqual(coerce_domain_list(['A.com', 'a.com']), ['A.com'])

    def test_flattens_sequences_and_skips_non_strings(self):
        self.assertEqual(coerce_domain_list(['a.com b.com', 5, None]), ['a.com', 'b.com'])

    def test_returns_empty_for_unsupported_types(self):
        self.assertEqual(coerce_domain_list(None), [])
        self.assertEqual(coerce_domain_list(42), [])


class DomainValidationTest(unittest.TestCase):
    def test_accepts_ordinary_domains(self):
        for domain in ('example.com', 'a-b.co.uk', 'x.io'):
            self.assertTrue(_is_valid_domain(domain), domain)

    def test_rejects_malformed_domains(self):
        for domain in ('', 'nodot', '.leading.com', 'trailing.com.',
                       '-bad.com', 'bad-.com', 'a..com', 'a.1',
                       'a.' + 'b' * 64, 'a' * 250 + '.com'):
            self.assertFalse(_is_valid_domain(domain), domain)

    def test_normalize_lowercases_dedupes_and_drops_invalid(self):
        self.assertEqual(
            _normalize_domain_pool(['B.com ', 'b.com', 'nodot', 'a.com']),
            ['b.com', 'a.com'],
        )


class DefaultDomainsTest(unittest.TestCase):
    def test_decoded_defaults_are_valid_domains(self):
        self.assertTrue(CFPROXY_DEFAULT_DOMAINS)
        for domain in CFPROXY_DEFAULT_DOMAINS:
            self.assertTrue(_is_valid_domain(domain), domain)

    def test_decoded_defaults_are_unique(self):
        self.assertEqual(
            len(set(CFPROXY_DEFAULT_DOMAINS)), len(CFPROXY_DEFAULT_DOMAINS)
        )


if __name__ == '__main__':
    unittest.main()
