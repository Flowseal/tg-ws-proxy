import asyncio
import logging
import threading

from proxy import tg_ws_proxy
from proxy.config import proxy_config

logging.basicConfig(level=logging.INFO)

_thread = None
_loop = None
_stop = None
_ready = None


async def _serve():
    global _loop, _stop
    _loop = asyncio.get_running_loop()
    _stop = asyncio.Event()
    _ready.set()
    await tg_ws_proxy._run(_stop)


def start(secret_hex, port):
    global _thread, _ready
    if _thread is not None and _thread.is_alive():
        return
    proxy_config.host = '127.0.0.1'
    proxy_config.port = int(port)
    proxy_config.secret = secret_hex
    proxy_config.dc_redirects = {2: '149.154.167.220', 4: '149.154.167.220'}
    proxy_config.buffer_size = 256 * 1024
    proxy_config.pool_size = 4
    proxy_config.fallback_cfproxy = True
    proxy_config.cfproxy_h2_media = True
    proxy_config.disable_secure = False
    proxy_config.fake_tls_domain = ''
    proxy_config.proxy_protocol = False
    proxy_config.force_test_dc = False

    _ready = threading.Event()
    _thread = threading.Thread(target=lambda: asyncio.run(_serve()), daemon=True)
    _thread.start()
    _ready.wait(5)


def stop():
    if _loop is not None and _stop is not None:
        _loop.call_soon_threadsafe(_stop.set)
