from __future__ import annotations

import os
import sys
import time
import struct
import asyncio
import hashlib
import argparse
import logging

from typing import Optional, Set, Tuple


if __name__ == '__main__' and (__package__ is None or __package__ == ''):
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    __package__ = 'proxy'

from .utils import *
from .stats import stats
from .config import proxy_config, parse_dc_ip_list, start_cfproxy_domain_refresh, coerce_domain_list
from .bridge import MsgSplitter, CryptoCtx, do_fallback, bridge_ws_reencrypt, reset_tcp_backoff
from .raw_websocket import set_sock_opts
from .fake_tls import proxy_to_masking_domain, verify_client_hello, build_server_hello, FakeTlsStream, TLS_RECORD_HANDSHAKE
from .balancer import balancer
from .pool import ws_pool, cf_worker_pool
from .cf_h2 import CfH2Pool
from .network_debug import log_ws_flow
from ._aes import Cipher, algorithms, modes


log = logging.getLogger('tg-mtproto-proxy')

LISTENER_CHECK_INTERVAL = 5.0
LISTENER_RESTART_DELAY = 1.0
BAD_CLIENT_DRAIN_TIMEOUT = 30.0
cf_h2_pool: Optional[CfH2Pool] = None


def _try_handshake(handshake: bytes, secret: bytes) -> Optional[Tuple[int, bool, bytes, bytes]]:
    dec_prekey_and_iv = handshake[SKIP_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    dec_prekey = dec_prekey_and_iv[:PREKEY_LEN]
    dec_iv = dec_prekey_and_iv[PREKEY_LEN:]

    dec_key = hashlib.sha256(dec_prekey + secret).digest()

    decrypted = Cipher(algorithms.AES(dec_key), modes.CTR(dec_iv)).encryptor().update(handshake)

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
        rnd = os.urandom(HANDSHAKE_LEN)
        if (rnd[0] not in RESERVED_FIRST_BYTES and rnd[:4] not in RESERVED_STARTS
                and rnd[4:8] != RESERVED_CONTINUE):
            break
    keystream = Cipher(
        algorithms.AES(rnd[SKIP_LEN:SKIP_LEN + PREKEY_LEN]),
        modes.CTR(rnd[SKIP_LEN + PREKEY_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN])
    ).encryptor().update(ZERO_64)
    tail = proto_tag + struct.pack('<h', dc_idx) + os.urandom(2)
    return rnd[:PROTO_TAG_POS] + bytes(a ^ b for a, b in zip(tail, keystream[PROTO_TAG_POS:]))


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
            async def drain():
                while await clt_reader.read(4096):
                    pass
            try:
                await asyncio.wait_for(drain(), BAD_CLIENT_DRAIN_TIMEOUT)
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

        media_tag = " media" if is_media else ""
        ws = await ws_pool.get(dc, is_media, is_test_dc=is_test_dc)
        if ws is None:
            log.info("[%s] DC%d%s WS pool unavailable -> fallback",
                     label, dc, media_tag)
            ok = await do_fallback(
                clt_reader, clt_writer, relay_init, label,
                dc, is_test_dc, is_media, media_tag,
                ctx, splitter=MsgSplitter(proto_int), h2_pool=cf_h2_pool, proto_tag=proto_tag)
            if ok:
                log.info("[%s] DC%d%s fallback closed", label, dc, media_tag)
            else:
                log.warning("[%s] DC%d%s no fallback available", label, dc, media_tag)
            return

        log.info("[%s] DC%d%s -> WS pool hit", label, dc, media_tag)
        stats.connections_ws += 1

        await ws.send(relay_init)
        await bridge_ws_reencrypt(clt_reader, clt_writer, ws, label, ctx,
                                   dc=dc, is_media=is_media,
                                   splitter=MsgSplitter(proto_int))

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
    global _server_instance, _server_stop_event, cf_h2_pool
    _server_stop_event = stop_event

    reset_tcp_backoff()
    ws_pool.reset()
    cf_worker_pool.reset()
    _client_tasks.clear()
    cf_h2_pool = CfH2Pool() if proxy_config.h2_enabled else None

    user_cf_domains = proxy_config.cfproxy_user_domains
    if user_cf_domains:
        balancer.update_domains_list(user_cf_domains)
    else:
        start_cfproxy_domain_refresh()

    secret_bytes = bytes.fromhex(proxy_config.secret)
    stopping = False

    def client_cb(r, w):
        if stopping:
            w.close()
            return
        task = asyncio.create_task(_handle_client(r, w, secret_bytes))
        _client_tasks.add(task)
        task.add_done_callback(_client_tasks.discard)

    server = await asyncio.start_server(client_cb, proxy_config.host, proxy_config.port)
    _server_instance = server

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
        user_domain = ", ".join(proxy_config.cfproxy_user_domains) if proxy_config.cfproxy_user_domains else "auto"
        log.info("  CF proxy:      enabled (%s)", user_domain)
    if proxy_config.cfproxy_worker_domains:
        log.info("  CF worker:     enabled (%s)",
                 ", ".join(proxy_config.cfproxy_worker_domains))
    if cf_h2_pool is not None:
        log.info('  CF H2 media:   enabled')
    if proxy_config.disable_secure:
        log.info("  No secure:     enabled (port 80 for CF proxy/worker)")
    log.info("=" * 60)
    log.info("  Connect:")
    if ftls:
        log.info("    %s", ee_link)
    else:
        log.info("    %s", dd_link)
    log.info("=" * 60)

    async def log_stats():
        next_stats = time.monotonic() + 60
        next_tick = time.monotonic() + 5
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if log.isEnabledFor(logging.DEBUG):
                if now - next_tick >= .25:
                    log.debug('NET LOOP late_ms=%.0f', (now - next_tick) * 1000)
                log_ws_flow(now)
                if cf_h2_pool is not None:
                    cf_h2_pool.log_flow(now)
            next_tick = time.monotonic() + 5
            if now < next_stats:
                continue
            next_stats = now + 60
            log.info("stats: %s", stats.summary())
            if cf_h2_pool is not None:
                cf_h2_pool.log_stats()

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

    serve_task = stop_task = watchdog_task = None
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
                break

            await _quiet_cancel(watchdog_task)
            await _quiet_cancel(serve_task)
            if stop_task is not None:
                await _quiet_cancel(stop_task)
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
            log.warning("Server restored, listening on %s:%d",
                        proxy_config.host, proxy_config.port)
    finally:
        stopping = True
        server.close()
        clients = list(_client_tasks)
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        for task in (serve_task, watchdog_task, stop_task):
            if task is not None:
                await _quiet_cancel(task)
        await server.wait_closed()
        await ws_pool.close()
        cf_worker_pool.reset()
        if cf_h2_pool is not None:
            await cf_h2_pool.close()
            cf_h2_pool = None
        _server_instance = _server_stop_event = None
        log_stats_task.cancel()
        try:
            await log_stats_task
        except asyncio.CancelledError:
            pass


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
    ap.add_argument('--dc-ip', metavar='DC:IP', nargs='?', action='append', const=None,
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
    ap.add_argument('--buf-kb', type=int, default=0, metavar='KB',
                    help='Socket send/recv buffer size in KB (default 0 = OS auto-tuning)')
    ap.add_argument('--pool-size', type=int, default=4, metavar='N',
                    help='WS connection pool size per DC (default 4, 0 disables direct WS)')
    ap.add_argument('--cfproxy-domain', action='append', default=None,
                    metavar='DOMAIN',
                    help='User defined Cloudflare-proxied domain for H2 media and WS fallback '
                         '(repeatable for multiple domains)')
    ap.add_argument('--cfproxy-worker-domain', action='append', default=None,
                    metavar='DOMAIN',
                    help='Cloudflare Worker domain for WS fallback '
                         '(tried before other fallback methods, '
                         'repeatable for multiple domains)')
    h2_flags = ap.add_mutually_exclusive_group()
    h2_flags.add_argument('--no-h2', action='store_true',
                         help='Disable HTTP/2 multiplexing for CF media (enabled by default)')
    h2_flags.add_argument('--cfproxy-h2-media', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--no-cfproxy', action='store_true',
                    help='Disable Cloudflare proxy fallback')
    ap.add_argument('--no-secure', action='store_true',
                        help='Use 80 port for CF-proxy and CF-worker connections')
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

    if args.dc_ip is None:
        args.dc_ip = [
            '2:149.154.167.220',
            '4:149.154.167.220',
        ]
    elif None in args.dc_ip:
        args.dc_ip = []

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
    proxy_config.buffer_size = max(4, args.buf_kb) * 1024 if args.buf_kb > 0 else 0
    proxy_config.pool_size = max(0, args.pool_size)
    proxy_config.fallback_cfproxy = not args.no_cfproxy
    proxy_config.cfproxy_user_domains = coerce_domain_list(args.cfproxy_domain)
    proxy_config.cfproxy_worker_domains = coerce_domain_list(args.cfproxy_worker_domain)
    proxy_config.cfproxy_h2_media = not args.no_h2
    proxy_config.disable_secure = args.no_secure
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
    console.addFilter(DomainCensorFilter())
    root.addHandler(console)

    if args.log_file:
        from utils.logging_setup import build_log_handler
        fh = build_log_handler(
            args.log_file,
            log_max_mb=args.log_max_mb,
            backups=args.log_backups,
        )
        fh.setFormatter(log_fmt)
        fh.addFilter(DomainCensorFilter())
        root.addHandler(fh)

    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        log.info("Shutting down. Final stats: %s", stats.summary())


if __name__ == '__main__':
    main()
