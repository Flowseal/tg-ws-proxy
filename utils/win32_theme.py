from __future__ import annotations

import sys

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
        uxtheme[135](1)
        uxtheme[136]()
    except Exception:
        pass
