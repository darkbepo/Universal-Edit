"""A picture turned into a 3D logo for printing (Qt-free; port: web/src/editor3d/logo.ts).

Steps: ``mask`` (which pixels belong to the logo) → ``trace`` (closed outlines along the pixel
edges, cut at the corners like marching squares, simplified) → ``shape`` (millimetres, outer
rings with their holes, specks left out) → ``extrude`` / ``badge`` (closed, watertight meshes).
``place`` puts a logo onto a flat face of a model (see ``pick.flat_face``).

Both apps run the same arithmetic in the same order, so ``tests/fixtures/logo3d_cases.json``
holds results both test suites check. Triangulation is earcut (desktop ``mapbox_earcut``, web
``earcut``; the same algorithm), whose triangle order may differ: cases compare counts, areas
and volumes, never indices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

MAX_SIDE = 1024  # pictures are scaled so the longer side is at most this (pages do it)
MAX_EDGES = 2_000_000  # more boundary than this = noise, not a logo
DETAIL = {"smooth": 1.5, "normal": 0.8, "fine": 0.5}  # simplification tolerance, pixels
SPECK_FRACTION = 0.005  # loops smaller than this share of the logo's area are left out
MIN_FEATURE_MM = 0.4  # ... and loops smaller than a 0.4 mm square (a nozzle's width)
SADDLE_NUDGE = 0.05  # px: corners where two parts touch are moved apart this much
SINK_MM = 0.2  # how far a placed logo goes into the surface, so slicers merge them
HOLE_RADIUS_MM = 2.5  # keychain hole
CORNER_SEGMENTS = 8  # per rounded plate corner
HOLE_SEGMENTS = 32

Point = tuple[float, float]
Ring = list[Point]


class LogoError(ValueError):
    """A plain-language reason a picture can't become a logo."""


# ----------------------------------------------------------------------------- mask
def luma(r: int, g: int, b: int) -> int:
    return (r * 299 + g * 587 + b * 114) // 1000


def has_transparency(rgba: bytes, w: int, h: int) -> bool:
    a = np.frombuffer(rgba, np.uint8, w * h * 4)[3::4]
    return bool((a < 255).any())


def default_mode(rgba: bytes, w: int, h: int) -> str:
    return "alpha" if has_transparency(rgba, w, h) else "dark"


def mask(
    rgba: bytes, w: int, h: int, mode: str = "alpha", threshold: int = 128, invert: bool = False
) -> np.ndarray:
    """(h, w) uint8 0 / 1. ``alpha``: opaque parts (alpha ≥ 128); ``dark`` / ``light``: opaque
    pixels darker / lighter than ``threshold`` (integer luma, the same in both apps)."""
    px = np.frombuffer(rgba, np.uint8, w * h * 4).reshape(h, w, 4).astype(np.int32)
    opaque = px[:, :, 3] >= 128
    if mode == "alpha":
        m = opaque
    else:
        y = (px[:, :, 0] * 299 + px[:, :, 1] * 587 + px[:, :, 2] * 114) // 1000
        m = opaque & ((y < threshold) if mode == "dark" else (y > threshold))
    if invert:
        m = ~m
    return m.astype(np.uint8)


# ----------------------------------------------------------------------------- trace
# directions on the pixel grid (image coordinates, y down): east, south, west, north
_DX = (1, 0, -1, 0)
_DY = (0, 1, 0, -1)


def _edges(m: np.ndarray) -> dict[tuple[int, int], int]:
    """Boundary edges between a logo pixel and a non-logo one, as (start corner, direction),
    walking clockwise around the logo pixel on screen (= counter-clockwise once y points up),
    in a fixed order: row by row, then top / right / bottom / left."""
    h, w = m.shape
    p = np.zeros((h + 2, w + 2), np.uint8)
    p[1:-1, 1:-1] = m
    filled = p[1:-1, 1:-1] == 1
    sides = (
        filled & (p[:-2, 1:-1] == 0),  # top: (x, y) → east
        filled & (p[1:-1, 2:] == 0),  # right: (x+1, y) → south
        filled & (p[2:, 1:-1] == 0),  # bottom: (x+1, y+1) → west
        filled & (p[1:-1, :-2] == 0),  # left: (x, y+1) → north
    )
    total = int(sum(int(s.sum()) for s in sides))
    if total > MAX_EDGES:
        raise LogoError("The picture is too busy to make a logo from — try a simpler one.")
    start = ((0, 0), (1, 0), (1, 1), (0, 1))
    rows: list[tuple[int, int, int, int]] = []  # (y, x, side) for the order, + key
    for side, s in enumerate(sides):
        ys, xs = np.nonzero(s)
        for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
            rows.append((y, x, side, 0))
    rows.sort()
    out: dict[tuple[int, int], int] = {}
    for y, x, side, _ in rows:
        sx, sy = start[side]
        out[((y + sy) * (w + 1) + x + sx, side)] = len(out)
    return out


def _loops(m: np.ndarray) -> tuple[list[list[tuple[int, int]]], set[int]]:
    """Closed loops of pixel corners (x, y), image coordinates, and the corners where two logo
    pixels touch only diagonally (the walk turns right there, so they stay separate shapes)."""
    h, w = m.shape
    edges = _edges(m)
    starts: dict[int, int] = {}
    for v, _ in edges:
        starts[v] = starts.get(v, 0) + 1
    saddles = {v for v, n in starts.items() if n > 1}
    used: set[tuple[int, int]] = set()
    loops = []
    for key in edges:  # dicts keep insertion order = the fixed order above
        if key in used:
            continue
        loop = []
        v, d = key
        while (v, d) not in used:
            used.add((v, d))
            x, y = v % (w + 1), v // (w + 1)
            loop.append((x, y))
            nv = (y + _DY[d]) * (w + 1) + x + _DX[d]
            for nd in ((d + 1) % 4, d, (d + 3) % 4):  # right, straight, left
                if (nv, nd) in edges:
                    v, d = nv, nd
                    break
            else:  # pragma: no cover - boundaries always close
                break
        loops.append(loop)
    return loops, saddles


def _simplify(points: list[Point], tol: float) -> list[Point]:
    """Douglas–Peucker on a closed loop: split at the first point and the one farthest from
    it, simplify both halves (iteratively, same order in both apps)."""
    n = len(points)
    if n < 4:
        return points
    x0, y0 = points[0]
    far, best = 0, -1.0
    for i in range(1, n):
        dx, dy = points[i][0] - x0, points[i][1] - y0
        dd = dx * dx + dy * dy
        if dd > best:
            far, best = i, dd
    keep = [False] * n
    keep[0] = keep[far] = True
    stack = [(0, far), (far, n)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b % n]
        dx, dy = bx - ax, by - ay
        length = math.sqrt(dx * dx + dy * dy)
        idx, worst = -1, tol
        for i in range(a + 1, b):
            px, py = points[i]
            if length == 0:
                dist = math.sqrt((px - ax) ** 2 + (py - ay) ** 2)
            else:
                dist = abs(dx * (ay - py) - dy * (ax - px)) / length
            if dist > worst:
                idx, worst = i, dist
        if idx >= 0:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(points, keep, strict=True) if k]


def _drop_flat(points: list[Point]) -> list[Point]:
    """Remove repeated points and points exactly on the line of their neighbours (earcut
    would skip them, leaving the walls with a corner the caps don't have)."""
    pts = list(points)
    changed = True
    while changed and len(pts) >= 3:
        changed = False
        out: list[Point] = []
        n = len(pts)
        for i in range(n):
            ax, ay = out[-1] if out else pts[i - 1]
            px, py = pts[i]
            bx, by = pts[(i + 1) % n]
            if (px == ax and py == ay) or (bx - ax) * (py - ay) - (by - ay) * (px - ax) == 0:
                changed = True
                continue
            out.append((px, py))
        pts = out
    return pts


def area(ring: Ring) -> float:
    """Signed area (shoelace): positive = counter-clockwise."""
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2


def trace(m: np.ndarray, detail: str = "normal") -> list[Ring]:
    """Closed outlines in pixel units with y up (outer rings counter-clockwise, holes
    clockwise): the pixel-edge corners where the boundary turns, simplified (a staircase
    becomes a slope, square corners stay square)."""
    h = m.shape[0]
    tol = DETAIL[detail]
    rings = []
    loops, saddles = _loops(m)
    w1 = m.shape[1] + 1
    for loop in loops:
        n = len(loop)
        corners: Ring = []
        for i in range(n - 1, -1, -1):  # backwards: counter-clockwise once y points up
            px, py = loop[i - 1]
            x, y = loop[i]
            nx, ny = loop[(i + 1) % n]
            ix, iy, ox, oy = x - px, y - py, nx - x, ny - y
            if (ix, iy) == (ox, oy):
                continue
            if y * w1 + x in saddles:  # into this part's own pixel: the parts no longer touch
                cx = x + SADDLE_NUDGE * (-iy - oy)
                cy = y + SADDLE_NUDGE * (ix + ox)
                corners.append((cx, h - cy))
            else:
                corners.append((float(x), float(h - y)))
        ring = _drop_flat(_simplify(corners, tol))
        if len(ring) < 3 or area(ring) == 0:  # simplified away: a tiny shape, keep it whole
            ring = _drop_flat(corners)
        if len(ring) >= 3 and area(ring) != 0:
            rings.append(ring)
    return rings


# ----------------------------------------------------------------------------- shape
def inside(pt: Point, ring: Ring) -> bool:
    """Even–odd ray test."""
    x, y = pt
    hit = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            hit = not hit
        j = i
    return hit


@dataclass
class Polygon:
    outer: Ring
    holes: list[Ring] = field(default_factory=list)


@dataclass
class Shape:
    polygons: list[Polygon]
    width: float  # mm
    height: float
    dropped: int = 0  # tiny bits left out

    @property
    def area(self) -> float:
        return sum(area(p.outer) + sum(area(h) for h in p.holes) for p in self.polygons)


def shape(rings: list[Ring], width_mm: float, height_mm: float | None = None) -> Shape:
    """Scale the traced rings to ``width_mm`` (height follows unless given), centre them on
    (0, 0), leave out specks and nest the holes in their outer rings."""
    if not rings:
        raise LogoError("The picture has no clear shape — try another “Shape from” setting.")
    xs = [x for r in rings for x, _ in r]
    ys = [y for r in rings for _, y in r]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise LogoError("The picture has no clear shape — try another “Shape from” setting.")
    sx = width_mm / (x1 - x0)
    sy = sx if height_mm is None else height_mm / (y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    scaled = [[((x - cx) * sx, (y - cy) * sy) for x, y in r] for r in rings]
    total = sum(area(r) for r in scaled if area(r) > 0)
    limit = max(total * SPECK_FRACTION, MIN_FEATURE_MM * MIN_FEATURE_MM)
    outers: list[Ring] = []
    holes: list[Ring] = []
    dropped = 0
    for r in scaled:
        a = area(r)
        if abs(a) < limit:
            dropped += 1
        elif a > 0:
            outers.append(r)
        else:
            holes.append(r)
    if not outers:
        raise LogoError("The logo would be too small to print — make it wider.")
    polygons = [Polygon(o) for o in outers]
    sizes = [area(o) for o in outers]
    for hole in holes:
        best = -1
        for i, o in enumerate(outers):
            if inside(hole[0], o) and (best < 0 or sizes[i] < sizes[best]):
                best = i
        if best < 0:
            dropped += 1
        else:
            polygons[best].holes.append(hole)
    xs = [x for p in polygons for x, _ in p.outer]
    ys = [y for p in polygons for _, y in p.outer]
    return Shape(polygons, max(xs) - min(xs), max(ys) - min(ys), dropped)


def shape_from_picture(
    rgba: bytes,
    w: int,
    h: int,
    width_mm: float,
    mode: str = "alpha",
    threshold: int = 128,
    invert: bool = False,
    detail: str = "normal",
) -> Shape:
    return shape(trace(mask(rgba, w, h, mode, threshold, invert), detail), width_mm)


# ----------------------------------------------------------------------------- meshes
@dataclass
class Mesh:
    vertices: np.ndarray  # (V, 3) float64, mm
    faces: np.ndarray  # (F, 3) int64, counter-clockwise seen from outside

    @property
    def volume(self) -> float:
        t = self.vertices[self.faces]
        return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6)


def _triangulate(poly: Polygon) -> list[tuple[int, int, int]]:
    """Cap triangles (indices into outer + holes, in that order), counter-clockwise."""
    import mapbox_earcut

    rings = [poly.outer, *poly.holes]
    pts = np.array([p for r in rings for p in r], dtype=np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = mapbox_earcut.triangulate_float64(pts, ends).reshape(-1, 3).tolist()
    out = []
    for a, b, c in idx:
        (ax, ay), (bx, by), (cx, cy) = pts[a], pts[b], pts[c]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
            b, c = c, b
        out.append((a, b, c))
    return out


def extrude(polygons: list[Polygon], bottom: float, top: float) -> Mesh:
    """A closed solid: each polygon from z = ``bottom`` to ``top``."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for poly in polygons:
        rings = [poly.outer, *poly.holes]
        n = sum(len(r) for r in rings)
        base = len(verts)
        for z in (bottom, top):
            verts.extend((x, y, z) for r in rings for x, y in r)
        for a, b, c in _triangulate(poly):
            faces.append((base + n + a, base + n + b, base + n + c))  # top, facing up
            faces.append((base + a, base + c, base + b))  # bottom, facing down
        start = 0
        for r in rings:
            k = len(r)
            for i in range(k):
                a, b = base + start + i, base + start + (i + 1) % k
                faces.append((a, b, b + n))
                faces.append((a, b + n, a + n))
            start += k
    return Mesh(np.array(verts, dtype=np.float64).reshape(-1, 3), np.array(faces, np.int64))


def logo_mesh(s: Shape, thickness: float, bottom: float = 0.0) -> Mesh:
    return extrude(s.polygons, bottom, bottom + thickness)


def _circle(cx: float, cy: float, r: float, segments: int, clockwise: bool) -> Ring:
    pts = [
        (
            cx + r * math.cos(2 * math.pi * i / segments),
            cy + r * math.sin(2 * math.pi * i / segments),
        )
        for i in range(segments)
    ]
    return pts[::-1] if clockwise else pts


def plate_outline(
    s: Shape, margin: float, kind: str = "rectangle", hole: bool = False
) -> tuple[Ring, Ring | None]:
    """The base plate around the logo (counter-clockwise), and the keychain hole (clockwise)
    on a tab at the left, or None."""
    x0, x1 = -s.width / 2 - margin, s.width / 2 + margin
    y0, y1 = -s.height / 2 - margin, s.height / 2 + margin
    ring_hole = None
    if hole:
        x0 -= 2 * HOLE_RADIUS_MM + margin
        ring_hole = _circle(x0 + margin + HOLE_RADIUS_MM, 0.0, HOLE_RADIUS_MM, HOLE_SEGMENTS, True)
    if kind == "rounded":
        r = min(max(margin, 1.0), (x1 - x0) / 2, (y1 - y0) / 2)
        outer: Ring = []
        corners = (
            (x1 - r, y0 + r, -90),
            (x1 - r, y1 - r, 0),
            (x0 + r, y1 - r, 90),
            (x0 + r, y0 + r, 180),
        )
        for cx, cy, start in corners:
            for i in range(CORNER_SEGMENTS + 1):
                t = math.radians(start + 90 * i / CORNER_SEGMENTS)
                outer.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    else:
        outer = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return outer, ring_hole


def badge(
    s: Shape,
    thickness: float,
    plate: str = "none",
    margin: float = 3.0,
    plate_thickness: float = 2.0,
    hole: bool = False,
) -> list[Mesh]:
    """A stand-alone logo: the logo alone, or on a base plate (then the logo stands
    ``SINK_MM`` into the plate's top so the slicer joins them). One mesh per part."""
    if plate == "none":
        return [logo_mesh(s, thickness)]
    outer, ring_hole = plate_outline(s, margin, plate, hole)
    plate_mesh = extrude([Polygon(outer, [ring_hole] if ring_hole else [])], 0.0, plate_thickness)
    bottom = plate_thickness - min(SINK_MM, plate_thickness / 2)
    return [plate_mesh, logo_mesh(s, thickness + (plate_thickness - bottom), bottom)]


# ----------------------------------------------------------------------------- placing
def frame(normal, rotation_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Logo axes on a face: x (the logo's right), y (its up), n (out of the face). On a level
    face x = world X; otherwise x is level along the face, so the logo stands upright."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    if abs(n[2]) > 0.999:
        x = np.array([1.0, 0.0, 0.0])
    else:
        x = np.cross([0.0, 0.0, 1.0], n)
        x = x / np.linalg.norm(x)
    y = np.cross(n, x)
    t = math.radians(rotation_deg)
    c, s_ = math.cos(t), math.sin(t)
    return x * c + y * s_, y * c - x * s_, n


def placement(point, normal, rotation_deg: float = 0.0, sink: float = SINK_MM) -> np.ndarray:
    """4×4 matrix taking logo coordinates (z = 0 = its bottom) onto the face at ``point``."""
    x, y, n = frame(normal, rotation_deg)
    m = np.identity(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = x, y, n
    m[:3, 3] = np.asarray(point, float) - n * sink
    return m


def transform(mesh: Mesh, m: np.ndarray) -> Mesh:
    v = mesh.vertices @ m[:3, :3].T + m[:3, 3]
    return Mesh(v, mesh.faces.copy())


def placed_logo(s: Shape, thickness: float, point, normal, rotation_deg=0.0, sink=SINK_MM) -> Mesh:
    """The logo raised ``thickness`` above the face (and ``sink`` into it)."""
    return transform(logo_mesh(s, thickness + sink), placement(point, normal, rotation_deg, sink))


def footprint(s: Shape, point, normal, rotation_deg: float = 0.0) -> np.ndarray:
    """(N, 3) the outer rings' points on the face (for the "hangs over the edge" check)."""
    x, y, _ = frame(normal, rotation_deg)
    pts = np.array([p for poly in s.polygons for p in poly.outer], float)
    return np.asarray(point, float) + pts[:, :1] * x + pts[:, 1:2] * y
