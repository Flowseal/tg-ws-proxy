from __future__ import annotations

import tkinter
import customtkinter as ctk
from typing import Any, List, Optional

from ui.ctk_anim import Tween, near_color

_TOOLTIP_BG = "#2b2b2b"
_TOOLTIP_TEXT = "#f0f0f0"
_TOOLTIP_RADIUS = 8
_TOOLTIP_FADE_MS = 90


class CtkTooltip:
    def __init__(
        self,
        widget: Any,
        text: str,
        *,
        delay_ms: int = 450,
        wraplength: int = 320,
    ) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: Optional[str] = None
        self._tip: Optional[tkinter.Toplevel] = None
        self._fade: Optional[Tween] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _schedule(self, _event: Any = None) -> None:
        if self.widget is None:
            return
        self._cancel_after()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_after(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._tip is not None:
            return
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return

        tw = tkinter.Toplevel(self.widget.winfo_toplevel())
        tw.wm_overrideredirect(True)
        key = near_color(_TOOLTIP_BG)
        tw.configure(bg=key)
        try:
            tw.wm_attributes("-transparentcolor", key)
        except tkinter.TclError:
            tw.configure(bg=_TOOLTIP_BG)
        try:
            tw.wm_attributes("-topmost", True)
            tw.wm_attributes("-alpha", 0.0)
        except tkinter.TclError:
            pass
        frame = ctk.CTkFrame(tw, fg_color=_TOOLTIP_BG, corner_radius=_TOOLTIP_RADIUS)
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(
            frame,
            text=self.text,
            justify="left",
            wraplength=self.wraplength,
            fg_color=_TOOLTIP_BG,
            text_color=_TOOLTIP_TEXT,
            corner_radius=0,
            font=("Segoe UI", 14) if _is_windows() else None,
        ).pack(padx=10, pady=8)
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tw.wm_geometry(f"+{x}+{y}")
        self._tip = tw
        self._fade = Tween(tw, _TOOLTIP_FADE_MS, self._set_alpha)
        self._fade.start()

    def _set_alpha(self, ratio: float) -> None:
        if self._tip is None:
            return
        try:
            self._tip.wm_attributes("-alpha", max(0.0, min(1.0, ratio)))
        except tkinter.TclError:
            pass

    def _hide(self, _event: Any = None) -> None:
        self._cancel_after()
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _on_destroy(self, _event: Any = None) -> None:
        self._hide()
        self.widget = None


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"


def attach_ctk_tooltip(
    widget: Any,
    text: str,
    *,
    delay_ms: int = 450,
    wraplength: int = 320,
) -> CtkTooltip:
    return CtkTooltip(widget, text, delay_ms=delay_ms, wraplength=wraplength)


def attach_tooltip_to_widgets(widgets: List[Any], text: str, **kwargs: Any) -> List[CtkTooltip]:
    return [attach_ctk_tooltip(w, text, **kwargs) for w in widgets]
