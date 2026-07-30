"""HTTP long-poll client for the Relay byte-pipe (TCP-model).

  GET  /open?dst=IP&tok=...  -> {"ok":true,"sid":...}
  POST /up?sid&tok   (body)  -> 200
  GET  /down?sid&tok         -> 200+bytes / 204 (poll timeout) / 410 (closed)
  POST /close?sid&tok        -> 200

Every request goes through the outbound proxy (get_outbound_proxy) when set,
using the same python_socks Proxy.connect pattern as raw_websocket.connect.
No new dependencies: HTTP is done manually over asyncio streams.
"""
import asyncio
import json
import logging
import ssl
from urllib.parse import urlsplit, urlencode

from .config import get_outbound_proxy

log = logging.getLogger('tg-mtproto-proxy')

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


class RelayError(Exception):
    pass


async def _http_request(host, method, path, body, timeout):
    proxy = get_outbound_proxy()
    try:
        if proxy:
            sock = await asyncio.wait_for(proxy.connect(host, 443), timeout=timeout)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(sock=sock, ssl=_ssl_ctx, server_hostname=host),
                timeout=timeout)
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 443, ssl=_ssl_ctx, server_hostname=host),
                timeout=timeout)
    except Exception as e:
        raise RelayError(f"connect to relay {host} failed: {e!r}")
    try:
        req = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
        if body is not None:
            req += (f"Content-Length: {len(body)}\r\n"
                    f"Content-Type: application/octet-stream\r\n")
        req += "\r\n"
        writer.write(req.encode() + (body or b""))
        await writer.drain()

        status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not status_line:
            raise RelayError("empty response from relay")
        parts = status_line.split()
        status = int(parts[1]) if len(parts) >= 2 else 0

        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if line in (b"\r\n", b"\n", b""):
                break
        resp_body = await asyncio.wait_for(reader.read(), timeout=timeout)
        return status, resp_body
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


class RelayTube:
    """send/recv over the Relay HTTP pipe (mimics a raw TCP socket to DC)."""

    OPEN_TIMEOUT = 10.0
    UP_TIMEOUT = 15.0
    DOWN_TIMEOUT = 35.0   # > server pollTimeout (25s)
    CLOSE_TIMEOUT = 5.0

    def __init__(self, base_url, token):
        u = urlsplit(base_url)
        if not u.hostname:
            raise RelayError(f"bad relay url: {base_url!r}")
        self.host = u.hostname
        self.token = token or ""
        self.sid = None

    def _q(self, extra):
        extra["tok"] = self.token
        return urlencode(extra)

    async def open(self, dst):
        status, body = await _http_request(
            self.host, "GET", f"/open?{self._q({'dst': dst})}",
            None, self.OPEN_TIMEOUT)
        if status != 200:
            raise RelayError(f"open failed: HTTP {status} {body[:120]!r}")
        self.sid = json.loads(body).get("sid")
        if not self.sid:
            raise RelayError(f"open: no sid in {body[:120]!r}")
        return self.sid

    async def send(self, data):
        if not self.sid:
            raise RelayError("send on closed tube")
        status, body = await _http_request(
            self.host, "POST", f"/up?{self._q({'sid': self.sid})}",
            data, self.UP_TIMEOUT)
        if status != 200:
            raise RelayError(f"up failed: HTTP {status} {body[:120]!r}")

    async def recv(self):
        """Next bytes, or None when upstream closed. Hides 204 poll timeouts."""
        if not self.sid:
            return None
        while True:
            status, body = await _http_request(
                self.host, "GET", f"/down?{self._q({'sid': self.sid})}",
                None, self.DOWN_TIMEOUT)
            if status == 200:
                return body
            if status == 204:
                continue
            if status == 410:
                return None
            raise RelayError(f"down failed: HTTP {status} {body[:120]!r}")

    async def close(self):
        if not self.sid:
            return
        sid, self.sid = self.sid, None
        try:
            await _http_request(
                self.host, "POST", f"/close?{self._q({'sid': sid})}",
                None, self.CLOSE_TIMEOUT)
        except Exception as e:
            log.debug("relay close: %s", e)