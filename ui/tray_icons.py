"""Иконка tray: загрузка icon.ico или синтез буквы «T» (Pillow), бейдж статуса."""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Tuple

from PIL import Image, ImageDraw, ImageFont

# Подсказка для tooltip (согласована с цветами бейджа)
BADGE_TOOLTIP_HINT = (
    "Бейдж: зелёный — работает, красный — ошибка, жёлтый — запуск/ожидание/остановка"
)


def _resample_lanczos() -> int:
    try:
        return Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except AttributeError:
        return Image.LANCZOS


def normalize_tray_icon_image(img: Image.Image, size: int = 64) -> Image.Image:
    """RGBA, единый размер (для стабильного бейджа). ICO — кадр с максимальной площадью."""
    im = img
    try:
        n = getattr(im, "n_frames", 1)
        if n > 1:
            best: Image.Image | None = None
            best_area = 0
            for i in range(n):
                im.seek(i)
                w, h = im.size
                a = w * h
                if a > best_area:
                    best_area = a
                    best = im.copy()
            im = best if best is not None else im.copy()
        else:
            im = im.copy()
    except Exception:
        im = img.copy()
    im = im.convert("RGBA")
    if im.size != (size, size):
        im = im.resize((size, size), _resample_lanczos())
    return im


def badge_rgb_for_phase(phase: str) -> Tuple[int, int, int]:
    """Зелёный — слушает, красный — ошибка, жёлтый — остальное."""
    if phase == "listening":
        return (34, 197, 94)
    if phase == "error":
        return (239, 68, 68)
    return (234, 179, 8)


def apply_status_badge(base: Image.Image, phase: str) -> Image.Image:
    """Круглый индикатор внизу справа (как бейдж уведомления)."""
    img = base.copy()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    rgb = badge_rgb_for_phase(phase)
    r = max(4, min(w, h) // 7)
    margin = max(1, min(w, h) // 18)
    cx = w - margin - r
    cy = h - margin - r
    draw = ImageDraw.Draw(img)
    # лёгкая тень для контраста на светлой панели
    draw.ellipse(
        [cx - r + 1, cy - r + 1, cx + r + 1, cy + r + 1],
        fill=(0, 0, 0, 70),
    )
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb + (255,))
    border_w = max(1, r // 5)
    draw.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=(255, 255, 255, 230),
        width=border_w,
    )
    return img


def _pick_font(size: int, candidates: List[str]) -> Any:
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=int(size * 0.55))
        except Exception:
            continue
    return ImageFont.load_default()


def synthesize_letter_t_icon(size: int, font_candidates: List[str]) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 2
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        fill=(0, 136, 204, 255),
    )
    font = _pick_font(size, font_candidates)
    bbox = draw.textbbox((0, 0), "T", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    draw.text((tx, ty), "T", fill=(255, 255, 255, 255), font=font)
    return img


def load_ico_or_synthesize(
    ico_path: Path,
    font_candidates: List[str],
    size: int = 64,
) -> Image.Image:
    if ico_path.exists():
        try:
            return Image.open(str(ico_path))
        except Exception:
            pass
    return synthesize_letter_t_icon(size, font_candidates)
