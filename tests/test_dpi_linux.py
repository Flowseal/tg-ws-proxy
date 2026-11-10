"""Exercise firewall lifecycle without changing the machine's network settings."""
import contextlib
import io
import subprocess
import unittest
from unittest.mock import patch

from deploy.linux import dpi


class FakeIptables:
    def __init__(self):
        self.chains = {'OUTPUT': [('existing-firewall-rule',)], 'OTHER': [('keep',)]}
        self.commands = []
        self.fail_queue = False

    def __call__(self, cmd, **kwargs):
        self.commands.append(cmd)
        op, chain, *rule = cmd[5:]
        rule = tuple(rule)
        rc = 0
        if op == '-S':
            rc = int(chain not in self.chains)
        elif op == '-N':
            if chain in self.chains:
                rc = 1
            else:
                self.chains[chain] = []
        elif op == '-F':
            self.chains[chain] = []
        elif op == '-X':
            del self.chains[chain]
        elif op == '-C':
            rc = int(rule not in self.chains.get(chain, []))
        elif op == '-A':
            if self.fail_queue and 'NFQUEUE' in rule:
                rc = 1
            else:
                self.chains[chain].append(rule)
        elif op == '-D':
            self.chains[chain].remove(rule)
        else:
            raise AssertionError(cmd)
        return subprocess.CompletedProcess(cmd, rc, '', 'simulated failure' if rc else '')


class FirewallLifecycleTest(unittest.TestCase):
    def test_automatic_tcpmss_is_independent_of_existing_nfqueue_rules(self):
        fake = FakeIptables()
        with patch.object(dpi.subprocess, 'run', side_effect=fake):
            original = dpi.Firewall('iptables')
            original.apply(443, 0, 200)
            expected = list(fake.chains['TGWS_DPI_443'])
            automatic = dpi.Firewall('iptables', tcpmss_only=True)
            automatic.apply(443, 88, None)
            automatic.apply(443, 92, None)
            automatic.verify(443, 92, None)
            self.assertEqual(len(fake.chains['TGWS_MSS_443']), 1)
            automatic.remove(443)
        self.assertEqual(fake.chains['TGWS_DPI_443'], expected)
        self.assertEqual(len(fake.chains['OUTPUT']), 2)

    def test_optional_ipv6_failure_keeps_verified_ipv4_rules(self):
        fake = FakeIptables()

        def run(cmd, **kwargs):
            if cmd[0] == 'ip6tables':
                return subprocess.CompletedProcess(cmd, 1, '', 'IPv6 unavailable')
            return fake(cmd, **kwargs)

        with patch.object(dpi.subprocess, 'run', side_effect=run), \
                patch.object(dpi.sys, 'platform', 'linux'), \
                patch.object(dpi.os, 'geteuid', return_value=0, create=True), \
                patch.object(dpi.shutil, 'which', side_effect=lambda name: name), \
                contextlib.redirect_stderr(io.StringIO()) as error, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dpi.main(['apply', '--tcpmss-only', '--ipv6-optional']), 0)
        self.assertIn('IPv6', error.getvalue())
        self.assertIn('TGWS_MSS_443', fake.chains)

    def test_apply_is_idempotent_and_remove_preserves_unrelated_rules(self):
        fake = FakeIptables()
        with patch.object(dpi.subprocess, 'run', side_effect=fake):
            firewall = dpi.Firewall('iptables')
            firewall.apply(443, 88, 200)
            firewall.apply(443, 88, 200)
            firewall.verify(443, 88, 200)
            self.assertEqual(len(fake.chains['OUTPUT']), 2)
            self.assertEqual(len(fake.chains['TGWS_DPI_443']), 2)
            firewall.remove(443)
            firewall.remove(443)
        self.assertEqual(fake.chains, {'OUTPUT': [('existing-firewall-rule',)], 'OTHER': [('keep',)]})
        self.assertTrue(all(cmd[1:5] == ['-w', '5', '-t', 'mangle'] for cmd in fake.commands))

    def test_status_detects_missing_mss_or_queue_rule(self):
        fake = FakeIptables()
        with patch.object(dpi.subprocess, 'run', side_effect=fake):
            firewall = dpi.Firewall('iptables')
            firewall.apply(8443, 88, None)
            with self.assertRaises(RuntimeError):
                firewall.verify(8443, 88, 200)
            with self.assertRaises(RuntimeError):
                firewall.verify(8443, 1200, None)

    def test_apply_failure_rolls_back_only_our_rules(self):
        fake = FakeIptables()
        fake.fail_queue = True
        with patch.object(dpi.subprocess, 'run', side_effect=fake), \
                patch.object(dpi.sys, 'platform', 'linux'), \
                patch.object(dpi.os, 'geteuid', return_value=0, create=True), \
                patch.object(dpi.shutil, 'which', side_effect=lambda name: name), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(dpi.main(['apply', '--nfqueue']), 1)
        self.assertEqual(fake.chains, {'OUTPUT': [('existing-firewall-rule',)], 'OTHER': [('keep',)]})

    def test_dry_run_never_executes_commands_and_shows_ipv6(self):
        output = io.StringIO()
        with patch.object(dpi.subprocess, 'run') as run, contextlib.redirect_stdout(output):
            self.assertEqual(dpi.main(['apply', '--dry-run', '--ipv6', '--nfqueue']), 0)
        run.assert_not_called()
        self.assertIn('ip6tables', output.getvalue())
        self.assertIn('--set-mss 88', output.getvalue())
        self.assertIn('--queue-bypass', output.getvalue())
        self.assertIn('lo', output.getvalue())

    def test_invalid_options_do_not_touch_the_firewall(self):
        for args in (['--port', '0'], ['--port', '65536'], ['--mss', '1'],
                     ['--queue-num', '-1'], ['--mss', '0'],
                     ['--tcpmss-only', '--nfqueue']):
            with self.subTest(args=args), patch.object(dpi.subprocess, 'run') as run, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                dpi.main(['apply', '--dry-run'] + args)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
