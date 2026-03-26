"""
Общая светлая тема и фабрика окон CustomTkinter для tray-приложений (Windows / Linux).
Цвета и отступы задаются в одном месте — правки темы не дублируются по платформам.
"""

from __future__ import annotations

import sys
import tkinter
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

_tk_variable_del_guard_installed = False


def _install_tkinter_variable_del_guard() -> None:
    """
    Убирает «Exception ignored» при выходе процесса: Tcl уже разрушен, а GC ещё
    вызывает Variable.__del__ (StringVar и т.д.) — напр. окно CTk в фоновом потоке.
    """
    global _tk_variable_del_guard_installed
    if _tk_variable_del_guard_installed:
        return
    _orig = tkinter.Variable.__del__

    def _safe_variable_del(self: Any, _orig: Any = _orig) -> None:
        try:
            _orig(self)
        except (RuntimeError, tkinter.TclError):
            pass

    tkinter.Variable.__del__ = _safe_variable_del  # type: ignore[assignment]
    _tk_variable_del_guard_installed = True

# Размеры и отступы (единые для диалогов настроек и первого запуска)
CONFIG_DIALOG_SIZE: Tuple[int, int] = (460, 560)
CONFIG_DIALOG_FRAME_PAD: Tuple[int, int] = (20, 14)
FIRST_RUN_SIZE: Tuple[int, int] = (520, 440)
FIRST_RUN_FRAME_PAD: Tuple[int, int] = (28, 24)


@dataclass(frozen=True)
class CtkTheme:
    """Палитра Telegram-style и семейства шрифтов для UI и моноширинного текста."""

    tg_blue: str = "#3390ec"
    tg_blue_hover: str = "#2b7cd4"
    bg: str = "#ffffff"
    field_bg: str = "#f0f2f5"
    field_border: str = "#d6d9dc"
    text_primary: str = "#000000"
    text_secondary: str = "#707579"
    ui_font_family: str = "Sans"
    mono_font_family: str = "Monospace"


def ctk_theme_for_platform() -> CtkTheme:
    if sys.platform == "win32":
        return CtkTheme(ui_font_family="Segoe UI", mono_font_family="Consolas")
    return CtkTheme()


def apply_ctk_appearance(ctk: Any) -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")


def center_ctk_geometry(root: Any, width: int, height: int) -> None:
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")


def create_ctk_root(
    ctk: Any,
    *,
    title: str,
    width: int,
    height: int,
    theme: CtkTheme,
    topmost: bool = True,
    after_create: Optional[Callable[[Any], None]] = None,
) -> Any:
    """
    Создаёт CTk: глобальная тема, заголовок, без ресайза, по центру экрана, фон из палитры.
    after_create — опционально: установка иконки окна (различается по ОС).
    """
    _install_tkinter_variable_del_guard()
    apply_ctk_appearance(ctk)
    root = ctk.CTk()
    root.title(title)
    root.resizable(False, False)
    if topmost:
        root.attributes("-topmost", True)
    center_ctk_geometry(root, width, height)
    root.configure(fg_color=theme.bg)
    if after_create:
        after_create(root)
    return root


def main_content_frame(
    ctk: Any,
    root: Any,
    theme: CtkTheme,
    *,
    padx: int,
    pady: int,
) -> Any:
    frame = ctk.CTkFrame(root, fg_color=theme.bg, corner_radius=0)
    frame.pack(fill="both", expand=True, padx=padx, pady=pady)
    return frame
