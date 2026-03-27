from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import logging.handlers
import os
import socket as _socket
import ssl
import struct
import sys
import time
from typing import Dict, List, Optional, Set, Tuple
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from urllib.parse import quote


DEFAULT_PORT = 1080
log = logging.getLogger('tg-ws-proxy')

_TCP_NODELAY = True
_RECV_BUF = 256 * 1024
_SEND_BUF = 256 * 1024
_WS_POOL_SIZE = 4
_WS_POOL_MAX_AGE = 120.0

_TG_RANGES = [
    # 185.76.151.0/24
    (struct.unpack('!I', _socket.inet_aton('185.76.151.0'))[0],
     struct.unpack('!I', _socket.inet_aton('185.76.151.255'))[0]),
    # 149.154.160.0/20
    (struct.unpack('!I', _socket.inet_aton('149.154.160.0'))[0],
     struct.unpack('!I', _socket.inet_aton('149.154.175.255'))[0]),
    # 91.105.192.0/23
    (struct.unpack('!I', _socket.inet_aton('91.105.192.0'))[0],
     struct.unpack('!I', _socket.inet_aton('91.105.193.255'))[0]),
    # 91.108.0.0/16
    (struct.unpack('!I', _socket.inet_aton('91.108.0.0'))[0],
     struct.unpack('!I', _socket.inet_aton('91.108.255.255'))[0]),
]

# IP -> (dc_id, is_media)
_IP_TO_DC: Dict[str, Tuple[int, bool]] = {
    # DC1
    '149.154.175.50': (1, False), '149.154.175.51': (1, False),
    '149.154.175.53': (1, False), '149.154.175.54': (1, False),
    '149.154.175.52': (1, True),
    # DC2
    '149.154.167.41': (2, False), '149.154.167.50': (2, False),
    '149.154.167.51': (2, False), '149.154.167.220': (2, False),
    '95.161.76.100':  (2, False),
    '149.154.167.151': (2, True), '149.154.167.222': (2, True),
    '149.154.167.223': (2, True), '149.154.162.123': (2, True),
    # DC3
    '149.154.175.100': (3, False), '149.154.175.101': (3, False),
    '149.154.175.102': (3, True),
    # DC4
    '149.154.167.91': (4, False), '149.154.167.92': (4, False),
    '149.154.164.250': (4, True), '149.154.166.120': (4, True),
    '149.154.166.121': (4, True), '149.154.167.118': (4, True),
    '149.154.165.111': (4, True),
    # DC5
    '91.108.56.100': (5, False), '91.108.56.101': (5, False),
    '91.108.56.116': (5, False), '91.108.56.126': (5, False),
    '149.154.171.5':  (5, False),
    '91.108.56.102': (5, True), '91.108.56.128': (5, True),
    '91.108.56.151': (5, True),
    # DC203
    '91.105.192.100': (203, False),
}

# This case might work but not actually sure
_DC_OVERRIDES: Dict[int, int] = {
    203: 2
}

_dc_opt: Dict[int, Optional[str]] = {}

_auth_user = ''
_auth_password = ''

# DCs where WS is known to fail (302 redirect)
# Raw TCP fallback will be used instead
# Keyed by (dc, is_media)
_ws_blacklist: Set[Tuple[int, bool]] = set()

# Rate-limit re-attempts per (dc, is_media)
_dc_fail_until: Dict[Tuple[int, bool], float] = {}
_DC_FAIL_COOLDOWN = 30.0   # seconds to keep reduced WS timeout after failure
_WS_FAIL_TIMEOUT = 2.0    # quick-retry timeout after a recent WS failure

_ZERO_64 = b'\x00' * 64


_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _set_sock_opts(transport):
    sock = transport.get_extra_info('socket')
    if sock is None:
        return
    if _TCP_NODELAY:
        try:
            sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass
    try:
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, _RECV_BUF)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, _SEND_BUF)
    except OSError:
        pass


class WsHandshakeError(Exception):
    def __init__(self, status_code: int, status_line: str,
                 headers: dict = None, location: str = None):
        self.status_code = status_code
        self.status_line = status_line
        self.headers = headers or {}
        self.location = location
        super().__init__(f"HTTP {status_code}: {status_line}")

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (301, 302, 303, 307, 308)


def _xor_mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    mask_rep = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^ int.from_bytes(mask_rep, 'big')).to_bytes(n, 'big')


# Pre-compiled struct formats
_st_BB = struct.Struct('>BB')
_st_BBH = struct.Struct('>BBH')
_st_BBQ = struct.Struct('>BBQ')
_st_BB4s = struct.Struct('>BB4s')
_st_BBH4s = struct.Struct('>BBH4s')
_st_BBQ4s = struct.Struct('>BBQ4s')
_st_H = struct.Struct('>H')
_st_Q = struct.Struct('>Q')
_st_I_net = struct.Struct('!I')
_st_Ih = struct.Struct('<Ih')
_st_I_le = struct.Struct('<I')
_PROTO_ABRIDGED = 0xEFEFEFEF
_PROTO_INTERMEDIATE = 0xEEEEEEEE
_PROTO_PADDED_INTERMEDIATE = 0xDDDDDDDD
_VALID_PROTOS = frozenset((
    _PROTO_ABRIDGED,
    _PROTO_INTERMEDIATE,
    _PROTO_PADDED_INTERMEDIATE,
))


class RawWebSocket:
    """
    Lightweight WebSocket client over asyncio reader/writer streams.

    Connects DIRECTLY to a target IP via TCP+TLS (bypassing any system
    proxy), performs the HTTP Upgrade handshake, and provides send/recv
    for binary frames with proper masking, ping/pong, and close handling.
    """
    __slots__ = ('reader', 'writer', '_closed')

    OP_CONTINUATION = 0x0
    OP_TEXT = 0x1
    OP_BINARY = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._closed = False

    @staticmethod
    async def connect(ip: str, domain: str, path: str = '/apiws',
                      timeout: float = 10.0) -> 'RawWebSocket':
        """
        Connect via TLS to the given IP,
        perform WebSocket upgrade, return a RawWebSocket.

        Raises WsHandshakeError on non-101 response.
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, 443, ssl=_ssl_ctx,
                                    server_hostname=domain),
            timeout=min(timeout, 10))
        _set_sock_opts(writer.transport)

        ws_key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f'GET {path} HTTP/1.1\r\n'
            f'Host: {domain}\r\n'
            f'Upgrade: websocket\r\n'
            f'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Key: {ws_key}\r\n'
            f'Sec-WebSocket-Version: 13\r\n'
            f'Sec-WebSocket-Protocol: binary\r\n'
            f'Origin: https://web.telegram.org\r\n'
            f'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            f'AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/131.0.0.0 Safari/537.36\r\n'
            f'\r\n'
        )
        writer.write(req.encode())
        await writer.drain()

        # Read HTTP response headers line-by-line so the reader stays
        # positioned right at the start of WebSocket frames.
        response_lines: list[str] = []
        try:
            while True:
                line = await asyncio.wait_for(reader.readline(),
                                              timeout=timeout)
                if line in (b'\r\n', b'\n', b''):
                    break
                response_lines.append(
                    line.decode('utf-8', errors='replace').strip())
        except asyncio.TimeoutError:
            writer.close()
            raise

        if not response_lines:
            writer.close()
            raise WsHandshakeError(0, 'empty response')

        first_line = response_lines[0]
        parts = first_line.split(' ', 2)
        try:
            status_code = int(parts[1]) if len(parts) >= 2 else 0
        except ValueError:
            status_code = 0

        if status_code == 101:
            return RawWebSocket(reader, writer)

        headers: dict[str, str] = {}
        for hl in response_lines[1:]:
            if ':' in hl:
                k, v = hl.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        writer.close()
        raise WsHandshakeError(status_code, first_line, headers,
                                location=headers.get('location'))

    async def send(self, data: bytes):
        """Send a masked binary WebSocket frame."""
        if self._closed:
            raise ConnectionError("WebSocket closed")
        frame = self._build_frame(self.OP_BINARY, data, mask=True)
        self.writer.write(frame)
        await self.writer.drain()

    async def send_batch(self, parts: List[bytes]):
        """Send multiple binary frames with a single drain (less overhead)."""
        if self._closed:
            raise ConnectionError("WebSocket closed")
        for part in parts:
            frame = self._build_frame(self.OP_BINARY, part, mask=True)
            self.writer.write(frame)
        await self.writer.drain()

    async def recv(self) -> Optional[bytes]:
        """
        Receive the next data frame.  Handles ping/pong/close
        internally.  Returns payload bytes, or None on clean close.
        """
        while not self._closed:
            opcode, payload = await self._read_frame()

            if opcode == self.OP_CLOSE:
                self._closed = True
                try:
                    reply = self._build_frame(
                        self.OP_CLOSE,
                        payload[:2] if payload else b'',
                        mask=True)
                    self.writer.write(reply)
                    await self.writer.drain()
                except Exception:
                    pass
                return None

            if opcode == self.OP_PING:
                try:
                    pong = self._build_frame(self.OP_PONG, payload,
                                             mask=True)
                    self.writer.write(pong)
                    await self.writer.drain()
                except Exception:
                    pass
                continue

            if opcode == self.OP_PONG:
                continue

            if opcode in (self.OP_TEXT, self.OP_BINARY):
                return payload

            # Unknown opcode — skip
            continue

        return None

    async def close(self):
        """Send close frame and shut down the transport."""
        if self._closed:
            return
        self._closed = True
        try:
            self.writer.write(
                self._build_frame(self.OP_CLOSE, b'', mask=True))
            await self.writer.drain()
        except Exception:
            pass
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass

    @staticmethod
    def _build_frame(opcode: int, data: bytes,
                     mask: bool = False) -> bytes:
        length = len(data)
        fb = 0x80 | opcode

        if not mask:
            if length < 126:
                return _st_BB.pack(fb, length) + data
            if length < 65536:
                return _st_BBH.pack(fb, 126, length) + data
            return _st_BBQ.pack(fb, 127, length) + data

        mask_key = os.urandom(4)
        masked = _xor_mask(data, mask_key)
        if length < 126:
            return _st_BB4s.pack(fb, 0x80 | length, mask_key) + masked
        if length < 65536:
            return _st_BBH4s.pack(fb, 0x80 | 126, length, mask_key) + masked
        return _st_BBQ4s.pack(fb, 0x80 | 127, length, mask_key) + masked

    async def _read_frame(self) -> Tuple[int, bytes]:
        hdr = await self.reader.readexactly(2)
        opcode = hdr[0] & 0x0F
        length = hdr[1] & 0x7F

        if length == 126:
            length = _st_H.unpack(
                await self.reader.readexactly(2))[0]
        elif length == 127:
            length = _st_Q.unpack(
                await self.reader.readexactly(8))[0]

        if hdr[1] & 0x80:
            mask_key = await self.reader.readexactly(4)
            payload = await self.reader.readexactly(length)
            return opcode, _xor_mask(payload, mask_key)

        payload = await self.reader.readexactly(length)
        return opcode, payload


def _human_bytes(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _is_telegram_ip(ip: str) -> bool:
    try:
        n = _st_I_net.unpack(_socket.inet_aton(ip))[0]
        return any(lo <= n <= hi for lo, hi in _TG_RANGES)
    except OSError:
        return False


def _is_http_transport(data: bytes) -> bool:
    return (data[:5] == b'POST ' or data[:4] == b'GET ' or
            data[:5] == b'HEAD ' or data[:8] == b'OPTIONS ')


def _dc_from_init(data: bytes):
    try:
        cipher = Cipher(algorithms.AES(data[8:40]), modes.CTR(data[40:56]))
        encryptor = cipher.encryptor()
        keystream = encryptor.update(_ZERO_64)
        plain = (int.from_bytes(data[56:64], 'big') ^
                 int.from_bytes(keystream[56:64], 'big')).to_bytes(8, 'big')
        
        proto, dc_raw = _st_Ih.unpack(plain[:6])
        
        log.debug("dc_from_init: proto=0x%08X dc_raw=%d plain=%s",
                  proto, dc_raw, plain.hex())
        
        if proto in _VALID_PROTOS:
            dc = abs(dc_raw)
            if 1 <= dc <= 5 or dc == 203:
                return dc, (dc_raw < 0), proto
            # IMPORTANT: If the protocol is valid, but dc_id is invalid (Android),
            # we must return the proto so that the Splitter knows the protocol type
            # and can split packets correctly, even if DC extraction failed.
            return None, False, proto
    except Exception as exc:
        log.debug("DC extraction failed: %s", exc)
        
    return None, False, None

def _patch_init_dc(data: bytes, dc: int) -> bytes:
    """
    Patch dc_id in the 64-byte MTProto init packet.

    Mobile clients with useSecret=0 leave bytes 60-61 as random.
    The WS relay needs a valid dc_id to route correctly.
    """
    if len(data) < 64:
        return data

    new_dc = struct.pack('<h', dc)
    try:
        cipher = Cipher(algorithms.AES(data[8:40]), modes.CTR(data[40:56]))
        enc = cipher.encryptor()
        ks = enc.update(_ZERO_64)
        patched = bytearray(data[:64])
        patched[60] = ks[60] ^ new_dc[0]
        patched[61] = ks[61] ^ new_dc[1]
        log.debug("init patched: dc_id -> %d", dc)
        if len(data) > 64:
            return bytes(patched) + data[64:]
        return bytes(patched)
    except Exception:
        return data


class _MsgSplitter:
    """
    Splits client TCP data into individual MTProto transport packets so
    each can be sent as a separate WebSocket frame.

    Some mobile clients coalesce multiple MTProto packets into one TCP
    write, and TCP reads may also cut a packet in half.  Keep a rolling
    buffer so incomplete packets are not forwarded as standalone frames.
    """

    __slots__ = ('_dec', '_proto', '_cipher_buf', '_plain_buf', '_disabled')

    def __init__(self, init_data: bytes, proto: int):
        cipher = Cipher(algorithms.AES(init_data[8:40]),
                        modes.CTR(init_data[40:56]))
        self._dec = cipher.encryptor()
        self._dec.update(_ZERO_64)  # skip init packet
        self._proto = proto
        self._cipher_buf = bytearray()
        self._plain_buf = bytearray()
        self._disabled = False

    def split(self, chunk: bytes) -> List[bytes]:
        """Decrypt to find packet boundaries, return complete ciphertext packets."""
        if not chunk:
            return []
        if self._disabled:
            return [chunk]

        self._cipher_buf.extend(chunk)
        self._plain_buf.extend(self._dec.update(chunk))

        parts = []
        while self._cipher_buf:
            packet_len = self._next_packet_len()
            if packet_len is None:
                break
            if packet_len <= 0:
                parts.append(bytes(self._cipher_buf))
                self._cipher_buf.clear()
                self._plain_buf.clear()
                self._disabled = True
                break
            parts.append(bytes(self._cipher_buf[:packet_len]))
            del self._cipher_buf[:packet_len]
            del self._plain_buf[:packet_len]
        return parts

    def flush(self) -> List[bytes]:
        if not self._cipher_buf:
            return []
        tail = bytes(self._cipher_buf)
        self._cipher_buf.clear()
        self._plain_buf.clear()
        return [tail]

    def _next_packet_len(self) -> Optional[int]:
        if not self._plain_buf:
            return None
        if self._proto == _PROTO_ABRIDGED:
            return self._next_abridged_len()
        if self._proto in (_PROTO_INTERMEDIATE, _PROTO_PADDED_INTERMEDIATE):
            return self._next_intermediate_len()
        return 0

    def _next_abridged_len(self) -> Optional[int]:
        first = self._plain_buf[0]
        if first in (0x7F, 0xFF):
            if len(self._plain_buf) < 4:
                return None
            payload_len = int.from_bytes(self._plain_buf[1:4], 'little') * 4
            header_len = 4
        else:
            payload_len = (first & 0x7F) * 4
            header_len = 1

        if payload_len <= 0:
            return 0

        packet_len = header_len + payload_len
        if len(self._plain_buf) < packet_len:
            return None
        return packet_len

    def _next_intermediate_len(self) -> Optional[int]:
        if len(self._plain_buf) < 4:
            return None

        payload_len = _st_I_le.unpack_from(self._plain_buf, 0)[0] & 0x7FFFFFFF
        if payload_len <= 0:
            return 0

        packet_len = 4 + payload_len
        if len(self._plain_buf) < packet_len:
            return None
        return packet_len


def _ws_domains(dc: int, is_media) -> List[str]:
    dc = _DC_OVERRIDES.get(dc, dc)
    if is_media is None or is_media:
        return [f'kws{dc}-1.web.telegram.org', f'kws{dc}.web.telegram.org']
    return [f'kws{dc}.web.telegram.org', f'kws{dc}-1.web.telegram.org']


class Stats:
    def __init__(self):
        self.connections_total = 0
        self.connections_ws = 0
        self.connections_tcp_fallback = 0
        self.connections_http_rejected = 0
        self.connections_not_tg_rejected = 0
        self.ws_errors = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self.pool_hits = 0
        self.pool_misses = 0

    def summary(self) -> str:
        return (f"total={self.connections_total} ws={self.connections_ws} "
                f"tcp_fb={self.connections_tcp_fallback} "
                f"http_skip={self.connections_http_rejected} "
                f"non_tg_skip={self.connections_not_tg_rejected} "
                f"err={self.ws_errors} "
                f"pool={self.pool_hits}/{self.pool_hits+self.pool_misses} "
                f"up={_human_bytes(self.bytes_up)} "
                f"down={_human_bytes(self.bytes_down)}")


_stats = Stats()


class _WsPool:
    def __init__(self):
        self._idle: Dict[Tuple[int, bool], list] = {}
        self._refilling: Set[Tuple[int, bool]] = set()

    async def get(self, dc: int, is_media: bool,
                  target_ip: str, domains: List[str]
                  ) -> Optional[RawWebSocket]:
        key = (dc, is_media)
        now = time.monotonic()

        bucket = self._idle.get(key, [])
        while bucket:
            ws, created = bucket.pop(0)
            age = now - created
            if age > _WS_POOL_MAX_AGE or ws._closed:
                asyncio.create_task(self._quiet_close(ws))
                continue
            _stats.pool_hits += 1
            log.debug("WS pool hit for DC%d%s (age=%.1fs, left=%d)",
                      dc, 'm' if is_media else '', age, len(bucket))
            self._schedule_refill(key, target_ip, domains)
            return ws

        _stats.pool_misses += 1
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
            bucket = self._idle.setdefault(key, [])
            needed = _WS_POOL_SIZE - len(bucket)
            if needed <= 0:
                return
            tasks = []
            for _ in range(needed):
                tasks.append(asyncio.create_task(
                    self._connect_one(target_ip, domains)))
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
    async def _connect_one(target_ip, domains) -> Optional[RawWebSocket]:
        for domain in domains:
            try:
                ws = await RawWebSocket.connect(
                    target_ip, domain, timeout=8)
                return ws
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

    async def warmup(self, dc_opt: Dict[int, Optional[str]]):
        """Pre-fill pool for all configured DCs on startup."""
        for dc, target_ip in dc_opt.items():
            if target_ip is None:
                continue
            for is_media in (False, True):
                domains = _ws_domains(dc, is_media)
                key = (dc, is_media)
                self._schedule_refill(key, target_ip, domains)
        log.info("WS pool warmup started for %d DC(s)", len(dc_opt))


_ws_pool = _WsPool()


async def _bridge_ws(reader, writer, ws: RawWebSocket, label,
                     dc=None, dst=None, port=None, is_media=False,
                     splitter: _MsgSplitter = None):
    """Bidirectional TCP <-> WebSocket forwarding."""
    dc_tag = f"DC{dc}{'m' if is_media else ''}" if dc else "DC?"
    dst_tag = f"{dst}:{port}" if dst else "?"

    up_bytes = 0
    down_bytes = 0
    up_packets = 0
    down_packets = 0
    start_time = asyncio.get_event_loop().time()

    async def tcp_to_ws():
        nonlocal up_bytes, up_packets
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    if splitter:
                        tail = splitter.flush()
                        if tail:
                            await ws.send(tail[0])
                    break
                n = len(chunk)
                _stats.bytes_up += n
                up_bytes += n
                up_packets += 1
                if splitter:
                    parts = splitter.split(chunk)
                    if not parts:
                        continue
                    if len(parts) > 1:
                        await ws.send_batch(parts)
                    else:
                        await ws.send(parts[0])
                else:
                    await ws.send(chunk)
        except (asyncio.CancelledError, ConnectionError, OSError):
            return
        except Exception as e:
            log.debug("[%s] tcp->ws ended: %s", label, e)

    async def ws_to_tcp():
        nonlocal down_bytes, down_packets
        try:
            while True:
                data = await ws.recv()
                if data is None:
                    break
                n = len(data)
                _stats.bytes_down += n
                down_bytes += n
                down_packets += 1
                writer.write(data)
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            return
        except Exception as e:
            log.debug("[%s] ws->tcp ended: %s", label, e)

    tasks = [asyncio.create_task(tcp_to_ws()),
             asyncio.create_task(ws_to_tcp())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:
                pass
        elapsed = asyncio.get_event_loop().time() - start_time
        log.info("[%s] %s (%s) WS session closed: "
                 "^%s (%d pkts) v%s (%d pkts) in %.1fs",
                 label, dc_tag, dst_tag,
                 _human_bytes(up_bytes), up_packets,
                 _human_bytes(down_bytes), down_packets,
                 elapsed)
        try:
            await ws.close()
        except BaseException:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except BaseException:
            pass


async def _bridge_tcp(reader, writer, remote_reader, remote_writer,
                      label, dc=None, dst=None, port=None,
                      is_media=False):
    """Bidirectional TCP <-> TCP forwarding (for fallback)."""
    async def forward(src, dst_w, is_up):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                n = len(data)
                if is_up:
                    _stats.bytes_up += n
                else:
                    _stats.bytes_down += n
                dst_w.write(data)
                await dst_w.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug("[%s] forward ended: %s", label, e)

    tasks = [
        asyncio.create_task(forward(reader, remote_writer, True)),
        asyncio.create_task(forward(remote_reader, writer, False)),
    ]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except BaseException:
                pass
        for w in (writer, remote_writer):
            try:
                w.close()
                await w.wait_closed()
            except BaseException:
                pass


async def _pipe(r, w):
    """Plain TCP relay for non-Telegram traffic."""
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    finally:
        try:
            w.close()
            await w.wait_closed()
        except Exception:
            pass


_SOCKS5_REPLIES = {s: bytes([0x05, s, 0x00, 0x01, 0, 0, 0, 0, 0, 0])
                   for s in (0x00, 0x05, 0x07, 0x08)}


def _socks5_reply(status):
    return _SOCKS5_REPLIES[status]


async def _tcp_fallback(reader, writer, dst, port, init, label,
                        dc=None, is_media=False):
    """
    Fall back to direct TCP to the original DC IP.
    Throttled by ISP, but functional.  Returns True on success.
    """
    try:
        rr, rw = await asyncio.wait_for(
            asyncio.open_connection(dst, port), timeout=10)
    except Exception as exc:
        log.warning("[%s] TCP fallback connect to %s:%d failed: %s",
                    label, dst, port, exc)
        return False

    _stats.connections_tcp_fallback += 1
    rw.write(init)
    await rw.drain()
    await _bridge_tcp(reader, writer, rr, rw, label,
                      dc=dc, dst=dst, port=port, is_media=is_media)
    return True


async def _handle_client(reader, writer):
    _stats.connections_total += 1
    peer = writer.get_extra_info('peername')
    label = f"{peer[0]}:{peer[1]}" if peer else "?"

    _set_sock_opts(writer.transport)

    try:
        # -- SOCKS5 greeting --
        hdr = await asyncio.wait_for(reader.readexactly(2), timeout=10)
        if hdr[0] != 5:
            log.debug("[%s] not SOCKS5 (ver=%d)", label, hdr[0])
            writer.close()
            return

        # Auth check
        nmethods = hdr[1]
        auth_methods = await reader.readexactly(nmethods)

        if not _auth_user and not _auth_password:
            writer.write(b'\x05\x00')  # no-auth
            await writer.drain()
        elif _auth_user and _auth_password:
            if b'\x02' not in auth_methods:
                log.debug("[%s] auth methods %s", label, auth_methods)
                writer.write(b'\x05\xff')
                await writer.drain()
                writer.close()
                return

            log.debug("[%s] auth requested", label)
            writer.write(b'\x05\x02')  # username/password requested
            await writer.drain()

            # Username/password subnegotiation
            auth_hdr = await asyncio.wait_for(reader.readexactly(1), timeout=10)
            if auth_hdr[0] != 1:
                log.debug("[%s] bad auth request ver: %s", label, auth_hdr)
                writer.write(b'\x01\x01')
                await writer.drain()
                writer.close()
                return

            ulen = (await reader.readexactly(1))[0]
            client_username = (await reader.readexactly(ulen)).decode('utf-8', errors='ignore')
            plen = (await reader.readexactly(1))[0]
            client_password = (await reader.readexactly(plen)).decode('utf-8', errors='ignore')

            if client_username != _auth_user or client_password != _auth_password:
                log.warning("[%s] auth failed with creds: %s/%s", label, client_username, client_password)
                writer.write(b'\x01\x01')
                await writer.drain()
                writer.close()
                return

            log.debug("[%s] auth success", label)
            writer.write(b'\x01\x00')  # Success
            await writer.drain()
        else:
            # If have some problems
            writer.write(b'\x05\xff')
            await writer.drain()
            writer.close()
            return


        # -- SOCKS5 CONNECT request --
        req = await asyncio.wait_for(reader.readexactly(4), timeout=10)
        _ver, cmd, _rsv, atyp = req
        if cmd != 1:
            writer.write(_socks5_reply(0x07))
            await writer.drain()
            writer.close()
            return

        if atyp == 1:  # IPv4
            raw = await reader.readexactly(4)
            dst = _socket.inet_ntoa(raw)
        elif atyp == 3:  # domain
            dlen = (await reader.readexactly(1))[0]
            dst = (await reader.readexactly(dlen)).decode()
        elif atyp == 4:  # IPv6
            raw = await reader.readexactly(16)
            dst = _socket.inet_ntop(_socket.AF_INET6, raw)
        else:
            writer.write(_socks5_reply(0x08))
            await writer.drain()
            writer.close()
            return

        port = _st_H.unpack(await reader.readexactly(2))[0]

        if ':' in dst:
            log.error(
                "[%s] IPv6 address detected: %s:%d — "
                "IPv6 addresses are not supported; "
                "disable IPv6 to continue using the proxy.",
                label, dst, port)
            writer.write(_socks5_reply(0x05))
            await writer.drain()
            writer.close()
            return

        # -- Non-Telegram IP -> drop connection --
        if not _is_telegram_ip(dst):
            _stats.connections_not_tg_rejected += 1
            log.error("[%s] Non-Telegram IP blocked -> %s:%d", label, dst, port)
            writer.write(_socks5_reply(0x05))
            await writer.drain()
            writer.close()
            return

        # -- Telegram DC: accept SOCKS, read init --
        writer.write(_socks5_reply(0x00))
        await writer.drain()

        try:
            init = await asyncio.wait_for(
                reader.readexactly(64), timeout=15)
        except asyncio.IncompleteReadError:
            log.debug("[%s] client disconnected before init", label)
            return

        # HTTP transport -> reject
        if _is_http_transport(init):
            _stats.connections_http_rejected += 1
            log.debug("[%s] HTTP transport to %s:%d (rejected)",
                      label, dst, port)
            writer.close()
            return

        # -- Extract DC ID --
        dc, is_media, proto = _dc_from_init(init)
        
        init_patched = False
        # Android (may be ios too) with useSecret=0 has random dc_id bytes — patch it
        if dc is None and dst in _IP_TO_DC:
            dc, is_media = _IP_TO_DC.get(dst)
            if dc in _dc_opt:
                init = _patch_init_dc(init, -dc if is_media else dc)
                init_patched = True

        if dc is None or dc not in _dc_opt:
            log.warning("[%s] unknown DC%s for %s:%d -> TCP passthrough",
                        label, dc, dst, port)
            await _tcp_fallback(reader, writer, dst, port, init, label)
            return

        dc_key = (dc, is_media if is_media is not None else True)
        now = time.monotonic()
        media_tag = (" media" if is_media
                     else (" media?" if is_media is None else ""))

        # -- WS blacklist check --
        if dc_key in _ws_blacklist:
            log.debug("[%s] DC%d%s WS blacklisted -> TCP %s:%d",
                      label, dc, media_tag, dst, port)
            ok = await _tcp_fallback(reader, writer, dst, port, init,
                                     label, dc=dc, is_media=is_media)
            if ok:
                log.info("[%s] DC%d%s TCP fallback closed",
                         label, dc, media_tag)
            return

        # -- Try WebSocket via direct connection --
        fail_until = _dc_fail_until.get(dc_key, 0)
        ws_timeout = _WS_FAIL_TIMEOUT if now < fail_until else 10.0

        domains = _ws_domains(dc, is_media)
        target = _dc_opt[dc]
        ws = None
        ws_failed_redirect = False
        all_redirects = True

        ws = await _ws_pool.get(dc, is_media, target, domains)
        if ws:
            log.info("[%s] DC%d%s (%s:%d) -> pool hit via %s",
                     label, dc, media_tag, dst, port, target)
        else:
            for domain in domains:
                url = f'wss://{domain}/apiws'
                log.info("[%s] DC%d%s (%s:%d) -> %s via %s",
                         label, dc, media_tag, dst, port, url, target)
                try:
                    ws = await RawWebSocket.connect(target, domain,
                                                    timeout=ws_timeout)
                    all_redirects = False
                    break
                except WsHandshakeError as exc:
                    _stats.ws_errors += 1
                    if exc.is_redirect:
                        ws_failed_redirect = True
                        log.warning("[%s] DC%d%s got %d from %s -> %s",
                                    label, dc, media_tag,
                                    exc.status_code, domain,
                                    exc.location or '?')
                        continue
                    else:
                        all_redirects = False
                        log.warning("[%s] DC%d%s WS handshake: %s",
                                    label, dc, media_tag, exc.status_line)
                except Exception as exc:
                    _stats.ws_errors += 1
                    all_redirects = False
                    err_str = str(exc)
                    if ('CERTIFICATE_VERIFY_FAILED' in err_str or
                            'Hostname mismatch' in err_str):
                        log.warning("[%s] DC%d%s SSL error: %s",
                                    label, dc, media_tag, exc)
                    else:
                        log.warning("[%s] DC%d%s WS connect failed: %s",
                                    label, dc, media_tag, exc)

        # -- WS failed -> fallback --
        if ws is None:
            if ws_failed_redirect and all_redirects:
                _ws_blacklist.add(dc_key)
                log.warning(
                    "[%s] DC%d%s blacklisted for WS (all 302)",
                    label, dc, media_tag)
            elif ws_failed_redirect:
                _dc_fail_until[dc_key] = now + _DC_FAIL_COOLDOWN
            else:
                _dc_fail_until[dc_key] = now + _DC_FAIL_COOLDOWN
                log.info("[%s] DC%d%s WS cooldown for %ds",
                         label, dc, media_tag, int(_DC_FAIL_COOLDOWN))

            log.info("[%s] DC%d%s -> TCP fallback to %s:%d",
                     label, dc, media_tag, dst, port)
            ok = await _tcp_fallback(reader, writer, dst, port, init,
                                     label, dc=dc, is_media=is_media)
            if ok:
                log.info("[%s] DC%d%s TCP fallback closed",
                         label, dc, media_tag)
            return

        # -- WS success --
        _dc_fail_until.pop(dc_key, None)
        _stats.connections_ws += 1

        splitter = None

        # Turning splitter on for mobile clients or media-connections, so as the big files don't get fragmented by the TCP socket.
        if proto is not None and (init_patched or is_media or proto != _PROTO_INTERMEDIATE):
            try:
                splitter = _MsgSplitter(init, proto)
                log.debug("[%s] MsgSplitter activated for proto 0x%08X", label, proto)
            except Exception:
                pass

        # Send the buffered init packet
        await ws.send(init)

        # Bidirectional bridge
        await _bridge_ws(reader, writer, ws, label,
                         dc=dc, dst=dst, port=port, is_media=is_media,
                         splitter=splitter)

    except asyncio.TimeoutError:
        log.warning("[%s] timeout during SOCKS5 handshake", label)
    except asyncio.IncompleteReadError:
        log.debug("[%s] client disconnected", label)
    except asyncio.CancelledError:
        log.debug("[%s] cancelled", label)
    except ConnectionResetError:
        log.debug("[%s] connection reset", label)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1236:
            log.debug("[%s] connection aborted by local system", label)
        else:
            log.error("[%s] unexpected os error: %s", label, exc)
    except Exception as exc:
        log.error("[%s] unexpected: %s", label, exc)
    finally:
        try:
            writer.close()
        except BaseException:
            pass


_server_instance = None
_server_stop_event = None


async def _run(port: int, dc_opt: Dict[int, Optional[str]],
               stop_event: Optional[asyncio.Event] = None,
               host: str = '127.0.0.1'):
    global _dc_opt, _server_instance, _server_stop_event
    _dc_opt = dc_opt
    _server_stop_event = stop_event

    server = await asyncio.start_server(
        _handle_client, host, port)
    _server_instance = server

    for sock in server.sockets:
        try:
            sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    log.info("=" * 60)
    log.info("  Telegram WS Bridge Proxy")
    log.info("  Listening on   %s:%d", host, port)
    log.info("  Target DC IPs:")
    for dc in dc_opt.keys():
        ip = dc_opt.get(dc)
        log.info("    DC%d: %s", dc, ip)
    log.info("=" * 60)
    log.info("  Configure Telegram Desktop:")
    if _auth_user and _auth_password:
        log.info("    SOCKS5 proxy -> %s:%d ( %s / %s )", host, port, _auth_user, _auth_password)
        log.info("    tg://socks/?server=%s&port=%s&user=%s&pass=%s", host, port, quote(_auth_user), quote(_auth_password))
    else:
        log.info("    SOCKS5 proxy -> %s:%d  (no user/pass)", host, port)
        log.info("    tg://socks/?server=%s&port=%s", host, port)
    log.info("=" * 60)

    async def log_stats():
        while True:
            await asyncio.sleep(60)
            bl = ', '.join(
                f'DC{d}{"m" if m else ""}'
                for d, m in sorted(_ws_blacklist)) or 'none'
            log.info("stats: %s | ws_bl: %s", _stats.summary(), bl)

    asyncio.create_task(log_stats())

    await _ws_pool.warmup(dc_opt)

    if stop_event:
        async def wait_stop():
            await stop_event.wait()
            server.close()
            me = asyncio.current_task()
            for task in list(asyncio.all_tasks()):
                if task is not me:
                    task.cancel()
            try:
                await server.wait_closed()
            except asyncio.CancelledError:
                pass
        asyncio.create_task(wait_stop())

    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            pass
    _server_instance = None


def set_auth_credentials(username: str, password: str):
    """Validate 'user' and 'password': both are specified, or both aren't"""
    empty_user = username is None or not str(username).strip()
    empty_pass = password is None or not str(password).strip()

    if empty_user != empty_pass:
        raise ValueError(
            "Both --username (-u) and --password (-P) must be specified together, or neither should be used")

    global _auth_user, _auth_password
    _auth_user = username
    _auth_password = password


def parse_dc_ip_list(dc_ip_list: List[str]) -> Dict[int, str]:
    """Parse list of 'DC:IP' strings into {dc: ip} dict."""
    dc_opt: Dict[int, str] = {}
    for entry in dc_ip_list:
        if ':' not in entry:
            raise ValueError(f"Invalid --dc-ip format {entry!r}, expected DC:IP")
        dc_s, ip_s = entry.split(':', 1)
        try:
            dc_n = int(dc_s)
            _socket.inet_aton(ip_s)
        except (ValueError, OSError):
            raise ValueError(f"Invalid --dc-ip {entry!r}")
        dc_opt[dc_n] = ip_s
    return dc_opt


def run_proxy(port: int, dc_opt: Dict[int, str],
              stop_event: Optional[asyncio.Event] = None,
              host: str = '127.0.0.1'):
    """Run the proxy (blocking). Can be called from threads."""
    asyncio.run(_run(port, dc_opt, stop_event, host))


def main():
    ap = argparse.ArgumentParser(
        description='Telegram Desktop WebSocket Bridge Proxy')
    ap.add_argument('--host', type=str, default='127.0.0.1',
                    help='Listen host (default 127.0.0.1)')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT,
                    help=f'Listen port (default {DEFAULT_PORT})')
    ap.add_argument('-u', '--user', type=str, default='',
                    help='User for proxy')
    ap.add_argument('-P', '--password', type=str, default='',
                    help='Password for proxy')
    ap.add_argument('--dc-ip', metavar='DC:IP', action='append',
                    default=[],
                    help='Target IP for a DC, e.g. --dc-ip 1:149.154.175.205'
                         ' --dc-ip 2:149.154.167.220')
    ap.add_argument('-v', '--verbose', action='store_true',
                    help='Debug logging')
    ap.add_argument('--log-file', type=str, default=None, metavar='PATH',
                    help='Log to file with rotation (default: stderr only)')
    ap.add_argument('--log-max-mb', type=float, default=5, metavar='MB',
                    help='Max log file size in MB before rotation (default 5)')
    ap.add_argument('--log-backups', type=int, default=0, metavar='N',
                    help='Number of rotated log files to keep (default 0)')
    ap.add_argument('--buf-kb', type=int, default=256, metavar='KB',
                    help='Socket send/recv buffer size in KB (default 256)')
    ap.add_argument('--pool-size', type=int, default=4, metavar='N',
                    help='WS connection pool size per DC (default 4, min 0)')
    args = ap.parse_args()

    if not args.dc_ip:
        args.dc_ip = ['2:149.154.167.220', '4:149.154.167.220']

    try:
        dc_opt = parse_dc_ip_list(args.dc_ip)
        set_auth_credentials(args.user, args.password)
    except ValueError as e:
        log.error(str(e))
        sys.exit(1)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_fmt = logging.Formatter('%(asctime)s  %(levelname)-5s  %(message)s',
                                datefmt='%H:%M:%S')
    root = logging.getLogger()
    root.setLevel(log_level)

    console = logging.StreamHandler()
    console.setFormatter(log_fmt)
    root.addHandler(console)

    if args.log_file:
        fh = logging.handlers.RotatingFileHandler(
            args.log_file,
            maxBytes=max(32 * 1024, args.log_max_mb * 1024 * 1024),
            backupCount=max(0, args.log_backups),
            encoding='utf-8',
        )
        fh.setFormatter(log_fmt)
        root.addHandler(fh)

    global _RECV_BUF, _SEND_BUF, _WS_POOL_SIZE
    _RECV_BUF = max(4, args.buf_kb) * 1024
    _SEND_BUF = _RECV_BUF
    _WS_POOL_SIZE = max(0, args.pool_size)

    try:
        asyncio.run(_run(args.port, dc_opt, host=args.host))
    except KeyboardInterrupt:
        log.info("Shutting down. Final stats: %s", _stats.summary())


if __name__ == '__main__':
    main()
