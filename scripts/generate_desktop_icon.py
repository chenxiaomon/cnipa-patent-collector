#!/usr/bin/env python3
"""Render the desktop mark at native icon sizes; Pillow is only needed to regenerate."""

from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from settings import DESKTOP_ICON_PNG_FILE, DESKTOP_ICON_ICNS_FILE, DESKTOP_ICON_ICO_FILE


def main() -> None:
    scale = 2
    side = 1024 * scale
    artwork = Image.new('RGBA', (side, side))
    rounded_mask = Image.new('L', (side, side))
    ImageDraw.Draw(rounded_mask).rounded_rectangle(
        (60 * scale, 60 * scale, 964 * scale, 964 * scale), radius=208 * scale, fill=255,
    )
    gradient = Image.new('RGBA', (side, side))
    gradient_draw = ImageDraw.Draw(gradient)
    for row in range(side):
        fraction = row / (side - 1)
        gradient_draw.line((0, row, side, row), fill=(
            round(24 - 18 * fraction), round(74 + 50 * fraction), round(122 + 17 * fraction), 255,
        ))
    artwork.paste(gradient, (0, 0), rounded_mask)
    drawing = ImageDraw.Draw(artwork)
    drawing.rounded_rectangle((242 * scale, 194 * scale, 692 * scale, 815 * scale),
                               radius=44 * scale, fill=(0, 35, 60, 70))
    document = [(258, 180), (554, 180), (688, 314), (688, 778), (258, 778)]
    drawing.polygon([(x * scale, y * scale) for x, y in document], fill='#F9FCFF')
    drawing.polygon([(554 * scale, 180 * scale), (554 * scale, 314 * scale), (688 * scale, 314 * scale)],
                    fill='#BFDBE7')
    for start, end, top in ((330, 485, 349), (330, 600, 425), (330, 535, 501), (330, 443, 577)):
        drawing.rounded_rectangle((start * scale, top * scale, end * scale, (top + 27) * scale),
                                   radius=13 * scale, fill='#297C9A')
    drawing.line([(756 * scale, 754 * scale), (847 * scale, 845 * scale)],
                 fill='#F5BE50', width=70 * scale)
    drawing.ellipse((812 * scale, 810 * scale, 882 * scale, 880 * scale), fill='#F5BE50')
    drawing.ellipse((508 * scale, 506 * scale, 798 * scale, 796 * scale),
                    fill='#F5BE50')
    drawing.ellipse((550 * scale, 548 * scale, 756 * scale, 754 * scale),
                    fill='#145B78')
    drawing.line([(602 * scale, 650 * scale), (640 * scale, 688 * scale), (705 * scale, 613 * scale)],
                 fill='#FFFFFF', width=25 * scale, joint='curve')
    artwork = artwork.resize((1024, 1024), Image.Resampling.LANCZOS)
    for icon_path, encoding in (
        (DESKTOP_ICON_PNG_FILE, 'PNG'), (DESKTOP_ICON_ICNS_FILE, 'ICNS'), (DESKTOP_ICON_ICO_FILE, 'ICO'),
    ):
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        with BytesIO() as encoded_icon:
            artwork.save(encoded_icon, format=encoding)
            temporary_icon = icon_path.with_suffix(icon_path.suffix + '.tmp')
            temporary_icon.write_bytes(encoded_icon.getvalue())
            temporary_icon.replace(icon_path)
        print(icon_path)


if __name__ == '__main__':
    main()
