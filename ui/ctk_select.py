from __future__ import annotations

import math
import tkinter
from typing import Any, Callable, List, Optional, Sequence

import customtkinter as ctk

from ui.ctk_anim import (
    CHECK_ANIM_MS,
    FRAME_MS,
    HOVER_ANIM_MS,
    Color,
    Icon,
    Tween,
    check_points,
    mix,
    near_color,
)

_ARROW_ANIM_MS = 140
_POPUP_ANIM_MS = 120
_PICK_DELAY_MS = 140
_POPUP_GAP = 4
_POPUP_SLIDE = 6
_WINDOW_CHECK_MS = 60
_ICON_BOX = 18
_ROW_HEIGHT = 30
_ROW_GAP = 2
_CARD_PAD = 4
_CARD_BORDER = 1
_ROW_PAD = 10


def _chevron_points(size: float, angle: float) -> List[float]:
    center = size / 2.0
    scale = size / _ICON_BOX
    cos_a, sin_a = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    points: List[float] = []
    for x, y in ((-4.0, -1.8), (0.0, 2.2), (4.0, -1.8)):
        points.append(center + (x * cos_a - y * sin_a) * scale)
        points.append(center + (x * sin_a + y * cos_a) * scale)
    return points


class _SelectOption(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        *,
        text: str,
        font: Any,
        text_color: Color,
        accent_color: Color,
        base_color: Color,
        hover_color: Color,
        on_click: Callable[[], None],
    ) -> None:
        super().__init__(master, width=1, height=_ROW_HEIGHT, corner_radius=8, fg_color=base_color)
        self.pack_propagate(False)
        self._text_color = text_color
        self._accent_color = accent_color
        self._base_color = base_color
        self._hover_color = hover_color
        self._on_click = on_click
        self._hover_ratio = 0.0
        self._check_ratio = 0.0
        self._hover_tween: Optional[Tween] = None
        self._check_tween: Optional[Tween] = None
        self._check_photo: Any = None

        self._label = ctk.CTkLabel(
            self, text=text, font=font, text_color=text_color, anchor="w", width=1,
        )
        self._label.pack(side="left", fill="x", expand=True, padx=(_ROW_PAD, 0))
        self._check = tkinter.Canvas(
            self, width=_ICON_BOX, height=_ICON_BOX,
            highlightthickness=0, borderwidth=0,
        )
        self._check.pack(side="right", padx=(0, _ROW_PAD))

        for widget in (self, self._label, self._check):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        self._render()

    def needed_width(self) -> int:
        icon = round(self._apply_widget_scaling(_ICON_BOX + 2 * _ROW_PAD))
        return self._label.winfo_reqwidth() + icon

    def set_selected(self, selected: bool, *, animate: bool = True) -> None:
        self._label.configure(
            text_color=self._accent_color if selected else self._text_color
        )
        if self._check_tween is not None:
            self._check_tween.stop()
        target = 1.0 if selected else 0.0
        if not animate:
            self._check_ratio = target
            self._render()
            return
        start = self._check_ratio
        self._check_tween = Tween(
            self, CHECK_ANIM_MS,
            lambda ratio: self._set_check_ratio(start + (target - start) * ratio),
        )
        self._check_tween.start()

    def stop_animations(self) -> None:
        for tween in (self._hover_tween, self._check_tween):
            if tween is not None:
                tween.stop()

    def _set_check_ratio(self, ratio: float) -> None:
        self._check_ratio = ratio
        self._render()

    def _set_hover_ratio(self, ratio: float) -> None:
        self._hover_ratio = ratio
        self._render()

    def _render(self) -> None:
        background = mix(
            self._apply_appearance_mode(self._base_color),
            self._apply_appearance_mode(self._hover_color),
            self._hover_ratio,
        )
        self.configure(fg_color=background)
        size = max(1, round(self._apply_widget_scaling(_ICON_BOX)))
        self._check.configure(bg=background, width=size, height=size)
        points = check_points(size, self._check_ratio)
        color = self._apply_appearance_mode(self._accent_color)
        stroke = 2.0 * size / _ICON_BOX
        icon = Icon(size)
        icon.stroke(points, width=stroke, color=color)
        self._check_photo = icon.photo()
        self._check.delete("all")
        if self._check_photo is not None:
            self._check.create_image(0, 0, anchor="nw", image=self._check_photo)
        elif len(points) >= 4:
            self._check.create_line(
                *points, width=stroke, capstyle="round", joinstyle="round", fill=color,
            )

    def _animate_hover(self, target: float) -> None:
        if self._hover_tween is not None:
            self._hover_tween.stop()
        start = self._hover_ratio
        if start == target:
            return
        self._hover_tween = Tween(
            self, HOVER_ANIM_MS,
            lambda ratio: self._set_hover_ratio(start + (target - start) * ratio),
        )
        self._hover_tween.start()

    def _pointer_inside(self) -> bool:
        try:
            x = self.winfo_pointerx() - self.winfo_rootx()
            y = self.winfo_pointery() - self.winfo_rooty()
            return 0 <= x < self.winfo_width() and 0 <= y < self.winfo_height()
        except tkinter.TclError:
            return False

    def _on_enter(self, _event: Any = None) -> None:
        self._animate_hover(1.0)

    def _on_leave(self, _event: Any = None) -> None:
        self.after(1, lambda: self._animate_hover(1.0 if self._pointer_inside() else 0.0))

    def _on_press(self, _event: Any = None) -> str:
        self._on_click()
        return "break"

    def _set_appearance_mode(self, mode_string: str) -> None:
        super()._set_appearance_mode(mode_string)
        self._render()


class CtkSelect(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        *,
        values: Sequence[str],
        variable: Optional[Any] = None,
        command: Optional[Callable[[str], None]] = None,
        font: Any = None,
        height: int = 32,
        corner_radius: int = 8,
        fg_color: Color = "#ffffff",
        border_color: Color = "#d6d9dc",
        text_color: Color = "#000000",
        accent_color: Color = "#3390ec",
        arrow_color: Color = "#707579",
        dropdown_fg_color: Color = "#f0f2f5",
        dropdown_hover_color: Color = "#d6d9dc",
        backdrop_color: Color = "#ffffff",
    ) -> None:
        super().__init__(
            master, height=height, corner_radius=corner_radius,
            fg_color=fg_color, border_color=border_color, border_width=1,
        )
        self.pack_propagate(False)
        self._values: List[str] = list(values)
        self._variable = variable
        self._command = command
        self._font = font
        self._text_color = text_color
        self._accent_color = accent_color
        self._arrow_color = arrow_color
        self._field_border_color = border_color
        self._dropdown_fg_color = dropdown_fg_color
        self._dropdown_hover_color = dropdown_hover_color
        self._backdrop_color = backdrop_color

        self._value = self._values[0] if self._values else ""
        if variable is not None and variable.get():
            self._value = variable.get()

        self._popup: Optional[tkinter.Toplevel] = None
        self._rows: List[_SelectOption] = []
        self._arrow_angle = 0.0
        self._accent_ratio = 0.0
        self._arrow_tween: Optional[Tween] = None
        self._accent_tween: Optional[Tween] = None
        self._popup_tween: Optional[Tween] = None
        self._arrow_photo: Any = None
        self._parent_geometry = ""
        self._trace_name: Optional[str] = None

        self._label = ctk.CTkLabel(
            self, text=self._value, font=font, text_color=text_color, anchor="w", width=1,
        )
        self._label.pack(side="left", fill="x", expand=True, padx=(_ROW_PAD, 0))
        self._arrow = tkinter.Canvas(
            self, width=_ICON_BOX, height=_ICON_BOX,
            highlightthickness=0, borderwidth=0,
        )
        self._arrow.pack(side="right", padx=(0, _ROW_PAD))

        for widget in (self, self._label, self._arrow):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        super().bind("<Destroy>", self._on_destroy)

        if variable is not None:
            self._trace_name = variable.trace_add("write", self._on_variable_write)

        self._bind_dismiss()
        self._render()

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value
        self._label.configure(text=value)
        if self._variable is not None and self._variable.get() != value:
            self._variable.set(value)
        for option, option_value in zip(self._rows, self._values):
            option.set_selected(option_value == value)

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "values" in kwargs:
            self._values = list(kwargs.pop("values"))
            self._close()
        super().configure(require_redraw=require_redraw, **kwargs)

    def _on_variable_write(self, *_args: Any) -> None:
        value = self._variable.get()
        if value != self._value:
            self.set(value)

    def _on_press(self, _event: Any = None) -> str:
        if self._popup is None:
            self._open()
        else:
            self._close()
        return "break"

    def _on_enter(self, _event: Any = None) -> None:
        self._animate_accent(1.0)

    def _on_leave(self, _event: Any = None) -> None:
        self.after(1, self._sync_accent)

    def _sync_accent(self) -> None:
        self._animate_accent(1.0 if self._popup is not None or self._pointer_inside() else 0.0)

    def _pointer_inside(self) -> bool:
        try:
            x = self.winfo_pointerx() - self.winfo_rootx()
            y = self.winfo_pointery() - self.winfo_rooty()
            return 0 <= x < self.winfo_width() and 0 <= y < self.winfo_height()
        except tkinter.TclError:
            return False

    def _animate_accent(self, target: float) -> None:
        if self._accent_tween is not None:
            self._accent_tween.stop()
        start = self._accent_ratio
        if start == target:
            return
        self._accent_tween = Tween(
            self, HOVER_ANIM_MS,
            lambda ratio: self._set_accent_ratio(start + (target - start) * ratio),
        )
        self._accent_tween.start()

    def _set_accent_ratio(self, ratio: float) -> None:
        self._accent_ratio = ratio
        self._render()

    def _animate_arrow(self, target: float) -> None:
        if self._arrow_tween is not None:
            self._arrow_tween.stop()
        start = self._arrow_angle
        self._arrow_tween = Tween(
            self, _ARROW_ANIM_MS,
            lambda ratio: self._set_arrow_angle(start + (target - start) * ratio),
        )
        self._arrow_tween.start()

    def _set_arrow_angle(self, angle: float) -> None:
        self._arrow_angle = angle
        self._render()

    def _render(self) -> None:
        border = mix(
            self._apply_appearance_mode(self._field_border_color),
            self._apply_appearance_mode(self._accent_color),
            self._accent_ratio,
        )
        arrow = mix(
            self._apply_appearance_mode(self._arrow_color),
            self._apply_appearance_mode(self._accent_color),
            self._accent_ratio,
        )
        self.configure(border_color=border)
        size = max(1, round(self._apply_widget_scaling(_ICON_BOX)))
        self._arrow.configure(
            bg=self._apply_appearance_mode(self._fg_color), width=size, height=size,
        )
        points = _chevron_points(size, self._arrow_angle)
        stroke = 2.0 * size / _ICON_BOX
        icon = Icon(size)
        icon.stroke(points, width=stroke, color=arrow)
        self._arrow_photo = icon.photo()
        self._arrow.delete("all")
        if self._arrow_photo is not None:
            self._arrow.create_image(0, 0, anchor="nw", image=self._arrow_photo)
        else:
            self._arrow.create_line(
                *points, width=stroke, capstyle="round", joinstyle="round", fill=arrow,
            )

    def _open(self) -> None:
        if not self._values or self._popup is not None:
            return
        parent = self.winfo_toplevel()
        popup = tkinter.Toplevel(parent)
        popup.withdraw()
        popup.overrideredirect(True)
        key = near_color(self._apply_appearance_mode(self._dropdown_fg_color))
        popup.configure(bg=key)
        try:
            popup.wm_attributes("-transparentcolor", key)
        except tkinter.TclError:
            popup.configure(bg=self._apply_appearance_mode(self._backdrop_color))
        try:
            popup.wm_attributes("-topmost", True)
        except tkinter.TclError:
            pass

        card = ctk.CTkFrame(
            popup, corner_radius=10, fg_color=self._dropdown_fg_color,
            border_width=_CARD_BORDER, border_color=self._field_border_color,
        )
        card.pack(fill="both", expand=True)

        self._rows = []
        for index, value in enumerate(self._values):
            option = _SelectOption(
                card, text=value, font=self._font,
                text_color=self._text_color, accent_color=self._accent_color,
                base_color=self._dropdown_fg_color,
                hover_color=self._dropdown_hover_color,
                on_click=lambda choice=value: self._choose(choice),
            )
            option.pack(
                fill="x", padx=_CARD_PAD,
                pady=(_CARD_PAD if index == 0 else _ROW_GAP, 0),
            )
            self._rows.append(option)
        self._popup = popup
        self._parent_geometry = parent.winfo_geometry()
        popup.update_idletasks()
        pad = round(self._apply_widget_scaling(_CARD_PAD))
        width = max(
            [self.winfo_width()]
            + [row.needed_width() + 2 * pad for row in self._rows]
        )
        height = (
            sum(row.winfo_reqheight() for row in self._rows)
            + (len(self._rows) - 1) * round(self._apply_widget_scaling(_ROW_GAP))
            + 2 * pad + 2 * _CARD_BORDER
        )
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + _POPUP_GAP
        if y + height > self.winfo_screenheight():
            y = self.winfo_rooty() - height - _POPUP_GAP

        popup.geometry(f"{width}x{height}+{x}+{y - _POPUP_SLIDE}")
        popup.deiconify()
        self._set_popup_alpha(0.0)
        self._animate_arrow(180.0)
        self._animate_accent(1.0)
        for option in self._rows:
            option.set_selected(False, animate=False)
        self.after(FRAME_MS, self._animate_selected_check)

        self._popup_tween = Tween(
            popup, _POPUP_ANIM_MS,
            lambda ratio: self._step_popup(ratio, x, y, width, height),
        )
        self._popup_tween.start()

    def _animate_selected_check(self) -> None:
        if self._popup is None or self._value not in self._values:
            return
        self._rows[self._values.index(self._value)].set_selected(True)

    def _step_popup(self, ratio: float, x: int, y: int, width: int, height: int) -> None:
        if self._popup is None:
            return
        offset = round(_POPUP_SLIDE * (1.0 - ratio))
        self._popup.geometry(f"{width}x{height}+{x}+{y - offset}")
        self._set_popup_alpha(ratio)

    def _set_popup_alpha(self, alpha: float) -> None:
        if self._popup is None:
            return
        try:
            self._popup.wm_attributes("-alpha", max(0.0, min(1.0, alpha)))
        except tkinter.TclError:
            pass

    def _choose(self, value: str) -> None:
        changed = value != self._value
        for option, option_value in zip(self._rows, self._values):
            option.set_selected(option_value == value)
        self._value = value
        self._label.configure(text=value)
        if self._variable is not None and self._variable.get() != value:
            self._variable.set(value)
        self.after(_PICK_DELAY_MS, self._close)
        if changed and self._command is not None:
            self._command(value)

    def _close(self) -> None:
        popup = self._popup
        if popup is None:
            return
        self._popup = None
        self._animate_arrow(0.0)
        self._sync_accent()
        for option in self._rows:
            option.stop_animations()
        self._rows = []
        if self._popup_tween is not None:
            self._popup_tween.stop()

        def _fade(ratio: float) -> None:
            try:
                popup.wm_attributes("-alpha", max(0.0, 1.0 - ratio))
            except tkinter.TclError:
                pass

        def _destroy() -> None:
            try:
                popup.destroy()
            except tkinter.TclError:
                pass

        self._popup_tween = Tween(popup, _POPUP_ANIM_MS, _fade, _destroy)
        self._popup_tween.start()

    def _bind_dismiss(self) -> None:
        parent = self.winfo_toplevel()
        for event in ("<Button-1>", "<MouseWheel>", "<Button-4>", "<Button-5>", "<Escape>"):
            parent.bind(event, self._on_dismiss_event, add="+")
        for event in ("<Configure>", "<Unmap>"):
            parent.bind(event, self._on_window_event, add="+")

    def _on_dismiss_event(self, _event: Any = None) -> None:
        if self._popup is None:
            return
        self._close()

    def _on_window_event(self, event: Any = None) -> None:
        if self._popup is None:
            return
        if event is not None and event.widget is not self.winfo_toplevel():
            return
        self.after(_WINDOW_CHECK_MS, self._close_if_window_changed)

    def _close_if_window_changed(self) -> None:
        if self._popup is None:
            return
        try:
            parent = self.winfo_toplevel()
            if parent.winfo_ismapped() and parent.winfo_geometry() == self._parent_geometry:
                return
        except tkinter.TclError:
            pass
        self._close()

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self._canvas:
            return
        if self._variable is not None and self._trace_name is not None:
            try:
                self._variable.trace_remove("write", self._trace_name)
            except Exception:
                pass
            self._trace_name = None
        for tween in (self._arrow_tween, self._accent_tween):
            if tween is not None:
                tween.stop()
        self._close()

    def _set_appearance_mode(self, mode_string: str) -> None:
        super()._set_appearance_mode(mode_string)
        self._render()
