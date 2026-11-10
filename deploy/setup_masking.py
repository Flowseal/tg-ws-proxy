#!/usr/bin/env python3
"""Provision a local HTTPS cover and a Fake TLS proxy, on Linux or in Docker.

Uses only the Python standard library. --prepare-only writes a reviewable
deployment without installing packages, touching system configs or starting it.
"""
import argparse
import datetime
import hashlib
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[1]
try:
    from configuration import apply_sources, validate
except ModuleNotFoundError:  # Direct script invocation from outside the repo.
    sys.path.insert(0, str(REPO))
    from configuration import apply_sources, validate

try:
    from .linux import nfqws
except ImportError:  # Direct invocation: python3 deploy/setup_masking.py
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from deploy.linux import nfqws


MARKER = '# Managed by tg-ws-proxy setup_masking.py'
HOST_NGINX = Path('/etc/nginx/conf.d/tg-ws-proxy-masking.conf')
HOST_UNIT = Path('/etc/systemd/system/tg-ws-proxy.service')
HOST_TCPMSS_UNIT = Path('/etc/systemd/system/tg-ws-proxy-tcpmss.service')


def run(args, **kwargs):
    return subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def write_file(path, text, mode=0o644):
    """Atomic replacement, including private permissions before writing secrets."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def valid_domain(domain):
    labels = domain.split('.')
    return (len(domain) <= 253 and len(labels) >= 2 and len(labels[-1]) >= 2
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                    for part in labels)
            and any(char.isalpha() for char in labels[-1]))


def prepare_state(args):
    directory = args.state_dir
    path = directory / 'state.json'
    previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if previous and previous['mode'] != args.mode:
        raise ValueError('Use a different --state-dir when switching deployment modes')
    if (previous and previous['domain'] != args.domain and not args.force_domain_change):
        raise ValueError('Changing the domain invalidates shared links; '
                         'use --force-domain-change to do so explicitly')
    secret = args.secret or previous.get('secret') or secrets.token_hex(16)
    if not re.fullmatch('[0-9a-fA-F]{32}', secret):
        raise ValueError('Secret must be exactly 32 hex characters')
    directory.mkdir(parents=True, exist_ok=True)
    # nginx workers and the DynamicUser proxy need directory traversal. Secret
    # files and the TLS directory have their own restrictive permissions.
    directory.chmod(0o755)
    state = {'mode': args.mode, 'domain': args.domain, 'port': args.port,
             'public_host': args.public_host or args.domain,
             'public_port': args.public_port or args.port,
             'bind_address': args.bind_address,
             'masking_mode': args.masking_mode, 'mask_host': args.mask_host,
             'drs': not args.no_fake_tls_drs, 'split': not args.no_fake_tls_split,
             'split_delay_ms': args.fake_tls_split_delay_ms,
             'split_jitter_ms': args.fake_tls_split_jitter_ms,
             'upstream': {key: getattr(args, key, None) for key in
                          ('dc_ip', 'pool_size', 'cfproxy_domain', 'cfproxy_worker_domain',
                           'no_h2', 'no_cfproxy', 'no_secure')},
             'mask_port': args.mask_port, 'secret': secret.lower(),
             'tcpmss': not args.no_tcpmss, 'tcpmss_mss': args.tcpmss,
             'nfqws': not args.no_nfqws, 'nfqws_ttl': args.nfqws_ttl,
             'certificate': previous.get('certificate', 'self-signed')}
    # A failed first setup must not generate a new secret on retry. For an
    # existing deployment, commit changed settings only after TLS validation.
    if not previous:
        write_file(path, json.dumps(state, indent=2) + '\n', 0o600)
    return state


def certificate_matches(cert, domain):
    # openssl x509 -checkhost prints a mismatch but may still exit with code 0.
    result = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout',
                             '-checkhost', domain], stdout=subprocess.PIPE,
                            universal_newlines=True, check=False)
    return result.returncode == 0 and 'does match certificate' in result.stdout


def certificate_fresh(cert):
    result = subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout',
                             '-checkend', '86400'], stdout=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def prepare_certificate(directory, domain, cert=None, key=None, allow_generate=True):
    tls = directory / 'tls'
    tls.mkdir(exist_ok=True)
    tls.chmod(0o700)
    target_cert, target_key = tls / 'cert.pem', tls / 'key.pem'
    if cert:
        certificate, private_key = cert.read_text(), key.read_text()
        # Validate the pair and hostname before replacing the deployed files.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key), password='')
        if not certificate_matches(cert, domain) or not certificate_fresh(cert):
            raise ValueError('Provided certificate must match the domain and be valid for >24 hours')
        write_file(target_key, private_key, 0o600)
        write_file(target_cert, certificate)
        return 'provided'
    if target_cert.exists() and target_key.exists():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(target_cert), str(target_key), password='')
        if certificate_matches(target_cert, domain) and certificate_fresh(target_cert):
            return None
        if not allow_generate:
            raise ValueError('Stored certificate needs replacement; supply --cert and --key')
    elif target_cert.exists() or target_key.exists():
        raise ValueError('Incomplete TLS pair in ' + str(tls) + '; supply --cert and --key')
    with tempfile.TemporaryDirectory(dir=str(tls)) as temporary:
        temp = Path(temporary)
        run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
             'ec_paramgen_curve:prime256v1', '-nodes', '-days', '3650',
             '-subj', '/CN=' + domain, '-addext', 'subjectAltName=DNS:' + domain,
             '-keyout', temp / 'key.pem', '-out', temp / 'cert.pem'],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        write_file(target_key, (temp / 'key.pem').read_text(), 0o600)
        write_file(target_cert, (temp / 'cert.pem').read_text())
    return 'self-signed'


def prepare_site(directory, domain):
    site = directory / 'site'
    site.mkdir(exist_ok=True)
    site.chmod(0o755)
    if not (site / 'index.html').exists():
        title = secrets.choice(('Field Notes', 'Small Journal', 'Daily Notebook'))
        page = ('<!doctype html><html lang="en"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + title + '</title><style>body{max-width:48rem;margin:12vh auto;'
                'padding:2rem;font:18px/1.7 Georgia,serif;color:#263238;background:#faf9f6}'
                'h1{font-weight:normal}footer{margin-top:5rem;color:#68777d;font-size:14px}'
                '</style><h1>' + title + '</h1><p>A place for ideas, projects and everyday '
                'discoveries.</p><p>New entries will appear here soon.</p><footer>'
                + html.escape(domain) + ' · ' + str(datetime.date.today().year)
                + '</footer></html>\n')
        write_file(site / 'index.html', page)


def nginx_config(domain, directory, port, docker=False):
    root = '/usr/share/nginx/html' if docker else str(directory / 'site')
    tls = '/etc/nginx/tgws-tls' if docker else str(directory / 'tls')
    listen = str(port) if docker else '127.0.0.1:' + str(port)
    return (MARKER + '\nserver {\n'
            '    listen ' + listen + ' ssl default_server;\n'
            '    server_name ' + domain + ';\n'
            '    ssl_certificate ' + json.dumps(tls + '/cert.pem') + ';\n'
            '    ssl_certificate_key ' + json.dumps(tls + '/key.pem') + ';\n'
            '    ssl_protocols TLSv1.2 TLSv1.3;\n'
            '    root ' + json.dumps(root) + ';\n'
            '    index index.html;\n'
            '    location / { try_files $uri $uri/ =404; }\n'
            '    access_log off;\n}\n')


def proxy_env(state):
    docker = state['mode'] == 'docker'
    values = {'TG_WS_PROXY_HOST': '0.0.0.0' if docker else state['bind_address'],
              'TG_WS_PROXY_PORT': '1443' if docker else str(state['port']),
              'TG_WS_PROXY_PUBLIC_HOST': state['public_host'],
              'TG_WS_PROXY_PUBLIC_PORT': str(state.get('public_port', state['port'])),
              'TG_WS_PROXY_SECRET': state['secret'],
              'TG_WS_PROXY_FAKE_TLS_DOMAIN': state['domain'],
              'TG_WS_PROXY_FAKE_TLS_DRS': str(state.get('drs', True)).lower(),
              'TG_WS_PROXY_FAKE_TLS_SPLIT': str(state.get('split', True)).lower(),
              'TG_WS_PROXY_FAKE_TLS_SPLIT_DELAY_MS': str(state.get('split_delay_ms', 5)),
              'TG_WS_PROXY_FAKE_TLS_SPLIT_JITTER_MS': str(state.get('split_jitter_ms', 10)),
              'TG_WS_PROXY_FAKE_TLS_MASK_HOST': (
                  state.get('mask_host') or state['domain']
                  if state.get('masking_mode') == 'external'
                  else 'cover' if docker else '127.0.0.1'),
              'TG_WS_PROXY_FAKE_TLS_MASK_PORT': str(state['mask_port'])}
    mappings = {'dc_ip': 'DC_IPS', 'pool_size': 'POOL_SIZE', 'cfproxy_domain': 'CF_DOMAIN',
                'cfproxy_worker_domain': 'CF_WORKER', 'no_h2': 'NO_H2',
                'no_cfproxy': 'NO_CFPROXY', 'no_secure': 'NO_SECURE'}
    for name, value in state.get('upstream', {}).items():
        if value is not None:
            values['TG_WS_PROXY_' + mappings[name]] = (
                ' '.join(value) if isinstance(value, list) else str(value).lower())
    return ''.join(key + '=' + value + '\n' for key, value in values.items())


def unit_arg(value):
    return json.dumps(str(value).replace('%', '%%'))


def proxy_unit(directory, tcpmss=True, desync=True, local_masking=True):
    dependency = ' tg-ws-proxy-tcpmss.service' if tcpmss else ''
    if desync:
        dependency += ' tg-ws-proxy-dpi.service'
    dependency = (' nginx.service' if local_masking else '') + dependency
    return (MARKER + '\n[Unit]\nDescription=TG WS Proxy with local HTTPS masking\n'
            'Requires=' + dependency.strip() + '\n'
            'After=network-online.target' + dependency + '\n'
            'Wants=network-online.target\n\n[Service]\nType=simple\nDynamicUser=yes\n'
            'WorkingDirectory=' + unit_arg(directory / 'runtime') + '\n'
            'EnvironmentFile=' + unit_arg(directory / 'proxy.env') + '\n'
            'Environment=PYTHONDONTWRITEBYTECODE=1\n'
            'ExecStart=' + unit_arg(directory / 'venv/bin/python') + ' '
            + unit_arg(directory / 'runtime/run_proxy.py') + '\n'
            'AmbientCapabilities=CAP_NET_BIND_SERVICE\n'
            'CapabilityBoundingSet=CAP_NET_BIND_SERVICE\nNoNewPrivileges=true\n'
            'ProtectSystem=strict\nProtectHome=true\nPrivateTmp=true\n'
            'Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n')


def tcpmss_unit(directory, port, mss):
    command = '/usr/bin/python3 ' + unit_arg(directory / 'tcpmss.py')
    options = ' --tcpmss-only --port ' + str(port) + ' --ipv6-optional'
    return (MARKER + '\n[Unit]\nDescription=TG WS Proxy TCPMSS clamp\n'
            'After=network.target\nBefore=tg-ws-proxy.service\n\n'
            '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
            'ExecStart=' + command + ' apply' + options + ' --mss ' + str(mss) + '\n'
            'ExecStop=' + command + ' remove' + options + '\n'
            'CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW\n'
            'NoNewPrivileges=true\nProtectSystem=strict\nProtectHome=true\n'
            '\n[Install]\nWantedBy=multi-user.target\n')


def compose_config(directory, state):
    health = ("import ssl,socket; "
              "c=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); c.check_hostname=False; "
              "c.verify_mode=ssl.CERT_NONE; "
              "s=c.wrap_socket(socket.create_connection(('127.0.0.1',1443),timeout=5),"
              "server_hostname=" + repr(state['domain']) + "); "
              "s.sendall(" + repr(('GET / HTTP/1.1\r\nHost: ' + state['domain']
                                   + '\r\nConnection: close\r\n\r\n').encode('ascii')) + "); "
              "r=s.makefile('rb').readline(4096).split(); "
              "assert r[0] in (b'HTTP/1.0',b'HTTP/1.1') and "
              + ("100<=int(r[1])<=599" if state.get('masking_mode') == 'external'
                 else "r[1]==b'200'"))
    config = {
        'name': 'tgws-mask-' + hashlib.sha256(str(directory).encode()).hexdigest()[:8],
        'services': {
            'cover': {
                'image': 'nginx:stable-alpine', 'restart': 'unless-stopped',
                'volumes': ['./nginx.conf:/etc/nginx/conf.d/default.conf:ro',
                            './tls:/etc/nginx/tgws-tls:ro',
                            './site:/usr/share/nginx/html:ro'],
                'networks': ['cover'],
                'healthcheck': {
                    'test': ['CMD-SHELL', 'wget -q --no-check-certificate -O /dev/null '
                             'https://127.0.0.1:' + str(state['mask_port']) + '/'],
                    'interval': '5s', 'timeout': '4s', 'retries': 12,
                },
            },
            'proxy': {
                'build': {'context': str(REPO)}, 'restart': 'unless-stopped',
                'env_file': ['./proxy.env'],
                'ports': [{'target': 1443, 'published': str(state['port']),
                           'host_ip': state['bind_address'], 'protocol': 'tcp'}],
                'depends_on': {'cover': {'condition': 'service_healthy'}},
                'networks': ['default', 'cover'],
                'healthcheck': {'test': ['CMD', '/opt/venv/bin/python', '-c', health],
                                'interval': '10s', 'timeout': '8s',
                                'retries': 6, 'start_period': '10s'},
            },
        },
        'networks': {'cover': {'internal': True}, 'default': {}},
    }
    if state.get('tcpmss', True):
        proxy = config['services']['proxy']
        proxy['user'] = '0:0'
        proxy['cap_add'] = ['NET_ADMIN']
        proxy['security_opt'] = ['no-new-privileges:true']
        proxy['environment'] = {'TG_WS_PROXY_TCPMSS': 'true',
                                'TG_WS_PROXY_TCPMSS_MSS': str(state.get('tcpmss_mss', 88))}
    if state.get('nfqws', True):
        proxy = config['services']['proxy']
        # Queue packets in the same namespace as the listener, before bridge NAT.
        config['services']['nfqws'] = {
            'build': {'context': str(REPO), 'target': 'nfqws'},
            'init': True,
            'restart': 'unless-stopped',
            'cap_drop': ['ALL'], 'cap_add': ['NET_ADMIN', 'NET_RAW'],
            'security_opt': ['no-new-privileges:true'],
            'command': ['--port', '1443', '--ttl', str(state.get('nfqws_ttl', 6))],
            'ports': proxy.pop('ports'), 'networks': proxy.pop('networks'),
            'healthcheck': {
                'test': ['CMD', 'python', '/app/deploy/linux/nfqws.py', 'health'],
                'interval': '5s', 'timeout': '3s', 'retries': 12,
            },
        }
        proxy['network_mode'] = 'service:nfqws'
        proxy['depends_on']['nfqws'] = {'condition': 'service_healthy', 'restart': True}
    if state.get('masking_mode') == 'external':
        del config['services']['cover']
        config['services']['proxy']['depends_on'].pop('cover')
        owner = config['services'].get('nfqws', config['services']['proxy'])
        owner['networks'] = ['default']
        del config['networks']['cover']
    return config


def install_host_packages(local_masking=True):
    if not shutil.which('apt-get') or not shutil.which('systemctl'):
        raise ValueError('Host mode requires Debian/Ubuntu with apt-get and systemd')
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
    run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'update'], env=env)
    run(['apt-get', '-o', 'DPkg::Lock::Timeout=120', 'install', '-y',
         'openssl', 'python3-venv', 'ca-certificates', 'iptables']
        + (['nginx'] if local_masking else []), env=env)


def check_managed_host_files():
    # Never replace unrelated operator files that happen to use our filenames.
    for path in (HOST_NGINX, HOST_UNIT, HOST_TCPMSS_UNIT, nfqws.UNIT, nfqws.DPI_UNIT):
        if path.exists() and not path.read_text().startswith(MARKER):
            raise ValueError('Refusing to overwrite unmanaged file: ' + str(path))


def install_tcpmss(directory, port, mss, enabled, previous=None):
    previous = previous or {}
    helper = directory / 'tcpmss.py'
    shutil.copyfile(REPO / 'deploy/linux/dpi.py', helper)
    existed = HOST_TCPMSS_UNIT.exists()
    if existed:
        run(['systemctl', 'stop', 'tg-ws-proxy-tcpmss.service'])
    # Stop uses the old unit's port. Also clean stale owned rules if the unit
    # was inactive or disappeared; never touch the independent NFQUEUE chain.
    ports = {port}
    if previous.get('tcpmss'):
        ports.add(previous['port'])
    for old_port in sorted(ports):
        run(['/usr/bin/python3', helper, 'remove', '--tcpmss-only',
             '--port', str(old_port), '--ipv6-optional'])
    if not enabled:
        if existed:
            run(['systemctl', 'disable', 'tg-ws-proxy-tcpmss.service'])
            HOST_TCPMSS_UNIT.unlink()
            run(['systemctl', 'daemon-reload'])
        return
    write_file(HOST_TCPMSS_UNIT, tcpmss_unit(directory, port, mss))
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'tg-ws-proxy-tcpmss.service'])
    run(['systemctl', 'restart', 'tg-ws-proxy-tcpmss.service'])
    # Do not report success solely from an active oneshot service.
    run(['/usr/bin/python3', helper, 'status', '--tcpmss-only', '--port', str(port),
         '--mss', str(mss), '--ipv6-optional'])


def install_nfqws(directory, port, ttl, enabled, previous=None):
    helper = directory / 'nfqueue.py'
    shutil.copyfile(REPO / 'deploy/linux/dpi.py', helper)
    # Build before stopping the installed services, retaining a working binary
    # and rules if fetching/compiling fails.
    if enabled:
        nfqws.build()
        run(nfqws.command(ttl=ttl) + ['--dry-run'])
    for path in (nfqws.DPI_UNIT, nfqws.UNIT):
        if path.exists():
            run(['systemctl', 'stop', path.name])
    ports = {port}
    if previous and previous.get('nfqws'):
        ports.add(previous['port'])
    for old_port in sorted(ports):
        run(['/usr/bin/python3', helper, 'remove', '--port', str(old_port), '--ipv6-optional'])
    if not enabled:
        for path in (nfqws.DPI_UNIT, nfqws.UNIT):
            if path.exists():
                run(['systemctl', 'disable', path.name])
                path.unlink()
        run(['systemctl', 'daemon-reload'])
        return
    for path, content in zip((nfqws.UNIT, nfqws.DPI_UNIT), nfqws.units(directory, port, ttl)):
        write_file(path, content)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', nfqws.UNIT.name, nfqws.DPI_UNIT.name])
    run(['systemctl', 'restart', nfqws.DPI_UNIT.name])
    # Type=simple alone does not demonstrate that the queue has been attached.
    deadline = time.monotonic() + 10
    while not nfqws.queue_ready():
        if time.monotonic() >= deadline:
            raise RuntimeError('nfqws did not attach NFQUEUE 200; inspect journalctl -u '
                               + nfqws.UNIT.name)
        time.sleep(.1)
    run(['systemctl', 'is-active', '--quiet', nfqws.UNIT.name])
    run(['/usr/bin/python3', helper, 'status', '--port', str(port), '--mss', '0',
         '--nfqueue', '--ipv6-optional'])


def install_host(directory, *, tcpmss=True, mss=88, port=443, previous=None,
                 desync=True, ttl=6, local_masking=True):
    check_managed_host_files()
    run([sys.executable, '-m', 'venv', directory / 'venv'])
    python = directory / 'venv/bin/python'
    run([python, '-m', 'pip', 'install', 'cryptography==46.0.5', 'certifi',
         'httpx[http2]==0.28.1'])
    runtime = directory / 'runtime'
    runtime.mkdir(exist_ok=True)
    for package in ('proxy', 'utils', 'configuration'):
        shutil.copytree(REPO / package, runtime / package, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(REPO / 'deploy/run_proxy.py', runtime / 'run_proxy.py')
    previous_nginx = HOST_NGINX.read_text() if HOST_NGINX.exists() else None
    if local_masking or previous_nginx is not None:
        nginx = shutil.which('nginx') or '/usr/sbin/nginx'
        run([nginx, '-t'])
        try:
            if local_masking:
                write_file(HOST_NGINX, (directory / 'nginx.conf').read_text())
            else:
                HOST_NGINX.unlink()
            run([nginx, '-t'])
            if local_masking:
                run(['systemctl', 'enable', 'nginx'])
            run(['systemctl', 'reload-or-restart', 'nginx'])
        except (OSError, subprocess.CalledProcessError):
            if previous_nginx is None:
                HOST_NGINX.unlink(missing_ok=True)
            else:
                write_file(HOST_NGINX, previous_nginx)
            raise
    install_tcpmss(directory, port, mss, tcpmss, previous)
    install_nfqws(directory, port, ttl, desync, previous)
    write_file(HOST_UNIT, (directory / 'tg-ws-proxy.service').read_text())
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', 'tg-ws-proxy.service'])
    run(['systemctl', 'restart', 'tg-ws-proxy.service'])


def check_cover(domain, port, host='127.0.0.1', timeout=30, external=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3) as raw:
                with context.wrap_socket(raw, server_hostname=domain) as tls:
                    tls.sendall(('GET / HTTP/1.1\r\nHost: ' + domain
                                 + '\r\nConnection: close\r\n\r\n').encode('ascii'))
                    response = b''
                    while b'\r\n' not in response and len(response) < 4096:
                        chunk = tls.recv(1024)
                        if not chunk:
                            break
                        response += chunk
                    status = response.split(b'\r\n', 1)[0]
                    valid_http = bool(re.fullmatch(rb'HTTP/1\.[01] [1-5][0-9]{2}(?: .*)?', status))
                    if not valid_http or (not external and b' 200 ' not in status):
                        raise ValueError('Cover site returned ' + repr(status))
                    return
        except (OSError, ValueError) as exc:
            error = exc
            time.sleep(.5)
    raise RuntimeError('HTTPS cover check through the proxy failed: ' + str(error))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--config', type=Path, help='TOML configuration file')
    parser.add_argument('--mode', choices=('host', 'docker'), default='host')
    parser.add_argument('--fake-tls-domain', '--domain', dest='domain', default='',
                        help='Fake TLS SNI domain (--domain is a compatibility alias)')
    parser.add_argument('--port', type=int, default=443, help='Public port (default 443)')
    parser.add_argument('--bind-address', default='0.0.0.0', help='Public listen IP')
    parser.add_argument('--public-host', help='Address in the Telegram link (default: SNI domain)')
    parser.add_argument('--public-port', type=int, help='Port in the Telegram link (default: --port)')
    parser.add_argument('--masking-mode', choices=('local', 'external'), default='local')
    parser.add_argument('--mask-host', help='External HTTPS backend (default: Fake TLS domain)')
    parser.add_argument('--mask-port', type=int, help='Backend port (local: 8443, external: 443)')
    parser.add_argument('--no-fake-tls-drs', dest='no_fake_tls_drs', action='store_true')
    parser.add_argument('--fake-tls-drs', dest='no_fake_tls_drs', action='store_false')
    parser.add_argument('--no-fake-tls-split', dest='no_fake_tls_split', action='store_true')
    parser.add_argument('--fake-tls-split', dest='no_fake_tls_split', action='store_false')
    parser.add_argument('--fake-tls-split-delay-ms', type=int, default=5)
    parser.add_argument('--fake-tls-split-jitter-ms', type=int, default=10)
    parser.add_argument('--dc-ip', action='append')
    parser.add_argument('--pool-size', type=int)
    parser.add_argument('--cfproxy-domain', action='append')
    parser.add_argument('--cfproxy-worker-domain', action='append')
    for name in ('h2', 'cfproxy', 'secure'):
        parser.add_argument('--no-' + name, dest='no_' + name, action='store_true', default=None)
        parser.add_argument('--' + name, dest='no_' + name, action='store_false')
    parser.add_argument('--tcpmss', type=int, default=88,
                        help='Advertised MSS (64..1460, default 88)')
    parser.add_argument('--no-tcpmss', action='store_true',
                        help='Disable TCPMSS and remove previously installed automatic rules')
    parser.add_argument('--no-nfqws', action='store_true',
                        help='Disable nfqws and remove previously installed automatic NFQUEUE rules')
    parser.add_argument('--nfqws', dest='no_nfqws', action='store_false')
    parser.add_argument('--nfqws-ttl', type=int, default=6,
                        help='Fake packet TTL (1..255, default 6)')
    parser.add_argument('--state-dir', type=Path)
    parser.add_argument('--secret', help='32 hex chars; generated once if omitted')
    parser.add_argument('--cert', type=Path, help='Existing PEM certificate/full chain')
    parser.add_argument('--key', type=Path, help='Existing unencrypted PEM private key')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--force-domain-change', action='store_true')
    argv = list(sys.argv[1:] if argv is None else argv)
    apply_sources(parser, argv, setup=True)
    args = parser.parse_args(argv)
    if args.mask_port is None:
        args.mask_port = 8443 if args.masking_mode == 'local' else 443
    if args.tcpmss == 0:
        args.no_tcpmss, args.tcpmss = True, 88
    if args.masking_mode == 'local' and args.mask_host:
        parser.error('--mask-host requires --masking-mode external')
    if args.masking_mode == 'external' and (args.cert or args.key):
        parser.error('--cert/--key apply only to local masking')
    try:
        if args.dc_ip is not None:
            validate('upstream.dc_ips', args.dc_ip)
        if args.pool_size is not None:
            validate('upstream.pool_size', args.pool_size)
        if args.mask_host is not None:
            validate('masking.backend_host', args.mask_host)
    except ValueError as exc:
        parser.error(str(exc))
    args.domain = args.domain.strip().lower()
    if not valid_domain(args.domain):
        parser.error('--fake-tls-domain must be an ASCII DNS name (punycode for IDNs)')
    if args.public_port is not None and not 1 <= args.public_port <= 65535:
        parser.error('--public-port must be 1..65535')
    for name in ('fake_tls_split_delay_ms', 'fake_tls_split_jitter_ms'):
        if not 0 <= getattr(args, name) <= 1000:
            parser.error(name + ' must be 0..1000')
    if args.public_host:
        try:
            ipaddress.ip_address(args.public_host)
        except ValueError:
            if not valid_domain(args.public_host.lower()):
                parser.error('--public-host must be an IP address or ASCII DNS name')
    try:
        ipaddress.ip_address(args.bind_address)
    except ValueError:
        parser.error('--bind-address must be an IP address')
    if not 1 <= args.port <= 65535 or not 1 <= args.mask_port <= 65535:
        parser.error('--port and --mask-port must be 1..65535')
    if args.masking_mode == 'local' and args.mask_port < 1024:
        parser.error('local --mask-port must be 1024..65535')
    if not 64 <= args.tcpmss <= 1460:
        parser.error('--tcpmss must be between 64 and 1460')
    if not 1 <= args.nfqws_ttl <= 255:
        parser.error('--nfqws-ttl must be between 1 and 255')
    if args.masking_mode == 'local' and args.mode == 'host' and args.port == args.mask_port:
        parser.error('public and masking ports must differ in host mode')
    if bool(args.cert) != bool(args.key):
        parser.error('--cert and --key must be supplied together')
    if args.secret and not re.fullmatch('[0-9a-fA-F]{32}', args.secret):
        parser.error('--secret must be exactly 32 hex characters')
    if args.state_dir is None:
        args.state_dir = (REPO / '.tgws-masking' if args.mode == 'docker'
                          else Path('/var/lib/tg-ws-proxy-masking'))
    args.state_dir = args.state_dir.expanduser().resolve()
    if any(char in str(args.state_dir) for char in '$;"\r\n'):
        parser.error('--state-dir contains unsupported configuration characters')
    if not args.prepare_only and args.mode == 'host':
        if sys.platform != 'linux' or os.geteuid() != 0:
            parser.error('host installation requires root on Linux; use --prepare-only to preview')
        if any(args.state_dir == Path(root) or Path(root) in args.state_dir.parents
               for root in ('/home', '/root', '/tmp', '/var/tmp', '/run/user')):
            parser.error('host --state-dir must be outside private home/tmp directories')
    try:
        state_path = args.state_dir / 'state.json'
        previous = json.loads(state_path.read_text()) if state_path.exists() else {}
        state = prepare_state(args)
        if not args.prepare_only:
            if args.mode == 'host':
                check_managed_host_files()
                install_host_packages(local_masking=args.masking_mode == 'local')
            else:
                run(['docker', 'compose', 'version'], stdout=subprocess.DEVNULL)
        if args.masking_mode == 'local' and not shutil.which('openssl'):
            raise ValueError('openssl is required to prepare/validate certificates')
        if args.masking_mode == 'local':
            certificate = prepare_certificate(args.state_dir, args.domain, args.cert, args.key,
                                              allow_generate=state['certificate'] != 'provided')
            if certificate:
                state['certificate'] = certificate
            prepare_site(args.state_dir, args.domain)
        else:
            # Keep provenance of an existing local certificate for switching
            # back: a provided certificate must never become auto-replaceable.
            state['certificate'] = previous.get('certificate', 'external')
        write_file(args.state_dir / 'proxy.env', proxy_env(state), 0o600)
        if args.masking_mode == 'local':
            write_file(args.state_dir / 'nginx.conf', nginx_config(
                args.domain, args.state_dir, args.mask_port, args.mode == 'docker'))
        if args.mode == 'docker':
            compose = args.state_dir / 'compose.yaml'
            write_file(compose, json.dumps(compose_config(args.state_dir, state), indent=2) + '\n')
            if not args.prepare_only:
                run(['docker', 'compose', '-f', compose, 'up', '-d', '--build',
                     '--force-recreate', '--remove-orphans', '--wait', '--wait-timeout', '90'])
        else:
            write_file(args.state_dir / 'tg-ws-proxy.service',
                       proxy_unit(args.state_dir, state['tcpmss'], state['nfqws'],
                                  args.masking_mode == 'local'))
            for name, content in zip(('tg-ws-proxy-nfqws.service', 'tg-ws-proxy-dpi.service'),
                                     nfqws.units(args.state_dir, args.port, args.nfqws_ttl)):
                if state['nfqws']:
                    write_file(args.state_dir / name, content)
                else:
                    (args.state_dir / name).unlink(missing_ok=True)
            if state['tcpmss']:
                write_file(args.state_dir / 'tg-ws-proxy-tcpmss.service',
                           tcpmss_unit(args.state_dir, args.port, args.tcpmss))
            else:
                (args.state_dir / 'tg-ws-proxy-tcpmss.service').unlink(missing_ok=True)
            if not args.prepare_only:
                install_host(args.state_dir, tcpmss=state['tcpmss'], mss=args.tcpmss,
                             port=args.port, previous=previous,
                             desync=state['nfqws'], ttl=args.nfqws_ttl,
                             local_masking=args.masking_mode == 'local')
        if not args.prepare_only:
            host = args.bind_address
            if host in ('0.0.0.0', '::'):
                host = '::1' if host == '::' else '127.0.0.1'
            check_cover(args.domain, args.port, host=host, external=args.masking_mode == 'external')
        write_file(args.state_dir / 'state.json', json.dumps(state, indent=2) + '\n', 0o600)
        print(('Prepared' if args.prepare_only else 'Started and HTTPS-checked')
              + ': ' + str(args.state_dir))
        print('Certificate: ' + ('external backend' if args.masking_mode == 'external'
                                 else state['certificate']))
        print('TCPMSS: ' + (str(args.tcpmss) if state['tcpmss'] else 'disabled')
              + (' (not applied; prepare-only)' if args.prepare_only else ''))
        print('nfqws: ' + ('enabled, TTL ' + str(args.nfqws_ttl) if state['nfqws'] else 'disabled')
              + (' (not applied; prepare-only)' if args.prepare_only else ''))
        print('Connection link (private): tg://proxy?' + urlencode({
            'server': state['public_host'], 'port': state['public_port'],
            'secret': 'ee' + state['secret'] + args.domain.encode('ascii').hex()}))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print('Setup failed: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
