from __future__ import annotations

import os
import sys
import time
import struct
import random
import asyncio
import hashlib
import argparse
import logging
import logging.handlers
import socket as _socket

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

if __name__ == '__main__' and (__package__ is None or __package__ == ''):
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    __package__ = 'proxy'

from .utils import *
from .stats import stats
from .config import proxy_config, parse_dc_ip_list, start_cfproxy_domain_refresh, CFPROXY_DEFAULT_DOMAINS
from .bridge import MsgSplitter, CryptoCtx, do_fallback, bridge_ws_reencrypt
from .raw_websocket import RawWebSocket, WsHandshakeError, set_sock_opts
from .fake_tls import proxy_to_masking_domain, verify_client_hello, build_server_hello, FakeTlsStream, TLS_RECORD_HANDSHAKE
from utils.log_utils import (
    LogAction,
    build_event_formatter,
    log_event,
    log_exception_event,
    mask_secret,
    set_text_event_meta_visible,
)


log = logging.getLogger('tg-mtproto-proxy')

DC_FAIL_COOLDOWN = 30.0
WS_FAIL_TIMEOUT = 2.0
ws_blacklist: Set[Tuple[int, bool]] = set()
dc_fail_until: Dict[Tuple[int, bool], float] = {}


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


def _ws_domains(dc: int, is_media) -> List[str]:
    if dc == 203:
        dc = 2
    if is_media is None or is_media:
        return [f'kws{dc}-1.web.telegram.org', f'kws{dc}.web.telegram.org']
    return [f'kws{dc}.web.telegram.org', f'kws{dc}-1.web.telegram.org']


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
            stats.pool_hits += 1
            log_event(
                log,
                logging.DEBUG,
                LogAction.WS_POOL_HIT,
                dc=dc,
                media=is_media,
                age_s=round(age, 1),
                left=len(bucket),
            )
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
                self._connect_one(target_ip, domains))
                for _ in range(needed)]
            for t in tasks:
                try:
                    ws = await t
                    if ws:
                        bucket.append((ws, time.monotonic()))
                except Exception:
                    pass
            log_event(log, logging.DEBUG, LogAction.WS_POOL_REFILLED, dc=dc, media=is_media, ready=len(bucket))
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
        log_event(log, logging.INFO, LogAction.WS_POOL_WARMUP_STARTED, dc_count=len(dc_redirects))

    def reset(self):
        self._idle.clear()
        self._refilling.clear()

_ws_pool = _WsPool()


async def _handle_client(reader, writer, secret: bytes):
    stats.connections_total += 1
    stats.connections_active += 1
    peer = writer.get_extra_info('peername')
    label = f"{peer[0]}:{peer[1]}" if peer else "?"

    set_sock_opts(writer.transport, proxy_config.buffer_size)

    tls_stream = None
    masking = proxy_config.fake_tls_domain

    try:
        if proxy_config.proxy_protocol:
            try:
                pp_line = await asyncio.wait_for(
                    reader.readline(), timeout=10)
            except asyncio.IncompleteReadError:
                log_event(log, logging.DEBUG, LogAction.CLIENT_DISCONNECTED_PROXY_HEADER, label=label)
                return
            pp_text = pp_line.decode('ascii', errors='replace').strip()
            if pp_text.startswith('PROXY '):
                parts = pp_text.split()
                if len(parts) >= 6:
                    label = f"{parts[2]}:{parts[4]}"
                log_event(log, logging.DEBUG, LogAction.PROXY_PROTOCOL_HEADER, label=label, header=pp_text)
            else:
                log_event(log, logging.DEBUG, LogAction.PROXY_PROTOCOL_HEADER_INVALID, label=label, header=pp_text[:60])

        try:
            first_byte = await asyncio.wait_for(
                reader.readexactly(1), timeout=10)
        except asyncio.IncompleteReadError:
            log_event(log, logging.DEBUG, LogAction.CLIENT_DISCONNECTED_BEFORE_HANDSHAKE, label=label)
            return

        if first_byte[0] == TLS_RECORD_HANDSHAKE and masking:
            try:
                hdr_rest = await asyncio.wait_for(
                    reader.readexactly(4), timeout=10)
            except asyncio.IncompleteReadError:
                log_event(log, logging.DEBUG, LogAction.TLS_RECORD_HEADER_INCOMPLETE, label=label)
                return

            tls_header = first_byte + hdr_rest
            record_len = struct.unpack('>H', tls_header[3:5])[0]

            try:
                record_body = await asyncio.wait_for(
                    reader.readexactly(record_len), timeout=10)
            except asyncio.IncompleteReadError:
                log_event(log, logging.DEBUG, LogAction.TLS_RECORD_BODY_INCOMPLETE, label=label, expected=record_len)
                return

            client_hello = tls_header + record_body

            tls_result = verify_client_hello(client_hello, secret)

            if tls_result is None:
                log_event(
                    log,
                    logging.DEBUG,
                    LogAction.FAKE_TLS_VERIFY_FAILED_MASKING,
                    label=label,
                    size=len(client_hello),
                    record_len=record_len,
                )
                await proxy_to_masking_domain(
                    reader, writer, client_hello, masking, label)
                return

            client_random, session_id, ts = tls_result
            log_event(log, logging.DEBUG, LogAction.FAKE_TLS_HANDSHAKE_OK, label=label, ts=ts)

            server_hello = build_server_hello(secret, client_random, session_id)
            writer.write(server_hello)
            await writer.drain()

            tls_stream = FakeTlsStream(reader, writer)

            try:
                handshake = await asyncio.wait_for(
                    tls_stream.readexactly(HANDSHAKE_LEN), timeout=10)
            except asyncio.IncompleteReadError:
                log_event(log, logging.DEBUG, LogAction.OBFS_INIT_INCOMPLETE_INSIDE_TLS, label=label)
                return
        elif masking:
            log_event(
                log,
                logging.DEBUG,
                LogAction.NON_TLS_REDIRECT_SENT,
                label=label,
                first_byte=f"0x{first_byte[0]:02X}",
            )
            redirect = (
                f"HTTP/1.1 301 Moved Permanently\r\n"
                f"Location: https://{masking}/\r\n"
                f"Content-Length: 0\r\n"
                f"Connection: close\r\n\r\n"
            ).encode()
            writer.write(redirect)
            await writer.drain()
            return
        else:
            try:
                rest = await asyncio.wait_for(
                    reader.readexactly(HANDSHAKE_LEN - 1), timeout=10)
            except asyncio.IncompleteReadError:
                log_event(log, logging.DEBUG, LogAction.CLIENT_DISCONNECTED_BEFORE_HANDSHAKE, label=label)
                return
            handshake = first_byte + rest

        result = _try_handshake(handshake, secret)
        if result is None:
            stats.connections_bad += 1
            log_event(log, logging.DEBUG, LogAction.BAD_HANDSHAKE, label=label, reason="wrong_secret_or_proto")
            try:
                drain_src = tls_stream or reader
                while await drain_src.read(4096):
                    pass
            except Exception:
                pass
            return

        clt_reader = tls_stream or reader
        clt_writer = tls_stream or writer

        dc, is_media, proto_tag, client_dec_prekey_iv = result

        if proto_tag == PROTO_TAG_ABRIDGED:
            proto_int = PROTO_ABRIDGED_INT
        elif proto_tag == PROTO_TAG_INTERMEDIATE:
            proto_int = PROTO_INTERMEDIATE_INT
        else:
            proto_int = PROTO_PADDED_INTERMEDIATE_INT

        dc_idx = -dc if is_media else dc

        log_event(
            log,
            logging.DEBUG,
            LogAction.HANDSHAKE_OK,
            label=label,
            dc=dc,
            media=is_media,
            proto=f"0x{proto_int:08X}",
        )

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

        ctx = CryptoCtx(clt_decryptor, clt_encryptor, tg_encryptor, tg_decryptor)

        dc_key = f'{dc}{"m" if is_media else ""}'
        media_tag = " media" if is_media else ""

        # Fallback if DC not in config or WS blacklisted for this DC/is_media
        if dc not in proxy_config.dc_redirects or dc_key in ws_blacklist:
            if dc not in proxy_config.dc_redirects:
                log_event(log, logging.INFO, LogAction.FALLBACK_DUE_TO_MISSING_DC, label=label, dc=dc)
            else:
                log_event(log, logging.INFO, LogAction.FALLBACK_DUE_TO_WS_BLACKLIST, label=label, dc=dc, media=is_media)
            splitter = None
            try:
                splitter = MsgSplitter(relay_init, proto_int)
            except Exception:
                pass
            ok = await do_fallback(
                clt_reader, clt_writer, relay_init, label,
                dc, is_media, media_tag,
                ctx, splitter=splitter)
            if not ok:
                log_event(log, logging.WARNING, LogAction.FALLBACK_UNAVAILABLE, label=label, dc=dc, media=is_media)
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
            log_event(log, logging.INFO, LogAction.WS_POOL_CONNECTION_USED, label=label, dc=dc, media=is_media, target=target)
        else:
            for domain in domains:
                url = f'wss://{domain}/apiws'
                log_event(log, logging.INFO, LogAction.WS_CONNECT_ATTEMPT, label=label, dc=dc, media=is_media, url=url, target=target)
                try:
                    ws = await RawWebSocket.connect(target, domain,
                                                    timeout=ws_timeout)
                    all_redirects = False
                    break
                except WsHandshakeError as exc:
                    stats.ws_errors += 1
                    if exc.is_redirect:
                        ws_failed_redirect = True
                        log_event(
                            log,
                            logging.WARNING,
                            LogAction.WS_REDIRECT_RECEIVED,
                            label=label,
                            dc=dc,
                            media=is_media,
                            status=exc.status_code,
                            domain=domain,
                            location=exc.location or "?",
                        )
                        continue
                    else:
                        all_redirects = False
                        log_event(
                            log,
                            logging.WARNING,
                            LogAction.WS_HANDSHAKE_FAILED,
                            label=label,
                            dc=dc,
                            media=is_media,
                            status_line=exc.status_line,
                        )
                except Exception as exc:
                    stats.ws_errors += 1
                    all_redirects = False
                    log_event(log, logging.WARNING, LogAction.WS_CONNECT_FAILED, label=label, dc=dc, media=is_media, error=str(exc))

        # WS failed -> fallback
        if ws is None:
            if ws_failed_redirect and all_redirects:
                ws_blacklist.add(dc_key)
                log_event(log, logging.WARNING, LogAction.WS_BLACKLISTED_ALL_REDIRECTS, label=label, dc=dc, media=is_media)
            elif ws_failed_redirect:
                dc_fail_until[dc_key] = now + DC_FAIL_COOLDOWN
            else:
                dc_fail_until[dc_key] = now + DC_FAIL_COOLDOWN
                log_event(
                    log,
                    logging.INFO,
                    LogAction.WS_COOLDOWN_STARTED,
                    label=label,
                    dc=dc,
                    media=is_media,
                    seconds=int(DC_FAIL_COOLDOWN),
                )

            splitter_fb = None
            try:
                splitter_fb = MsgSplitter(relay_init, proto_int)
            except Exception:
                pass
            ok = await do_fallback(
                clt_reader, clt_writer, relay_init, label,
                dc, is_media, media_tag,
                ctx, splitter=splitter_fb)
            if ok:
                log_event(log, logging.INFO, LogAction.FALLBACK_CLOSED, label=label, dc=dc, media=is_media)
            return

        dc_fail_until.pop(dc_key, None)
        stats.connections_ws += 1

        splitter = None
        try:
            splitter = MsgSplitter(relay_init, proto_int)
            log_event(log, logging.DEBUG, LogAction.MSG_SPLITTER_ACTIVATED, label=label, proto=f"0x{proto_int:08X}")
        except Exception:
            pass

        await ws.send(relay_init)

        await bridge_ws_reencrypt(clt_reader, clt_writer, ws, label,
                                   dc=dc, is_media=is_media,
                                   ctx=ctx, splitter=splitter)

    except asyncio.TimeoutError:
        log_event(log, logging.WARNING, LogAction.HANDSHAKE_TIMEOUT, label=label)
    except asyncio.IncompleteReadError:
        log_event(log, logging.DEBUG, LogAction.CLIENT_DISCONNECTED, label=label)
    except asyncio.CancelledError:
        log_event(log, logging.DEBUG, LogAction.CLIENT_TASK_CANCELLED, label=label)
    except ConnectionResetError:
        log_event(log, logging.DEBUG, LogAction.CONNECTION_RESET, label=label)
    except OSError as exc:
        if getattr(exc, 'winerror', None) == 1236:
            log_event(log, logging.DEBUG, LogAction.CONNECTION_ABORTED_LOCAL_SYSTEM, label=label)
        else:
            log_event(log, logging.ERROR, LogAction.UNEXPECTED_OS_ERROR, label=label, error=str(exc))
    except Exception as exc:
        log_exception_event(log, LogAction.UNEXPECTED_CLIENT_ERROR, label=label, error=str(exc))
    finally:
        stats.connections_active -= 1
        try:
            writer.close()
            await writer.wait_closed()
        except BaseException:
            pass


_server_instance = None
_server_stop_event = None
_client_tasks: Set[asyncio.Task] = set()


async def _run(stop_event: Optional[asyncio.Event] = None):
    global _server_instance, _server_stop_event
    _server_stop_event = stop_event

    _ws_pool.reset()
    ws_blacklist.clear()
    dc_fail_until.clear()
    _client_tasks.clear()

    if proxy_config.fallback_cfproxy:
        user = proxy_config.cfproxy_user_domain
        if user:
            proxy_config.cfproxy_domains = [user]
            proxy_config.active_cfproxy_domain = user
        else:
            proxy_config.cfproxy_domains = list(CFPROXY_DEFAULT_DOMAINS)
            proxy_config.active_cfproxy_domain = random.choice(CFPROXY_DEFAULT_DOMAINS)
            start_cfproxy_domain_refresh()

    secret_bytes = bytes.fromhex(proxy_config.secret)

    def client_cb(r, w):
        task = asyncio.create_task(_handle_client(r, w, secret_bytes))
        _client_tasks.add(task)
        task.add_done_callback(_client_tasks.discard)

    server = await asyncio.start_server(client_cb, proxy_config.host, proxy_config.port)
    _server_instance = server

    for sock in server.sockets:
        try:
            sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        except (OSError, AttributeError):
            pass

    link_host = get_link_host(proxy_config.host)
    ftls = proxy_config.fake_tls_domain
    dd_link = (f"tg://proxy?server={link_host}"
               f"&port={proxy_config.port}"
               f"&secret=dd{proxy_config.secret}")
    ee_link = ""
    if ftls:
        domain_hex = ftls.encode('ascii').hex()
        ee_link = (f"tg://proxy?server={link_host}"
                   f"&port={proxy_config.port}"
                   f"&secret=ee{proxy_config.secret}{domain_hex}")

    log_event(
        log,
        logging.INFO,
        LogAction.PROXY_SERVER_STARTED,
        host=proxy_config.host,
        port=proxy_config.port,
        secret=mask_secret(proxy_config.secret),
    )
    if ftls:
        log_event(log, logging.INFO, LogAction.FAKE_TLS_ENABLED, domain=ftls)
    for dc in sorted(proxy_config.dc_redirects.keys()):
        ip = proxy_config.dc_redirects.get(dc)
        log_event(log, logging.INFO, LogAction.DC_TARGET, dc=dc, ip=ip)
    if proxy_config.fallback_cfproxy:
        prio = 'CF first' if proxy_config.fallback_cfproxy_priority else 'TCP first'
        user_domain = "user" if proxy_config.cfproxy_user_domain else "auto"
        log_event(log, logging.INFO, LogAction.CFPROXY_ENABLED, priority=prio, domain_source=user_domain)
    if ftls:
        log_event(log, logging.INFO, LogAction.CONNECT_LINK_EE, url=ee_link)
    else:
        log_event(log, logging.INFO, LogAction.CONNECT_SECRET_STANDARD, secret=mask_secret(proxy_config.secret))
        log_event(log, logging.INFO, LogAction.CONNECT_LINK_DD, url=dd_link)

    async def log_stats():
        try:
            while True:
                await asyncio.sleep(60)
                bl = ', '.join(f'DC{k}' for k in sorted(ws_blacklist)) or 'none'
                log_event(log, logging.INFO, LogAction.RUNTIME_STATS, summary=stats.summary(), ws_blacklist=bl)
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


def run_proxy(stop_event: Optional[asyncio.Event] = None):
    asyncio.run(_run(stop_event,))


def main():
    ap = argparse.ArgumentParser(
        description='Telegram MTProto WebSocket Bridge Proxy')
    ap.add_argument('--port', type=int, default=1443,
                    help='Listen port (default 1443)')
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
    ap.add_argument('--log-json', action='store_true',
                    help='Emit logs in JSON lines format')
    ap.add_argument('--buf-kb', type=int, default=256, metavar='KB',
                    help='Socket send/recv buffer size in KB (default 256)')
    ap.add_argument('--pool-size', type=int, default=4, metavar='N',
                    help='WS connection pool size per DC (default 4, min 0)')
    ap.add_argument('--cfproxy-domain', type=str, default='',
                    metavar='DOMAIN',
                    help='User defined Cloudflare-proxied domain for WS fallback')
    ap.add_argument('--no-cfproxy', action='store_true',
                    help='Disable Cloudflare proxy fallback')
    ap.add_argument('--cfproxy-priority', type=bool, default=True,
                    help='Try cfproxy before tcp fallback (default: true)')
    ap.add_argument('--fake-tls-domain', type=str, default='',
                    metavar='DOMAIN',
                    help='Enable Fake TLS (ee-secret) masking with the given '
                         'SNI domain, e.g. example.com')
    ap.add_argument('--proxy-protocol', action='store_true',
                    help='Accept PROXY protocol v1 header '
                         '(for use behind nginx/haproxy with proxy_protocol on)')
    args = ap.parse_args()

    if not args.dc_ip:
        args.dc_ip = ['2:149.154.167.220', '4:149.154.167.220']

    try:
        dc_redirects = parse_dc_ip_list(args.dc_ip)
    except ValueError as e:
        log_event(log, logging.ERROR, LogAction.INVALID_DC_IP_ARG, error=str(e))
        sys.exit(1)

    if args.secret:
        secret_hex = args.secret.strip()
        if len(secret_hex) != 32:
            log_event(log, logging.ERROR, LogAction.INVALID_SECRET_LENGTH)
            sys.exit(1)
        try:
            bytes.fromhex(secret_hex)
        except ValueError:
            log_event(log, logging.ERROR, LogAction.INVALID_SECRET_HEX)
            sys.exit(1)
    else:
        secret_hex = os.urandom(16).hex()
        log_event(log, logging.INFO, LogAction.SECRET_GENERATED, secret=mask_secret(secret_hex))

    proxy_config.port = args.port
    proxy_config.host = args.host
    proxy_config.secret = secret_hex
    proxy_config.dc_redirects = dc_redirects
    proxy_config.buffer_size = max(4, args.buf_kb) * 1024
    proxy_config.pool_size = max(0, args.pool_size)
    proxy_config.fallback_cfproxy = not args.no_cfproxy
    proxy_config.fallback_cfproxy_priority = args.cfproxy_priority
    proxy_config.cfproxy_user_domain = args.cfproxy_domain
    proxy_config.fake_tls_domain = args.fake_tls_domain.strip()
    proxy_config.proxy_protocol = args.proxy_protocol

    log_level = logging.DEBUG if args.verbose else logging.INFO
    set_text_event_meta_visible(args.verbose or args.log_json)
    text_fmt = '%(asctime)s  %(levelname)-5s  %(message)s'
    root = logging.getLogger()
    root.setLevel(log_level)

    console = logging.StreamHandler()
    console.setFormatter(
        build_event_formatter(json_mode=args.log_json, datefmt='%H:%M:%S', text_fmt=text_fmt)
    )
    root.addHandler(console)

    if args.log_file:
        fh = logging.handlers.RotatingFileHandler(
            args.log_file,
            maxBytes=max(32 * 1024, int(args.log_max_mb * 1024 * 1024)),
            backupCount=max(0, args.log_backups),
            encoding='utf-8',
        )
        fh.setFormatter(
            build_event_formatter(json_mode=args.log_json, datefmt='%Y-%m-%d %H:%M:%S', text_fmt=text_fmt)
        )
        root.addHandler(fh)

    logging.getLogger('asyncio').setLevel(logging.WARNING)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log_event(log, logging.INFO, LogAction.SHUTDOWN_KEYBOARD_INTERRUPT, final_stats=stats.summary())


if __name__ == '__main__':
    main()
