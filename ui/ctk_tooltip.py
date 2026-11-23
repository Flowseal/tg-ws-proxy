from __future__ import annotations

from typing import Any, List, Optional

from ui.ctk_theme import ctk_theme_for_platform, fade, popup
from ui.i18n import t


class CtkTooltip:
    def __init__(self, widget: Any, key: str, *, delay_ms: int = 400) -> None:
        self.widget = widget
        self.key = key
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Any = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Button>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._hide()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _hide(self, _event: Any = None) -> None:
        try:
            if self._after_id is not None:
                self.widget.after_cancel(self._after_id)
            if self._tip is not None:
                self._tip.destroy()
        except Exception:
            pass
        self._after_id = self._tip = None

    def _show(self) -> None:
        self._after_id = None
        try:
            if not self.widget.winfo_exists():
                return
        except Exception:
            return
        tw = self._tip = popup(self.widget, t(self.key), ctk_theme_for_platform())
        w, h = tw.winfo_reqwidth(), tw.winfo_reqheight()
        sw, sh = tw.winfo_screenwidth(), tw.winfo_screenheight()
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if x < sw:
            x = min(x, sw - w - 8)
        if y < sh and y + h > sh - 8:
            y = self.widget.winfo_rooty() - h - 6
        tw.geometry(f"+{x}+{y}")
        fade(tw, 1.0, 140)


def attach_ctk_tooltip(widget: Any, key: str, *, delay_ms: int = 400) -> None:
    CtkTooltip(widget, key, delay_ms=delay_ms)


def attach_tooltip_to_widgets(widgets: List[Any], key: str, **kwargs: Any) -> None:
    for w in widgets:
        attach_ctk_tooltip(w, key, **kwargs)
