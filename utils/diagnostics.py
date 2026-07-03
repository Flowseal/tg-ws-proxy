from __future__ import annotations

import errno
import webbrowser

from typing import Optional, Tuple, Callable

# Windows WinSock error codes (exc.winerror); errno may differ from POSIX.
_WSA_EACCES = 10013
_WSA_EFAULT = 10014
_WSA_EADDRINUSE = 10048
_WSA_EADDRNOTAVAIL = 10049


def diagnose_listen_error(exc: BaseException) -> Tuple[Optional[str], Optional[Callable]]:
    """Map a listen-socket bind failure to a user-facing message.

    Returns None when the exception is not a recognizable bind failure,
    so callers can fall back to generic handling.
    """
    from ui.i18n import t

    if not isinstance(exc, OSError):
        return None

    err = exc.errno
    winerror = getattr(exc, "winerror", None)

    if err == errno.EADDRINUSE or winerror == _WSA_EADDRINUSE:
        return t("diagnostics.port_busy"), None
    if err == errno.EACCES or winerror == _WSA_EACCES:
        return t("diagnostics.permission"), None
    if (winerror in (_WSA_EFAULT, _WSA_EADDRNOTAVAIL)
            or err in (errno.EADDRNOTAVAIL, errno.EFAULT)):
        return t("diagnostics.bad_address"), lambda : webbrowser.open("https://github.com/Flowseal/tg-ws-proxy/issues/903#issuecomment-4726752103")
    return None, None
