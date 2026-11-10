#!/usr/bin/env python3
"""Manage only this proxy's Linux mangle chains; never flush the host firewall.

No external Python dependencies. Run on the VPS that owns the public TCP socket.
nfqws is installed and supervised separately, not downloaded by this helper.
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys


def rules(port, mss, queue, tcpmss_only=False):
    chain = ('TGWS_MSS_' if tcpmss_only else 'TGWS_DPI_') + str(port)
    jump = ['OUTPUT', '!', '-o', 'lo', '-p', 'tcp', '--sport', str(port), '-j', chain]
    entries = []
    if mss:
        entries.append([chain, '-p', 'tcp', '--tcp-flags', 'SYN,ACK', 'SYN,ACK',
                        '-j', 'TCPMSS', '--set-mss', str(mss)])
    if queue is not None:
        entries.append([chain, '-p', 'tcp', '--tcp-flags', 'SYN,RST,FIN', 'NONE',
                        '-j', 'NFQUEUE', '--queue-num', str(queue), '--queue-bypass'])
    return chain, jump, entries


class Firewall:
    def __init__(self, binary, dry_run=False, tcpmss_only=False):
        self.binary = binary
        self.dry_run = dry_run
        self.tcpmss_only = tcpmss_only

    def rules(self, port, mss, queue):
        return rules(port, mss, queue, self.tcpmss_only)

    def call(self, *args, check=True):
        cmd = [self.binary, '-w', '5', '-t', 'mangle'] + list(args)
        if self.dry_run:
            print(shlex.join(cmd))
            # Plan a new chain/jump, and print the verification operations too.
            return 0 if check else 1
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True)
        if check and result.returncode:
            raise RuntimeError(shlex.join(cmd) + ': ' + result.stderr.strip())
        return result.returncode

    def apply(self, port, mss, queue):
        chain, jump, entries = self.rules(port, mss, queue)
        self.call('-S', 'OUTPUT')
        if self.call('-S', chain, check=False):
            self.call('-N', chain)
        self.call('-F', chain)
        for entry in entries:
            self.call('-A', *entry)
        if self.call('-C', *jump, check=False):
            self.call('-A', *jump)
        self.verify(port, mss, queue)

    def verify(self, port, mss, queue):
        _, jump, entries = self.rules(port, mss, queue)
        self.call('-C', *jump)
        for entry in entries:
            self.call('-C', *entry)

    def remove(self, port):
        chain, jump, _ = self.rules(port, 0, None)
        if self.dry_run:
            self.call('-D', *jump)
            self.call('-F', chain)
            self.call('-X', chain)
            return
        # Distinguish an absent chain from an inaccessible firewall backend.
        self.call('-S', 'OUTPUT')
        # Remove duplicates left by older/manual invocations, bounded on purpose.
        for _ in range(16):
            if self.call('-C', *jump, check=False):
                break
            self.call('-D', *jump)
        if not self.call('-S', chain, check=False):
            self.call('-F', chain)
            self.call('-X', chain)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('apply', 'remove', 'status'))
    parser.add_argument('--port', type=int, default=443,
                        help='Public listen port (nginx port if it is the TCP frontend)')
    parser.add_argument('--mss', type=int, default=88,
                        help='Advertised MSS, 64..1460; 0 disables clamping (default 88)')
    parser.add_argument('--nfqueue', action='store_true',
                        help='Queue downstream data to a separately running nfqws')
    parser.add_argument('--tcpmss-only', action='store_true',
                        help='Use a separate TCPMSS-only chain, independent of NFQUEUE')
    parser.add_argument('--queue-num', type=int, default=200)
    ipv6 = parser.add_mutually_exclusive_group()
    ipv6.add_argument('--ipv6', action='store_true',
                      help='Require IPv6 rules too; failure is reported, not ignored')
    ipv6.add_argument('--ipv6-optional', action='store_true',
                      help='Try IPv6 too; report failures while retaining verified IPv4 rules')
    parser.add_argument('--dry-run', action='store_true', help='Print commands without executing')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.mss != 0 and not 64 <= args.mss <= 1460:
        parser.error('--mss must be 0 or between 64 and 1460')
    if not 0 <= args.queue_num <= 65535:
        parser.error('--queue-num must be between 0 and 65535')
    if args.action == 'apply' and not args.mss and not args.nfqueue:
        parser.error('enable --mss or --nfqueue')
    if args.tcpmss_only and (args.nfqueue or (args.action != 'remove' and not args.mss)):
        parser.error('--tcpmss-only requires a nonzero MSS and cannot use --nfqueue')
    if not args.dry_run and sys.platform != 'linux':
        parser.error('firewall operations require Linux; use --dry-run to preview')
    if not args.dry_run and args.action != 'status' and os.geteuid() != 0:
        parser.error('firewall changes require root')

    firewalls = []
    for name in (['iptables', 'ip6tables'] if args.ipv6 or args.ipv6_optional else ['iptables']):
        binary = name if args.dry_run else shutil.which(name)
        if binary is None:
            if name == 'ip6tables' and args.ipv6_optional:
                print('IPv6 TCPMSS/DPI unavailable: ip6tables not found', file=sys.stderr)
                continue
            parser.error(name + ' is required')
        firewalls.append(Firewall(binary, args.dry_run, args.tcpmss_only))
    queue = args.queue_num if args.nfqueue else None
    try:
        for firewall in firewalls:
            try:
                if args.action == 'apply':
                    firewall.apply(args.port, args.mss, queue)
                elif args.action == 'remove':
                    firewall.remove(args.port)
                else:
                    firewall.verify(args.port, args.mss, queue)
                if firewall is not firewalls[0] and not args.dry_run:
                    print('IPv6 DPI rules: ' + args.action + ' OK')
            except (OSError, RuntimeError) as exc:
                if args.ipv6_optional and firewall is not firewalls[0]:
                    print('IPv6 TCPMSS/DPI not verified: ' + str(exc), file=sys.stderr)
                    if args.action == 'apply':
                        try:
                            firewall.remove(args.port)
                        except (OSError, RuntimeError) as cleanup_error:
                            print('IPv6 cleanup failed: ' + str(cleanup_error), file=sys.stderr)
                    continue
                raise
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        if args.action == 'apply':
            # Fail open: remove only our chains after a partially applied setup.
            for firewall in firewalls:
                try:
                    firewall.remove(args.port)
                except (OSError, RuntimeError) as cleanup_error:
                    print('Cleanup failed: ' + str(cleanup_error), file=sys.stderr)
        return 1
    if not args.dry_run:
        print('IPv4 DPI rules: ' + args.action + ' OK (does not verify bypass effectiveness)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
