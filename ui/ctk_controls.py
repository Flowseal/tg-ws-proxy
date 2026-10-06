from __future__ import annotations

import sys
import time
from typing import Any, Callable, Optional

import customtkinter
from PIL import Image, ImageDraw, ImageFont

from ui.ctk_theme import CtkTheme, animate, mix
from ui.ctk_tooltip import attach_tooltip_to_widgets
from ui.i18n import t


def on_relabel(widget: Any, fn: Callable[[], None]) -> None:
    top = widget.winfo_toplevel()
    hooks = getattr(top, "_relabel_hooks", None)
    if hooks is None:
        hooks = top._relabel_hooks = []
    hooks.append(fn)


def bind_text(widget: Any, key: str, apply: Optional[Callable[[str], None]] = None, **kwargs: Any) -> None:
    apply = apply or (lambda text: widget.configure(text=text))
    apply(t(key, **kwargs))
    on_relabel(widget, lambda: apply(t(key, **kwargs)))


def relabel(window: Any) -> None:
    for fn in getattr(window, "_relabel_hooks", []):
        try:
            fn()
        except Exception:
            pass


def _supersampled(w: int, h: int, draw: Callable[[Image.Image, int], Image.Image]) -> Image.Image:
    s = 4
    img = draw(Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0)), s)
    return img.resize((w * 2, h * 2), Image.LANCZOS)


def _find_icon_font() -> Optional[str]:
    if sys.platform != "win32":
        return None
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        try:
            ImageFont.truetype(name, 8)
            return name
        except OSError:
            continue
    return None


_ICON_FONT = _find_icon_font()
_GLYPHS = {
    "refresh": chr(0xE72C), "heart": chr(0xEB52), "help": chr(0xE897),
    "download": chr(0xE896), "warning": chr(0xE7BA),
}
_icons: dict = {}


def icon(name: str, size: int, color: tuple) -> Any:
    if name != "check" and _ICON_FONT is None:
        return None
    key = (name, size, color)
    if key not in _icons:
        def render(c):
            def draw(img, s):
                d = ImageDraw.Draw(img)
                if name == "check":
                    pts = [(x * img.width, y * img.height) for x, y in ((0.18, 0.54), (0.4, 0.75), (0.82, 0.28))]
                    d.line(pts, fill=c, width=round(img.width / 9), joint="curve")
                else:
                    font = ImageFont.truetype(_ICON_FONT, size * s)
                    d.text((img.width / 2, img.height / 2), _GLYPHS[name], font=font, fill=c, anchor="mm")
                return img
            return _supersampled(size, size, draw)
        _icons[key] = customtkinter.CTkImage(render(color[0]), render(color[1]), size=(size, size))
    return _icons[key]


SPIN_STEPS = 36


def spinner(i: int, theme: CtkTheme) -> Any:
    key = ("spin", i)
    if key not in _icons:
        def render(c):
            def draw(img, s):
                a = i * 360 / SPIN_STEPS
                ImageDraw.Draw(img).arc((2 * s, 2 * s, img.width - 2 * s, img.height - 2 * s),
                                        a, a + 270, fill=c, width=2 * s)
                return img
            return _supersampled(16, 16, draw)
        _icons[key] = customtkinter.CTkImage(render(theme.text_secondary[0]), render(theme.text_secondary[1]),
                                             size=(16, 16))
    return _icons[key]


def spin(btn: Any, theme: CtkTheme, busy: Callable[[], bool], done: Callable[[], None]) -> None:
    t0 = time.perf_counter()

    def step():
        if not btn.winfo_exists():
            return
        if busy():
            btn.configure(image=spinner(int((time.perf_counter() - t0) / 0.75 * SPIN_STEPS) % SPIN_STEPS, theme))
            btn.after(16, step)
        else:
            btn.configure(image="")
            done()

    step()


CHECK_SIZE, CHECK_STEPS = 18, 16


def _check_png(k: float, dark: int, theme: CtkTheme) -> Image.Image:
    def draw(img, s):
        d = ImageDraw.Draw(img)
        a = theme.tg_blue[dark].lstrip("#")
        fill = tuple(int(a[i:i + 2], 16) for i in (0, 2, 4)) + (round(255 * min(1.0, k * 2)),)
        d.rounded_rectangle((s, s, img.width - s - 1, img.height - s - 1), radius=5 * s, fill=fill,
                            outline=mix(theme.check_border[dark], theme.tg_blue[dark], min(1.0, k * 2)),
                            width=round(1.5 * s))
        pts = [(4.6 * s, 9.4 * s), (7.6 * s, 12.4 * s), (13.4 * s, 5.8 * s)]
        seg = [((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 for (x1, y1), (x2, y2) in zip(pts, pts[1:])]
        left = max(0.0, (k - 0.3) / 0.7) * sum(seg)
        path = [pts[0]]
        for (x1, y1), (x2, y2), length in zip(pts, pts[1:], seg):
            part = min(1.0, left / length)
            path.append((x1 + (x2 - x1) * part, y1 + (y2 - y1) * part))
            left -= length
            if left <= 0:
                break
        if len(path) > 1 and k > 0.3:
            w = 2 * s
            d.line(path, fill="#ffffff", width=w, joint="curve")
            for x, y in (path[0], path[-1]):
                d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill="#ffffff")
        return img
    return _supersampled(CHECK_SIZE, CHECK_SIZE, draw)


def _check_image(k: float, theme: CtkTheme) -> Any:
    i = round(k * CHECK_STEPS)
    key = ("check_box", i)
    if key not in _icons:
        p = i / CHECK_STEPS
        _icons[key] = customtkinter.CTkImage(_check_png(p, 0, theme), _check_png(p, 1, theme),
                                             size=(CHECK_SIZE, CHECK_SIZE))
    return _icons[key]


class Check(customtkinter.CTkLabel):
    def __init__(self, master: Any, theme: CtkTheme, key: str, variable: Any) -> None:
        self._var, self._theme = variable, theme
        self._k = float(bool(variable.get()))
        super().__init__(master, text="", image=_check_image(self._k, theme), compound="left",
                         anchor="w", font=(theme.ui_font_family, 13), text_color=theme.text_primary,
                         cursor="hand2")
        bind_text(self, key, lambda s: self.configure(text="  " + s))
        self.bind("<Button-1>", lambda _: self.toggle())
        variable.trace_add("write", self._sync)

    def toggle(self) -> None:
        if str(self.cget("state")) != "disabled":
            self._var.set(not self._var.get())

    def _sync(self, *_: Any) -> None:
        a, b = self._k, float(bool(self._var.get()))
        animate(self, 260, lambda k: self._show(a + (b - a) * k))

    def _show(self, k: float) -> None:
        self._k = k
        self.configure(image=_check_image(k, self._theme))


class AnimatedButton(customtkinter.CTkButton):
    _cur = None

    def _paint(self, color: str) -> None:
        self._cur = color
        self._canvas.itemconfig("inner_parts", fill=color, outline=color)
        for lbl in (self._text_label, self._image_label):
            if lbl is not None:
                lbl.configure(bg=color)

    def _tween(self, spec: Any, ms: int = 140) -> None:
        target = self._apply_appearance_mode(spec)
        start = self._cur or self._apply_appearance_mode(self._fg_color)
        animate(self, ms, lambda k: self._paint(mix(start, target, k)), slot="_hover_job")

    def _draw(self, no_color_updates: bool = False) -> None:
        self._cur = None
        super()._draw(no_color_updates)

    def _on_enter(self, event: Any = None) -> None:
        self._mouse_inside = True
        if self._state == "normal" and self._hover_color:
            self._tween(self._hover_color)

    def _on_leave(self, event: Any = None) -> None:
        self._mouse_inside = False
        self._click_animation_running = False
        self._tween(self._fg_color, 200)


def _button_style(theme: CtkTheme, kind: str) -> dict:
    return {
        "primary": dict(fg_color=theme.tg_blue, hover_color=theme.tg_blue_hover, text_color="#ffffff"),
        "secondary": dict(fg_color=theme.field_bg, hover_color=theme.field_border, text_color=theme.text_primary),
        "ghost": dict(fg_color=theme.card, hover_color=theme.field_bg, text_color=theme.text_secondary),
        "donate": dict(fg_color="#22c55e", hover_color="#16a34a", text_color="#ffffff"),
    }[kind]


def create_button(ctk: Any, parent: Any, theme: CtkTheme, key: Optional[str], kind: str = "primary",
                  *, image: Any = None, **kw: Any) -> Any:
    kw.setdefault("height", 34)
    btn = AnimatedButton(
        parent, text="", image=image, compound="left", corner_radius=8, border_width=0,
        font=(theme.ui_font_family, 13, "bold" if kind in ("primary", "donate") else "normal"),
        **_button_style(theme, kind), **kw,
    )
    if key:
        bind_text(btn, key, lambda s: btn.configure(text=(" " if image else "") + s))
    return btn


def create_icon_button(ctk: Any, parent: Any, theme: CtkTheme, name: str, tip_key: str,
                       command: Optional[Callable[[], None]] = None) -> Any:
    image = icon(name, 16, theme.text_secondary)
    btn = AnimatedButton(
        parent, text="" if image else "?", image=image, width=30, height=30, corner_radius=8,
        border_width=0, font=(theme.ui_font_family, 13), command=command, **_button_style(theme, "ghost"),
    )
    attach_tooltip_to_widgets([btn], tip_key)
    return btn


def create_entry(ctk, parent, theme, *, var=None, width=0, height=34, radius=8, **kw):
    opts = {
        "font": (theme.ui_font_family, 13), "corner_radius": radius,
        "fg_color": theme.field_bg, "border_color": theme.field_bg,
        "border_width": 1, "text_color": theme.text_primary,
    }
    if var is not None:
        opts["textvariable"] = var
    if width:
        opts["width"] = width
    opts["height"] = height
    opts.update(kw)
    entry = ctk.CTkEntry(parent, **opts)
    entry.bind("<FocusIn>", lambda _: entry.configure(border_color=theme.tg_blue), add="+")
    entry.bind("<FocusOut>", lambda _: entry.configure(border_color=theme.field_bg), add="+")
    return entry


def create_checkbox(ctk, parent, theme, key, variable):
    return Check(parent, theme, key, variable)


def create_label(ctk, parent, theme, key, *, size=12, bold=False, secondary=True, upper=False, **kw):
    lbl = ctk.CTkLabel(
        parent, text="",
        font=(theme.ui_font_family, size, "bold" if bold else "normal"),
        text_color=theme.text_secondary if secondary else theme.text_primary,
        anchor="w", **kw,
    )
    bind_text(lbl, key, lambda s: lbl.configure(text=s.upper() if upper else s))
    return lbl


def create_labeled_entry(ctk, parent, theme, label_key, value, *, tip_key="", width=0):
    col = ctk.CTkFrame(parent, fg_color="transparent")
    lbl = create_label(ctk, col, theme, label_key)
    lbl.pack(anchor="w", pady=(0, 2))
    var = ctk.StringVar(master=parent, value=str(value))
    ent = create_entry(ctk, col, theme, var=var, width=width)
    ent.pack(fill="x")
    if tip_key:
        attach_tooltip_to_widgets([lbl, ent], tip_key)
    return col, var


def create_config_section(ctk: Any, parent: Any, theme: CtkTheme, title_key: str) -> Any:
    create_label(ctk, parent, theme, title_key, size=11, bold=True, upper=True).pack(
        fill="x", padx=8, pady=(14, 4))
    card = ctk.CTkFrame(parent, fg_color=theme.card, corner_radius=12)
    card.pack(fill="x")
    inner = ctk.CTkFrame(card, fg_color="transparent")
    inner.pack(fill="x", padx=14, pady=10)
    return inner


def create_segmented(ctk, parent, theme, *, variable, values, command=None):
    return ctk.CTkSegmentedButton(
        parent, values=values, variable=variable, command=command, height=30,
        corner_radius=8, border_width=3, font=(theme.ui_font_family, 12), text_color=theme.text_primary,
        fg_color=theme.track, unselected_color=theme.track, unselected_hover_color=theme.track_hover,
        selected_color=theme.pill, selected_hover_color=theme.pill,
    )
