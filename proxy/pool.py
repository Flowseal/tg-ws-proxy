import asyncio
import logging
import random
import time

from collections import deque
from urllib.parse import urlencode
from typing import Dict, List, Optional, Tuple, Set

from .raw_websocket import RawWebSocket, WsHandshakeError
from .stats import stats
from .config import proxy_config
from .utils import ws_domains, DC_DEFAULT_IPS

log = logging.getLogger('tg-mtproto-proxy')

class _WsPool:
    WS_POOL_MAX_AGE = 120.0
    WS_POOL_CHECK_INTERVAL = 5.0
    REFILL_BACKOFF_INITIAL = 60.0
    REFILL_BACKOFF_MAX = 3600.0
    
    def __init__(self):
        self._idle: Dict[Tuple[int, bool], deque] = {}
        self._refilling: Set[Tuple[int, bool]] = set()
        self._rotating: Dict[Tuple[int, bool], asyncio.Task] = {}
        self._refill_failures: Dict[Tuple[int, bool], int] = {}
        self._refill_after: Dict[Tuple[int, bool], float] = {}
        self.try_fronting_first = False

    async def get(self, dc: int, is_media: bool,
                  target_ip: str, domains: List[str],
                  *, allow_refill: bool = True
                  ) -> Optional[RawWebSocket]:
        key = (dc, is_media)
        now = time.monotonic()

        bucket = self._idle.get(key)
        if bucket is None:
            bucket = deque()
            self._idle[key] = bucket
        while bucket:
            ws, created = bucket.popleft()
            age = now - created
            if (age > self.WS_POOL_MAX_AGE or ws._closed
                    or ws.writer.transport.is_closing()):
                asyncio.create_task(self._quiet_close(ws))
                continue
            stats.pool_hits += 1
            log.debug("WS pool hit DC%d%s (age=%.1fs, left=%d)",
                      dc, 'm' if is_media else '', age, len(bucket))
            self.report_success(dc, is_media)
            if allow_refill:
                self._schedule_refill(key, target_ip, domains)
            return ws

        stats.pool_misses += 1
        if allow_refill:
            self._schedule_refill(key, target_ip, domains)
        return None

    def _schedule_refill(self, key, target_ip, domains):
        if (key in self._refilling
                or time.monotonic() < self._refill_after.get(key, 0)):
            return
        self._refilling.add(key)
        asyncio.create_task(self._refill(key, target_ip, domains))

    def report_success(self, dc: int, is_media: bool) -> None:
        key = (dc, is_media)
        self._refill_failures.pop(key, None)
        self._refill_after.pop(key, None)

    async def _refill(self, key, target_ip, domains):
        dc, is_media = key
        try:
            bucket = self._idle.setdefault(key, deque())
            needed = proxy_config.pool_size - len(bucket)
            if needed <= 0:
                return
            connected = 0
            tasks = [asyncio.create_task(
                self._connect_one(target_ip, domains))
                for _ in range(needed)]
            for t in tasks:
                try:
                    ws = await t
                    if ws:
                        bucket.append((ws, time.monotonic()))
                        connected += 1
                        self._schedule_rotation(key, target_ip, domains)
                except Exception:
                    pass
            if connected:
                self.report_success(dc, is_media)
            else:
                failures = self._refill_failures.get(key, 0) + 1
                self._refill_failures[key] = failures
                delay = min(
                    self.REFILL_BACKOFF_INITIAL
                    * (2 ** min(failures - 1, 6)),
                    self.REFILL_BACKOFF_MAX,
                )
                self._refill_after[key] = time.monotonic() + delay
                log.info(
                    "WS pool refill failed for DC%d%s, retry in %.0fs",
                    dc, 'm' if is_media else '', delay)
            log.debug("WS pool refilled DC%d%s: %d ready",
                      dc, 'm' if is_media else '', len(bucket))
        finally:
            self._refilling.discard(key)

    def _schedule_rotation(self, key, target_ip, domains):
        if key in self._rotating:
            return
        self._rotating[key] = asyncio.create_task(
            self._rotate(key, target_ip, domains))

    async def _rotate(self, key, target_ip, domains):
        dc, is_media = key
        try:
            while True:
                bucket = self._idle.get(key)
                if not bucket:
                    return

                expires_at = min(
                    created + self.WS_POOL_MAX_AGE
                    for _, created in bucket)
                await asyncio.sleep(min(
                    self.WS_POOL_CHECK_INTERVAL,
                    max(0, expires_at - time.monotonic())))

                now = time.monotonic()
                expired = []
                ready = deque()
                while bucket:
                    ws, created = bucket.popleft()
                    if (now - created >= self.WS_POOL_MAX_AGE
                            or ws._closed
                            or ws.writer.transport.is_closing()):
                        expired.append(ws)
                    else:
                        ready.append((ws, created))
                bucket.extend(ready)

                if expired:
                    for ws in expired:
                        asyncio.create_task(self._quiet_close(ws))
                    log.debug(
                        "WS pool rotated DC%d%s: %d stale, %d ready",
                        dc, 'm' if is_media else '', len(expired), len(bucket))
                    self._schedule_refill(key, target_ip, domains)
        finally:
            if self._rotating.get(key) is asyncio.current_task():
                self._rotating.pop(key, None)

    async def _connect_one(self, target_ip, domains) -> Optional[RawWebSocket]:
        for domain in domains:
            if self.try_fronting_first:
                ws = await self._connect_fronted(target_ip, domain)
                if ws:
                    return ws
            try:
                ws = await RawWebSocket.connect(
                    target_ip, domain, timeout=8)
                self.try_fronting_first = False
                return ws
            except asyncio.TimeoutError:
                if self.try_fronting_first:
                    return None
                return await self._connect_fronted(target_ip, domain)
            except WsHandshakeError as exc:
                if exc.is_redirect:
                    continue
                return None
            except Exception:
                return None
        return None

    async def _connect_fronted(self, target_ip, domain) -> Optional[RawWebSocket]:
        try:
            ws = await RawWebSocket.connect(
                target_ip, domain, timeout=7, sni="sprinthost.ru")
        except Exception:
            return None

        stats.connections_fronting += 1
        self.try_fronting_first = True
        return ws

    async def _quiet_close(self, ws):
        try:
            await ws.close()
        except Exception:
            pass

    async def warmup(self):
        for dc, target_ip in proxy_config.dc_redirects.items():
            if target_ip is None:
                continue
            for is_media in (False, True):
                domains = ws_domains(dc, is_media)
                self._schedule_refill((dc, is_media), target_ip, domains)
        log.info("WS pool warmup started for %d DC(s)", len(proxy_config.dc_redirects))

    def reset(self):
        loop = asyncio.get_running_loop()
        for task in self._rotating.values():
            if not task.done() and task.get_loop() is loop:
                task.cancel()
        self._idle.clear()
        self._refilling.clear()
        self._rotating.clear()
        self._refill_failures.clear()
        self._refill_after.clear()
        self.try_fronting_first = False


class _CfWorkerPool:
    WS_POOL_MAX_AGE = 100.0
    PER_DC_LIMIT = 1

    def __init__(self):
        self._idle: Dict[int, deque] = {}
        self._refilling: Set[int] = set()
        self._exhausted_until: Dict[str, float] = {}

    async def get(self, dc: int, fallback_dst: str,
                  worker_domains: List[str]
                  ) -> Optional[Tuple[RawWebSocket, str]]:
        now = time.monotonic()

        bucket = self._idle.get(dc)
        if bucket is None:
            bucket = deque()
            self._idle[dc] = bucket
        while bucket:
            ws, created, worker_domain = bucket.popleft()
            age = now - created
            if (age > self.WS_POOL_MAX_AGE or ws._closed
                    or ws.writer.transport.is_closing()):
                asyncio.create_task(self._quiet_close(ws))
                continue
            stats.cf_pool_hits += 1
            log.debug(
                "CF worker pool hit DC%d via %s (age=%.1fs, left=%d)",
                dc, worker_domain, age, len(bucket))
            self._schedule_refill(dc, fallback_dst, worker_domains)
            return ws, worker_domain

        stats.cf_pool_misses += 1
        return None

    def _schedule_refill(self, dc, fallback_dst, worker_domains):
        if dc in self._refilling:
            return
        self._refilling.add(dc)
        asyncio.create_task(self._refill(
            dc, fallback_dst, list(worker_domains)))

    async def _refill(self, dc, fallback_dst, worker_domains):
        try:
            bucket = self._idle.setdefault(dc, deque())
            target_size = min(proxy_config.pool_size, self.PER_DC_LIMIT)
            needed = target_size - len(bucket)
            if needed <= 0:
                return

            for _ in range(needed):
                connected = await self._connect_one(
                    worker_domains, fallback_dst, dc)
                if connected is None:
                    break
                ws, worker_domain = connected
                bucket.append((ws, time.monotonic(), worker_domain))
            log.debug("CF worker pool refilled DC%d: %d ready",
                      dc, len(bucket))
        finally:
            self._refilling.discard(dc)

    async def _connect_one(self, worker_domains, fallback_dst, dc):
        query = urlencode({
            'dst': fallback_dst,
            'dc': str(dc),
        })
        path = f'/apiws?{query}'
        for worker_domain in self.available_domains(worker_domains):
            try:
                ws = await RawWebSocket.connect(
                    worker_domain, worker_domain, timeout=8, path=path)
                return ws, worker_domain
            except Exception as exc:
                self.report_failure(worker_domain, exc)
        return None

    def available_domains(self, worker_domains: List[str]) -> List[str]:
        now = time.time()
        domains = list()
        for domain in worker_domains:
            if domain in domains:
                continue
            exhausted_until = self._exhausted_until.get(domain, 0)
            if exhausted_until > now:
                continue
            if exhausted_until:
                self._exhausted_until.pop(domain, None)
            domains.append(domain)
        random.shuffle(domains)
        return domains

    def report_failure(self, worker_domain: str, exc: Exception) -> None:
        return  # TODO: check status code after daily limit reached
        if not isinstance(exc, WsHandshakeError) or exc.status_code != 429:
            return

        now = time.time()
        if self._exhausted_until.get(worker_domain, 0) > now:
            return
        exhausted_until = now + (86400 - (now % 86400))
        self._exhausted_until[worker_domain] = exhausted_until
        log.warning(
            "CF worker %s reached its request limit, disabled for %d seconds", worker_domain, int(exhausted_until - now))

    async def _quiet_close(self, ws):
        try:
            await ws.close()
        except Exception:
            pass

    async def warmup(self):
        cf_fallbacks = {
            dc: ip for dc, ip in DC_DEFAULT_IPS.items()
            if dc not in proxy_config.dc_redirects
        }

        if not cf_fallbacks or not proxy_config.cfproxy_worker_domains:
            return

        worker_domains = list(proxy_config.cfproxy_worker_domains)
        for dc, fallback_dst in cf_fallbacks.items():
            self._schedule_refill(dc, fallback_dst, worker_domains)

        log.info("CF worker pool warmup started for %d DC(s)", len(cf_fallbacks))

    def reset(self):
        self._idle.clear()
        self._refilling.clear()
        self._exhausted_until.clear()


ws_pool = _WsPool()
cf_worker_pool = _CfWorkerPool()
