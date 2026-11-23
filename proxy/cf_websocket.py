import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .raw_websocket import RawWebSocket


log = logging.getLogger('tg-mtproto-proxy')


@dataclass
class CfConStats:
    tls: int = 0
    no_tls: int = 0

CF_TIMEOUT_COOLDOWN = 30.0
CF_CONNECT_TIMEOUTS = (3.0, 3.0, 5.0, 5.0, 10.0)
_cf_timeout_until: Dict[Tuple[str, bool], float] = {}
_cf_connection_stats = CfConStats()


def reset_cf_timeouts() -> None:
    _cf_timeout_until.clear()
    _cf_connection_stats.no_tls = 0
    _cf_connection_stats.tls = 0


def cf_prefers_tls() -> bool:
    return _cf_connection_stats.tls >= _cf_connection_stats.no_tls


def cf_tls_in_cooldown(name: str) -> bool:
    retry_at = _cf_timeout_until.get((name, True))
    return retry_at is not None and time.monotonic() < retry_at


async def connect_cf_domains(candidates: Iterable[Tuple[str, str]], *, label: str,
                             secure: bool, path: str = '/apiws', timeout: float = 10.0
                             ) -> Optional[Tuple[RawWebSocket, str]]:
    domains = list(candidates)
    retry_single_domain = secure and len(domains) == 1
    if retry_single_domain:
        domains.append(domains[0])

    timeouts = 0
    tls_first = cf_prefers_tls()

    for index, (name, host) in enumerate(domains):
        use_tls = secure and (tls_first != bool(timeouts % 2))
        if retry_single_domain and index == 1 and timeouts == 0:
            # The first try was skipped because of _cf_timeout_until.
            use_tls = not tls_first

        key = (name, use_tls)
        retry_at = _cf_timeout_until.get(key)
        if retry_at is not None:
            if time.monotonic() < retry_at:
                continue
            _cf_timeout_until[key] = math.inf

        try:
            attempt_timeout = min(timeout, CF_CONNECT_TIMEOUTS[timeouts])
            ws = await RawWebSocket.connect(host, host, path=path,
                                            secure=use_tls, timeout=attempt_timeout)

        except (asyncio.TimeoutError, TimeoutError):
            now = time.monotonic()
            if retry_at is not None or key not in _cf_timeout_until:
                for old_key, until in list(_cf_timeout_until.items()):
                    if until + CF_TIMEOUT_COOLDOWN <= now:
                        del _cf_timeout_until[old_key]
                _cf_timeout_until[key] = now + CF_TIMEOUT_COOLDOWN
                log.warning('[%s] CF WebSocket timed out (host=%s tls=%s); retry in %.0fs',
                            label, host, use_tls, CF_TIMEOUT_COOLDOWN)
            timeouts += 1
            if timeouts >= len(CF_CONNECT_TIMEOUTS):
                break
            continue

        except Exception as exc:
            log.warning('[%s] CF WebSocket failed (host=%s tls=%s): %r', label, host, use_tls, exc)
            ws = None

        finally:
            if retry_at is not None and _cf_timeout_until.get(key) == math.inf:
                _cf_timeout_until[key] = retry_at

        _cf_timeout_until.pop(key, None)

        if ws is not None:
            if use_tls:
                _cf_connection_stats.tls += 1
            else:
                _cf_connection_stats.no_tls += 1
            return ws, name

        if retry_single_domain:
            break

    return None
