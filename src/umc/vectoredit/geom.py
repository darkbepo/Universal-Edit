"""Numbers, 2D affine matrices and boxes — the same results in both apps (``geom.ts``)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


def num(x: float) -> str:
    """At most 3 decimals, no trailing zeros, no "-0": the only way the model writes numbers.

    Rounds half up on the thousandths (``floor(x * 1000 + 0.5)``) in both apps, and builds the
    text from integers, so Python and JavaScript always write the same characters.
    """
    if not math.isfinite(x):
        raise ValueError(f"not a finite number: {x}")
    k = math.floor(x * 1000 + 0.5)
    sign = "-" if k < 0 else ""
    k = abs(k)
    whole, frac = divmod(k, 1000)
    if frac == 0:
        return "0" if whole == 0 else f"{sign}{whole}"
    return f"{sign}{whole}." + f"{frac:03d}".rstrip("0")


@dataclass(frozen=True)
class Matrix:
    """``x' = a x + c y + e``, ``y' = b x + d y + f`` (SVG's ``matrix(a b c d e f)``)."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def __matmul__(self, o: Matrix) -> Matrix:
        """``self @ o`` applies ``o`` first, then ``self`` (SVG lists read left to right)."""
        return Matrix(
            self.a * o.a + self.c * o.b,
            self.b * o.a + self.d * o.b,
            self.a * o.c + self.c * o.d,
            self.b * o.c + self.d * o.d,
            self.a * o.e + self.c * o.f + self.e,
            self.b * o.e + self.d * o.f + self.f,
        )

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f

    def inverse(self) -> Matrix:
        det = self.a * self.d - self.b * self.c
        if det == 0:
            raise ValueError("the transform squashes everything flat")
        return Matrix(
            self.d / det,
            -self.b / det,
            -self.c / det,
            self.a / det,
            (self.c * self.f - self.d * self.e) / det,
            (self.b * self.e - self.a * self.f) / det,
        )

    def is_identity(self) -> bool:
        return self == IDENTITY

    def is_translate(self) -> bool:
        return (self.a, self.b, self.c, self.d) == (1, 0, 0, 1)


IDENTITY = Matrix()


def translate(tx: float, ty: float = 0.0) -> Matrix:
    return Matrix(1, 0, 0, 1, tx, ty)


def scale(sx: float, sy: float | None = None) -> Matrix:
    return Matrix(sx, 0, 0, sx if sy is None else sy, 0, 0)


def rotate(deg: float, cx: float = 0.0, cy: float = 0.0) -> Matrix:
    r = math.radians(deg)
    cos, sin = math.cos(r), math.sin(r)
    turn = Matrix(cos, sin, -sin, cos, 0, 0)
    if cx == 0 and cy == 0:
        return turn
    return translate(cx, cy) @ turn @ translate(-cx, -cy)


_NUMBER = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_FUNC = re.compile(
    r"[ \t\r\n\f]*(matrix|translate|scale|rotate|skewX|skewY)[ \t\r\n\f]*\(([^)]*)\)[ \t\r\n\f]*,?"
)
_ARGS = re.compile(_NUMBER)
_ARITY = {
    "matrix": (6,),
    "translate": (1, 2),
    "scale": (1, 2),
    "rotate": (1, 3),
    "skewX": (1,),
    "skewY": (1,),
}


def parse_transform(text: str) -> Matrix:
    """An SVG ``transform`` list → one matrix. Raises ValueError on anything malformed."""
    m = IDENTITY
    pos, text = 0, text.strip(" \t\r\n\f")
    while pos < len(text):
        hit = _FUNC.match(text, pos)
        if hit is None:
            raise ValueError(f"bad transform: {text!r}")
        name, inner = hit.group(1), hit.group(2)
        args = [float(v) for v in _ARGS.findall(inner)]
        leftover = _ARGS.sub("", inner).replace(",", "").strip(" \t\r\n\f")
        if leftover or len(args) not in _ARITY[name]:
            raise ValueError(f"bad transform: {text!r}")
        m = m @ _one(name, args)
        pos = hit.end()
    return m


def _one(name: str, v: list[float]) -> Matrix:
    if name == "matrix":
        return Matrix(*v)
    if name == "translate":
        return translate(v[0], v[1] if len(v) > 1 else 0.0)
    if name == "scale":
        return scale(v[0], v[1] if len(v) > 1 else None)
    if name == "rotate":
        return rotate(v[0], v[1], v[2]) if len(v) == 3 else rotate(v[0])
    t = math.tan(math.radians(v[0]))
    return Matrix(1, 0, t, 1, 0, 0) if name == "skewX" else Matrix(1, t, 0, 1, 0, 0)


def format_transform(m: Matrix) -> str:
    """The shortest of "", ``translate(…)``, ``scale(…)`` or ``matrix(…)`` that writes ``m``."""
    parts = [num(v) for v in (m.a, m.b, m.c, m.d, m.e, m.f)]
    a, b, c, d, e, f = parts
    if (a, b, c, d) == ("1", "0", "0", "1"):
        if (e, f) == ("0", "0"):
            return ""
        return f"translate({e})" if f == "0" else f"translate({e} {f})"
    if (b, c, e, f) == ("0", "0", "0", "0"):
        return f"scale({a})" if a == d else f"scale({a} {d})"
    return f"matrix({' '.join(parts)})"


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def union(self, o: Box | None) -> Box:
        if o is None:
            return self
        return Box(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    def transformed(self, m: Matrix) -> Box:
        pts = [
            m.apply(x, y)
            for x, y in (
                (self.x0, self.y0),
                (self.x1, self.y0),
                (self.x0, self.y1),
                (self.x1, self.y1),
            )
        ]
        return points_box(pts)


def points_box(pts: list[tuple[float, float]]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return Box(min(xs), min(ys), max(xs), max(ys))
