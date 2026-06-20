from PIL import Image, ImageDraw, ImageFont


def render_app_icon(size):
    scale = size / 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    outer = tuple(round(value * scale) for value in (92, 92, 932, 932))
    draw.ellipse(outer, fill=(0, 151, 221, 255))
    font = ImageFont.truetype(
        "/System/Library/Fonts/Helvetica.ttc",
        round(430 * scale),
    )
    box = draw.textbbox((0, 0), "T", font=font)
    width = box[2] - box[0]
    height = box[3] - box[1]
    draw.text(
        (
            (size - width) / 2 - box[0],
            (size - height) / 2 - box[1] - round(10 * scale),
        ),
        "T",
        font=font,
        fill=(255, 255, 255, 255),
    )
    return image
