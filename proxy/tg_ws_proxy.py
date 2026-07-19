from __future__ import annotations

import os
import sys
import time
import struct
import asyncio
import hashlib
import argparse
import logging
import logging.handlers
import socket as _socket

from typing import Dict, Optional, Set, Tuple


if __name__ == '__main__' and (__package__ is None or __package__ == ''):
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    __package__ = 'proxy'

from .utils import *
from .stats import stats
from .config import proxy_config, parse_dc_ip_list, start_cfproxy_domain_refresh, coerce_domain_list
from .bridge import MsgSplitter, CryptoCtx, do_fallback, bridge_ws_reencrypt
from .raw_websocket import RawWebSocket, WsHandshakeError, set_sock_opts
from .fake_tls import proxy_to_masking_domain, verify_client_hello, build_server_hello, FakeTlsStream, TLS_RECORD_HANDSHAKE
from .balancer import balancer
from .pool import ws_pool, cf_worker_pool
from ._aes import Cipher, algorithms, modes


log = logging.getLogger('tg-mtproto-proxy')

IP_FAIL_COOLDOWN = 3600.0
DC_FAIL_COOLDOWN = 60.0
WS_FAIL_TIMEOUT = 2.0
LISTENER_CHECK_INTERVAL = 5.0
LISTENER_RESTART_DELAY = 1.0
ws_blacklist: Set[str] = set()
dc_fail_until: Dict[str, float] = {}
ip_fail_until: Dict[str, float] = {}


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





async def _read_client_init(reader, writer, secret, label, masking):
    if proxy_config.proxy_protocol:
        try:
            pp_line = await asyncio.wait_for(
                reader.readline(), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] disconnected during PROXY header", label)
            return None
        pp_text = pp_line.decode('ascii', errors='replace').strip()
        if pp_text.startswith('PROXY '):
            parts = pp_text.split()
            if len(parts) >= 6:
                label = f"{parts[2]}:{parts[4]}"
            log.debug("[%s] PROXY protocol: %s", label, pp_text)
        else:
            log.debug("[%s] expected PROXY header, got: %r", label,
                      pp_text[:60])

    try:
        first_byte = await asyncio.wait_for(
            reader.readexactly(1), timeout=10)
    except asyncio.IncompleteReadError:
        log.debug("[%s] client disconnected before handshake", label)
        return None

    if first_byte[0] == TLS_RECORD_HANDSHAKE and masking:
        try:
            hdr_rest = await asyncio.wait_for(
                reader.readexactly(4), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] incomplete TLS record header", label)
            return None

        tls_header = first_byte + hdr_rest
        record_len = struct.unpack('>H', tls_header[3:5])[0]

        try:
            record_body = await asyncio.wait_for(
                reader.readexactly(record_len), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] incomplete TLS record body", label)
            return None

        client_hello = tls_header + record_body

        tls_result = verify_client_hello(client_hello, secret)

        if tls_result is None:
            log.debug("[%s] Fake TLS verify failed (size=%d rec=%d) "
                      "-> masking",
                      label, len(client_hello), record_len)
            await proxy_to_masking_domain(
                reader, writer, client_hello, masking, label)
            return None

        client_random, session_id, ts = tls_result
        log.debug("[%s] Fake TLS handshake ok (ts=%d)", label, ts)

        server_hello = build_server_hello(secret, client_random, session_id)
        writer.write(server_hello)
        await writer.drain()

        tls_stream = FakeTlsStream(reader, writer)

        try:
            handshake = await asyncio.wait_for(
                tls_stream.readexactly(HANDSHAKE_LEN), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] incomplete obfs2 init inside TLS", label)
            return None

        return handshake, tls_stream, tls_stream, label

    elif masking:
        log.debug("[%s] non-TLS byte 0x%02X -> HTTP redirect", label,
                  first_byte[0])
        redirect = (
            f"HTTP/1.1 301 Moved Permanently\r\n"
            f"Location: https://{masking}/\r\n"
            f"Content-Length: 0\r\n"
            f"Connection: close\r\n\r\n"
        ).encode()
        writer.write(redirect)
        await writer.drain()
        return None

    else:
        try:
            rest = await asyncio.wait_for(
                reader.readexactly(HANDSHAKE_LEN - 1), timeout=10)
        except asyncio.IncompleteReadError:
            log.debug("[%s] client disconnected before handshake", label)
            return None
        return first_byte + rest, reader, writer, label


def _build_crypto_ctx(client_dec_prekey_iv, secret, relay_init):
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

    return CryptoCtx(clt_decryptor, clt_encryptor, tg_encryptor, tg_decryptor)


async def _handle_client(reader, writer, secret: bytes):
    stats.connections_total += 1
    stats.connections_active += 1
    peer = writer.get_extra_info('peername')
    label = f"{peer[0]}:{peer[1]}" if peer else "?"

    set_sock_opts(writer.transport, proxy_config.buffer_size)

    try:
        init = await _read_client_init(
            reader, writer, secret, label, proxy_config.fake_tls_domain)
        if init is None:
            return

        handshake, clt_reader, clt_writer, label = init

        result = _try_handshake(handshake, secret)
        if result is None:
            stats.connections_bad += 1
            log.warning("[%s] bad handshake (wrong secret or proto)", label)
            try:
                while await clt_reader.read(4096):
                    pass
            except Exception:
                pass
            return

        dc, is_media, proto_tag, client_dec_prekey_iv = result

        is_test_dc = proxy_config.force_test_dc or dc >= 10000
        if dc >= 10000:
            log.info("[%s] test DC%d -> DC%d", label, dc, dc - 10000)
            dc -= 10000

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
        ctx = _build_crypto_ctx(client_dec_prekey_iv, secret, relay_init)

        dc_key = f'{dc}{"t" if is_test_dc else ""}{"m" if is_media else ""}'
        media_tag = " media" if is_media else ""
        now = time.monotonic()
        ws_path = WS_PATH_TEST if is_test_dc else WS_PATH
        target = proxy_config.dc_redirects.get(dc)
        is_any_cf_fallback = proxy_config.fallback_cfproxy or proxy_config.cfproxy_worker_domains

        # Fallback if DC not in config, if WS blacklisted for this DC/is_media or if connect to ip is timed out
        if (dc not in proxy_config.dc_redirects
            or dc_key in ws_blacklist
            or now < ip_fail_until.get(target, 0) and is_any_cf_fallback):

            if dc not in proxy_config.dc_redirects:
                log.info("[%s] DC%d not in config -> fallback",
                         label, dc)
            elif dc_key in ws_blacklist:
                log.info("[%s] DC%d%s WS blacklisted -> fallback",
                         label, dc, media_tag)
            else:
                log.info("[%s] DC%d%s WS connect to %s was timed out -> fallback",
                         label, dc, media_tag, target)
            splitter = None
            try:
                splitter = MsgSplitter(relay_init, proto_int)
            except Exception:
                pass
            ok = await do_fallback(
                clt_reader, clt_writer, relay_init, label,
                dc, is_test_dc, is_media, media_tag,
                ctx, splitter=splitter)
            if not ok:
                log.warning("[%s] DC%d%s no fallback available",
                            label, dc, media_tag)
            return

        ws_timeout = WS_FAIL_TIMEOUT if now < dc_fail_until.get(dc_key, 0) else 5.0

        domains = ws_domains(dc, is_media)
        ws = None
        ws_failed_redirect = False
        ws_timed_out = False
        all_redirects = True

        ws = await ws_pool.get(dc, is_media, target, domains) if not is_test_dc else None
        if ws:
            log.info("[%s] DC%d%s -> pool hit via %s",
                     label, dc, media_tag, target)
        else:
            for domain in domains:
                url = f'wss://{domain}{ws_path}'
                log.info("[%s] DC%d%s -> %s via %s",
                         label, dc, media_tag, url, target)
                try:
                    ws = await RawWebSocket.connect(target, domain,
                                                    timeout=ws_timeout,
                                                    path=ws_path)
                    all_redirects = False
                    break
                except WsHandshakeError as exc:
                    stats.ws_errors += 1
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
                except asyncio.TimeoutError:
                    stats.ws_errors += 1
                    ws_timed_out = True
                    log.warning("[%s] DC%d%s WS connect timed out via %s",
                                label, dc, media_tag, domain)
                    break
                except Exception as exc:
                    stats.ws_errors += 1
                    all_redirects = False
                    log.warning("[%s] DC%d%s WS connect failed: %s",
                                label, dc, media_tag, repr(exc))

        # WS failed -> fallback
        if ws is None:
            if ws_timed_out:
                ip_fail_until[target] = now + IP_FAIL_COOLDOWN
                log.info("[%s] DC%d%s WS connect to %s timed out, cooldown for %ds",
                         label, dc, media_tag, target, int(IP_FAIL_COOLDOWN))

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

            splitter_fb = None
            try:
                splitter_fb = MsgSplitter(relay_init, proto_int)
            except Exception:
                pass
            ok = await do_fallback(
                clt_reader, clt_writer, relay_init, label,
                dc, is_test_dc, is_media, media_tag,
                ctx, splitter=splitter_fb)
            if ok:
                log.info("[%s] DC%d%s fallback closed",
                         label, dc, media_tag)
            return

        dc_fail_until.pop(dc_key, None)
        ip_fail_until.pop(target, None)
        stats.connections_ws += 1

        splitter = None
        try:
            splitter = MsgSplitter(relay_init, proto_int)
            log.debug("[%s] MsgSplitter activated for proto 0x%08X",
                      label, proto_int)
        except Exception:
            pass

        await ws.send(relay_init)

        await bridge_ws_reencrypt(clt_reader, clt_writer, ws, label, ctx,
                                   dc=dc, is_media=is_media,
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
            log.error("[%s] unexpected OS error: %s", label, repr(exc))
    except Exception as exc:
        log.error("[%s] unexpected: %s", label, exc, exc_info=True)
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

    ws_pool.reset()
    cf_worker_pool.reset()
    ws_blacklist.clear()
    dc_fail_until.clear()
    ip_fail_until.clear()
    _client_tasks.clear()

    if proxy_config.fallback_cfproxy:
        user = proxy_config.cfproxy_user_domains
        if user:
            balancer.update_domains_list(user)
        else:
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

    log.info("=" * 60)
    log.info("  Telegram MTProto WS Bridge Proxy")
    log.info("  Listening on   %s:%d", proxy_config.host, proxy_config.port)
    log.info("  Secret:        %s", proxy_config.secret)
    if ftls:
        log.info("  Fake TLS:      %s", ftls)
    log.info("  Target DC IPs:")
    for dc in sorted(proxy_config.dc_redirects.keys()):
        ip = proxy_config.dc_redirects.get(dc)
        log.info("    DC%d: %s", dc, ip)
    if proxy_config.fallback_cfproxy:
        user_domain = "user" if proxy_config.cfproxy_user_domains else "auto"
        log.info("  CF proxy:      enabled (%s)", user_domain)
    if proxy_config.cfproxy_worker_domains:
        log.info("  CF worker:     enabled (%s)",
                 ", ".join(proxy_config.cfproxy_worker_domains))
    log.info("=" * 60)
    log.info("  Connect:")
    if ftls:
        log.info("    %s", ee_link)
    else:
        log.info("    %s", dd_link)
    log.info("=" * 60)

    async def log_stats():
        try:
            while True:
                await asyncio.sleep(60)
                bl = ', '.join(f'DC{k}' for k in sorted(ws_blacklist)) or 'none'
                log.info("stats: %s | ws_bl: %s", stats.summary(), bl)
        except asyncio.CancelledError:
            raise

    log_stats_task = asyncio.create_task(log_stats())

    await ws_pool.warmup()
    await cf_worker_pool.warmup()

    async def _quiet_cancel(t):
        if not t.done():
            t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    try:
        while True:
            serve_task = asyncio.create_task(server.serve_forever())
            stop_task = (asyncio.create_task(stop_event.wait())
                         if stop_event else None)

            async def _listener_watchdog():
                while True:
                    await asyncio.sleep(LISTENER_CHECK_INTERVAL)
                    socks = server.sockets
                    if not socks or all(s.fileno() < 0 for s in socks):
                        return

            watchdog_task = asyncio.create_task(_listener_watchdog())
            waiters = [serve_task, watchdog_task]
            if stop_task is not None:
                waiters.append(stop_task)

            done, _ = await asyncio.wait(
                waiters, return_when=asyncio.FIRST_COMPLETED)

            if stop_task is not None and stop_task in done:
                for task in list(_client_tasks):
                    task.cancel()
                if _client_tasks:
                    await asyncio.gather(
                        *_client_tasks, return_exceptions=True)
                await _quiet_cancel(watchdog_task)
                await _quiet_cancel(serve_task)
                server.close()
                await server.wait_closed()
                break

            await _quiet_cancel(watchdog_task)
            await _quiet_cancel(serve_task)
            log.warning(
                "Listening socket died, restarting server")
            server.close()
            try:
                await server.wait_closed()
            except Exception:
                pass
            await asyncio.sleep(LISTENER_RESTART_DELAY)
            try:
                server = await asyncio.start_server(
                    client_cb, proxy_config.host, proxy_config.port)
            except OSError as exc:
                log.error("Failed to restart server: %s", repr(exc))
                break
            _server_instance = server
            for sock in server.sockets:
                try:
                    sock.setsockopt(
                        _socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):
                    pass
            log.warning("Server restored, listening on %s:%d",
                        proxy_config.host, proxy_config.port)
    finally:
        log_stats_task.cancel()
        try:
            await log_stats_task
        except asyncio.CancelledError:
            pass
        try:
            server.close()
            await server.wait_closed()
        except Exception:
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
    ap.add_argument('--log-backups', type=int, default=1, metavar='N',
                    help='Number of rotated log files to keep (min 1; '
                         'rotation needs at least one backup to bound size)')
    ap.add_argument('--buf-kb', type=int, default=256, metavar='KB',
                    help='Socket send/recv buffer size in KB (default 256)')
    ap.add_argument('--pool-size', type=int, default=4, metavar='N',
                    help='WS connection pool size per DC (default 4, min 0)')
    ap.add_argument('--cfproxy-domain', action='append', default=None,
                    metavar='DOMAIN',
                    help='User defined Cloudflare-proxied domain for WS fallback '
                         '(repeatable for multiple domains)')
    ap.add_argument('--cfproxy-worker-domain', action='append', default=None,
                    metavar='DOMAIN',
                    help='Cloudflare Worker domain for WS fallback '
                         '(tried before other fallback methods, '
                         'repeatable for multiple domains)')
    ap.add_argument('--no-cfproxy', action='store_true',
                    help='Disable Cloudflare proxy fallback')
    ap.add_argument('--fake-tls-domain', type=str, default='',
                    metavar='DOMAIN',
                    help='Enable Fake TLS (ee-secret) masking with the given '
                         'SNI domain, e.g. example.com')
    ap.add_argument('--force-test-dc', action='store_true',
                    help='Force ALL traffic to Telegram TEST datacenters. '
                         'Not needed for Telegram Desktop (test DCs 10001+ '
                         'are detected automatically); use for clients that '
                         'signal test DCs as plain 1-3')
    ap.add_argument('--proxy-protocol', action='store_true',
                    help='Accept PROXY protocol v1 header '
                         '(for use behind nginx/haproxy with proxy_protocol on)')
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

    proxy_config.port = args.port
    proxy_config.host = args.host
    proxy_config.secret = secret_hex
    proxy_config.dc_redirects = dc_redirects
    proxy_config.buffer_size = max(4, args.buf_kb) * 1024
    proxy_config.pool_size = max(0, args.pool_size)
    proxy_config.fallback_cfproxy = not args.no_cfproxy
    proxy_config.cfproxy_user_domains = coerce_domain_list(args.cfproxy_domain)
    proxy_config.cfproxy_worker_domains = coerce_domain_list(args.cfproxy_worker_domain)
    proxy_config.fake_tls_domain = args.fake_tls_domain.strip()
    proxy_config.proxy_protocol = args.proxy_protocol
    proxy_config.force_test_dc = args.force_test_dc

    log_level = logging.DEBUG if args.verbose else logging.INFO
    log_fmt = logging.Formatter('%(asctime)s  %(levelname)-5s  %(message)s',
                                datefmt='%H:%M:%S')
    root = logging.getLogger()
    root.setLevel(log_level)

    console = logging.StreamHandler()
    console.setFormatter(log_fmt)
    root.addHandler(console)

    if args.log_file:
        from utils.logging_setup import build_log_handler
        fh = build_log_handler(
            args.log_file,
            log_max_mb=args.log_max_mb,
            backups=args.log_backups,
        )
        fh.setFormatter(log_fmt)
        root.addHandler(fh)

    logging.getLogger('asyncio').setLevel(logging.WARNING)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down. Final stats: %s", stats.summary())


if __name__ == '__main__':
    main()
