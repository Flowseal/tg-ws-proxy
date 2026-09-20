"""Android-only CfProxy connectivity checks, independent of desktop UI."""

import base64
import certifi
import os
import re
import socket
import ssl

from proxy.balancer import balancer
from proxy.config import CFPROXY_DEFAULT_DOMAINS


TEST_DCS = (1, 2, 3, 4, 5, 203)
WORKER_DST = {
    1: '149.154.175.50', 2: '149.154.167.51',
    3: '149.154.175.100', 4: '149.154.167.91',
    5: '149.154.171.5', 203: '91.105.192.100',
}


def _valid_domain(domain):
    return (len(domain) <= 253 and all(
        len(label) <= 63 and re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?', label)
        for label in domain.split('.')))


def _probe_case(dc, host, sni, request_host, path, secure):
    port = 443 if secure else 80
    try:
        with socket.create_connection((host, port), timeout=5) as raw:
            connection = (ssl.create_default_context(cafile=certifi.where())
                          .wrap_socket(raw, server_hostname=sni) if secure else raw)
            with connection as sock:
                key = base64.b64encode(os.urandom(16)).decode('ascii')
                request = (
                    f'GET {path} HTTP/1.1\r\nHost: {request_host}\r\n'
                    f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                    f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n'
                    f'Sec-WebSocket-Protocol: binary\r\n\r\n'
                )
                sock.sendall(request.encode('ascii'))
                sock.settimeout(5)
                response = bytearray()
                while b'\r\n\r\n' not in response and len(response) < 8192:
                    chunk = sock.recv(512)
                    if not chunk:
                        break
                    response.extend(chunk)
                first = response.decode('utf-8', errors='replace').split('\r\n', 1)[0]
                return True if re.match(r'^HTTP/\d(?:\.\d)? 101(?:\s|$)', first) else (first or 'no response')
    except socket.timeout:
        return 'timeout'
    except OSError as exc:
        return str(exc)[:60] or type(exc).__name__


def run_diagnostics(mode, domains, no_secure=False, probe=None):
    if mode not in ('auto', 'custom', 'worker'):
        raise ValueError('Unsupported diagnostics mode')
    if mode == 'auto':
        domains = list(domains or balancer.domains or CFPROXY_DEFAULT_DOMAINS)
        domains.reverse()
    else:
        domains = list(domains)
        if not domains:
            raise ValueError('At least one domain is required')
    if not domains or any(not _valid_domain(domain) for domain in domains):
        raise ValueError('Invalid diagnostics domain')

    secure = not bool(no_secure)
    probe = probe or _probe_case
    per_domain = {}
    merged = {}
    selected_domain = None
    for domain in domains:
        cases = {}
        for dc in TEST_DCS:
            host = domain if mode == 'worker' else f'kws{dc}.{domain}'
            path = (f'/apiws?dst={WORKER_DST[dc]}&dc={dc}&media=0'
                    if mode == 'worker' else '/apiws')
            outcome = probe(dc, host, host, host, path, secure)
            cases[str(dc)] = 'ok' if outcome is True else str(outcome or 'failed')[:80]
        per_domain[domain] = cases
        if mode == 'auto':
            for dc, outcome in cases.items():
                if outcome == 'ok':
                    merged[dc] = 'ok'
                    selected_domain = domain
                elif dc not in merged:
                    merged[dc] = outcome
            if all(outcome == 'ok' for outcome in cases.values()):
                break

    values = merged if mode == 'auto' else {
        f'{domain}:{dc}': outcome
        for domain, cases in per_domain.items() for dc, outcome in cases.items()
    }
    return {
        'ok': any(outcome == 'ok' for outcome in values.values()),
        'mode': mode,
        'secure': secure,
        'selected_domain': selected_domain,
        'success_count': sum(outcome == 'ok' for outcome in values.values()),
        'total_count': len(values),
        'per_dc': merged if mode == 'auto' else {},
        'per_domain': per_domain,
    }
