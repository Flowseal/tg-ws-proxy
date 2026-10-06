from __future__ import annotations

import ctypes
import io
import os
import sys
import time
import tkinter
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

import customtkinter

_tk_variable_del_guard_installed = False
_user32 = ctypes.WinDLL("user32") if sys.platform == "win32" else None
_dwmapi = ctypes.WinDLL("dwmapi") if sys.platform == "win32" else None


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

CONFIG_DIALOG_SIZE: Tuple[int, int] = (460, 600)
CONFIG_DIALOG_FRAME_PAD: Tuple[int, int] = (14, 10)
FIRST_RUN_SIZE: Tuple[int, int] = (520, 500)
FIRST_RUN_FRAME_PAD: Tuple[int, int] = (28, 24)


@dataclass(frozen=True)
class CtkTheme:
    tg_blue: tuple = ("#3390ec", "#3390ec")
    tg_blue_hover: tuple = ("#2b7cd4", "#4a9ef0")

    bg: tuple = ("#f3f3f3", "#202020")
    card: tuple = ("#ffffff", "#2b2b2b")
    field_bg: tuple = ("#f3f3f3", "#1f1f1f")
    field_border: tuple = ("#e5e5e5", "#3a3a3a")
    track: tuple = ("#ebebeb", "#1f1f1f")
    track_hover: tuple = ("#e0e0e0", "#262626")
    pill: tuple = ("#ffffff", "#454545")
    check_border: tuple = ("#8a8a8a", "#9d9d9d")

    text_primary: tuple = ("#1a1a1a", "#ffffff")
    text_secondary: tuple = ("#6b6b6b", "#9d9d9d")
    ok: tuple = ("#1f9d55", "#4fbf7a")
    error: tuple = ("#d93025", "#ff6b6b")
    toast: tuple = ("#2b2b2b", "#3d3d3d")

    ui_font_family: str = "Sans"
    mono_font_family: str = "Monospace"


def ctk_theme_for_platform() -> CtkTheme:
    if sys.platform == "win32":
        return CtkTheme(ui_font_family="Segoe UI", mono_font_family="Consolas")
    if sys.platform == "darwin":
        return CtkTheme(ui_font_family="Helvetica Neue", mono_font_family="Menlo")
    return CtkTheme()


_APPEARANCE_MODE_MAP = {"auto": "system", "light": "Light", "dark": "Dark"}


def apply_ctk_appearance(ctk: Any, mode: str = "auto") -> None:
    ctk.set_appearance_mode(_APPEARANCE_MODE_MAP.get(mode, "system"))
    ctk.set_default_color_theme("blue")


def animate(widget: Any, ms: int, fn: Callable[[float], None],
            then: Optional[Callable[[], None]] = None, slot: str = "_anim_job") -> None:
    old = getattr(widget, slot, None)
    if old:
        widget.after_cancel(old)
    t0 = time.perf_counter()

    def step():
        if not widget.winfo_exists():
            return
        k = min(1.0, (time.perf_counter() - t0) * 1000 / ms)
        fn(1 - (1 - k) ** 3)
        if k < 1:
            setattr(widget, slot, widget.after(10, step))
        else:
            setattr(widget, slot, None)
            if then:
                then()

    step()


def mix(a: str, b: str, k: float) -> str:
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(
        f"{round(int(a[i:i + 2], 16) * (1 - k) + int(b[i:i + 2], 16) * k):02x}" for i in (0, 2, 4)
    )


def fade(win: Any, target: float, ms: int = 160, then: Optional[Callable[[], None]] = None) -> None:
    try:
        start = float(win.attributes("-alpha"))
    except tkinter.TclError:
        return
    animate(win, ms, lambda k: win.attributes("-alpha", start + (target - start) * k), then, "_fade_job")


def fade_destroy(win: Any) -> None:
    fade(win, 0.0, 120, win.destroy)


def _hwnd(win: Any) -> int:
    return _user32.GetParent(win.winfo_id())


def _dwm(win: Any, attr: int, value: int) -> None:
    if _dwmapi is None:
        return
    try:
        _dwmapi.DwmSetWindowAttribute(_hwnd(win), attr, ctypes.byref(ctypes.c_int(value)), 4)
    except Exception:
        pass


def style_titlebar(win: Any) -> None:
    if _user32 is None:
        return
    rgb = win._apply_appearance_mode(win.cget("fg_color")).lstrip("#")
    _dwm(win, 20, int(customtkinter.get_appearance_mode() == "Dark"))
    _dwm(win, 35, int(rgb[4:6] + rgb[2:4] + rgb[:2], 16))
    _user32.SetWindowPos(_hwnd(win), 0, 0, 0, 0, 0, 0x37)


_hicons: dict = {}


def _hicon(path: str, n: int) -> int:
    if (path, n) not in _hicons:
        from PIL import Image

        src = Image.open(path)
        src.size = max(src.info["sizes"])
        buf = io.BytesIO()
        src.convert("RGBA").resize((n, n), Image.LANCZOS).save(buf, "PNG")
        raw = buf.getvalue()
        _hicons[(path, n)] = _user32.CreateIconFromResourceEx(raw, len(raw), True, 0x30000, n, n, 0)
    return _hicons[(path, n)]


def set_icon(win: Any, path: Optional[str]) -> None:
    if not path or _user32 is None:
        return
    try:
        _user32.CreateIconFromResourceEx.restype = ctypes.c_void_p
        _user32.SendMessageW.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p)
        for kind, px in ((0, 16), (1, 24)):
            _user32.SendMessageW(_hwnd(win), 0x80, kind, _hicon(path, round(px * win._get_window_scaling())))
    except Exception:
        pass


def present(win: Any) -> None:
    win.deiconify()
    win.lift()
    win.attributes("-topmost", True)
    win.after(150, lambda: win.winfo_exists() and win.attributes("-topmost", False))
    win.focus_force()


def crossfade(win: Any, change: Callable[[], None], ms: int = 280) -> None:
    try:
        from PIL import ImageGrab, ImageTk

        r = (ctypes.c_long * 4)()
        _dwmapi.DwmGetWindowAttribute(_hwnd(win), 9, ctypes.byref(r), 16)
        shot = ImageTk.PhotoImage(ImageGrab.grab(bbox=tuple(r), all_screens=True), master=win)
    except Exception:
        change()
        return
    cover = tkinter.Toplevel(win)
    cover.overrideredirect(True)
    cover.attributes("-topmost", True)
    cover.geometry(f"{r[2] - r[0]}x{r[3] - r[1]}+{r[0]}+{r[1]}")
    tkinter.Label(cover, image=shot, bd=0, highlightthickness=0).pack()
    cover.shot = shot
    _dwm(cover, 33, 2)
    cover.update()
    change()
    win.update_idletasks()
    fade(cover, 0.0, ms, cover.destroy)


_POPUP_COLORS = (("#ffffff", "#2b2b2b"), ("#1a1a1a", "#ffffff"), ("#e0e0e0", "#3a3a3a"))


def popup(master: Any, text: str, theme: CtkTheme, *, image: Any = None, toast: bool = False) -> Any:
    dark = int(customtkinter.get_appearance_mode() == "Dark")
    if toast:
        bg, fg, bd = theme.toast[dark], "#ffffff", theme.toast[dark]
    else:
        bg, fg, bd = (c[dark] for c in _POPUP_COLORS)
    s = customtkinter.ScalingTracker.get_widget_scaling(master)
    pad = (16, 8) if toast else (10, 6)
    tw = tkinter.Toplevel(master)
    tw.overrideredirect(True)
    tw.attributes("-topmost", True)
    tw.attributes("-alpha", 0.0)
    lbl = tkinter.Label(tw, text=text, bg=bg, fg=fg, font=(theme.ui_font_family, 10 if toast else 9),
                        justify="left", wraplength=round(280 * s), padx=round(pad[0] * s),
                        pady=round(pad[1] * s), highlightthickness=1, highlightbackground=bd)
    if image is not None:
        tw.photo = image.create_scaled_photo_image(s, "dark" if dark else "light")
        lbl.configure(image=tw.photo, compound="left")
    lbl.pack()
    tw.update_idletasks()
    _dwm(tw, 33, 2 if toast else 3)
    return tw


def toast(win: Any, text: str, theme: CtkTheme, *, image: Any = None, bottom: int = 84) -> None:
    old = getattr(win, "_toast", None)
    if old is not None and old.winfo_exists():
        old.destroy()
    t = win._toast = popup(win, ("  " if image else "") + text, theme, image=image, toast=True)
    s = customtkinter.ScalingTracker.get_widget_scaling(win)
    x = win.winfo_rootx() + (win.winfo_width() - t.winfo_reqwidth()) // 2
    y = win.winfo_rooty() + win.winfo_height() - t.winfo_reqheight() - round(bottom * s)

    def at(k):
        t.geometry(f"+{x}+{round(y + 14 * s * (1 - k))}")
        t.attributes("-alpha", k)

    animate(t, 240, at)
    t.after(1800, lambda: t.winfo_exists() and animate(t, 220, lambda k: at(1 - k), t.destroy))


class _Smooth:
    _deactivate_windows_window_header_manipulation = True

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        style_titlebar(self)


class SmoothToplevel(_Smooth, customtkinter.CTkToplevel):
    pass


def center_ctk_geometry(root: Any, width: int, height: int) -> None:
    s = root._get_window_scaling()
    x = (root.winfo_screenwidth() - round(width * s)) // 2
    y = (root.winfo_screenheight() - round(height * s)) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")


_app_icon: Optional[str] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico") if _user32 else None


def create_ctk_toplevel(
    ctk: Any,
    *,
    title: str,
    width: int,
    height: int,
    theme: CtkTheme,
    topmost: bool = False,
    after_create: Optional[Callable[[Any], None]] = None,
    icon: Optional[str] = None,
) -> Any:
    global _app_icon
    icon = _app_icon = icon or _app_icon
    root = SmoothToplevel(fg_color=theme.bg)
    root.attributes("-alpha", 0.0)
    if icon:
        root.iconbitmap(icon)
    root.title(title)
    root.resizable(False, False)
    center_ctk_geometry(root, width, height)

    def show():
        style_titlebar(root)
        set_icon(root, icon)
        present(root)
        if topmost:
            root.after(160, lambda: root.winfo_exists() and root.attributes("-topmost", True))
        fade(root, 1.0, 180)
        if after_create:
            after_create(root)

    root.after(30, show)
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
