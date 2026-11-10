"""Self-contained release Compose lifecycle; no host-side installer needed."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import sys
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from configuration import read_settings, validate
except ImportError:
    raise SystemExit('Image is missing the configuration package') from None

STATE = Path('/var/lib/tg-ws-proxy')
CONFIG = Path('/etc/tg-ws-proxy/config.toml')
READY = Path('/tmp/tgws-ready')
APP_UID = APP_GID = 10001


def load_source(path=CONFIG):
    values = read_settings(path)
    domain = values.get('fake_tls.domain', '').strip().lower()
    labels = domain.split('.')
    if (not domain.isascii() or len(domain) > 253 or len(labels) < 2
            or not any(c.isalpha() for c in labels[-1]) or len(labels[-1]) < 2
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', s)
                   for s in labels)):
        raise ValueError('fake_tls.domain must be an ASCII DNS name')
    values['fake_tls.domain'] = domain
    mode = values.setdefault('masking.mode', 'local')
    values.setdefault('masking.port', 8443 if mode == 'local' else 443)
    if mode == 'local' and (values['masking.port'] < 1024 or values.get('masking.backend_host')):
        raise ValueError('Local masking needs port >=1024 and no backend_host')
    cert, key = values.get('masking.certificate'), values.get('masking.private_key')
    if bool(cert) != bool(key) or (mode == 'external' and (cert or key)):
        raise ValueError('certificate/private_key must be paired and require local masking')
    secret = values.get('access.secret')
    if secret and not re.fullmatch('[0-9a-fA-F]{32}', secret):
        raise ValueError('access.secret must be exactly 32 hex characters')
    public_host = values.setdefault('server.public_host', domain)
    if not public_host or any(c in public_host for c in ' /?#&='):
        raise ValueError('server.public_host must be a hostname or IP address')
    try:
        ipaddress.ip_address(public_host)
    except ValueError:
        if not re.fullmatch(r'[a-zA-Z0-9.-]+', public_host):
            raise ValueError('server.public_host must be a hostname or IP address') from None
    return values


def snapshot():
    values = json.loads((STATE / 'settings.json').read_text())
    for key, value in values.items():
        validate(key, value)
    return values


def prepare_cover(values):
    # These helpers use only stdlib/OpenSSL and never install packages here.
    from deploy.setup_masking import prepare_certificate, prepare_site, nginx_config, write_file

    STATE.mkdir(parents=True, exist_ok=True)
    STATE.chmod(0o755)
    secret_file = STATE / 'secret'
    secret = (values.get('access.secret') or
              (secret_file.read_text().strip() if secret_file.exists() else secrets.token_hex(16)))
    if not re.fullmatch('[0-9a-fA-F]{32}', secret):
        raise ValueError('Stored secret is invalid; restore the state volume')
    metadata = STATE / 'cover.json'
    previous = json.loads(metadata.read_text()) if metadata.exists() else {}
    provenance = previous.get('certificate', 'self-signed')
    if values['masking.mode'] == 'local':
        cert = Path(values['masking.certificate']) if values.get('masking.certificate') else None
        key = Path(values['masking.private_key']) if values.get('masking.private_key') else None
        kind = prepare_certificate(STATE, values['fake_tls.domain'], cert, key,
                                   allow_generate=provenance != 'provided')
        provenance = kind or provenance
        prepare_site(STATE, values['fake_tls.domain'])
        nginx = nginx_config(values['fake_tls.domain'], STATE, values['masking.port'])
        nginx = nginx.replace('listen 127.0.0.1:', 'listen ')
        write_file(Path('/etc/nginx/conf.d/tg-ws-proxy.conf'), nginx)
        Path('/etc/nginx/sites-enabled/default').unlink(missing_ok=True)
        subprocess.run(['nginx', '-t'], check=True)
    # Commit private state only after certificate/config validation succeeds.
    write_file(secret_file, secret.lower() + '\n', 0o600)
    os.chown(secret_file, APP_UID, APP_GID)
    write_file(metadata, json.dumps({'certificate': provenance}) + '\n', 0o600)
    public = {key: value for key, value in values.items() if key != 'access.secret'}
    write_file(STATE / 'settings.json', json.dumps(public) + '\n')
    READY.write_text('ready\n')


def firewall(action, *options):
    helper = ROOT / 'deploy/linux/dpi.py'
    subprocess.run([sys.executable, str(helper), action, '--port', '1443',
                    '--ipv6-optional', *options], check=True)


def start_network(values):
    from deploy.linux.nfqws import BINARY, command

    mss = values.get('deployment.tcpmss', 88)
    firewall('remove', '--tcpmss-only')
    if mss:
        firewall('apply', '--tcpmss-only', '--mss', str(mss))
    if values.get('deployment.nfqws', True):
        args = command(ttl=values.get('deployment.nfqws_ttl', 6))
        subprocess.run(args + ['--dry-run'], check=True)
        firewall('apply', '--mss', '0', '--nfqueue')
        READY.write_text('ready\n')
        os.execv(str(BINARY), args)
    firewall('remove')
    READY.write_text('ready\n')
    os.execlp('sleep', 'sleep', 'infinity')


def published_port(values):
    port = values.get('server.public_port') or int(os.environ.get('TG_WS_PROXY_PUBLISHED_PORT', '443'))
    if not 1 <= port <= 65535:
        raise ValueError('Published/public port must be 1..65535')
    return port


def link(values):
    secret = (STATE / 'secret').read_text().strip()
    return 'tg://proxy?' + urlencode({
        'server': values['server.public_host'], 'port': published_port(values),
        'secret': 'ee' + secret + values['fake_tls.domain'].encode('ascii').hex()})


def proxy_args(values):
    args = ['--host', '0.0.0.0', '--port', '1443',
            '--public-host', values['server.public_host'],
            '--public-port', str(published_port(values)),
            '--secret', (STATE / 'secret').read_text().strip(),
            '--fake-tls-domain', values['fake_tls.domain'],
            '--fake-tls-mask-host', ('cover' if values['masking.mode'] == 'local'
                                     else values.get('masking.backend_host') or values['fake_tls.domain']),
            '--fake-tls-mask-port', str(values['masking.port'])]
    for key, flag in (('fake_tls.split_delay_ms', '--fake-tls-split-delay-ms'),
                      ('fake_tls.split_jitter_ms', '--fake-tls-split-jitter-ms'),
                      ('upstream.pool_size', '--pool-size')):
        if key in values:
            args.extend([flag, str(values[key])])
    for key, flag in (('fake_tls.drs', '--no-fake-tls-drs'),
                      ('fake_tls.server_hello_split', '--no-fake-tls-split'),
                      ('upstream.cfproxy', '--no-cfproxy'), ('upstream.h2', '--no-h2'),
                      ('upstream.secure', '--no-secure')):
        if not values.get(key, True):
            args.append(flag)
    for key, flag in (('upstream.dc_ips', '--dc-ip'), ('upstream.domains', '--cfproxy-domain'),
                      ('upstream.workers', '--cfproxy-worker-domain')):
        if key == 'upstream.dc_ips' and values.get(key) == []:
            args.append(flag)
        for value in values.get(key, []):
            args.extend([flag, value])
    return args


def health(role, values):
    if role == 'nfqws':
        if not READY.exists():
            return False
        from deploy.linux.dpi import Firewall
        mss = values.get('deployment.tcpmss', 88)
        if mss:
            Firewall('iptables', tcpmss_only=True).verify(1443, mss, None)
        if values.get('deployment.nfqws', True):
            from deploy.linux.nfqws import queue_ready
            Firewall('iptables').verify(1443, 0, 200)
            return queue_ready()
        return True
    if role == 'cover' and values['masking.mode'] == 'external':
        return READY.exists()
    port = values['masking.port'] if role == 'cover' else 1443
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(('127.0.0.1', port), timeout=3) as raw:
        with context.wrap_socket(raw, server_hostname=values['fake_tls.domain']) as tls:
            tls.sendall(('GET / HTTP/1.1\r\nHost: ' + values['fake_tls.domain']
                         + '\r\nConnection: close\r\n\r\n').encode('ascii'))
            status = tls.makefile('rb').readline(4096).split()
            return (len(status) >= 2 and status[0] in (b'HTTP/1.0', b'HTTP/1.1')
                    and (100 <= int(status[1]) <= 599 if values['masking.mode'] == 'external'
                         else status[1] == b'200'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('cover', 'nfqws', 'proxy', 'health', 'link'))
    parser.add_argument('role', nargs='?', choices=('cover', 'nfqws', 'proxy'))
    args = parser.parse_args()
    try:
        if args.action in ('cover', 'nfqws'):
            READY.unlink(missing_ok=True)
        if args.action == 'cover':
            values = load_source()
            prepare_cover(values)
            if values['masking.mode'] == 'local':
                os.execlp('nginx', 'nginx', '-g', 'daemon off;')
            os.execlp('sleep', 'sleep', 'infinity')
        values = snapshot()
        if args.action == 'health':
            return 0 if args.role and health(args.role, values) else 1
        if args.action == 'link':
            print(link(values))
            return 0
        if args.action == 'nfqws':
            start_network(values)
        if args.action == 'proxy':
            argv = proxy_args(values)
            # The mounted source config is read by cover, not the listener.
            # Runtime values here are the validated snapshot plus fixed topology.
            for key in list(os.environ):
                if key.startswith('TG_WS_PROXY_'):
                    del os.environ[key]
            os.execv(sys.executable, [sys.executable, '-u', '-m', 'proxy.tg_ws_proxy', *argv])
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        if args.action != 'health':
            print('Container startup failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
