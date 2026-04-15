import logging
import os
import string
import random
import socket as _socket
import threading

from dataclasses import dataclass, field
from typing import Dict, List
from urllib.request import Request, urlopen
from utils.log_utils import LogAction, log_event

log = logging.getLogger('tg-mtproto-proxy')

CFPROXY_DOMAINS_URL = (
    "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main"
    "/.github/cfproxy-domains.txt"
)

_CFPROXY_ENC: List[str] = ['virkgj.com', 'vmmzovy.com', 'mkuosckvso.com', 'zaewayzmplad.com', 'twdmbzcm.com']
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


@dataclass
class ProxyConfig:
    port: int = 1443
    host: str = '127.0.0.1'
    secret: str = field(default_factory=lambda: os.urandom(16).hex())
    dc_redirects: Dict[int, str] = field(default_factory=lambda: {2: '149.154.167.220', 4: '149.154.167.220'})
    buffer_size: int = 256 * 1024
    pool_size: int = 4
    fallback_cfproxy: bool = True
    fallback_cfproxy_priority: bool = True
    cfproxy_user_domain: str = ''
    cfproxy_domains: List[str] = field(default_factory=lambda: list(CFPROXY_DEFAULT_DOMAINS))
    active_cfproxy_domain: str = field(default_factory=lambda: random.choice(CFPROXY_DEFAULT_DOMAINS))
    fake_tls_domain: str = ''
    proxy_protocol: bool = False


proxy_config = ProxyConfig()


def _fetch_cfproxy_domain_list() -> List[str]:
    try:
        req = Request(CFPROXY_DOMAINS_URL + "?" + "".join(random.choices(string.ascii_letters, k=7)),
                       headers={'User-Agent': 'tg-ws-proxy'})
        with urlopen(req, timeout=10) as resp:
            text = resp.read().decode('utf-8', errors='replace')
        encoded = [
            line.strip() for line in text.splitlines()
            if line.strip() and not line.startswith('#')
        ]
        return [_dd(d) for d in encoded]
    except Exception as exc:
        log_event(log, logging.WARNING, LogAction.CFPROXY_DOMAINS_FETCH_FAILED, error=str(exc))
        return []


def refresh_cfproxy_domains() -> None:
    if proxy_config.cfproxy_user_domain:
        return
    
    fetched = _fetch_cfproxy_domain_list()

    if fetched:
        seen = set()
        pool = [d for d in fetched if not (d in seen or seen.add(d))]
        log_event(log, logging.INFO, LogAction.CFPROXY_DOMAINS_UPDATED, source="github", count=len(pool))
    else:
        pool = list(proxy_config.cfproxy_domains) or list(CFPROXY_DEFAULT_DOMAINS)

    proxy_config.cfproxy_domains = pool
    proxy_config.active_cfproxy_domain = random.choice(pool)


_refresh_stop: threading.Event = threading.Event()


def start_cfproxy_domain_refresh() -> None:
    global _refresh_stop
    _refresh_stop.set()
    _refresh_stop = threading.Event()
    stop = _refresh_stop

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