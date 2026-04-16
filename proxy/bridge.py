import asyncio
import logging
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from typing import Dict, List, Optional

from .utils import *
from .stats import stats
from .balancer import balancer
from .config import proxy_config
from .raw_websocket import RawWebSocket


log = logging.getLogger('tg-mtproto-proxy')
_st_I_le = struct.Struct('<I')

ZERO_64 = b'\x00' * 64
DC_DEFAULT_IPS: Dict[int, str] = {
    1: '149.154.175.50',
    2: '149.154.167.51',
    3: '149.154.175.100',
    4: '149.154.167.91',
    5: '149.154.171.5',
    203: '91.105.192.100'
}


class CryptoCtx:
    __slots__ = ('clt_dec', 'clt_enc', 'tg_enc', 'tg_dec')

    def __init__(self, clt_dec, clt_enc, tg_enc, tg_dec):
        self.clt_dec = clt_dec  # decrypt from client
        self.clt_enc = clt_enc  # encrypt to client
        self.tg_enc = tg_enc    # encrypt to telegram
        self.tg_dec = tg_dec    # decrypt from telegram


class MsgSplitter:
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



async def do_fallback(reader, writer, relay_init, label,
                       dc, is_media, media_tag,
                       ctx: CryptoCtx, splitter=None):
    fallback_dst = DC_DEFAULT_IPS.get(dc)
    use_cf = proxy_config.fallback_cfproxy
    cf_first = proxy_config.fallback_cfproxy_priority

    methods: List[str] = ['tcp']

    if use_cf:
        methods.insert(0 if cf_first else 1, 'cf')

    for method in methods:
        if method == 'cf':
            ok = await _cfproxy_fallback(
                reader, writer, relay_init, label,
                dc=dc, is_media=is_media,
                ctx=ctx, splitter=splitter)
            if ok:
                return True
        elif method == 'tcp' and fallback_dst:
            log.info("[%s] DC%d%s -> TCP fallback to %s:443",
                     label, dc, media_tag, fallback_dst)
            ok = await _tcp_fallback(
                reader, writer, fallback_dst, 443,
                relay_init, label, dc=dc, is_media=is_media, ctx=ctx)
            if ok:
                return True
    return False


async def _cfproxy_fallback(reader, writer, relay_init, label,
                            dc=None, is_media=False,
                            ctx: CryptoCtx = None, splitter=None):
    media_tag = ' media' if is_media else ''
    ws = None
    chosen_domain = None

    log.info("[%s] DC%d%s -> trying CF proxy",
            label, dc, media_tag)

    for base_domain in balancer.get_domains_for_dc(dc):
        domain = f'kws{dc}.{base_domain}'
        try:
            ws = await RawWebSocket.connect(domain, domain, timeout=10.0)
            chosen_domain = base_domain
            break
        except Exception as exc:
            log.warning("[%s] DC%d%s CF proxy failed: %s",
                        label, dc, media_tag, exc)

    if ws is None:
        return False

    if chosen_domain and balancer.update_domain_for_dc(dc, chosen_domain):
        log.info("[%s] Switched active CF domain", label)

    stats.connections_cfproxy += 1
    await ws.send(relay_init)
    await bridge_ws_reencrypt(reader, writer, ws, label,
                               dc=dc, is_media=is_media,
                               ctx=ctx, splitter=splitter)
    return True


async def _tcp_fallback(reader, writer, dst, port, relay_init, label,
                        dc=None, is_media=False, ctx: CryptoCtx = None):
    try:
        rr, rw = await asyncio.wait_for(
            asyncio.open_connection(dst, port), timeout=10)
    except Exception as exc:
        log.warning("[%s] TCP fallback to %s:%d failed: %s",
                    label, dst, port, exc)
        return False

    stats.connections_tcp_fallback += 1
    rw.write(relay_init)
    await rw.drain()
    await _bridge_tcp_reencrypt(reader, writer, rr, rw, label,
                                dc=dc, is_media=is_media, ctx=ctx)
    return True


async def bridge_ws_reencrypt(reader, writer, ws: RawWebSocket, label,
                               dc=None, is_media=False,
                               ctx: CryptoCtx = None,
                               splitter: MsgSplitter = None):
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
                stats.bytes_up += n
                up_bytes += n
                up_packets += 1
                plain = ctx.clt_dec.update(chunk)
                chunk = ctx.tg_enc.update(plain)
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
                stats.bytes_down += n
                down_bytes += n
                down_packets += 1
                plain = ctx.tg_dec.update(data)
                data = ctx.clt_enc.update(plain)
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
                 human_bytes(up_bytes), up_packets,
                 human_bytes(down_bytes), down_packets,
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
                                ctx: CryptoCtx = None):
    """Bidirectional TCP <-> TCP with re-encryption."""

    async def forward(src, dst_w, is_up):
        try:
            while True:
                data = await src.read(65536)
                if not data:
                    break
                n = len(data)
                if is_up:
                    stats.bytes_up += n
                    plain = ctx.clt_dec.update(data)
                    data = ctx.tg_enc.update(plain)
                else:
                    stats.bytes_down += n
                    plain = ctx.tg_dec.update(data)
                    data = ctx.clt_enc.update(plain)
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