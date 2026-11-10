#!/usr/bin/env python3
"""Build the supported zapret release, or bootstrap an NFQUEUE container."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ZAPRET_TAG = 'v72.12'
BINARY = Path('/opt/tg-ws-proxy-nfqws/nfqws')
MARKER = '# Managed by tg-ws-proxy setup_masking.py'
UNIT = Path('/etc/systemd/system/tg-ws-proxy-nfqws.service')
DPI_UNIT = Path('/etc/systemd/system/tg-ws-proxy-dpi.service')


def command(binary=BINARY, ttl=6):
    return [str(binary), '--qnum=200', '--dpi-desync=fake,split2',
            '--dpi-desync-any-protocol=1', '--dpi-desync-ttl=' + str(ttl),
            '--dpi-desync-split-pos=1', '--dpi-desync-fooling=md5sig,badseq']


def build():
    """Stage the build so a download/build failure retains the working binary."""
    version = BINARY.with_suffix('.version')
    if BINARY.exists() and version.exists() and version.read_text().strip() == ZAPRET_TAG:
        return
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'update'], check=True, env=env)
    subprocess.run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y',
                    'build-essential', 'git', 'ca-certificates', 'libnetfilter-queue-dev',
                    'libnfnetlink-dev', 'libcap-dev', 'libmnl-dev', 'zlib1g-dev'],
                   check=True, env=env)
    BINARY.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.build-', dir=str(BINARY.parent)) as folder:
        source = Path(folder) / 'zapret'
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', ZAPRET_TAG,
                        'https://github.com/bol-van/zapret.git', str(source)], check=True)
        subprocess.run(['make', '-C', str(source / 'nfq')], check=True)
        # --dry-run also requests NET_ADMIN/NET_RAW, unavailable in image builds.
        # Strategy validation runs at startup, with the runtime capabilities.
        subprocess.run([str(source / 'nfq/nfqws'), '--version'], check=True)
        staged = Path(folder) / 'nfqws'
        shutil.copyfile(source / 'nfq/nfqws', staged)
        staged.chmod(0o755)
        os.replace(staged, BINARY)
        staged_version = Path(folder) / 'version'
        staged_version.write_text(ZAPRET_TAG + '\n')
        os.replace(staged_version, version)


def units(directory, port, ttl):
    # Imported only by the host installer; the container needs no systemd.
    import json

    helper = json.dumps(str(directory / 'nfqueue.py').replace('%', '%%'))
    options = ' --port ' + str(port) + ' --mss 0 --nfqueue --ipv6-optional'
    service = (MARKER + '\n[Unit]\nDescription=TG WS Proxy nfqws TCP desync\n'
               'After=network.target\n\n[Service]\nType=simple\nExecStart='
               + ' '.join(command(ttl=ttl)) + '\nRestart=always\nRestartSec=3\n'
               'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW\n'
               'AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW\nNoNewPrivileges=true\n'
               'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\n'
               '\n[Install]\nWantedBy=multi-user.target\n')
    rules = (MARKER + '\n[Unit]\nDescription=TG WS Proxy NFQUEUE rules\n'
             'Requires=tg-ws-proxy-nfqws.service\n'
             'After=network.target tg-ws-proxy-nfqws.service\n'
             'Before=tg-ws-proxy.service\n\n[Service]\nType=oneshot\nRemainAfterExit=yes\n'
             'ExecStart=/usr/bin/python3 ' + helper + ' apply' + options + '\n'
             'ExecStop=/usr/bin/python3 ' + helper + ' remove' + options + '\n'
             'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW\nNoNewPrivileges=true\n'
             'ProtectSystem=strict\nProtectHome=true\n'
             '\n[Install]\nWantedBy=multi-user.target\n')
    return service, rules


def queue_ready():
    """The kernel lists only queues with an attached userspace handler."""
    try:
        return any(line.split() and int(line.split()[0]) == 200
                   for line in Path('/proc/net/netfilter/nfnetlink_queue').read_text().splitlines())
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'container', 'health'))
    parser.add_argument('--port', type=int, default=1443)
    parser.add_argument('--ttl', type=int, default=6)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 1 <= args.ttl <= 255:
        parser.error('port must be 1..65535 and TTL must be 1..255')
    if args.action == 'health':
        return 0 if queue_ready() else 1
    if args.action == 'build':
        build()
        return 0
    subprocess.run(command(ttl=args.ttl) + ['--dry-run'], check=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name('dpi.py')),
                    'apply', '--port', str(args.port), '--mss', '0', '--nfqueue',
                    '--ipv6-optional'], check=True)
    os.execv(str(BINARY), command(ttl=args.ttl))


if __name__ == '__main__':
    sys.exit(main())
