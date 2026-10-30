"""Square iOS/PWA artwork using Orion's existing blue symbol.

Rasterized at 4x with Pillow, already an application dependency. This is
the exact vector geometry from app/static/orion-symbol.svg, adapted to a
solid square icon canvas; no arbitrary initials or third-party image host.
"""
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw

BLUE = "#347FFF"
NAVY = "#0B1020"


def _curve(a, b, c, d, steps=32):
    points = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        points.append((
            u**3 * a[0] + 3 * u**2 * t * b[0] + 3 * u * t**2 * c[0] + t**3 * d[0],
            u**3 * a[1] + 3 * u**2 * t * b[1] + 3 * u * t**2 * c[1] + t**3 * d[1],
        ))
    return points


def _line(draw, points, radius, scale):
    coords = [(round(x * scale), round(y * scale)) for x, y in points]
    width = round(radius * 2 * scale)
    draw.line(coords, fill=BLUE, width=width, joint="curve")
    for x, y in (coords[0], coords[-1]):
        d = round(radius * scale)
        draw.ellipse((x-d, y-d, x+d, y+d), fill=BLUE)


def _eye(draw, coords, scale):
    draw.polygon([(round(x*scale), round(y*scale)) for x,y in coords], fill=BLUE)


@lru_cache(maxsize=3)
def icon_png(size: int) -> bytes:
    if type(size) is not int or size not in (180, 192, 512):
        raise ValueError("Unsupported Orion application icon size")
    large = size * 4
    image = Image.new("RGB", (large, large), NAVY)
    draw = ImageDraw.Draw(image)
    scale = large / 256
    outline = (
        _curve((178,193), (160,209), (141,213), (119,211)) +
        _curve((119,211), (68,207), (39,169), (39,122))[1:] +
        _curve((39,122), (39,76), (77,39), (128,39))[1:] +
        _curve((128,39), (180,39), (216,76), (216,126))[1:]
    )
    _line(draw, outline, 17, scale)
    _line(draw, _curve((214,142), (207,166), (191,186), (163,192)), 6, scale)
    p1 = (_curve((90,129),(87,117),(93,107),(102,106)) +
          _curve((102,106),(114,105),(119,115),(118,131))[1:] +
          [(90,134)])
    p2 = (_curve((138,131),(137,115),(142,105),(154,106)) +
          _curve((154,106),(163,107),(169,117),(166,129))[1:] +
          [(138,134)])
    _eye(draw, p1, scale)
    _eye(draw, p2, scale)
    draw.ellipse(((156-11)*scale,(193-11)*scale,(156+11)*scale,(193+11)*scale), fill=BLUE)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
