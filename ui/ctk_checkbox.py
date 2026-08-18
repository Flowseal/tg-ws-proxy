from __future__ import annotations

import tkinter
from typing import Any, Optional

import customtkinter as ctk

from ui.ctk_anim import (
    CHECK_ANIM_MS,
    HOVER_ANIM_MS,
    Color,
    Icon,
    Tween,
    check_points,
    mix,
    rounded_rect_points,
)

_BOX_SIZE = 20
_BOX_INSET = 1.0
_BOX_RADIUS = 6.0
_BOX_GAP = 8
_TEXT_PAD = 2
_CHECK_COLOR = "#ffffff"


class CtkCheckBox(ctk.CTkFrame):
    def __init__(
        self,
        master: Any,
        *,
        text: str,
        variable: Any,
        font: Any = None,
        text_color: Color = "#000000",
        accent_color: Color = "#3390ec",
        accent_hover_color: Color = "#2b7cd4",
        border_color: Color = "#d6d9dc",
        box_color: Color = "#ffffff",
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._variable = variable
        self._text_color = text_color
        self._accent_color = accent_color
        self._accent_hover_color = accent_hover_color
        self._box_border_color = border_color
        self._box_color = box_color

        self._checked = bool(variable.get())
        self._check_ratio = 1.0 if self._checked else 0.0
        self._hover_ratio = 0.0
        self._check_tween: Optional[Tween] = None
        self._hover_tween: Optional[Tween] = None
        self._box_photo: Any = None

        self._box = tkinter.Canvas(
            self, width=_BOX_SIZE, height=_BOX_SIZE,
            highlightthickness=0, borderwidth=0,
        )
        self._box.pack(side="left")
        self._label = ctk.CTkLabel(
            self, text=text, font=font, text_color=text_color,
            anchor="w", width=1,
        )
        self._label.pack(side="left", padx=(_BOX_GAP, _TEXT_PAD))

        for widget in (self._box, self._label):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        for sequence, handler in (
            ("<Button-1>", self._on_press),
            ("<Enter>", self._on_enter),
            ("<Leave>", self._on_leave),
            ("<Destroy>", self._on_destroy),
        ):
            super().bind(sequence, handler)

        self._trace_name = variable.trace_add("write", self._on_variable_write)
        self._render()

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "text" in kwargs:
            self._label.configure(text=kwargs.pop("text"))
        super().configure(require_redraw=require_redraw, **kwargs)

    def bind(self, sequence: Any = None, command: Any = None, add: Any = True) -> None:
        super().bind(sequence, command, add=add)
        self._label.bind(sequence, command, add=True)
        self._box.bind(sequence, command, add="+")

    def toggle(self) -> None:
        self._variable.set(not self._variable.get())

    def _on_press(self, _event: Any = None) -> str:
        self.toggle()
        return "break"

    def _on_variable_write(self, *_args: Any) -> None:
        checked = bool(self._variable.get())
        if checked == self._checked:
            return
        self._checked = checked
        self._animate_check(1.0 if checked else 0.0)

    def _animate_check(self, target: float) -> None:
        if self._check_tween is not None:
            self._check_tween.stop()
        start = self._check_ratio
        self._check_tween = Tween(
            self, CHECK_ANIM_MS,
            lambda ratio: self._set_check_ratio(start + (target - start) * ratio),
        )
        self._check_tween.start()

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

    def _set_check_ratio(self, ratio: float) -> None:
        self._check_ratio = ratio
        self._render()

    def _set_hover_ratio(self, ratio: float) -> None:
        self._hover_ratio = ratio
        self._render()

    def _render(self) -> None:
        backdrop = self._apply_appearance_mode(self._bg_color)
        accent = mix(
            self._apply_appearance_mode(self._accent_color),
            self._apply_appearance_mode(self._accent_hover_color),
            self._hover_ratio,
        )
        border = mix(
            mix(
                self._apply_appearance_mode(self._box_border_color),
                self._apply_appearance_mode(self._accent_color),
                self._hover_ratio * 0.6,
            ),
            accent,
            self._check_ratio,
        )
        fill = mix(self._apply_appearance_mode(self._box_color), accent, self._check_ratio)

        size = max(1, round(self._apply_widget_scaling(_BOX_SIZE)))
        unit = size / _BOX_SIZE
        squeeze = 3.2 * (1.0 - self._check_ratio) * self._check_ratio
        inset = (_BOX_INSET + squeeze) * unit
        side = size - 2 * inset
        radius = _BOX_RADIUS * unit * side / (size - 2 * _BOX_INSET * unit)
        points = check_points(size - 6 * unit, self._check_ratio, offset=3.0 * unit)
        stroke = 2.0 * unit

        self._box.configure(bg=backdrop, width=size, height=size)
        icon = Icon(size)
        icon.rounded_rect(
            inset, inset, size - inset, size - inset, radius,
            fill=fill, outline=border, width=stroke,
        )
        icon.stroke(points, width=stroke, color=_CHECK_COLOR)
        self._box_photo = icon.photo()
        self._box.delete("all")
        if self._box_photo is not None:
            self._box.create_image(0, 0, anchor="nw", image=self._box_photo)
            return
        self._box.create_polygon(
            rounded_rect_points(inset, inset, size - inset, size - inset, radius),
            fill=fill, outline=border, width=stroke, smooth=False,
        )
        if len(points) >= 4:
            self._box.create_line(
                *points, width=stroke, capstyle="round", joinstyle="round",
                fill=_CHECK_COLOR,
            )

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

    def _on_destroy(self, event: Any = None) -> None:
        if event is not None and event.widget is not self._canvas:
            return
        if self._trace_name is not None:
            try:
                self._variable.trace_remove("write", self._trace_name)
            except Exception:
                pass
            self._trace_name = None
        for tween in (self._check_tween, self._hover_tween):
            if tween is not None:
                tween.stop()

    def _set_appearance_mode(self, mode_string: str) -> None:
        super()._set_appearance_mode(mode_string)
        self._render()
