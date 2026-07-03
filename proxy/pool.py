import asyncio
import logging
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
    
    def __init__(self):
        self._idle: Dict[Tuple[int, bool], deque] = {}
        self._refilling: Set[Tuple[int, bool]] = set()
        self.fronting_until: float = 0.0

    async def get(self, dc: int, is_media: bool,
                  target_ip: str, domains: List[str]
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
            self._schedule_refill(key, target_ip, domains)
            return ws

        stats.pool_misses += 1
        self._schedule_refill(key, target_ip, domains)
        return None

    def _schedule_refill(self, key, target_ip, domains):
        if key in self._refilling:
            return
        self._refilling.add(key)
        asyncio.create_task(self._refill(key, target_ip, domains))

    async def _refill(self, key, target_ip, domains):
        dc, is_media = key
        try:
            bucket = self._idle.setdefault(key, deque())
            needed = proxy_config.pool_size - len(bucket)
            if needed <= 0:
                return
            tasks = [asyncio.create_task(
                self._connect_one(target_ip, domains, time.monotonic() < self.fronting_until))
                for _ in range(needed)]
            for t in tasks:
                try:
                    ws = await t
                    if ws:
                        bucket.append((ws, time.monotonic()))
                except Exception:
                    pass
            log.debug("WS pool refilled DC%d%s: %d ready",
                      dc, 'm' if is_media else '', len(bucket))
        finally:
            self._refilling.discard(key)

    @staticmethod
    async def _connect_one(target_ip, domains, fronting_active) -> Optional[RawWebSocket]:
        for domain in domains:
            try:
                return await RawWebSocket.connect(
                    target_ip, domain, timeout=8, sni="sprinthost.ru" if fronting_active else None)
            except WsHandshakeError as exc:
                if exc.is_redirect:
                    continue
                return None
            except Exception:
                return None
        return None

    @staticmethod
    async def _quiet_close(ws):
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
        self._idle.clear()
        self._refilling.clear()
        self.fronting_until = 0.0


class _CfWorkerPool:
    WS_POOL_MAX_AGE = 100.0

    def __init__(self):
        self._idle: Dict[Tuple[int, str], deque] = {}
        self._refilling: Set[Tuple[int, str]] = set()

    async def get(self, dc: int, worker_domain: str, fallback_dst: str) -> Optional[RawWebSocket]:
        now = time.monotonic()
        key = (dc, worker_domain)

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
            stats.cf_pool_hits += 1
            log.debug("CF worker pool hit DC%d (age=%.1fs, left=%d)",
                      dc, age, len(bucket))
            self._schedule_refill(key, fallback_dst)
            return ws

        stats.cf_pool_misses += 1
        self._schedule_refill(key, fallback_dst)
        return None

    def _schedule_refill(self, key, fallback_dst):
        if key in self._refilling:
            return
        self._refilling.add(key)
        asyncio.create_task(self._refill(key, fallback_dst))

    async def _refill(self, key, fallback_dst):
        dc, worker_domain = key
        try:
            bucket = self._idle.setdefault(key, deque())
            needed = proxy_config.pool_size - len(bucket)
            if needed <= 0:
                return
            tasks = [asyncio.create_task(
                self._connect_one(worker_domain, fallback_dst, dc))
                for _ in range(needed)]
            for t in tasks:
                try:
                    ws = await t
                    if ws:
                        bucket.append((ws, time.monotonic()))
                except Exception:
                    pass
            log.debug("CF worker pool refilled DC%d: %d ready",
                      dc, len(bucket))
        finally:
            self._refilling.discard(key)

    @staticmethod
    async def _connect_one(worker_domain, fallback_dst, dc) -> Optional[RawWebSocket]:
        query = urlencode({
            'dst': fallback_dst,
            'dc': str(dc),
        })
        path = f'/apiws?{query}'
        try:
            return await RawWebSocket.connect(
                worker_domain, worker_domain, timeout=8, path=path)
        except Exception:
            return None

    @staticmethod
    async def _quiet_close(ws):
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

        for worker_domain in proxy_config.cfproxy_worker_domains:
            for dc, fallback_dst in cf_fallbacks.items():
                self._schedule_refill((dc, worker_domain), fallback_dst)

        log.info("CF worker pool warmup started for %d DC(s)", len(cf_fallbacks))

    def reset(self):
        self._idle.clear()
        self._refilling.clear()


ws_pool = _WsPool()
cf_worker_pool = _CfWorkerPool()