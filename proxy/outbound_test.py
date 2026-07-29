import asyncio
from .config import proxy_config, get_outbound_proxy
from .utils import DC_DEFAULT_IPS


def build_proxy(p_type, p_host, p_port, p_user, p_password):
    """Proxy из явных полей (не из глобального proxy_config) — для теста из UI."""
    if not p_type or not p_host:
        return None
    from python_socks.async_.asyncio import Proxy
    auth = f"{p_user}:{p_password}@" if p_user else ""
    return Proxy.from_url(f"{p_type}://{auth}{p_host}:{p_port}")


def _targets():
    ips = list(dict.fromkeys(proxy_config.dc_redirects.values()))
    return ips or list(DC_DEFAULT_IPS.values())   # те же IP, куда идёт бой


async def test_outbound_proxy(proxy=None, *, params=None, timeout: float = 8.0):
    # Proxy строим ЗДЕСЬ (внутри running loop), а не в GUI-потоке
    if proxy is None:
        proxy = build_proxy(*params) if params is not None else get_outbound_proxy()
    if proxy is None:
        return False, "connectivity.outbound_not_set", {}
    last = None
    for ip in _targets():
        try:
            sock = await asyncio.wait_for(proxy.connect(ip, 443), timeout=timeout)
            try:
                sock.close()
            except Exception:
                pass
            return True, "connectivity.outbound_ok", {"ip": ip}
        except Exception as e:
            last = e
    return False, *_classify(last)


def _classify(e):
    try:
        from python_socks import ProxyTimeoutError
        if isinstance(e, ProxyTimeoutError):
            return "connectivity.outbound_timeout", {}
    except Exception:
        pass
    s = (str(e) or "").lower()
    if "timeout" in s or "timed out" in s:
        return "connectivity.outbound_timeout", {}
    if "407" in s or "auth" in s or "denied" in s or "credential" in s:
        return "connectivity.outbound_bad_auth", {}
    if "unreachable" in s or "refused" in s or "502" in s or "503" in s or "504" in s:
        return "connectivity.outbound_blocked", {"err": e}
    return "connectivity.outbound_fail", {"err": e}