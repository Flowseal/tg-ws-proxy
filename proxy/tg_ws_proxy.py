from __future__ import annotations

import os
import ssl
import sys
import time
import base64
import struct
import asyncio
import hashlib
import argparse
import logging
import logging.handlers
import socket as _socket

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


@dataclass
class ProxyConfig:
    port: int = 1443
    host: str = '127.0.0.1'
    secret: str = field(default_factory=lambda: os.urandom(16).hex())
    dc_redirects: Dict[int, str] = field(default_factory=lambda: {2: '149.154.167.220', 4: '149.154.167.220'})
    dc_overrides: Dict[int, int] = field(default_factory=lambda: {203: 2})
    buffer_size: int = 256 * 1024
    pool_size: int = 4


proxy_config = ProxyConfig()
log = logging.getLogger('tg-mtproto-proxy')

DC_DEFAULT_IPS: Dict[int, str] = {
    1: '149.154.175.50',
    2: '149.154.167.51',
    3: '149.154.175.100',
    4: '149.154.167.91',
    5: '149.154.171.5',
    203: '91.105.192.100'
}

HANDSHAKE_LEN = 64
SKIP_LEN = 8
PREKEY_LEN = 32
KEY_LEN = 32
IV_LEN = 16
PROTO_TAG_POS = 56
DC_IDX_POS = 60

PROTO_TAG_ABRIDGED = b'\xef\xef\xef\xef'
PROTO_TAG_INTERMEDIATE = b'\xee\xee\xee\xee'
PROTO_TAG_SECURE = b'\xdd\xdd\xdd\xdd'

PROTO_ABRIDGED_INT = 0xEFEFEFEF
PROTO_INTERMEDIATE_INT = 0xEEEEEEEE
PROTO_PADDED_INTERMEDIATE_INT = 0xDDDDDDDD

RESERVED_FIRST_BYTES = {0xEF}
RESERVED_STARTS = {b'\x48\x45\x41\x44', b'\x50\x4F\x53\x54',
                    b'\x47\x45\x54\x20', b'\xee\xee\xee\xee',
                    b'\xdd\xdd\xdd\xdd', b'\x16\x03\x01\x02'}
RESERVED_CONTINUE = b'\x00\x00\x00\x00'

DC_FAIL_COOLDOWN = 30.0
WS_FAIL_TIMEOUT = 2.0
ws_blacklist: Set[Tuple[int, bool]] = set()
dc_fail_until: Dict[Tuple[int, bool], float] = {}

_st_BB = struct.Struct('>BB')
_st_BBH = struct.Struct('>BBH')
_st_BBQ = struct.Struct('>BBQ')
_st_BB4s = struct.Struct('>BB4s')
_st_BBH4s = struct.Struct('>BBH4s')
_st_BBQ4s = struct.Struct('>BBQ4s')
_st_H = struct.Struct('>H')
_st_Q = struct.Struct('>Q')
_st_I_le = struct.Struct('<I')

ZERO_64 = b'\x00' * 64

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def _set_sock_opts(transport):
    sock = transport.get_extra_info('socket')
    if sock is None:
        return
    
    try:
        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    
    try:
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, proxy_config.buffer_size)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, proxy_config.buffer_size)
    except OSError:
        pass


def _xor_mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    mask_rep = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^
            int.from_bytes(mask_rep, 'big')).to_bytes(n, 'big')


def get_link_host(host: str) -> Optional[str]:
    if host == '0.0.0.0':
        try:
            with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as _s:
                _s.connect(('8.8.8.8', 80))
                link_host = _s.getsockname()[0]
        except OSError:
            link_host = '127.0.0.1'
        return link_host
    else:
        return host


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


class RawWebSocket:
    __slots__ = ('reader', 'writer', '_closed')

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
        if self._closed:
            raise ConnectionError("WebSocket closed")
        frame = self._build_frame(self.OP_BINARY, data, mask=True)
        self.writer.write(frame)
        await self.writer.drain()

    async def send_batch(self, parts: List[bytes]):
        if self._closed:
            raise ConnectionError("WebSocket closed")
        for part in parts:
            self.writer.write(
                self._build_frame(self.OP_BINARY, part, mask=True))
        await self.writer.drain()

    async def recv(self) -> Optional[bytes]:
        while not self._closed:
            opcode, payload = await self._read_frame()

            if opcode == self.OP_CLOSE:
                self._closed = True
                try:
                    self.writer.write(self._build_frame(
                        self.OP_CLOSE,
                        payload[:2] if payload else b'', mask=True))
                    await self.writer.drain()
                except Exception:
                    pass
                return None

            if opcode == self.OP_PING:
                try:
                    self.writer.write(
                        self._build_frame(self.OP_PONG, payload, mask=True))
                    await self.writer.drain()
                except Exception:
                    pass
                continue

            if opcode == self.OP_PONG:
                continue

            if opcode in (0x1, 0x2):
                return payload
            continue
        return None

    async def close(self):
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
            length = _st_H.unpack(await self.reader.readexactly(2))[0]
        elif length == 127:
            length = _st_Q.unpack(await self.reader.readexactly(8))[0]
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


def _try_handshake(handshake: bytes, secret: bytes) -> Optional[Tuple[int, bool, bytes, bytes]]:
    dec_prekey_and_iv = handshake[SKIP_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    dec_prekey = dec_prekey_and_iv[:PREKEY_LEN]
    dec_iv = dec_prekey_and_iv[PREKEY_LEN:]

    dec_key = hashlib.sha256(dec_prekey + secret).digest()

    dec_iv_int = int.from_bytes(dec_iv, 'big')
    decryptor = Cipher(
        algorithms.AES(dec_key), modes.CTR(dec_iv_int.to_bytes(16, 'big'))
    ).encryptor()
    decrypted = decryptor.update(handshake)

    proto_tag = decrypted[PROTO_TAG_POS:PROTO_TAG_POS + 4]
    if proto_tag not in (PROTO_TAG_ABRIDGED, PROTO_TAG_INTERMEDIATE,
                         PROTO_TAG_SECURE):
        return None

    dc_idx = int.from_bytes(
        decrypted[DC_IDX_POS:DC_IDX_POS + 2], 'little', signed=True)

    dc_id = abs(dc_idx)
    is_media = dc_idx < 0

    return dc_id, is_media, proto_tag, dec_prekey_and_iv


def _generate_relay_init(proto_tag: bytes, dc_idx: int) -> bytes:
    while True:
        rnd = bytearray(os.urandom(HANDSHAKE_LEN))
        if rnd[0] in RESERVED_FIRST_BYTES:
            continue
        if bytes(rnd[:4]) in RESERVED_STARTS:
            continue
        if rnd[4:8] == RESERVED_CONTINUE:
            continue
        break

    rnd_bytes = bytes(rnd)

    enc_key = rnd_bytes[SKIP_LEN:SKIP_LEN + PREKEY_LEN]
    enc_iv = rnd_bytes[SKIP_LEN + PREKEY_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]

    encryptor = Cipher(
        algorithms.AES(enc_key), modes.CTR(enc_iv)
    ).encryptor()

    dc_bytes = struct.pack('<h', dc_idx)
    tail_plain = proto_tag + dc_bytes + os.urandom(2)

    encrypted_full = encryptor.update(rnd_bytes)
    keystream_tail = bytes(
        encrypted_full[i] ^ rnd_bytes[i] for i in range(56, 64))
    encrypted_tail = bytes(
        tail_plain[i] ^ keystream_tail[i] for i in range(8))

    result = bytearray(rnd_bytes)
    result[PROTO_TAG_POS:HANDSHAKE_LEN] = encrypted_tail
    return bytes(result)


class _MsgSplitter:
    """
    Splits TCP stream data into individual MTProto transport packets
    so each can be sent as a separate WS frame.
    """
    __slots__ = ('_dec', '_proto', '_cipher_buf', '_plain_buf', '_disabled')

    def __init__(self, relay_init: bytes, proto_int: int):
        cipher = Cipher(algorithms.AES(relay_init[8:40]),
                        modes.CTR(relay_init[40:56]))
        self._dec = cipher.encryptor()
        self._dec.update(ZERO_64)
        self._proto = proto_int
        self._cipher_buf = bytearray()
        self._plain_buf = bytearray()
        self._disabled = False

    def split(self, chunk: bytes) -> List[bytes]:
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
        if self._proto == PROTO_ABRIDGED_INT:
            return self._next_abridged_len()
        if self._proto in (PROTO_INTERMEDIATE_INT,
                           PROTO_PADDED_INTERMEDIATE_INT):
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
    dc = proxy_config.dc_overrides.get(dc, dc)
    if is_media is None or is_media:
        return [f'kws{dc}-1.web.telegram.org', f'kws{dc}.web.telegram.org']
    return [f'kws{dc}.web.telegram.org', f'kws{dc}-1.web.telegram.org']


class Stats:
    def __init__(self):
        self.connections_total = 0
        self.connections_active = 0
        self.connections_ws = 0
        self.connections_tcp_fallback = 0
        self.connections_bad = 0
        self.ws_errors = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self.pool_hits = 0
        self.pool_misses = 0

    def summary(self) -> str:
        pool_total = self.pool_hits + self.pool_misses
        pool_s = (f"{self.pool_hits}/{pool_total}"
                  if pool_total else "n/a")
        return (f"total={self.connections_total} "
                f"active={self.connections_active} "
                f"ws={self.connections_ws} "
                f"tcp_fb={self.connections_tcp_fallback} "
                f"bad={self.connections_bad} "
                f"err={self.ws_errors} "
                f"pool={pool_s} "
                f"up={_human_bytes(self.bytes_up)} "
                f"down={_human_bytes(self.bytes_down)}")

_stats = Stats()


class _WsPool:
    WS_POOL_MAX_AGE = 120.0
    
    def __init__(self):
        self._idle: Dict[Tuple[int, bool], deque] = {}
        self._refilling: Set[Tuple[int, bool]] = set()

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
            _stats.pool_hits += 1
            log.debug("WS pool hit DC%d%s (age=%.1fs, left=%d)",
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
            bucket = self._idle.setdefault(key, deque())
            needed = proxy_config.pool_size - len(bucket)
            if needed <= 0:
                return
            tasks = [asyncio.create_task(
                self._connect_one(target_ip, domains))
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
    async def _connect_one(target_ip, domains) -> Optional[RawWebSocket]:
        for domain in domains:
            try:
                return await RawWebSocket.connect(
                    target_ip, domain, timeout=8)
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

    async def warmup(self, dc_redirects: Dict[int, Optional[str]]):
        for dc, target_ip in dc_redirects.items():
            if target_ip is None:
                continue
            for is_media in (False, True):
                domains = _ws_domains(dc, is_media)
                self._schedule_refill((dc, is_media), target_ip, domains)
        log.info("WS pool warmup started for %d DC(s)", len(dc_redirects))

_ws_pool = _WsPool()


async def _bridge_ws_reencrypt(reader, writer, ws: RawWebSocket, label,
                               dc=None, is_media=False,
                               clt_decryptor=None, clt_encryptor=None,
                               tg_encryptor=None, tg_decryptor=None,
                               splitter: _MsgSplitter = None):
    """
    Bidirectional TCP(client) <-> WS(telegram) with re-encryption.
    client ciphertext → decrypt(clt_key) → encrypt(tg_key) → WS
    WS data → decrypt(tg_key) → encrypt(clt_key) → client TCP
    """
    dc_tag = f"DC{dc}{'m' if is_media else ''}" if dc else "DC?"

    up_bytes = 0
    down_bytes = 0
    up_packets = 0
    down_packets = 0
    start_time = asyncio.get_running_loop().time()

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
                plain = clt_decryptor.update(chunk)
                chunk = tg_encryptor.update(plain)
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
                plain = tg_decryptor.update(data)
                data = clt_encryptor.update(plain)
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
        elapsed = asyncio.get_running_loop().time() - start_time
        log.info("[%s] %s WS session closed: "
                 "^%s (%d pkts) v%s (%d pkts) in %.1fs",
                 label, dc_tag,
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


async def _bridge_tcp_reencrypt(reader, writer, remote_reader, remote_writer,
                                label, dc=None, is_media=False,
                                clt_decryptor=None, clt_encryptor=None,
                                tg_encryptor=None, tg_decryptor=None):
    """Bidirectional TCP <-> TCP with re-encryption."""

    async def forward(src, dst_w, is_up):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                n = len(data)
                if is_up:
                    _stats.bytes_up += n
                    plain = clt_decryptor.update(data)
                    data = tg_encryptor.update(plain)
                else:
                    _stats.bytes_down += n
                    plain = tg_decryptor.update(data)
                    data = clt_encryptor.update(plain)
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


async def _tcp_fallback(reader, writer, dst, port, relay_init, label,
                        dc=None, is_media=False,
                        clt_decryptor=None, clt_encryptor=None,
                        tg_encryptor=None, tg_decryptor=None):
    try:
        rr, rw = await asyncio.wait_for(
            asyncio.open_connection(dst, port), timeout=10)
    except Exception as exc:
        log.warning("[%s] TCP fallback to %s:%d failed: %s",
                    label, dst, port, exc)
        return False

    _stats.connections_tcp_fallback += 1
    rw.write(relay_init)
    await rw.drain()
    await _bridge_tcp_reencrypt(reader, writer, rr, rw, label,
                                dc=dc, is_media=is_media,
                                clt_decryptor=clt_decryptor,
                                clt_encryptor=clt_encryptor,
                                tg_encryptor=tg_encryptor,
                                tg_decryptor=tg_decryptor)
    return True


def _fallback_ip(dc: int) -> Optional[str]:
    return DC_DEFAULT_IPS.get(dc)


async def _handle_client(reader, writer, secret: bytes):
    _stats.connections_total += 1
    _stats.connections_active += 1
    peer = writer.get_extra_info('peername')
    label = f"{peer[0]}:{peer[1]}" if peer else "?"

    _set_sock_opts(writer.transport)

    try:
        try:
            handshake = await asyncio.wait_for(
                reader.readexactly(HANDSHAKE_LEN), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] client disconnected before handshake", label)
            return

        result = _try_handshake(handshake, secret)
        if result is None:
            _stats.connections_bad += 1
            log.debug("[%s] bad handshake (wrong secret or proto)", label)
            try:
                while await reader.read(4096):
                    pass
            except Exception:
                pass
            return

        dc, is_media, proto_tag, client_dec_prekey_iv = result

        if proto_tag == PROTO_TAG_ABRIDGED:
            proto_int = PROTO_ABRIDGED_INT
        elif proto_tag == PROTO_TAG_INTERMEDIATE:
            proto_int = PROTO_INTERMEDIATE_INT
        else:
            proto_int = PROTO_PADDED_INTERMEDIATE_INT

        dc_idx = -dc if is_media else dc

        log.debug("[%s] handshake ok: DC%d%s proto=0x%08X",
                  label, dc, ' media' if is_media else '', proto_int)

        relay_init = _generate_relay_init(proto_tag, dc_idx)

        # key = SHA256(prekey + secret), iv from handshake
        # "dec" = decrypt data from client; "enc" = encrypt data to client
        clt_dec_prekey = client_dec_prekey_iv[:PREKEY_LEN]
        clt_dec_iv = client_dec_prekey_iv[PREKEY_LEN:]
        clt_dec_key = hashlib.sha256(clt_dec_prekey + secret).digest()

        clt_enc_prekey_iv = client_dec_prekey_iv[::-1]
        clt_enc_key = hashlib.sha256(
            clt_enc_prekey_iv[:PREKEY_LEN] + secret).digest()
        clt_enc_iv = clt_enc_prekey_iv[PREKEY_LEN:]

        clt_decryptor = Cipher(
            algorithms.AES(clt_dec_key), modes.CTR(clt_dec_iv)
        ).encryptor()
        clt_encryptor = Cipher(
            algorithms.AES(clt_enc_key), modes.CTR(clt_enc_iv)
        ).encryptor()

        # fast-forward client decryptor past the 64-byte init
        clt_decryptor.update(ZERO_64)

        # relay side: standard obfuscation (no secret hash, raw key)
        relay_enc_key = relay_init[SKIP_LEN:SKIP_LEN + PREKEY_LEN]
        relay_enc_iv = relay_init[SKIP_LEN + PREKEY_LEN:
                                  SKIP_LEN + PREKEY_LEN + IV_LEN]

        relay_dec_prekey_iv = relay_init[SKIP_LEN:
                                         SKIP_LEN + PREKEY_LEN + IV_LEN][::-1]
        relay_dec_key = relay_dec_prekey_iv[:KEY_LEN]
        relay_dec_iv = relay_dec_prekey_iv[KEY_LEN:]

        tg_encryptor = Cipher(
            algorithms.AES(relay_enc_key), modes.CTR(relay_enc_iv)
        ).encryptor()
        tg_decryptor = Cipher(
            algorithms.AES(relay_dec_key), modes.CTR(relay_dec_iv)
        ).encryptor()
        
        tg_encryptor.update(ZERO_64)

        dc_key = (dc, is_media)
        media_tag = " media" if is_media else ""

        # Fallback if DC not in config or WS blacklisted for this DC/is_media
        if dc not in proxy_config.dc_redirects or dc_key in ws_blacklist:
            fallback_dst = _fallback_ip(dc)
            if fallback_dst:
                if dc not in proxy_config.dc_redirects:
                    log.info("[%s] DC%d not in config -> TCP fallback %s:443",
                             label, dc, fallback_dst)
                else:
                    log.info("[%s] DC%d%s WS blacklisted -> TCP fallback %s:443",
                             label, dc, media_tag, fallback_dst)
                await _tcp_fallback(reader, writer, fallback_dst, 443,
                                    relay_init, label, dc=dc,
                                    is_media=is_media,
                                    clt_decryptor=clt_decryptor,
                                    clt_encryptor=clt_encryptor,
                                    tg_encryptor=tg_encryptor,
                                    tg_decryptor=tg_decryptor)
            else:
                log.warning("[%s] DC%d%s no fallback available",
                            label, dc, media_tag)
            return

        now = time.monotonic()
        fail_until = dc_fail_until.get(dc_key, 0)
        ws_timeout = WS_FAIL_TIMEOUT if now < fail_until else 10.0

        domains = _ws_domains(dc, is_media)
        target = proxy_config.dc_redirects[dc]
        ws = None
        ws_failed_redirect = False
        all_redirects = True

        ws = await _ws_pool.get(dc, is_media, target, domains)
        if ws:
            log.info("[%s] DC%d%s -> pool hit via %s",
                     label, dc, media_tag, target)
        else:
            for domain in domains:
                url = f'wss://{domain}/apiws'
                log.info("[%s] DC%d%s -> %s via %s",
                         label, dc, media_tag, url, target)
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
                    log.warning("[%s] DC%d%s WS connect failed: %s",
                                label, dc, media_tag, exc)

        # WS failed -> fallback
        if ws is None:
            if ws_failed_redirect and all_redirects:
                ws_blacklist.add(dc_key)
                log.warning("[%s] DC%d%s blacklisted for WS (all 302)",
                            label, dc, media_tag)
            elif ws_failed_redirect:
                dc_fail_until[dc_key] = now + DC_FAIL_COOLDOWN
            else:
                dc_fail_until[dc_key] = now + DC_FAIL_COOLDOWN
                log.info("[%s] DC%d%s WS cooldown for %ds",
                         label, dc, media_tag, int(DC_FAIL_COOLDOWN))

            fallback_dst = _fallback_ip(dc) or target
            log.info("[%s] DC%d%s -> TCP fallback to %s:443",
                     label, dc, media_tag, fallback_dst)
            ok = await _tcp_fallback(reader, writer, fallback_dst, 443,
                                     relay_init, label, dc=dc,
                                     is_media=is_media,
                                     clt_decryptor=clt_decryptor,
                                     clt_encryptor=clt_encryptor,
                                     tg_encryptor=tg_encryptor,
                                     tg_decryptor=tg_decryptor)
            if ok:
                log.info("[%s] DC%d%s TCP fallback closed",
                         label, dc, media_tag)
            return

        dc_fail_until.pop(dc_key, None)
        _stats.connections_ws += 1

        splitter = None
        try:
            splitter = _MsgSplitter(relay_init, proto_int)
            log.debug("[%s] MsgSplitter activated for proto 0x%08X",
                      label, proto_int)
        except Exception:
            pass

        await ws.send(relay_init)

        await _bridge_ws_reencrypt(reader, writer, ws, label,
                                   dc=dc, is_media=is_media,
                                   clt_decryptor=clt_decryptor,
                                   clt_encryptor=clt_encryptor,
                                   tg_encryptor=tg_encryptor,
                                   tg_decryptor=tg_decryptor,
                                   splitter=splitter)

    except asyncio.TimeoutError:
        log.warning("[%s] timeout during handshake", label)
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
            log.error("[%s] unexpected OS error: %s", label, exc)
    except Exception as exc:
        log.error("[%s] unexpected: %s", label, exc, exc_info=True)
    finally:
        _stats.connections_active -= 1
        try:
            writer.close()
        except BaseException:
            pass


_server_instance = None
_server_stop_event = None


async def _run(stop_event: Optional[asyncio.Event] = None):
    global _server_instance, _server_stop_event
    _server_stop_event = stop_event

    secret_bytes = bytes.fromhex(proxy_config.secret)

    def client_cb(r, w):
        asyncio.create_task(_handle_client(r, w, secret_bytes))

    server = await asyncio.start_server(client_cb, proxy_config.host, proxy_config.port)
    _server_instance = server

    for sock in server.sockets:
        try:
            sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    link_host = get_link_host(proxy_config.host)
    tg_link = f"tg://proxy?server={link_host}&port={proxy_config.port}&secret=dd{proxy_config.secret}"

    log.info("=" * 60)
    log.info("  Telegram MTProto WS Bridge Proxy")
    log.info("  Listening on   %s:%d", proxy_config.host, proxy_config.port)
    log.info("  Secret:        %s", proxy_config.secret)
    log.info("  Target DC IPs:")
    for dc in sorted(proxy_config.dc_redirects.keys()):
        ip = proxy_config.dc_redirects.get(dc)
        log.info("    DC%d: %s", dc, ip)
    log.info("=" * 60)
    log.info("  Connect link:")
    log.info("    %s", tg_link)
    log.info("=" * 60)

    async def log_stats():
        try:
            while True:
                await asyncio.sleep(60)
                bl = ', '.join(
                    f'DC{d}{"m" if m else ""}'
                    for d, m in sorted(ws_blacklist)) or 'none'
                log.info("stats: %s | ws_bl: %s", _stats.summary(), bl)
        except asyncio.CancelledError:
            raise

    log_stats_task = asyncio.create_task(log_stats())

    await _ws_pool.warmup(proxy_config.dc_redirects)

    try:
        async with server:
            if stop_event:
                serve_task = asyncio.create_task(server.serve_forever())
                stop_task = asyncio.create_task(stop_event.wait())
                done, _ = await asyncio.wait(
                    (serve_task, stop_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    server.close()
                    await server.wait_closed()
                    if not serve_task.done():
                        serve_task.cancel()
                        try:
                            await serve_task
                        except asyncio.CancelledError:
                            pass
                else:
                    stop_task.cancel()
                    try:
                        await stop_task
                    except asyncio.CancelledError:
                        pass
            else:
                await server.serve_forever()
    finally:
        log_stats_task.cancel()
        try:
            await log_stats_task
        except asyncio.CancelledError:
            pass
    _server_instance = None


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


def run_proxy(stop_event: Optional[asyncio.Event] = None):
    asyncio.run(_run(stop_event,))


def main():
    ap = argparse.ArgumentParser(
        description='Telegram MTProto WebSocket Bridge Proxy')
    ap.add_argument('--port', type=int, default=1443,
                    help=f'Listen port (default 1443)')
    ap.add_argument('--host', type=str, default='127.0.0.1',
                    help='Listen host (default 127.0.0.1)')
    ap.add_argument('--secret', type=str, default=None,
                    help='MTProto proxy secret (32 hex chars). '
                         'Auto-generated if not provided.')
    ap.add_argument('--dc-ip', metavar='DC:IP', action='append',
                    help='Target IP for a DC, e.g. --dc-ip 2:149.154.167.220')
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
        dc_redirects = parse_dc_ip_list(args.dc_ip)
    except ValueError as e:
        log.error(str(e))
        sys.exit(1)

    if args.secret:
        secret_hex = args.secret.strip()
        if len(secret_hex) != 32:
            log.error("Secret must be exactly 32 hex characters")
            sys.exit(1)
        try:
            bytes.fromhex(secret_hex)
        except ValueError:
            log.error("Secret must be valid hex")
            sys.exit(1)
    else:
        secret_hex = os.urandom(16).hex()
        log.info("Generated secret: %s", secret_hex)

    global proxy_config
    proxy_config = ProxyConfig(
        port=args.port,
        host=args.host,
        secret=secret_hex,
        dc_redirects=dc_redirects,
        buffer_size=max(4, args.buf_kb) * 1024,
        pool_size=max(0, args.pool_size)
    )

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
            maxBytes=max(32 * 1024, int(args.log_max_mb * 1024 * 1024)),
            backupCount=max(0, args.log_backups),
            encoding='utf-8',
        )
        fh.setFormatter(log_fmt)
        root.addHandler(fh)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down. Final stats: %s", _stats.summary())


if __name__ == '__main__':
    main()
