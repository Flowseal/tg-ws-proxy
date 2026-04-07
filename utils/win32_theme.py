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