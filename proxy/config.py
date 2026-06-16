import logging
import os
import string
import random
import socket as _socket
import threading

from dataclasses import dataclass, field
from typing import Dict, List
from urllib.request import Request

from .balancer import balancer
from .utils import build_github_opener

log = logging.getLogger('tg-mtproto-proxy')

CFPROXY_DOMAINS_URL = (
    "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main"
    "/.github/cfproxy-domains.txt"
)

_CFPROXY_ENC: List[str] = [
    'virkgj.com', 
    'vmmzovy.com', 
    'mkuosckvso.com', 
    'zaewayzmplad.com', 
    'twdmbzcm.com',
    'awzwsldi.com',
    'clngqrflngqin.com',
    'tjacxbqtj.com',
    'bxaxtxmrw.com',
    'dmohrsgmohcrwb.com',
    'vwbmtmoi.com',
    'khgrre.com',
    'ulihssf.com',
    'tmhqsdqmfpmk.com',
    'xwuwoqbm.com'
]
_S = ''.join(chr(c) for c in (46, 99, 111, 46, 117, 107))


def _dd(s: str) -> str:
    """Only for decoding CF proxy domains"""
    if not s[-4:] == '.com':
        return s
    p, n = s[:-4], sum(c.isalpha() for c in s[:-4])
    return ''.join(
        chr((ord(c) - (97 if c > '`' else 65) - n) % 26 + (97 if c > '`' else 65))
        if c.isalpha() else c for c in p
    ) + _S


CFPROXY_DEFAULT_DOMAINS: List[str] = [_dd(d) for d in _CFPROXY_ENC]
_CFPROXY_MIN_VALID_DOMAINS = 3


@dataclass
class ProxyConfig:
    port: int = 1443
    host: str = '127.0.0.1'
    secret: str = field(default_factory=lambda: os.urandom(16).hex())
    dc_redirects: Dict[int, str] = field(default_factory=lambda: {2: '149.154.167.220', 4: '149.154.167.220'})
    buffer_size: int = 256 * 1024
    pool_size: int = 4
    fallback_cfproxy: bool = True
    cfproxy_user_domains: List[str] = field(default_factory=list)
    cfproxy_worker_domains: List[str] = field(default_factory=list)
    fake_tls_domain: str = ''
    proxy_protocol: bool = False
    ws_keepalive_interval: float = 30.0


proxy_config = ProxyConfig()


def coerce_domain_list(value) -> List[str]:
    if isinstance(value, str):
        items = value.replace(',', ' ').replace(';', ' ').split()
    elif isinstance(value, (list, tuple)):
        items: List[str] = []
        for entry in value:
            if isinstance(entry, str):
                items.extend(entry.replace(',', ' ').replace(';', ' ').split())
    else:
        return []
    seen = set()
    result: List[str] = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _fetch_cfproxy_domain_list() -> List[str]:
    try:
        req = Request(CFPROXY_DOMAINS_URL + "?" + "".join(random.choices(string.ascii_letters, k=7)),
                       headers={'User-Agent': 'tg-ws-proxy'})
        with build_github_opener().open(req, timeout=10) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        encoded = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.startswith('#')
        ]
        return [_dd(d) for d in encoded]
    except Exception as exc:
        log.warning("Failed to fetch CF proxy domain list: %s", repr(exc))
        return []


def _is_valid_domain(domain: str) -> bool:
    if not domain or len(domain) > 253:
        return False
    if domain.startswith('.') or domain.endswith('.'):
        return False
    labels = domain.split('.')
    if len(labels) < 2:
        return False
    for label in labels:
        if not label or len(label) > 63:
            return False
        if label[0] == '-' or label[-1] == '-':
            return False
        if not all(ch.isalnum() or ch == '-' for ch in label):
            return False
    # TLD should contain letters and be at least 2 chars.
    tld = labels[-1]
    if len(tld) < 2 or not any(ch.isalpha() for ch in tld):
        return False
    return True


def _normalize_domain_pool(domains: List[str]) -> List[str]:
    seen = set()
    normalized: List[str] = []
    for domain in domains:
        item = domain.strip().lower()
        if not _is_valid_domain(item):
            continue
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def refresh_cfproxy_domains() -> None:
    if proxy_config.cfproxy_user_domains:
        return

    fetched = _fetch_cfproxy_domain_list()
    pool = _normalize_domain_pool(fetched)
    if len(pool) >= _CFPROXY_MIN_VALID_DOMAINS:
        balancer.update_domains_list(pool)
        log.info("CF proxy domain pool updated from GitHub (%d domains)", len(pool))
        return

    if fetched:
        log.warning(
            "Ignoring fetched CF proxy domains due to low-quality payload "
            "(total=%d, valid=%d, required>=%d); keeping current domain pool",
            len(fetched), len(pool), _CFPROXY_MIN_VALID_DOMAINS,
        )
    else:
        log.warning(
            "CF proxy domain refresh failed or empty response; "
            "keeping current domain pool",
        )


_refresh_stop: threading.Event = threading.Event()


def start_cfproxy_domain_refresh() -> None:
    global _refresh_stop
    _refresh_stop.set()
    _refresh_stop = threading.Event()
    stop = _refresh_stop

    balancer.update_domains_list(CFPROXY_DEFAULT_DOMAINS)

    def _loop():
        refresh_cfproxy_domains()
        while not stop.wait(timeout=3600):
            refresh_cfproxy_domains()

    threading.Thread(target=_loop, daemon=True, name='cfproxy-domains-refresh').start()


def parse_dc_ip_list(dc_ip_list: List[str]) -> Dict[int, str]:
    dc_redirects: Dict[int, str] = {}
    for entry in dc_ip_list:
        if ':' not in entry:
            raise ValueError(
                f"Invalid --dc-ip format {entry!r}, expected DC:IP")
        dc_s, ip_s = entry.split(':', 1)
        try:
            dc_n = int(dc_s)
            _socket.inet_aton(ip_s)
        except (ValueError, OSError):
            raise ValueError(f"Invalid --dc-ip {entry!r}")
        dc_redirects[dc_n] = ip_s
    return dc_redirects
