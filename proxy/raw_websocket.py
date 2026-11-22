import os
import logging
import base64
import hashlib
import struct
import asyncio
import socket as _socket

from typing import List, Optional, Tuple
from .config import proxy_config
from .utils import STREAM_CLOSE_TIMEOUT, close_stream, create_ssl_context

log = logging.getLogger('tg-mtproto-proxy')


_st_BB = struct.Struct('>BB')
_st_BBH = struct.Struct('>BBH')
_st_BBQ = struct.Struct('>BBQ')
_st_BB4s = struct.Struct('>BB4s')
_st_BBH4s = struct.Struct('>BBH4s')
_st_BBQ4s = struct.Struct('>BBQ4s')
_st_H = struct.Struct('>H')
_st_Q = struct.Struct('>Q')

_XOR_TABLES = tuple(bytes(value ^ key for value in range(256)) for key in range(256))

_MAX_UPGRADE_HEADERS = 32 * 1024
_WS_ACCEPT_GUID = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

_ssl_ctx = create_ssl_context()
_ssl_ctx_fronting = create_ssl_context(check_hostname=False)

class WsHandshakeError(Exception):
    def __init__(self, status_code: int, status_line: str,
                 headers: Optional[dict] = None, location: Optional[str] = None):
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
    if n >= 512:
        masked = bytearray(data)
        for offset in range(4):
            masked[offset::4] = masked[offset::4].translate(_XOR_TABLES[mask[offset]])
        return bytes(masked)
    mask_rep = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, 'big') ^
            int.from_bytes(mask_rep, 'big')).to_bytes(n, 'big')


def set_sock_opts(transport, buffer_size):
    sock = transport.get_extra_info('socket')
    if sock is None:
        return
    
    try:
        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
    except (OSError, AttributeError):
        pass
    
    try:
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, buffer_size)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_SNDBUF, buffer_size)
    except OSError:
        pass


async def _read_upgrade_response(reader, writer, request: bytes) -> List[str]:
    writer.write(request)
    await writer.drain()

    lines = []
    remaining = _MAX_UPGRADE_HEADERS
    while True:
        try:
            line = await reader.readline()
        except ValueError as exc:
            raise WsHandshakeError(0, 'response header line too large') from exc
        remaining -= len(line)
        if remaining < 0:
            raise WsHandshakeError(0, 'response headers too large')
        if line in (b'\r\n', b'\n'):
            return lines
        if not line.endswith(b'\n'):
            raise WsHandshakeError(0, 'incomplete response headers')
        lines.append(line.decode('utf-8', errors='replace').strip())


class RawWebSocket:
    __slots__ = ('reader', 'writer', '_closed', '_frag')

    OP_CONT = 0x0
    OP_BINARY = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    MAX_MESSAGE_LEN = 16 * 1024 * 1024

    def __init__(self, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self._closed = False
        self._frag = bytearray()

    @staticmethod
    async def connect(host: str, domain: str, timeout: float = 10.0,
                      path: str = '/apiws', *,
                      sni: Optional[str] = None, secure = True) -> 'RawWebSocket':
        ssl_context = _ssl_ctx_fronting if sni else _ssl_ctx

        if sni is None:
            sni = domain

        port = 443 if secure else 80
        tls_options = {'ssl': ssl_context, 'server_hostname': sni} if secure else {}
        writer = None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, **tls_options),
                timeout=min(timeout, 10),
            )
            set_sock_opts(writer.transport, proxy_config.buffer_size)

            ws_key = base64.b64encode(os.urandom(16)).decode()

            req = (
                f'GET {path} HTTP/1.1\r\n'
                f'Host: {domain}\r\n'
                f'Upgrade: websocket\r\n'
                f'Connection: Upgrade\r\n'
                f'Sec-WebSocket-Key: {ws_key}\r\n'
                f'Sec-WebSocket-Version: 13\r\n'
                f'Sec-WebSocket-Protocol: binary\r\n'
                f'\r\n'
            )

            response_lines = await asyncio.wait_for(
                _read_upgrade_response(reader, writer, req.encode()), deadline - loop.time())

            if not response_lines:
                raise WsHandshakeError(0, 'empty response')

            first_line = response_lines[0]
            parts = first_line.split(' ', 2)
            try:
                status_code = int(parts[1]) if len(parts) >= 2 else 0
            except ValueError:
                status_code = 0

            headers: dict[str, str] = {}
            for hl in response_lines[1:]:
                if ':' in hl:
                    k, v = hl.split(':', 1)
                    name = k.strip().lower()
                    value = v.strip()
                    headers[name] = f'{headers[name]}, {value}' if name in headers else value

            if status_code == 101:
                upgrade = {token.strip() for token in headers.get('upgrade', '').lower().split(',')}
                connection = {token.strip() for token in headers.get('connection', '').lower().split(',')}
                accept = base64.b64encode(
                    hashlib.sha1(ws_key.encode('ascii') + _WS_ACCEPT_GUID).digest()
                ).decode('ascii')
                if ('websocket' not in upgrade or 'upgrade' not in connection
                        or headers.get('sec-websocket-accept') != accept):
                    raise WsHandshakeError(101, 'invalid WebSocket upgrade headers', headers)
                return RawWebSocket(reader, writer)

            raise WsHandshakeError(status_code, first_line, headers,
                                    location=headers.get('location'))
        except BaseException as exc:
            if writer is not None:
                writer.close()
            if isinstance(exc, Exception) and log.isEnabledFor(logging.DEBUG):
                if writer is not None:
                    stage = 'ws_upgrade'
                    local = writer.get_extra_info('sockname')
                    remote = writer.get_extra_info('peername')
                else:
                    stage = 'tcp/tls' if secure else 'tcp'
                    if isinstance(exc, _socket.gaierror):
                        stage = 'dns'
                    local = remote = None
                log.debug('WS setup failed: host=%s port=%d stage=%s local=%s remote=%s error=%r',
                          host, port, stage, local, remote, exc)
            raise

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
            opcode, payload, fin = await self._read_frame()

            if opcode == self.OP_CLOSE:
                self._closed = True
                code, reason = self._parse_close(payload)
                log.debug("WS OP_CLOSE from upstream: code=%s reason=%r",
                          code, reason)
                try:
                    self.writer.write(self._build_frame(
                        self.OP_CLOSE,
                        payload[:2] if payload else b'', mask=True))
                    await asyncio.wait_for(self.writer.drain(), STREAM_CLOSE_TIMEOUT)
                except (OSError, asyncio.TimeoutError):
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

            if opcode in (self.OP_CONT, 0x1, self.OP_BINARY):
                if fin and not self._frag:
                    return payload
                self._frag.extend(payload)
                if len(self._frag) > self.MAX_MESSAGE_LEN:
                    raise ConnectionError(
                        f"WS message too large: {len(self._frag)} bytes")
                if not fin:
                    continue
                message = bytes(self._frag)
                self._frag.clear()
                return message
            continue
        return None

    async def close(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_CLOSE_TIMEOUT
        try:
            if not self._closed:
                self._closed = True
                self.writer.write(self._build_frame(self.OP_CLOSE, b'', mask=True))
                await asyncio.wait_for(self.writer.drain(), deadline - loop.time())
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            await close_stream(self.writer, deadline - loop.time())

    _WS_CLOSE_REASONS = {
        1000: 'normal', 1001: 'going_away', 1002: 'protocol_error',
        1003: 'unsupported_data', 1006: 'abnormal', 1007: 'bad_data',
        1008: 'policy_violation', 1009: 'too_big', 1010: 'missing_extension',
        1011: 'internal_error',
    }

    @classmethod
    def _parse_close(cls, payload: Optional[bytes]) -> Tuple[Optional[int], str]:
        if not payload or len(payload) < 2:
            return None, ''
        try:
            code = int.from_bytes(payload[:2], 'big')
            text = payload[2:].decode('utf-8', errors='replace')
            name = cls._WS_CLOSE_REASONS.get(code)
            return code, f"{text} ({name})" if name else text
        except Exception:
            return None, ''

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

    async def _read_frame(self) -> Tuple[int, bytes, bool]:
        hdr = await self.reader.readexactly(2)
        fin = bool(hdr[0] & 0x80)
        opcode = hdr[0] & 0x0F
        length = hdr[1] & 0x7F
        if length == 126:
            length = _st_H.unpack(await self.reader.readexactly(2))[0]
        elif length == 127:
            length = _st_Q.unpack(await self.reader.readexactly(8))[0]
        if length > self.MAX_MESSAGE_LEN:
            raise ConnectionError(f"WS frame too large: {length} bytes")
        if hdr[1] & 0x80:
            mask_key = await self.reader.readexactly(4)
            payload = await self.reader.readexactly(length)
            return opcode, _xor_mask(payload, mask_key), fin
        payload = await self.reader.readexactly(length)
        return opcode, payload, fin
