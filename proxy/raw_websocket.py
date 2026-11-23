import os
import logging
import math
import base64
import struct
import asyncio
import socket as _socket

from typing import Dict, Iterable, List, Optional, Tuple
from .config import proxy_config
from .utils import create_ssl_context

log = logging.getLogger('tg-mtproto-proxy')


CF_TIMEOUT_COOLDOWN = 30.0
CF_CONNECT_TIMEOUTS = (10.0, 6.0, 4.0, 3.0, 3.0)
_cf_timeout_until: Dict[Tuple[str, bool], float] = {}


_st_BB = struct.Struct('>BB')
_st_BBH = struct.Struct('>BBH')
_st_BBQ = struct.Struct('>BBQ')
_st_BB4s = struct.Struct('>BB4s')
_st_BBH4s = struct.Struct('>BBH4s')
_st_BBQ4s = struct.Struct('>BBQ4s')
_st_H = struct.Struct('>H')
_st_Q = struct.Struct('>Q')

_XOR_TABLES = tuple(bytes(value ^ key for value in range(256)) for key in range(256))

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


async def _wait_close(awaitable, timeout):
    # wait_for() can lose external cancellation when its task finishes at the
    # same time on Python < 3.12. wait() leaves cancellation with the caller.
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait((task,), timeout=timeout)
        if not done:
            raise asyncio.TimeoutError()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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


class RawWebSocket:
    __slots__ = ('reader', 'writer', '_closed', '_frag')

    OP_CONT = 0x0
    OP_BINARY = 0x2
    OP_CLOSE = 0x8
    OP_PING = 0x9
    OP_PONG = 0xA

    MAX_MESSAGE_LEN = 16 * 1024 * 1024
    CLOSE_TIMEOUT = 2.0

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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        writer = None
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

            writer.write(req.encode())
            await asyncio.wait_for(writer.drain(), deadline - loop.time())

            response_lines: list[str] = []
            while True:
                line = await asyncio.wait_for(reader.readline(), deadline - loop.time())
                if line in (b'\r\n', b'\n', b''):
                    break
                response_lines.append(
                    line.decode('utf-8', errors='replace').strip())

            if not response_lines:
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

            raise WsHandshakeError(status_code, first_line, headers,
                                    location=headers.get('location'))
        except BaseException as exc:
            if writer is not None:
                writer.transport.abort()
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

    async def send_init(self, data: bytes):
        try:
            await asyncio.wait_for(self.send(data), timeout=10)
        except BaseException:
            self._closed = True
            self.writer.transport.abort()
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
        try:
            if not self._closed:
                self._closed = True
                try:
                    self.writer.write(
                        self._build_frame(self.OP_CLOSE, b'', mask=True))
                    await _wait_close(self.writer.drain(), self.CLOSE_TIMEOUT)
                except Exception:
                    pass
        finally:
            try:
                self.writer.close()
                # A silent TLS peer would hold us here for ssl_shutdown_timeout
                # (30s) and delay proxy shutdown past the tray's stop timeout
                await _wait_close(self.writer.wait_closed(), self.CLOSE_TIMEOUT)
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                try:
                    self.writer.transport.abort()
                except Exception:
                    pass
                if isinstance(exc, asyncio.CancelledError):
                    raise
            except Exception:
                pass

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


def reset_cf_timeouts() -> None:
    _cf_timeout_until.clear()


async def connect_cf_domains(candidates: Iterable[Tuple[str, str]], *, label: str,
                             secure: bool, path: str = '/apiws', timeout: float = 10.0
                             ) -> Optional[Tuple[RawWebSocket, str]]:
    loop = asyncio.get_running_loop()
    timeouts = 0

    for name, host in candidates:
        key = (host, secure)
        now = loop.time()

        retry_at = _cf_timeout_until.get(key)
        if retry_at is not None:
            if now < retry_at:
                continue
            _cf_timeout_until[key] = math.inf

        try:
            attempt_timeout = min(timeout, CF_CONNECT_TIMEOUTS[timeouts])
            ws = await RawWebSocket.connect(host, host, path=path, secure=secure, timeout=attempt_timeout)

        except (asyncio.TimeoutError, TimeoutError):
            now = loop.time()
            if retry_at is not None or key not in _cf_timeout_until:
                for old_key, until in list(_cf_timeout_until.items()):
                    if until + CF_TIMEOUT_COOLDOWN <= now:
                        del _cf_timeout_until[old_key]
                _cf_timeout_until[key] = now + CF_TIMEOUT_COOLDOWN
                log.warning('[%s] CF WebSocket timed out (host=%s port=%d); retry in %.0fs',
                            label, host, 443 if secure else 80, CF_TIMEOUT_COOLDOWN)
            timeouts += 1
            if timeouts >= len(CF_CONNECT_TIMEOUTS):
                break
            continue

        except Exception as exc:
            log.warning('[%s] CF WebSocket failed (host=%s port=%d): %r',
                        label, host, 443 if secure else 80, exc)
            ws = None

        finally:
            if retry_at is not None and _cf_timeout_until.get(key) == math.inf:
                _cf_timeout_until[key] = retry_at
        
        _cf_timeout_until.pop(key, None)
        if ws is not None:
            return ws, name
    
    return None
