"""Transparent Bot API tunnel for api.telegram.org (zero-config bots).

Opt-in redirect of api.telegram.org → 127.0.0.2 via hosts, listen on :443,
bridge raw TCP through Cloudflare Worker /apiws?dst=api.telegram.org.
TLS stays end-to-end; bots need no base_url changes.
Off by default so regular MTProto users are unaffected.
"""
from __future__ import annotations

import asyncio
import atexit
import logging
import os
import random
import socket
import sys
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from .config import proxy_config
from .raw_websocket import RawWebSocket, set_sock_opts

log = logging.getLogger('tg-bot-api-proxy')

BOT_API_HOST = 'api.telegram.org'
BOT_API_PORT = 443
HOSTS_BEGIN = '# tg-ws-proxy-botapi-begin'
HOSTS_END = '# tg-ws-proxy-botapi-end'

_hosts_applied = False
_pinned_ips: List[str] = []
_atexit_registered = False


def _ensure_atexit() -> None:
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(restore_hosts)
        _atexit_registered = True


def _hosts_path() -> str:
    if sys.platform == 'win32':
        root = os.environ.get('SystemRoot', r'C:\Windows')
        return os.path.join(root, 'System32', 'drivers', 'etc', 'hosts')
    return '/etc/hosts'


def _resolve_public_ips(hostname: str) -> List[str]:
    ips: List[str] = []
    seen = set()
    try:
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                infos = socket.getaddrinfo(hostname, BOT_API_PORT, family, socket.SOCK_STREAM)
            except socket.gaierror:
                continue
            for info in infos:
                ip = info[4][0]
                if not ip or ip in seen:
                    continue
                if ip.startswith('127.') or ip == '::1':
                    continue
                seen.add(ip)
                ips.append(ip)
    except Exception as exc:
        log.warning('Failed to resolve %s before hosts edit: %s', hostname, repr(exc))
    return ips


def _strip_hosts_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: List[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == HOSTS_BEGIN:
            skipping = True
            continue
        if stripped == HOSTS_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return ''.join(out)


def apply_hosts_redirect() -> None:
    global _hosts_applied
    path = _hosts_path()
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            original = f.read()
    except OSError as exc:
        raise RuntimeError(f'Cannot read hosts file {path}: {exc}') from exc

    cleaned = _strip_hosts_block(original)
    block = (
        f'{HOSTS_BEGIN}\n'
        f'127.0.0.2 {BOT_API_HOST}\n'
        f'::2 {BOT_API_HOST}\n'
        f'{HOSTS_END}\n'
    )
    if not cleaned.endswith('\n') and cleaned:
        cleaned += '\n'
    new_text = cleaned + block
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_text)
    except OSError as exc:
        raise RuntimeError(
            f'Cannot write hosts file {path} (admin/root required): {exc}'
        ) from exc
    _hosts_applied = True
    _ensure_atexit()
    log.info('Hosts redirect installed: %s → 127.0.0.2 / ::2', BOT_API_HOST)


def restore_hosts() -> None:
    global _hosts_applied
    if not _hosts_applied:
        # Still try to clean leftover block from a previous crash.
        pass
    path = _hosts_path()
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            original = f.read()
    except OSError as exc:
        log.warning('Cannot read hosts to restore: %s', repr(exc))
        return
    if HOSTS_BEGIN not in original:
        _hosts_applied = False
        return
    cleaned = _strip_hosts_block(original)
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(cleaned)
        log.info('Hosts redirect removed for %s', BOT_API_HOST)
    except OSError as exc:
        log.error('Failed to restore hosts file %s: %s', path, repr(exc))
        return
    _hosts_applied = False


async def _bridge_tcp_ws(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    ws: RawWebSocket,
) -> None:
    buf = proxy_config.buffer_size

    async def client_to_ws() -> None:
        try:
            while True:
                data = await reader.read(buf)
                if not data:
                    break
                await ws.send(data)
        except (asyncio.CancelledError, ConnectionError, OSError):
            raise
        except Exception as exc:
            log.debug('botapi tcp→ws: %s', repr(exc))
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    async def ws_to_client() -> None:
        try:
            while True:
                data = await ws.recv()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            raise
        except Exception as exc:
            log.debug('botapi ws→tcp: %s', repr(exc))
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    await asyncio.gather(client_to_ws(), ws_to_client(), return_exceptions=True)


async def _bridge_tcp_tcp(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
) -> None:
    buf = proxy_config.buffer_size

    async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while True:
                data = await src.read(buf)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (asyncio.CancelledError, ConnectionError, OSError):
            raise
        except Exception:
            pass
        finally:
            try:
                dst.close()
                await dst.wait_closed()
            except Exception:
                pass

    await asyncio.gather(
        pipe(reader, up_writer),
        pipe(up_reader, writer),
        return_exceptions=True,
    )


async def _open_worker_ws(worker_domain: str) -> RawWebSocket:
    query = urlencode({'dst': BOT_API_HOST})
    path = f'/apiws?{query}'
    return await RawWebSocket.connect(
        worker_domain, worker_domain, timeout=10.0, path=path,
    )


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    peer = writer.get_extra_info('peername')
    set_sock_opts(writer.transport, proxy_config.buffer_size)
    workers = list(proxy_config.cfproxy_worker_domains)
    random.shuffle(workers)

    try:
        for domain in workers:
            ws = None
            try:
                ws = await _open_worker_ws(domain)
                log.info('botapi tunnel peer=%s via worker %s', peer, domain)
                await _bridge_tcp_ws(reader, writer, ws)
                return
            except Exception as exc:
                log.warning('botapi worker %s failed for %s: %s', domain, peer, repr(exc))
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass

        # Last resort: direct TCP to pinned public IP (pre-hosts resolve).
        for ip in _pinned_ips:
            up_reader = up_writer = None
            try:
                up_reader, up_writer = await asyncio.open_connection(ip, BOT_API_PORT)
                set_sock_opts(up_writer.transport, proxy_config.buffer_size)
                log.info('botapi tunnel peer=%s via direct %s', peer, ip)
                await _bridge_tcp_tcp(reader, writer, up_reader, up_writer)
                return
            except Exception as exc:
                log.warning('botapi direct %s failed for %s: %s', ip, peer, repr(exc))
                if up_writer is not None:
                    try:
                        up_writer.close()
                        await up_writer.wait_closed()
                    except Exception:
                        pass

        log.error('botapi no upstream available for peer=%s', peer)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.debug('botapi client error peer=%s: %s', peer, repr(exc))
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


class BotApiHandle:
    """Manages one or more :443 listeners and hosts restore."""

    def __init__(self, servers: Sequence[asyncio.AbstractServer]):
        self.servers = list(servers)

    @property
    def sockets(self):
        out = []
        for s in self.servers:
            if s.sockets:
                out.extend(s.sockets)
        return out

    async def serve_forever(self) -> None:
        if not self.servers:
            await asyncio.Future()  # never
        await asyncio.gather(*(s.serve_forever() for s in self.servers))

    def close(self) -> None:
        for s in self.servers:
            s.close()

    async def wait_closed(self) -> None:
        for s in self.servers:
            try:
                await s.wait_closed()
            except Exception:
                pass
        restore_hosts()


async def start_bot_api_server() -> Optional[BotApiHandle]:
    """Start transparent Bot API tunnel. Returns handle or None if disabled/failed."""
    global _pinned_ips

    if not proxy_config.bot_api_enabled:
        log.info('Bot API transparent proxy disabled')
        return None

    if not proxy_config.cfproxy_worker_domains:
        log.error(
            'Bot API transparent mode needs Cloudflare Worker domain(s). '
            'Set cfproxy_worker_domain / --cfproxy-worker-domain'
        )
        return None

    _pinned_ips = _resolve_public_ips(BOT_API_HOST)
    if _pinned_ips:
        log.info('Pinned %s IPs before hosts edit: %s', BOT_API_HOST, ', '.join(_pinned_ips))
    else:
        log.warning(
            'Could not resolve public IPs for %s before hosts edit; '
            'direct fallback will be unavailable',
            BOT_API_HOST,
        )

    try:
        apply_hosts_redirect()
    except RuntimeError as exc:
        log.error('%s', exc)
        return None

    servers: List[asyncio.AbstractServer] = []
    # Use 127.0.0.2 / ::2 so a service bound only to 127.0.0.1:443 does not collide.
    bind_targets: List[Tuple[str, int]] = [
        ('127.0.0.2', BOT_API_PORT),
        ('::2', BOT_API_PORT),
    ]

    for host, port in bind_targets:
        try:
            family = socket.AF_INET6 if ':' in host else socket.AF_INET
            srv = await asyncio.start_server(
                _handle_client, host, port, family=family,
            )
            for sock in srv.sockets or []:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (OSError, AttributeError):
                    pass
            servers.append(srv)
            log.info('Bot API transparent tunnel listening on [%s]:%d', host, port)
        except OSError as exc:
            if host == '::2':
                log.info('IPv6 Bot API listener skipped: %s', repr(exc))
                continue
            log.error(
                'Failed to bind Bot API on %s:%d (admin/root usually required; '
                'or another app holds *:443): %s',
                host, port, repr(exc),
            )
            for s in servers:
                s.close()
                try:
                    await s.wait_closed()
                except Exception:
                    pass
            restore_hosts()
            return None

    if not servers:
        restore_hosts()
        return None

    log.info(
        'Bot API transparent mode active: start any bot as usual '
        '(no base_url). Traffic to %s is tunneled via CF Worker.',
        BOT_API_HOST,
    )
    return BotApiHandle(servers)


# Backwards-compatible alias used by tg_ws_proxy._run
start_bot_api_transparent = start_bot_api_server
