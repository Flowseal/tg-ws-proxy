from __future__ import annotations

import sys
from typing import Any

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_SWP_REPAINT_FRAME = 0x0001 | 0x0002 | 0x0004 | 0x0020

def is_windows_dark_theme() -> bool:
    if sys.platform != "win32":
        return False

    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False

def apply_windows_dark_theme() -> None:
    try:
        import ctypes
        uxtheme = ctypes.windll.uxtheme
        
        try:
            set_preferred = uxtheme[135]
            result = set_preferred(2)
            if result == 0:
                flush = uxtheme[136]
                flush()
        except Exception:
            try:
                allow_dark = uxtheme[135]
                allow_dark(True)
            except Exception:
                pass
    except Exception:
        pass

def apply_titlebar_theme(window: Any, dark: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.wm_frame(), 16)
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_REPAINT_FRAME)
    except Exception:
        pass
