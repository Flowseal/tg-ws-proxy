from __future__ import annotations

import base64
import os
import socket as _socket
from contextlib import nullcontext

from proxy.utils import create_ssl_context
from ui.ctk_dialogs import show_message
from ui.i18n import t

_CFPROXY_TEST_DCS = [1, 2, 3, 4, 5, 203]
_CFWORKER_TEST_DST = {
    1: '149.154.175.50',
    2: '149.154.167.51',
    3: '149.154.175.100',
    4: '149.154.167.91',
    5: '149.154.171.5',
    203: '91.105.192.100',
}


def _run_connectivity_test(cases: list, *, secure: bool = True) -> dict:
    ctx = create_ssl_context() if secure else None
    port = 443 if secure else 80
    results = {}
    for dc, connect_host, sni_host, req_host, path in cases:
        try:
            with _socket.create_connection((connect_host, port), timeout=5) as raw:
                connection = (ctx.wrap_socket(raw, server_hostname=sni_host)
                              if secure else nullcontext(raw))
                with connection as ssock:
                    ws_key = base64.b64encode(os.urandom(16)).decode()
                    req = (
                        f"GET {path} HTTP/1.1\r\n"
                        f"Host: {req_host}\r\n"
                        f"Upgrade: websocket\r\n"
                        f"Connection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {ws_key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\n"
                        f"Sec-WebSocket-Protocol: binary\r\n"
                        f"\r\n"
                    ).encode()
                    ssock.sendall(req)
                    ssock.settimeout(5)
                    buf = b""
                    while b"\r\n\r\n" not in buf:
                        chunk = ssock.recv(512)
                        if not chunk:
                            break
                        buf += chunk
                    first = buf.decode("utf-8", errors="replace").split("\r\n")[0]
                    if "101" in first:
                        results[dc] = True
                    else:
                        results[dc] = first or t("connectivity.no_response")
                    ssock.close()
                raw.close()
        except _socket.timeout:
            results[dc] = t("connectivity.timeout")
        except OSError as exc:
            msg = str(exc)
            results[dc] = msg[:60] if len(msg) > 60 else msg
    return results


def _run_cfproxy_connectivity_test(domain: str, *, secure: bool = True) -> dict:
    cases = []
    for dc in _CFPROXY_TEST_DCS:
        host = f"kws{dc}.{domain}"
        cases.append((dc, host, host, host, "/apiws"))
    return _run_connectivity_test(cases, secure=secure)


def _run_cfworker_connectivity_test(domain: str, *, secure: bool = True) -> dict:
    cases = []
    for dc in _CFPROXY_TEST_DCS:
        dst = _CFWORKER_TEST_DST[dc]
        path = f"/apiws?dst={dst}&dc={dc}&media=0"
        cases.append((dc, domain, domain, domain, path))
    return _run_connectivity_test(cases, secure=secure)


def run_cfproxy_multi_test(domains: list, *, secure: bool = True) -> dict:
    return {domain: _run_cfproxy_connectivity_test(domain, secure=secure) for domain in domains}


def run_cfworker_multi_test(domains: list, *, secure: bool = True) -> dict:
    return {domain: _run_cfworker_connectivity_test(domain, secure=secure) for domain in domains}


def run_cfproxy_auto_test(domains: list, *, secure: bool = True) -> tuple:
    merged: dict = {}
    best_domain = None
    for domain in reversed(domains):
        res = _run_cfproxy_connectivity_test(domain, secure=secure)
        if all(v is True for v in res.values()):
            return domain, res
        for dc, v in res.items():
            if v is True:
                merged[dc] = True
                best_domain = domain
            elif dc not in merged:
                merged[dc] = v
    return best_domain, merged


def show_connectivity_results(title_base: str, results: dict,
                               domain: str = '', label_prefix: str = 'DC',
                               auto_mode: bool = False,
                               unavailable_message: str = '') -> None:
    ok = [dc for dc, v in results.items() if v is True]
    total = len(_CFPROXY_TEST_DCS)
    if auto_mode:
        kind = "ok" if domain else "error"
        if domain:
            title = t("connectivity.available", title=title_base)
            msg = t("connectivity.auto_ok", title=title_base, ok=len(ok), total=total)
        else:
            title = t("connectivity.unavailable", title=title_base)
            msg = unavailable_message
    else:
        fail = [(dc, v) for dc, v in results.items() if v is not True]
        kind = "ok" if len(ok) == total else "warning" if ok else "error"
        if len(ok) == total:
            title = t("connectivity.all_ok", title=title_base)
            msg = t("connectivity.all_ok_domain", total=total, domain=domain)
        elif not ok:
            title = t("connectivity.unavailable", title=title_base)
            errors = "\n".join(
                t("connectivity.error_line", prefix=label_prefix, dc=dc, error=v)
                for dc, v in fail
            )
            msg = t("connectivity.none_ok", domain=domain, errors=errors)
        else:
            title = t("connectivity.partial", title=title_base)
            ok_list = ", ".join(f"{label_prefix}{dc}" for dc in ok)
            fail_list = "\n".join(
                t("connectivity.error_line", prefix=label_prefix, dc=dc, error=v)
                for dc, v in fail
            )
            msg = t("connectivity.partial_detail", domain=domain, ok_list=ok_list, fail_list=fail_list)

    show_message(msg, title=title, kind=kind)


def show_multi_connectivity_results(title_base: str, per_domain: dict,
                                     label_prefix: str = 'DC') -> None:
    total = len(_CFPROXY_TEST_DCS)
    all_ok = True
    any_ok = False
    blocks = []
    for domain, results in per_domain.items():
        ok = [dc for dc, v in results.items() if v is True]
        fail = [(dc, v) for dc, v in results.items() if v is not True]
        if len(ok) == total:
            any_ok = True
            blocks.append(t("connectivity.multi_all_ok", domain=domain, total=total))
        elif not ok:
            all_ok = False
            blocks.append(t("connectivity.multi_fail", domain=domain))
        else:
            all_ok = False
            any_ok = True
            ok_list = ", ".join(f"{label_prefix}{dc}" for dc in ok)
            fail_list = ", ".join(f"{label_prefix}{dc}" for dc, _ in fail)
            blocks.append(
                t("connectivity.multi_partial", domain=domain, ok_list=ok_list, fail_list=fail_list)
            )

    kind = "ok" if all_ok else "warning" if any_ok else "error"
    title = t({"ok": "connectivity.all_ok", "warning": "connectivity.partial",
               "error": "connectivity.unavailable"}[kind], title=title_base)
    show_message("\n\n".join(blocks), title=title, kind=kind)
