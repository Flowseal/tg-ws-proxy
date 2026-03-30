from __future__ import annotations

import tkinter as tk
from typing import Any, List, Optional


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
        self._tip: Optional[tk.Toplevel] = None
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

        tw = tk.Toplevel(self.widget.winfo_toplevel())
        tw.wm_overrideredirect(True)
        try:
            tw.wm_attributes("-topmost", True)
        except Exception:
            pass
        tw.configure(bg="#2b2b2b")
        lbl = tk.Label(
            tw,
            text=self.text,
            justify="left",
            wraplength=self.wraplength,
            background="#2b2b2b",
            foreground="#f0f0f0",
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
            font=("Segoe UI", 10) if _is_windows() else None,
        )
        lbl.pack()
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tw.wm_geometry(f"+{x}+{y}")
        self._tip = tw

    def _hide(self, _event: Any = None) -> None:
        self._cancel_after()
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
) -> None:
    CtkTooltip(widget, text, delay_ms=delay_ms, wraplength=wraplength)


def attach_tooltip_to_widgets(widgets: List[Any], text: str, **kwargs: Any) -> None:
    for w in widgets:
        attach_ctk_tooltip(w, text, **kwargs)
