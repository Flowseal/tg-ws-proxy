from __future__ import annotations

import sys
import tkinter
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

_tk_variable_del_guard_installed = False


def install_tkinter_variable_del_guard() -> None:
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

CONFIG_DIALOG_SIZE: Tuple[int, int] = (460, 560)
CONFIG_DIALOG_FRAME_PAD: Tuple[int, int] = (20, 14)
FIRST_RUN_SIZE: Tuple[int, int] = (520, 480)
FIRST_RUN_FRAME_PAD: Tuple[int, int] = (28, 24)


@dataclass(frozen=True)
class CtkTheme:
    tg_blue: tuple = ("#3390ec", "#3390ec")
    tg_blue_hover: tuple = ("#2b7cd4", "#2b7cd4")

    bg: tuple = ("#ffffff", "#1e1e1e")
    field_bg: tuple = ("#f0f2f5", "#2b2b2b")
    field_border: tuple = ("#d6d9dc", "#3a3a3a")

    text_primary: tuple = ("#000000", "#ffffff")
    text_secondary: tuple = ("#707579", "#aaaaaa")

    ui_font_family: str = "Sans"
    mono_font_family: str = "Monospace"


def ctk_theme_for_platform() -> CtkTheme:
    if sys.platform == "win32":
        return CtkTheme(ui_font_family="Segoe UI", mono_font_family="Consolas")
    return CtkTheme()


def apply_ctk_appearance(ctk: Any) -> None:
    ctk.set_appearance_mode("auto")
    ctk.set_default_color_theme("blue")

def center_ctk_geometry(root: Any, width: int, height: int) -> None:
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f"{width}x{height}+{(sw - width) // 2}+{(sh - height) // 2}")


def create_ctk_toplevel(
    ctk: Any,
    *,
    title: str,
    width: int,
    height: int,
    theme: CtkTheme,
    topmost: bool = True,
    after_create: Optional[Callable[[Any], None]] = None,
) -> Any:
    root = ctk.CTkToplevel()
    root.title(title)
    root.resizable(False, False)
    center_ctk_geometry(root, width, height)
    root.configure(fg_color=theme.bg)
    if topmost:
        root.attributes("-topmost", True)
    root.lift()
    root.focus_force()
    if after_create:
        _after_id = root.after(300, lambda: after_create(root))
        _orig_destroy = root.destroy

        def _safe_destroy():
            try:
                root.after_cancel(_after_id)
            except Exception:
                pass
            _orig_destroy()

        root.destroy = _safe_destroy
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