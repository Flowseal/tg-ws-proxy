import asyncio
import base64
import json
import logging
import os
import socket
import threading
from logging.handlers import RotatingFileHandler

from proxy import tg_ws_proxy
from proxy.config import CFPROXY_DEFAULT_DOMAINS, coerce_domain_list, parse_dc_ip_list, proxy_config
from proxy.utils import create_ssl_context

logging.basicConfig(level=logging.INFO)

LOG_FILE_NAME = "tg-ws-proxy.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
TEST_DCS = [1, 2, 3, 4, 5, 203]
WORKER_DST = {
    1: "149.154.175.50",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "149.154.171.5",
    203: "91.105.192.100",
}

_state = {"thread": None, "loop": None, "stop": None}
_log_dir = None


def init_logging(log_dir):
    global _log_dir
    if _log_dir is not None:
        return
    _log_dir = log_dir
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, LOG_FILE_NAME),
        maxBytes=LOG_MAX_BYTES,
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def _apply(cfg):
    proxy_config.host = cfg["host"]
    proxy_config.port = int(cfg["port"])
    proxy_config.secret = cfg["secret"]
    proxy_config.dc_redirects = parse_dc_ip_list(cfg["dc_ip"])
    proxy_config.buffer_size = max(4, int(cfg["buf_kb"])) * 1024
    proxy_config.pool_size = max(0, int(cfg["pool_size"]))
    proxy_config.fallback_cfproxy = bool(cfg["cfproxy"])
    proxy_config.cfproxy_h2_media = bool(cfg["h2"])
    user_domains = coerce_domain_list(cfg["cfproxy_user_domain"])
    worker_domains = coerce_domain_list(cfg["cfproxy_worker_domain"])
    proxy_config.cfproxy_user_domains = user_domains if cfg["cfproxy_user_domain_enabled"] else []
    proxy_config.cfproxy_worker_domains = worker_domains if cfg["cfproxy_worker_enabled"] else []
    proxy_config.disable_secure = bool(cfg["no_secure"])
    proxy_config.force_test_dc = False
    logging.getLogger().setLevel(logging.DEBUG if cfg["verbose"] else logging.INFO)


def _thread_main(ready):
    async def main():
        _state["loop"] = asyncio.get_running_loop()
        _state["stop"] = asyncio.Event()
        ready.set()
        await tg_ws_proxy._run(_state["stop"])

    try:
        asyncio.run(main())
    except Exception:
        logging.getLogger("tg-android").exception("proxy stopped with error")
    finally:
        _state["loop"] = None
        _state["stop"] = None
        ready.set()


def start(config_json):
    stop()
    try:
        _apply(json.loads(config_json))
    except Exception as e:
        return str(e)
    ready = threading.Event()
    thread = threading.Thread(target=_thread_main, args=(ready,), daemon=True)
    _state["thread"] = thread
    thread.start()
    ready.wait(5)
    return ""


def stop():
    loop, stop_event = _state["loop"], _state["stop"]
    if loop is not None and stop_event is not None:
        loop.call_soon_threadsafe(stop_event.set)
    thread = _state["thread"]
    if thread is not None:
        thread.join(10)
        _state["thread"] = None


def _ws_check(connect_host, sni_host, req_host, path):
    raw = socket.create_connection((connect_host, 443), timeout=5)
    try:
        sock = create_ssl_context().wrap_socket(raw, server_hostname=sni_host)
        try:
            key = base64.b64encode(os.urandom(16)).decode()
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {req_host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Protocol: binary\r\n"
                "\r\n"
            )
            sock.sendall(request.encode())
            sock.settimeout(5)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(512)
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()
    finally:
        raw.close()
    first = buf.decode("utf-8", errors="replace").split("\r\n")[0]
    if "101" in first:
        return True
    return first or "no response"


def _cfproxy_case(domain, dc):
    host = f"kws{dc}.{domain}"
    return _ws_check(host, host, host, "/apiws")


def _cfworker_case(domain, dc):
    path = f"/apiws?dst={WORKER_DST[dc]}&dc={dc}&media=0"
    return _ws_check(domain, domain, domain, path)


def _domain_results(check, domain):
    out = {}
    for dc in TEST_DCS:
        try:
            out[str(dc)] = check(domain, dc)
        except socket.timeout:
            out[str(dc)] = "timeout"
        except OSError as e:
            out[str(dc)] = str(e)[:60]
    return out


def run_test(kind, domains_json):
    domains = json.loads(domains_json)
    if kind.startswith("cfproxy") and not domains:
        domains = list(CFPROXY_DEFAULT_DOMAINS)
    if kind == "cfproxy_auto":
        best, results = "", {}
        for domain in reversed(domains):
            res = _domain_results(_cfproxy_case, domain)
            results[domain] = res
            if all(v is True for v in res.values()):
                best = domain
                break
        return json.dumps({"best": best, "results": results})
    check =_cfproxy_case if kind == "cfproxy_multi" else _cfworker_case
    results = {d: _domain_results(check, d) for d in domains}
    return json.dumps({"best": "", "results": results})
