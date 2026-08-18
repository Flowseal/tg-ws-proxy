from __future__ import annotations

import math
import time
import tkinter
from typing import Any, Callable, List, Optional, Sequence, Tuple, Union

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None

Color = Union[str, Tuple[str, str]]

FRAME_MS = 16
SUPERSAMPLE = 4
CHECK_ANIM_MS = 190
HOVER_ANIM_MS = 110


def hex_to_rgb(color: str) -> Tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def mix(start: str, end: str, ratio: float) -> str:
    try:
        sr, sg, sb = hex_to_rgb(start)
        er, eg, eb = hex_to_rgb(end)
    except ValueError:
        return end if ratio > 0.5 else start
    return "#{:02x}{:02x}{:02x}".format(
        round(sr + (er - sr) * ratio),
        round(sg + (eg - sg) * ratio),
        round(sb + (eb - sb) * ratio),
    )


def near_color(color: str, shift: int = 3) -> str:
    try:
        r, g, b = hex_to_rgb(color)
    except ValueError:
        return color
    return "#{:02x}{:02x}{:02x}".format(
        r + shift if r < 128 else r - shift,
        g,
        b + shift if b < 128 else b - shift,
    )


def ease_out(ratio: float) -> float:
    return 1.0 - (1.0 - ratio) ** 3


def check_points(size: float, progress: float, *, offset: float = 0.0) -> List[float]:
    scale = size / 18.0
    path = ((3.6, 9.4), (7.4, 13.2), (14.4, 5.2))
    spans = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
    left = sum(spans) * max(0.0, min(1.0, progress))
    points = [path[0][0] * scale + offset, path[0][1] * scale + offset]
    for (ax, ay), (bx, by), span in zip(path, path[1:], spans):
        if left <= 0.0:
            break
        ratio = min(1.0, left / span)
        points.extend([
            (ax + (bx - ax) * ratio) * scale + offset,
            (ay + (by - ay) * ratio) * scale + offset,
        ])
        left -= span
    return points


def rounded_rect_points(
    x0: float, y0: float, x1: float, y1: float, radius: float, steps: int = 5,
) -> List[float]:
    radius = max(0.0, min(radius, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    corners = (
        (x1 - radius, y1 - radius, 0.0),
        (x0 + radius, y1 - radius, 90.0),
        (x0 + radius, y0 + radius, 180.0),
        (x1 - radius, y0 + radius, 270.0),
    )
    points: List[float] = []
    for cx, cy, start_angle in corners:
        for step in range(steps + 1):
            angle = math.radians(start_angle + 90.0 * step / steps)
            points.extend([cx + radius * math.cos(angle), cy + radius * math.sin(angle)])
    return points


class Tween:
    def __init__(
        self,
        widget: Any,
        duration_ms: int,
        on_step: Callable[[float], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        self._widget = widget
        self._duration_ms = duration_ms
        self._on_step = on_step
        self._on_done = on_done
        self._after_id: Optional[str] = None
        self._started = 0.0

    def start(self) -> None:
        self.stop()
        self._started = time.monotonic()
        self._tick()

    def stop(self) -> None:
        if self._after_id is None:
            return
        try:
            self._widget.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None

    def _tick(self) -> None:
        self._after_id = None
        elapsed = (time.monotonic() - self._started) * 1000.0
        ratio = 1.0 if self._duration_ms <= 0 else min(1.0, elapsed / self._duration_ms)
        try:
            self._on_step(ease_out(ratio))
            if ratio < 1.0:
                self._after_id = self._widget.after(FRAME_MS, self._tick)
                return
        except tkinter.TclError:
            return
        if self._on_done is not None:
            self._on_done()


class Icon:
    def __init__(self, size: float, supersample: int = SUPERSAMPLE) -> None:
        self._size = max(1, int(round(size)))
        self._scale = supersample
        self._image = None
        self._draw = None
        if Image is not None:
            box = (self._size * self._scale, self._size * self._scale)
            self._image = Image.new("RGBA", box, (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)

    @property
    def available(self) -> bool:
        return self._draw is not None

    def rounded_rect(
        self,
        x0: float, y0: float, x1: float, y1: float, radius: float,
        *,
        fill: Optional[str] = None,
        outline: Optional[str] = None,
        width: float = 1.0,
    ) -> None:
        if self._draw is None:
            return
        scale = self._scale
        self._draw.rounded_rectangle(
            (x0 * scale, y0 * scale, x1 * scale - 1, y1 * scale - 1),
            radius=radius * scale, fill=fill, outline=outline,
            width=max(1, round(width * scale)),
        )

    def stroke(self, points: Sequence[float], *, width: float, color: str) -> None:
        if self._draw is None or len(points) < 4:
            return
        scaled = [value * self._scale for value in points]
        line_width = max(1, round(width * self._scale))
        self._draw.line(scaled, fill=color, width=line_width, joint="curve")
        radius = line_width / 2.0
        for index in range(0, len(scaled), 2):
            x, y = scaled[index], scaled[index + 1]
            self._draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

    def photo(self) -> Any:
        if self._image is None:
            return None
        return ImageTk.PhotoImage(
            self._image.resize((self._size, self._size), Image.LANCZOS)
        )
