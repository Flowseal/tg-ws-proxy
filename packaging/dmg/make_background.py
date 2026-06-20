import os
import sys

from PIL import Image, ImageDraw

BASE_W = 660
BASE_H = 440
SS = 3

ICON_Y = 220
APP_X = 145
APPS_X = 515

THEMES = {
    "light": {
        "top": (255, 255, 255),
        "bottom": (228, 242, 254),
        "arrow": (20, 38, 52, 255),
    },
}


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, top, bottom):
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(h):
        row = lerp(top, bottom, y / max(1, h - 1))
        for x in range(w):
            px[x, y] = row
    return img


def render(theme_name):
    t = THEMES[theme_name]
    w, h = BASE_W * SS, BASE_H * SS
    img = gradient((w, h), t["top"], t["bottom"]).convert("RGBA")
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    cy = ICON_Y * SS
    x0 = (APP_X + 82) * SS
    x1 = (APPS_X - 82) * SS
    line_width = 7 * SS
    d.line(
        [(x0, cy), (x1 - 2 * SS, cy)],
        fill=t["arrow"],
        width=line_width,
    )
    radius = line_width // 2
    d.ellipse(
        [x0 - radius, cy - radius, x0 + radius, cy + radius],
        fill=t["arrow"],
    )
    d.line(
        [
            (x1 - 34 * SS, cy - 27 * SS),
            (x1, cy),
            (x1 - 34 * SS, cy + 27 * SS),
        ],
        fill=t["arrow"],
        width=line_width,
        joint="curve",
    )
    for px, py in (
        (x1 - 34 * SS, cy - 27 * SS),
        (x1 - 34 * SS, cy + 27 * SS),
    ):
        d.ellipse(
            [
                px - radius,
                py - radius,
                px + radius,
                py + radius,
            ],
            fill=t["arrow"],
        )

    return Image.alpha_composite(img, overlay).convert("RGB")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__)
    os.makedirs(out, exist_ok=True)
    for name in THEMES:
        hi = render(name)
        hi.resize((BASE_W, BASE_H), Image.LANCZOS).save(
            os.path.join(out, f"background-{name}.png")
        )
        hi.resize((BASE_W * 2, BASE_H * 2), Image.LANCZOS).save(
            os.path.join(out, f"background-{name}@2x.png")
        )
        print(f"wrote background-{name}.png ({BASE_W}x{BASE_H}) + @2x")


if __name__ == "__main__":
    main()
