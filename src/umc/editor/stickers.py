"""Built-in stickers (no Qt): simple shapes drawn on a 100 × 100 grid, made for this app and
released as CC0 (public domain).

Each sticker is a list of shapes:
  {"poly": [[x, y], …], "fill": c, "stroke": c, "width": w}   a closed polygon
  {"ellipse": [x0, y0, x1, y1], "fill": c, "stroke": c, "width": w}
  {"line": [[x, y], …], "stroke": c, "width": w}               an open line, round ends
Colours c: "front" / "back" (the colour swatches) or "#rrggbb". ``web/src/editor/stickers.json``
is the same data for the website (``tests/test_editor_annotate.py`` checks they match; run this
module to print it).
"""

from __future__ import annotations

import json
import math

from PIL import Image, ImageDraw

SUPERSAMPLE = 4


def _circle_pts(cx: float, cy: float, r: float, n: int = 48, a0: float = 0, a1: float = 360):
    out = []
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        out.append([round(cx + r * math.cos(a), 2), round(cy + r * math.sin(a), 2)])
    return out


def _star(cx=50, cy=53, r_out=46, r_in=19):
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(-90 + 36 * i)
        pts.append([round(cx + r * math.cos(a), 2), round(cy + r * math.sin(a), 2)])
    return pts


def _heart():
    pts = []
    for i in range(64):
        t = 2 * math.pi * i / 64
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append([round(50 + x * 2.8, 2), round(46 - y * 2.8, 2)])
    return pts


def _rounded_rect(x0, y0, x1, y1, r, n=8):
    pts = []
    for cx, cy, a0 in (
        (x1 - r, y0 + r, -90),
        (x1 - r, y1 - r, 0),
        (x0 + r, y1 - r, 90),
        (x0 + r, y0 + r, 180),
    ):
        pts += _circle_pts(cx, cy, r, n, a0, a0 + 90)
    return pts


def _bubble():
    body = _rounded_rect(6, 10, 94, 70, 14)
    # a tail at the bottom left, inserted between the bottom corners
    i = next(k for k, (x, y) in enumerate(body) if y >= 69.9 and x < 50)
    tail = [[34, 70], [20, 92], [44, 70]]
    return body[:i] + tail[::-1] + body[i:]


STICKERS: dict[str, dict] = {
    "check": {
        "label": "Check mark",
        "shapes": [{"line": [[16, 54], [40, 78], [86, 24]], "stroke": "#2e9e4f", "width": 16}],
    },
    "cross": {
        "label": "Cross",
        "shapes": [
            {"line": [[20, 20], [80, 80]], "stroke": "#d43c3c", "width": 16},
            {"line": [[80, 20], [20, 80]], "stroke": "#d43c3c", "width": 16},
        ],
    },
    "star": {
        "label": "Star",
        "shapes": [{"poly": _star(), "fill": "#ffc83d", "stroke": "#b8860b", "width": 4}],
    },
    "heart": {
        "label": "Heart",
        "shapes": [{"poly": _heart(), "fill": "#e63b5a", "stroke": "#a3203a", "width": 3}],
    },
    "bubble": {
        "label": "Speech bubble",
        "shapes": [{"poly": _bubble(), "fill": "#ffffff", "stroke": "#222222", "width": 4}],
    },
    "warning": {
        "label": "Exclamation",
        "shapes": [
            {"ellipse": [4, 4, 96, 96], "fill": "#e0452f"},
            {"poly": [[44, 18], [56, 18], [54, 62], [46, 62]], "fill": "#ffffff"},
            {"ellipse": [43, 70, 57, 84], "fill": "#ffffff"},
        ],
    },
    "info": {
        "label": "Info",
        "shapes": [
            {"ellipse": [4, 4, 96, 96], "fill": "#2f6fe0"},
            {"ellipse": [43, 17, 57, 31], "fill": "#ffffff"},
            {"poly": [[44, 40], [56, 40], [56, 82], [44, 82]], "fill": "#ffffff"},
        ],
    },
    "arrow": {
        "label": "Arrow",
        "shapes": [
            {
                "poly": [[6, 40], [58, 40], [58, 18], [94, 50], [58, 82], [58, 60], [6, 60]],
                "fill": "front",
            }
        ],
    },
    "ring": {
        "label": "Circle (highlight)",
        "shapes": [{"ellipse": [8, 14, 92, 86], "stroke": "front", "width": 7}],
    },
    "smiley": {
        "label": "Smiley",
        "shapes": [
            {"ellipse": [4, 4, 96, 96], "fill": "#ffd23f", "stroke": "#8a6d00", "width": 3},
            {"ellipse": [31, 30, 41, 44], "fill": "#222222"},
            {"ellipse": [59, 30, 69, 44], "fill": "#222222"},
            {"line": _circle_pts(50, 50, 26, 24, 25, 155), "stroke": "#222222", "width": 6},
        ],
    },
    "pin": {
        "label": "Map pin",
        "shapes": [
            {
                "poly": _circle_pts(50, 38, 30, 40, 150, 390) + [[50, 96]],
                "fill": "#d43c3c",
                "stroke": "#8f1f1f",
                "width": 3,
            },
            {"ellipse": [38, 26, 62, 50], "fill": "#ffffff"},
        ],
    },
}


def _colour(c, front, back) -> tuple[int, int, int, int]:
    if c == "front":
        return tuple(front)
    if c == "back":
        return tuple(back)
    v = c.lstrip("#")
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)


def render_sticker(
    name: str, size: int, front=(0, 0, 0, 255), back=(255, 255, 255, 255)
) -> Image.Image:
    """Sticker ``name`` drawn ``size`` × ``size`` px, anti-aliased."""
    s = SUPERSAMPLE
    k = size * s / 100
    big = Image.new("RGBA", (size * s, size * s))
    draw = ImageDraw.Draw(big)
    for shape in STICKERS[name]["shapes"]:
        fill = _colour(shape["fill"], front, back) if "fill" in shape else None
        stroke = _colour(shape["stroke"], front, back) if "stroke" in shape else None
        width = max(1, round(shape.get("width", 0) * k)) if stroke else 0
        if "poly" in shape:
            pts = [(x * k, y * k) for x, y in shape["poly"]]
            draw.polygon(pts, fill=fill, outline=stroke, width=width)
        elif "ellipse" in shape:
            x0, y0, x1, y1 = (v * k for v in shape["ellipse"])
            draw.ellipse((x0, y0, x1, y1), fill=fill, outline=stroke, width=width)
        elif "line" in shape:
            pts = [(x * k, y * k) for x, y in shape["line"]]
            draw.line(pts, fill=stroke, width=width, joint="curve")
            r = width / 2
            for cx, cy in (pts[0], pts[-1]):  # round ends
                draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=stroke)
    return big.convert("RGBa").reduce(s).convert("RGBA")  # premultiplied: no dark fringe


def as_json() -> str:
    return json.dumps(
        {
            "_note": "Built-in stickers (CC0), generated from src/umc/editor/stickers.py.",
            **STICKERS,
        },
        indent=1,
    )


if __name__ == "__main__":
    print(as_json())
