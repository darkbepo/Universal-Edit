"""SVG path data (the ``d`` attribute): parse, write, transform, bounds (``pathdata.ts``).

Parsed paths are lists of absolute segments, tuples whose first item is the command:
``("M", x, y)``, ``("L", x, y)``, ``("C", x1, y1, x2, y2, x, y)``, ``("Q", x1, y1, x, y)``,
``("A", rx, ry, rotation, large, sweep, x, y)`` and ``("Z",)``. H / V become L, S becomes C and
T becomes Q (with the reflected control point), relative commands become absolute.
"""

from __future__ import annotations

import math
import re

from umc.vectoredit.geom import Box, Matrix, num, points_box

Seg = tuple

_TOKEN = re.compile(
    r"[ \t\r\n\f]*,?[ \t\r\n\f]*([MmZzLlHhVvCcSsQqTtAa]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
)
_FLAG = re.compile(r"[ \t\r\n\f]*,?[ \t\r\n\f]*([01])")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


class _Reader:
    def __init__(self, d: str) -> None:
        self.d, self.pos = d, 0

    def peek_command(self) -> str | None:
        hit = _TOKEN.match(self.d, self.pos)
        if hit is None or not hit.group(1).isalpha():
            return None
        return hit.group(1)

    def command(self) -> str:
        hit = _TOKEN.match(self.d, self.pos)
        assert hit is not None
        self.pos = hit.end()
        return hit.group(1)

    def number(self) -> float | None:
        hit = _TOKEN.match(self.d, self.pos)
        if hit is None or hit.group(1).isalpha():
            return None
        self.pos = hit.end()
        return float(hit.group(1))

    def flag(self) -> float | None:
        hit = _FLAG.match(self.d, self.pos)
        if hit is None:
            return None
        self.pos = hit.end()
        return float(hit.group(1))

    def at_end(self) -> bool:
        return re.fullmatch(r"[ \t\r\n\f]*", self.d[self.pos :]) is not None


def parse(d: str) -> list[Seg]:
    """Parse ``d``. Like browsers, a mistake ends the path there: what came before is kept."""
    r = _Reader(d)
    out: list[Seg] = []
    x = y = sx = sy = 0.0
    last_c: tuple[float, float] | None = None  # second control point of the last C / S
    last_q: tuple[float, float] | None = None  # control point of the last Q / T
    cmd: str | None = None
    while not r.at_end():
        nxt = r.peek_command()
        if nxt is not None:
            cmd = r.command()
        elif cmd is None or cmd in "Zz":
            break  # numbers with no command (or after Z): stop, as browsers do
        upper, rel = cmd.upper(), cmd.islower()
        if upper == "Z":
            out.append(("Z",))
            x, y = sx, sy
            last_c = last_q = None
            continue
        args: list[float] = []
        for k in range(_ARGS[upper]):
            v = r.flag() if upper == "A" and k in (3, 4) else r.number()
            if v is None:
                return out
            args.append(v)
        if not out and upper != "M":
            return out  # a path must start with a move
        ox, oy = (x, y) if rel else (0.0, 0.0)
        new_c = new_q = None
        if upper == "M":
            x, y = args[0] + ox, args[1] + oy
            sx, sy = x, y
            out.append(("M", x, y))
            cmd = "l" if rel else "L"  # extra pairs after a move are lines
        elif upper == "L":
            x, y = args[0] + ox, args[1] + oy
            out.append(("L", x, y))
        elif upper == "H":
            x = args[0] + ox
            out.append(("L", x, y))
        elif upper == "V":
            y = args[0] + (y if rel else 0.0)
            out.append(("L", x, y))
        elif upper in ("C", "S"):
            if upper == "C":
                x1, y1 = args[0] + ox, args[1] + oy
                rest = args[2:]
            else:
                x1, y1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                rest = args
            x2, y2 = rest[0] + ox, rest[1] + oy
            x, y = rest[2] + ox, rest[3] + oy
            out.append(("C", x1, y1, x2, y2, x, y))
            new_c = (x2, y2)
        elif upper in ("Q", "T"):
            if upper == "Q":
                x1, y1 = args[0] + ox, args[1] + oy
                end = args[2:]
            else:
                x1, y1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                end = args
            x, y = end[0] + ox, end[1] + oy
            out.append(("Q", x1, y1, x, y))
            new_q = (x1, y1)
        else:  # A
            rx, ry, rot, large, sweep = abs(args[0]), abs(args[1]), args[2], args[3], args[4]
            x, y = args[5] + ox, args[6] + oy
            out.append(("A", rx, ry, rot, large, sweep, x, y))
        last_c, last_q = new_c, new_q
    return out


def write(segs: list[Seg]) -> str:
    """Absolute commands separated by spaces, numbers through :func:`num`."""
    parts = []
    for s in segs:
        if s[0] == "Z":
            parts.append("Z")
        elif s[0] == "A":
            rx, ry, rot, large, sweep, x, y = s[1:]
            parts.append(
                f"A{num(rx)} {num(ry)} {num(rot)} {int(large)} {int(sweep)} {num(x)} {num(y)}"
            )
        else:
            parts.append(s[0] + " ".join(num(v) for v in s[1:]))
    return " ".join(parts)


def arc_to_cubics(
    x0: float,
    y0: float,
    rx: float,
    ry: float,
    rot: float,
    large: float,
    sweep: float,
    x: float,
    y: float,
) -> list[Seg]:
    """An SVG arc as cubic curves (at most 90° each), following the SVG implementation notes."""
    if (x0, y0) == (x, y):
        return []
    if rx == 0 or ry == 0:
        return [("C", x0, y0, x, y, x, y)]
    phi = math.radians(rot % 360)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x) / 2, (y0 - y) / 2
    x1p, y1p = cos * dx + sin * dy, -sin * dx + cos * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        k = math.sqrt(lam)
        rx, ry = rx * k, ry * k
    num_ = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num_ / den)) if den else 0.0
    if bool(large) == bool(sweep):
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos * cxp - sin * cyp + (x0 + x) / 2
    cy = sin * cxp + cos * cyp + (y0 + y) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    step = dt / n
    alpha = 4 / 3 * math.tan(step / 4)
    out: list[Seg] = []

    def point(t: float) -> tuple[float, float, float, float]:
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        tx, ty = -rx * math.sin(t), ry * math.cos(t)
        return (
            cos * ex - sin * ey + cx,
            sin * ex + cos * ey + cy,
            cos * tx - sin * ty,
            sin * tx + cos * ty,
        )

    px, py, pdx, pdy = point(t1)
    px, py = x0, y0
    for k in range(n):
        t = t1 + step * (k + 1)
        qx, qy, qdx, qdy = point(t)
        if k == n - 1:
            qx, qy = x, y
        out.append(
            ("C", px + alpha * pdx, py + alpha * pdy, qx - alpha * qdx, qy - alpha * qdy, qx, qy)
        )
        px, py, pdx, pdy = qx, qy, qdx, qdy
    return out


def without_arcs(segs: list[Seg]) -> list[Seg]:
    """The same path with every arc replaced by cubics."""
    out: list[Seg] = []
    x = y = sx = sy = 0.0
    for s in segs:
        if s[0] == "A":
            out += arc_to_cubics(x, y, *s[1:])
        else:
            out.append(s)
        x, y, sx, sy = _advance(s, x, y, sx, sy)
    return out


def _advance(s: Seg, x: float, y: float, sx: float, sy: float) -> tuple[float, ...]:
    if s[0] == "Z":
        return sx, sy, sx, sy
    if s[0] == "M":
        return s[1], s[2], s[1], s[2]
    return s[-2], s[-1], sx, sy


def transform(segs: list[Seg], m: Matrix) -> list[Seg]:
    """Every point through ``m``. Arcs stay arcs under a move; otherwise they become cubics."""
    if not m.is_translate():
        segs = without_arcs(segs)
    out: list[Seg] = []
    for s in segs:
        if s[0] == "Z":
            out.append(s)
        elif s[0] == "A":
            out.append((*s[:6], *m.apply(s[6], s[7])))
        else:
            pts = [m.apply(s[k], s[k + 1]) for k in range(1, len(s), 2)]
            out.append((s[0], *(v for p in pts for v in p)))
    return out


def _cubic_extrema(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    """Parameters in (0, 1) where one coordinate of a cubic turns."""
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    if abs(a) < 1e-12:
        return [-c / b] if abs(b) > 1e-12 and 0 < -c / b < 1 else []
    disc = b * b - 4 * a * c
    if disc < 0:
        return []
    r = math.sqrt(disc)
    return [t for t in ((-b + r) / (2 * a), (-b - r) / (2 * a)) if 0 < t < 1]


def _cubic_at(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    u = 1 - t
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3


def bounds(segs: list[Seg]) -> Box | None:
    """The path's tight bounding box (curve extrema, not control points); None when empty."""
    pts: list[tuple[float, float]] = []
    x = y = sx = sy = 0.0
    for s in without_arcs(segs):
        if s[0] in ("M", "L"):
            pts.append((s[1], s[2]))
        elif s[0] == "Q":
            c1x, c1y = x + 2 / 3 * (s[1] - x), y + 2 / 3 * (s[2] - y)
            c2x, c2y = s[3] + 2 / 3 * (s[1] - s[3]), s[4] + 2 / 3 * (s[2] - s[4])
            pts += _cubic_points(x, y, c1x, c1y, c2x, c2y, s[3], s[4])
        elif s[0] == "C":
            pts += _cubic_points(x, y, *s[1:])
        x, y, sx, sy = _advance(s, x, y, sx, sy)
    return points_box(pts) if pts else None


def _cubic_points(
    x0: float, y0: float, x1: float, y1: float, x2: float, y2: float, x3: float, y3: float
) -> list[tuple[float, float]]:
    ts = [1.0] + _cubic_extrema(x0, x1, x2, x3) + _cubic_extrema(y0, y1, y2, y3)
    return [(_cubic_at(x0, x1, x2, x3, t), _cubic_at(y0, y1, y2, y3, t)) for t in ts]


# ------------------------------------------------------------------------------- nodes

# A point the Node tool can drag: (segment index, slot, x, y); slot "end" = the segment's end
# point (a node), "c1" / "c2" = a curve's first / second control point (a Q's one is "c1").
Point = tuple[int, str, float, float]


def points(segs: list[Seg]) -> list[Point]:
    out: list[Point] = []
    for i, s in enumerate(segs):
        if s[0] == "C":
            out += [(i, "c1", s[1], s[2]), (i, "c2", s[3], s[4]), (i, "end", s[5], s[6])]
        elif s[0] == "Q":
            out += [(i, "c1", s[1], s[2]), (i, "end", s[3], s[4])]
        elif s[0] != "Z":
            out.append((i, "end", s[-2], s[-1]))
    return out


def _with_point(s: Seg, slot: str, x: float, y: float) -> Seg:
    v = list(s[1:])
    if slot == "end":
        v[-2], v[-1] = x, y
    elif slot == "c1":
        v[0], v[1] = x, y
    else:  # c2 (cubics only)
        v[2], v[3] = x, y
    return (s[0], *v)


def move_point(segs: list[Seg], index: int, slot: str, x: float, y: float) -> list[Seg]:
    """``segs`` with one point moved. A node takes its two curve handles along (the incoming
    one on its own segment, the outgoing one on the next), so the curve keeps its shape."""
    out = list(segs)
    s = out[index]
    if slot != "end":
        if s[0] not in ("C", "Q") or (slot == "c2" and s[0] != "C"):
            raise ValueError("no such handle")
        out[index] = _with_point(s, slot, x, y)
        return out
    if s[0] == "Z":
        raise ValueError("no such point")
    dx, dy = x - s[-2], y - s[-1]
    out[index] = _with_point(s, "end", x, y)
    if s[0] == "C":
        out[index] = _with_point(out[index], "c2", s[3] + dx, s[4] + dy)
    if index + 1 < len(out) and out[index + 1][0] == "C":
        n = out[index + 1]
        out[index + 1] = _with_point(n, "c1", n[1] + dx, n[2] + dy)
    return out


def delete_point(segs: list[Seg], index: int) -> list[Seg]:
    """Remove the node that ends segment ``index``; the path then goes straight from the node
    before to the one after (a curve keeps its outgoing handle). Fewer than two nodes left in
    a part = that part is removed."""
    s = segs[index]
    if s[0] == "Z":
        raise ValueError("no such point")
    out = list(segs)
    if s[0] == "M":
        nxt = index + 1
        if nxt < len(out) and out[nxt][0] not in ("M", "Z"):
            out[nxt] = ("M", out[nxt][-2], out[nxt][-1])
        del out[index]
    else:
        del out[index]
    # drop parts that are only a move (and its Z)
    cleaned: list[Seg] = []
    for i, seg in enumerate(out):
        if seg[0] == "M" and (i + 1 >= len(out) or out[i + 1][0] in ("M", "Z")):
            continue
        if seg[0] == "Z" and (not cleaned or cleaned[-1][0] in ("M", "Z")):
            continue
        cleaned.append(seg)
    return cleaned


def split(segs: list[Seg], index: int, t: float = 0.5) -> list[Seg]:
    """Add a node inside segment ``index`` (at ``t``), keeping the curve's shape; an arc is
    turned into curves first."""
    s = segs[index]
    if s[0] in ("M", "Z"):
        raise ValueError("no segment there")
    x0 = y0 = sx = sy = 0.0
    for seg in segs[:index]:
        x0, y0, sx, sy = _advance(seg, x0, y0, sx, sy)
    if s[0] == "L":
        mid = ("L", x0 + (s[1] - x0) * t, y0 + (s[2] - y0) * t)
        return [*segs[:index], mid, s, *segs[index + 1 :]]
    if s[0] == "A":
        return [*segs[:index], *arc_to_cubics(x0, y0, *s[1:]), *segs[index + 1 :]]
    if s[0] == "Q":
        (x1, y1), (x2, y2) = (s[1], s[2]), (s[3], s[4])
        a = (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        b = (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
        m = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return [*segs[:index], ("Q", *a, *m), ("Q", *b, x2, y2), *segs[index + 1 :]]
    p = [(x0, y0), (s[1], s[2]), (s[3], s[4]), (s[5], s[6])]

    def lerp(u, v):
        return (u[0] + (v[0] - u[0]) * t, u[1] + (v[1] - u[1]) * t)

    a, b, c = lerp(p[0], p[1]), lerp(p[1], p[2]), lerp(p[2], p[3])
    d, e = lerp(a, b), lerp(b, c)
    f = lerp(d, e)
    return [*segs[:index], ("C", *a, *d, *f), ("C", *e, *c, *p[3]), *segs[index + 1 :]]
