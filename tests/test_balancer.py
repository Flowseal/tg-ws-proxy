import unittest
from unittest import mock

from proxy.balancer import _Balancer


class BalancerCooldownTest(unittest.TestCase):
    def _balancer(self, domains):
        balancer = _Balancer()
        with mock.patch('proxy.balancer.random.choice', side_effect=lambda seq: seq[-1]):
            balancer.update_domains_list(domains)
        return balancer

    def test_failed_domain_is_skipped_until_cooldown_expires(self):
        balancer = self._balancer(['a.com', 'b.com'])
        clock = [1000.0]
        with mock.patch('proxy.balancer.time.monotonic', side_effect=lambda: clock[0]), \
                mock.patch('proxy.balancer.random.shuffle', lambda seq: None):
            balancer.report_failure('b.com')
            self.assertEqual(list(balancer.get_domains_for_dc(1)), ['a.com'])

            clock[0] += balancer.FAILURE_COOLDOWN_INITIAL
            self.assertEqual(
                set(balancer.get_domains_for_dc(1)), {'a.com', 'b.com'})

    def test_cooldown_grows_and_is_capped(self):
        balancer = self._balancer(['a.com'])
        delays = [balancer.report_failure('a.com') for _ in range(6)]
        self.assertEqual(delays[0], balancer.FAILURE_COOLDOWN_INITIAL)
        self.assertEqual(delays[1], balancer.FAILURE_COOLDOWN_INITIAL * 2)
        self.assertEqual(delays[2], balancer.FAILURE_COOLDOWN_MAX)
        for delay in delays[2:]:
            self.assertEqual(delay, balancer.FAILURE_COOLDOWN_MAX)

    def test_success_resets_failure_counter(self):
        balancer = self._balancer(['a.com'])
        balancer.report_failure('a.com')
        balancer.report_failure('a.com')
        balancer.report_success('a.com')

        self.assertEqual(list(balancer.get_domains_for_dc(1)), ['a.com'])
        self.assertEqual(
            balancer.report_failure('a.com'),
            balancer.FAILURE_COOLDOWN_INITIAL,
        )

    def test_all_domains_cooling_down_yield_nothing(self):
        balancer = self._balancer(['a.com', 'b.com'])
        balancer.report_failure('a.com')
        balancer.report_failure('b.com')
        self.assertEqual(list(balancer.get_domains_for_dc(1)), [])

    def test_current_domain_is_tried_first(self):
        balancer = self._balancer(['a.com', 'b.com', 'c.com'])
        with mock.patch('proxy.balancer.random.choice', return_value='b.com'):
            balancer.update_domains_list(['c.com', 'b.com'])
        with mock.patch('proxy.balancer.random.shuffle', lambda seq: None):
            self.assertEqual(
                list(balancer.get_domains_for_dc(1)),
                ['b.com', 'c.com'],
            )

    def test_update_keeps_health_of_unchanged_pool(self):
        balancer = self._balancer(['a.com'])
        balancer.report_failure('a.com')
        with mock.patch('proxy.balancer.random.choice', side_effect=lambda seq: seq[0]):
            balancer.update_domains_list(['a.com'])
        self.assertEqual(list(balancer.get_domains_for_dc(1)), [])

    def test_update_drops_health_of_removed_domains(self):
        balancer = self._balancer(['a.com'])
        balancer.report_failure('a.com')

        with mock.patch('proxy.balancer.random.choice', side_effect=lambda seq: seq[0]):
            balancer.update_domains_list(['b.com'])
            balancer.update_domains_list(['a.com'])

        self.assertEqual(list(balancer.get_domains_for_dc(1)), ['a.com'])

    def test_reset_health_clears_cooldowns(self):
        balancer = self._balancer(['a.com'])
        balancer.report_failure('a.com')
        balancer.reset_health()
        self.assertEqual(list(balancer.get_domains_for_dc(1)), ['a.com'])

    def test_empty_pool_does_not_crash(self):
        balancer = self._balancer(['a.com'])
        balancer.update_domains_list([])
        self.assertEqual(list(balancer.get_domains_for_dc(1)), [])
        self.assertEqual(balancer.report_failure('a.com'), balancer.FAILURE_COOLDOWN_INITIAL)


if __name__ == '__main__':
    unittest.main()
