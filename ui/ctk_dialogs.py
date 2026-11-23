from __future__ import annotations

from typing import Any, Optional, Sequence

import customtkinter
from PIL import ImageDraw

from ui.ctk_controls import _supersampled, create_button
from ui.ctk_theme import center_ctk_geometry, create_ctk_toplevel, ctk_theme_for_platform, fade_destroy

_BADGE = 28
_COLORS = {"info": "#3390ec", "question": "#3390ec", "ok": "#22c55e", "warning": "#f59e0b", "error": "#ef4444"}
_badges: dict = {}


def _badge(kind: str) -> Any:
    if kind not in _badges:
        def draw(img, s):
            d = ImageDraw.Draw(img)
            w = img.width
            r = w / 22
            d.ellipse((0, 0, w - 1, w - 1), fill=_COLORS[kind])

            def stroke(*pts):
                d.line([(x * w, y * w) for x, y in pts], fill="#ffffff", width=round(2 * r), joint="curve")
                for x, y in (pts[0], pts[-1]):
                    d.ellipse((x * w - r, y * w - r, x * w + r, y * w + r), fill="#ffffff")

            def dot(x, y):
                d.ellipse((x * w - 1.4 * r, y * w - 1.4 * r, x * w + 1.4 * r, y * w + 1.4 * r), fill="#ffffff")

            if kind == "ok":
                stroke((0.29, 0.52), (0.44, 0.66), (0.71, 0.37))
            elif kind == "error":
                stroke((0.35, 0.35), (0.65, 0.65))
                stroke((0.65, 0.35), (0.35, 0.65))
            elif kind == "warning":
                stroke((0.5, 0.27), (0.5, 0.55))
                dot(0.5, 0.71)
            elif kind == "info":
                dot(0.5, 0.29)
                stroke((0.5, 0.45), (0.5, 0.72))
            else:
                d.arc((0.37 * w, 0.24 * w, 0.63 * w, 0.5 * w), 180, 90, fill="#ffffff", width=round(2 * r))
                stroke((0.5, 0.5), (0.5, 0.56))
                dot(0.5, 0.72)
            return img

        im = _supersampled(_BADGE, _BADGE, draw)
        _badges[kind] = customtkinter.CTkImage(im, im, size=(_BADGE, _BADGE))
    return _badges[kind]


def show_message(text: str, *, title: str, kind: str = "info", buttons: Sequence[str] = ("button.ok",),
                 **window: Any) -> Optional[int]:
    theme = ctk_theme_for_platform()
    width = 420
    root = create_ctk_toplevel(customtkinter, title=title, width=width, height=150, theme=theme, **window)
    result = {"value": None}

    def close(i: Optional[int]) -> None:
        result["value"] = i
        fade_destroy(root)

    body = customtkinter.CTkFrame(root, fg_color="transparent")
    body.pack(fill="x", padx=20, pady=(20, 18))
    customtkinter.CTkLabel(body, text="", image=_badge(kind), width=_BADGE).pack(side="left", anchor="n")
    customtkinter.CTkLabel(
        body, text=text, justify="left", anchor="w", wraplength=width - 96,
        font=(theme.ui_font_family, 13), text_color=theme.text_primary,
    ).pack(side="left", fill="x", expand=True, padx=(14, 0))

    row = customtkinter.CTkFrame(root, fg_color="transparent")
    row.pack(fill="x", padx=20, pady=(0, 16))
    for i in reversed(range(len(buttons))):
        create_button(customtkinter, row, theme, buttons[i], "primary" if i == 0 else "secondary", width=96,
                      command=lambda i=i: close(i)).pack(side="right", padx=(8, 0))

    root.protocol("WM_DELETE_WINDOW", lambda: close(None))
    root.bind("<Return>", lambda _: close(0))
    root.bind("<Escape>", lambda _: close(None))
    root.update_idletasks()
    center_ctk_geometry(root, width, round(root.winfo_reqheight() / root._get_window_scaling()))
    root.wait_window()
    return result["value"]
