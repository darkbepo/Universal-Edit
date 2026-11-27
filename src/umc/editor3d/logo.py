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
MIN_WALL_MM = 1.0  # plate left all round the keychain hole
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
    return [ring for ring, _raw in trace_both(m, detail)]


def trace_both(m: np.ndarray, detail: str = "normal") -> list[tuple[Ring, Ring]]:
    """``trace``, with each ring's unsimplified corners next to it (``shape`` falls back to
    them where simplifying made a hole touch its outline)."""
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
        raw = _drop_flat(corners)
        ring = _drop_flat(_simplify(corners, tol))
        if len(ring) < 3 or area(ring) == 0:  # simplified away: a tiny shape, keep it whole
            ring = raw
        if len(ring) >= 3 and area(ring) != 0:
            rings.append((ring, raw))
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
    # cap triangles, made once (``prepare``): moving / turning a logo only transforms it
    caps: list[tuple[int, int, int]] | None = field(default=None, compare=False, repr=False)
    # the unsimplified rings, used if earcut can't triangulate the simplified ones cleanly
    raw: Polygon | None = field(default=None, compare=False, repr=False)


@dataclass
class Shape:
    polygons: list[Polygon]
    width: float  # mm
    height: float
    dropped: int = 0  # tiny bits left out

    @property
    def area(self) -> float:
        return sum(area(p.outer) + sum(area(h) for h in p.holes) for p in self.polygons)


def shape(
    rings: list[Ring],
    width_mm: float,
    height_mm: float | None = None,
    raw: list[Ring] | None = None,
) -> Shape:
    """Scale the traced rings to ``width_mm`` (height follows unless given), centre them on
    (0, 0), leave out specks and nest the holes in their outer rings. With ``raw`` (the
    unsimplified rings, same order), a polygon whose simplified rings touch or cross uses
    those instead (else the walls would share an edge or cut each other)."""
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
    unsimplified = (
        [[((x - cx) * sx, (y - cy) * sy) for x, y in r] for r in raw] if raw is not None else None
    )
    plain = {id(r): i for i, r in enumerate(scaled)}  # simplified ring → its index
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
    if unsimplified is not None:
        for p in polygons:
            raw_outer = unsimplified[plain[id(p.outer)]]
            raw_holes = [unsimplified[plain[id(h)]] for h in p.holes]
            if _rings_meet([p.outer, *p.holes]):
                p.outer, p.holes = raw_outer, raw_holes
            else:
                p.raw = Polygon(raw_outer, raw_holes)
    xs = [x for p in polygons for x, _ in p.outer]
    ys = [y for p in polygons for _, y in p.outer]
    return Shape(polygons, max(xs) - min(xs), max(ys) - min(ys), dropped)


def _rings_meet(rings: list[Ring]) -> bool:
    """Does any ring cross or touch itself (edges that aren't neighbours), or another ring?
    Only edges whose x ranges overlap are compared (sorted by their left end)."""
    a0 = np.concatenate([np.array(r, dtype=np.float64).reshape(-1, 2) for r in rings])
    a1 = np.concatenate(
        [np.roll(np.array(r, dtype=np.float64).reshape(-1, 2), -1, axis=0) for r in rings]
    )
    ring = np.concatenate([np.full(len(r), k) for k, r in enumerate(rings)])
    local = np.concatenate([np.arange(len(r)) for r in rings])
    size = np.concatenate([np.full(len(r), len(r)) for r in rings])
    lo, hi = np.minimum(a0, a1), np.maximum(a0, a1)
    # the same "on the line" distance _split_at uses, so both agree on what touches
    eps = (float(np.abs(a0).max()) or 1.0) * 1e-9
    order = np.argsort(lo[:, 0], kind="stable")
    left = lo[order, 0]
    first = np.searchsorted(left, lo[:, 0], side="left")
    last = np.searchsorted(left, hi[:, 0], side="right")
    n = len(a0)
    step = 4096
    for s0 in range(0, n, step):  # bounded memory on huge pictures
        idx = np.arange(s0, min(s0 + step, n))
        counts = last[idx] - first[idx]
        if not counts.sum():
            continue
        i = np.repeat(idx, counts)
        offset = np.arange(len(i)) - np.repeat(np.cumsum(counts) - counts, counts)
        j = order[np.repeat(first[idx], counts) + offset]
        d = np.abs(local[i] - local[j])
        same = ring[i] == ring[j]
        keep = (i != j) & ~(same & ((d == 1) | (d == size[i] - 1)))
        keep &= (hi[i, 1] >= lo[j, 1]) & (lo[i, 1] <= hi[j, 1])
        i, j = i[keep], j[keep]
        if not len(i):
            continue
        p, q, r, t = a0[i], a1[i], a0[j], a1[j]
        hit = (_orient(p, q, r, eps) * _orient(p, q, t, eps) <= 0) & (
            _orient(r, t, p, eps) * _orient(r, t, q, eps) <= 0
        )
        if hit.any():  # collinear pairs count too: their boxes overlap, so they share a point
            return True
    return False


def _orient(o, a, b, eps: float):
    """Which side of line o → a point b is on (0 within ``eps`` of the line)."""
    v = (a[..., 0] - o[..., 0]) * (b[..., 1] - o[..., 1]) - (a[..., 1] - o[..., 1]) * (
        b[..., 0] - o[..., 0]
    )
    length = np.hypot(a[..., 0] - o[..., 0], a[..., 1] - o[..., 1])
    return np.where(np.abs(v) <= eps * length, 0.0, np.sign(v))


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
    return shape_of_mask(mask(rgba, w, h, mode, threshold, invert), detail, width_mm)


def shape_of_mask(m: np.ndarray, detail: str, width_mm: float) -> Shape:
    both = trace_both(m, detail)
    return shape([r for r, _ in both], width_mm, raw=[raw for _, raw in both])


def prepare(s: Shape) -> Shape:
    """Make the cap triangles now (a worker thread), so placing / dragging is quick."""
    for p in s.polygons:
        _caps(p)
    return s


# ----------------------------------------------------------------------------- meshes
@dataclass
class Mesh:
    vertices: np.ndarray  # (V, 3) float64, mm
    faces: np.ndarray  # (F, 3) int64, counter-clockwise seen from outside

    @property
    def volume(self) -> float:
        t = self.vertices[self.faces]
        return float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6)


def _caps(poly: Polygon) -> list[tuple[int, int, int]]:
    if poly.caps is None:
        tris = _triangulate(poly)
        if poly.raw is not None and not _covers(poly, tris):
            # earcut overlapped triangles on these (valid) rings: use the pixel-exact ones
            poly.outer, poly.holes = poly.raw.outer, poly.raw.holes
            tris = _triangulate(poly)
        poly.raw = None
        poly.caps = tris
    return poly.caps


def _covers(poly: Polygon, tris: list[tuple[int, int, int]]) -> bool:
    """Do the triangles cover exactly the polygon's area (none overlapping)?"""
    xy = [p for r in (poly.outer, *poly.holes) for p in r]
    total = 0.0
    for a, b, c in tris:
        (ax, ay), (bx, by), (cx, cy) = xy[a], xy[b], xy[c]
        total += abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) / 2
    want = abs(area(poly.outer)) - sum(abs(area(h)) for h in poly.holes)
    return abs(total - want) <= 1e-7 * max(abs(area(poly.outer)), 1e-9)


def _triangulate(poly: Polygon) -> list[tuple[int, int, int]]:
    """Cap triangles (indices into outer + holes, in that order), counter-clockwise, meeting
    the walls exactly: earcut leaves zero-area triangles where a hole's edge and an outer
    edge lie on one line, and the triangles beside them then span a corner point (the walls
    have a corner there, the cap doesn't). Those are dropped and such triangles are split at
    the corner points they span."""
    import mapbox_earcut

    rings = [poly.outer, *poly.holes]
    pts = np.array([p for r in rings for p in r], dtype=np.float64)
    ends = np.cumsum([len(r) for r in rings]).astype(np.uint32)
    idx = mapbox_earcut.triangulate_float64(pts, ends).reshape(-1, 3).tolist()
    xy = pts.tolist()
    scale = max((abs(v) for p in xy for v in p), default=1.0) or 1.0
    eps = scale * 1e-9
    tris: list[tuple[int, int, int]] = []
    for a, b, c in idx:
        (ax, ay), (bx, by), (cx, cy) = xy[a], xy[b], xy[c]
        area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if abs(area2) <= eps * scale:  # zero area: nothing to cover
            continue
        tris.append((a, c, b) if area2 < 0 else (a, b, c))
    return _split_at(tris, xy, eps)


def _split_at(tris, xy: list, eps: float) -> list[tuple[int, int, int]]:
    """Split every triangle whose edge passes over a corner point (a fan from the opposite
    corner) until none does. Corners on a pixel row / column share an exact y / x, so level
    and upright edges look them up; sloping edges check the corners within their x range."""
    import bisect

    pts = np.array(xy, dtype=np.float64).reshape(-1, 2)
    by_x = np.argsort(pts[:, 0], kind="stable")
    xs = pts[by_x, 0]
    rows: dict[float, list[tuple[float, int]]] = {}
    cols: dict[float, list[tuple[float, int]]] = {}
    for i, (x, y) in enumerate(xy):
        rows.setdefault(y, []).append((x, i))
        cols.setdefault(x, []).append((y, i))
    for d in (rows, cols):
        for v in d.values():
            v.sort()
    seen: dict[tuple[int, int], list[int]] = {}

    def inside(u: int, v: int) -> list[int]:
        """Corners strictly inside edge u → v, in order from u."""
        if (v, u) in seen:
            return seen[(v, u)][::-1]
        if (u, v) not in seen:
            seen[(u, v)] = _inside(u, v)
        return seen[(u, v)]

    def _inside(u: int, v: int) -> list[int]:
        (ax, ay), (bx, by) = xy[u], xy[v]
        if ay == by or ax == bx:
            line, k0, k1 = (rows[ay], ax, bx) if ay == by else (cols[ax], ay, by)
            lo, hi = min(k0, k1), max(k0, k1)
            start = bisect.bisect_right(line, (lo, len(xy)))
            found = []
            for k, i in line[start:]:
                if k >= hi:
                    break
                found.append((k, i))
            found.sort(reverse=k0 > k1)
            return [i for _k, i in found]
        dx, dy = bx - ax, by - ay
        length = math.sqrt(dx * dx + dy * dy)
        lo = np.searchsorted(xs, min(ax, bx) - eps, side="left")
        hi = np.searchsorted(xs, max(ax, bx) + eps, side="right")
        idx = by_x[lo:hi]
        if not len(idx):
            return []
        rx, ry = pts[idx, 0] - ax, pts[idx, 1] - ay
        t = (rx * dx + ry * dy) / (length * length)
        near = np.abs(dx * ry - dy * rx) <= eps * length
        on = np.nonzero(near & (t * length > eps) & (t * length < length - eps))[0]
        return [int(idx[i]) for i in on[np.argsort(t[on], kind="stable")]]

    todo = list(tris)
    out: list[tuple[int, int, int]] = []
    while todo:
        tri = todo.pop()
        for k in range(3):
            on = inside(tri[k], tri[(k + 1) % 3])
            if on:
                seq = [tri[k], *on, tri[(k + 1) % 3]]
                w = tri[(k + 2) % 3]
                todo += [(seq[i], seq[i + 1], w) for i in range(len(seq) - 1)]
                break
        else:
            out.append(tri)
    out.reverse()
    return out


def extrude(polygons: list[Polygon], bottom: float, top: float) -> Mesh:
    """A closed solid: each polygon from z = ``bottom`` to ``top``."""
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for poly in polygons:
        caps = _caps(poly)  # first: it may switch the polygon to its unsimplified rings
        rings = [poly.outer, *poly.holes]
        n = sum(len(r) for r in rings)
        base = len(verts)
        for z in (bottom, top):
            verts.extend((x, y, z) for r in rings for x, y in r)
        for a, b, c in caps:
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
        # at least MIN_WALL_MM of plate all round the hole, however thin the logo / margin
        wall = max(margin, MIN_WALL_MM)
        x0 -= 2 * HOLE_RADIUS_MM + wall
        grow = (2 * HOLE_RADIUS_MM + 2 * MIN_WALL_MM - (y1 - y0)) / 2
        if grow > 0:
            y0, y1 = y0 - grow, y1 + grow
        ring_hole = _circle(x0 + wall + HOLE_RADIUS_MM, 0.0, HOLE_RADIUS_MM, HOLE_SEGMENTS, True)
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


# ----------------------------------------------------------------------------- curved surfaces
FITS = ("flat", "wrap")
STYLES = ("raised", "engraved")
FOLD_COS = math.cos(math.radians(75))  # a wrapped triangle tilted more than this folds over
ENGRAVE_LIFT_MM = 0.5  # an engraving cutter reaches this far above the surface


def refine_edge(width: float) -> float:
    """Longest cap edge for a logo ``width`` mm wide that follows a curved surface."""
    return min(2.0, max(0.3, width / 60))


def refine(
    xy: list[Point], tris: list[tuple[int, int, int]], max_edge: float
) -> tuple[list[Point], list[tuple[int, int, int]]]:
    """Split every edge longer than ``max_edge`` at its midpoint, the new point shared by the
    two triangles on that edge, until none is longer (a triangle with 1 / 2 / 3 split edges
    becomes 2 / 3 / 4). The triangles stay counter-clockwise and still meet edge to edge, so
    the outline edges are split the same way and the walls can follow them."""
    pts = list(xy)
    out = list(tris)
    limit = max_edge * max_edge
    while True:
        mids: dict[tuple[int, int], int] = {}
        for tri in out:
            for k in range(3):
                a, b = tri[k], tri[(k + 1) % 3]
                key = (a, b) if a < b else (b, a)
                if key in mids:
                    continue
                (ax, ay), (bx, by) = pts[a], pts[b]
                if (bx - ax) ** 2 + (by - ay) ** 2 > limit:
                    mids[key] = len(pts)
                    pts.append(((ax + bx) / 2, (ay + by) / 2))
        if not mids:
            return pts, out
        nxt: list[tuple[int, int, int]] = []
        for tri in out:
            m = [mids.get((min(tri[k], tri[(k + 1) % 3]), max(tri[k], tri[(k + 1) % 3])), -1)
                 for k in range(3)]  # fmt: skip
            split = sum(1 for v in m if v >= 0)
            if split == 0:
                nxt.append(tri)
                continue
            # turn the triangle so its first edge is split (and, with two, the second too)
            r = 0
            if split == 1:
                r = next(k for k in range(3) if m[k] >= 0)
            elif split == 2:
                r = next(k for k in range(3) if m[k] < 0)
                r = (r + 1) % 3
            a, b, c = tri[r], tri[(r + 1) % 3], tri[(r + 2) % 3]
            mab, mbc, mca = m[r], m[(r + 1) % 3], m[(r + 2) % 3]
            if split == 1:
                nxt += [(a, mab, c), (mab, b, c)]
            elif split == 2:
                nxt += [(mab, b, mbc), (a, mab, mbc), (a, mbc, c)]
            else:
                nxt += [(a, mab, mca), (mab, b, mbc), (mca, mbc, c), (mab, mbc, mca)]
        out = nxt


def outline_edges(tris: list[tuple[int, int, int]]) -> list[tuple[int, int]]:
    """The directed edges used by one triangle only (the outline; inside on the left), in
    the order the triangles list them."""
    seen: dict[tuple[int, int], int] = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            seen[e] = seen.get(e, 0) + 1
    return [e for e in seen if (e[1], e[0]) not in seen]


@dataclass
class Fitted:
    mesh: Mesh
    missed: int = 0  # outline / cap points whose ray found no surface
    folded: int = 0  # wrapped triangles tilted more than 75° from the logo's direction
    area: float = 0.0  # mm², of the logo's outline (for the volume)


def _solid(bottom: np.ndarray, top: np.ndarray, tris: list[tuple[int, int, int]]) -> tuple:
    """Faces of a closed solid between two copies of one refined cap (same vertex order)."""
    n = len(bottom)
    faces = []
    for a, b, c in tris:
        faces.append((n + a, n + b, n + c))  # top, facing out
        faces.append((a, c, b))  # bottom, facing in
    for a, b in outline_edges(tris):
        faces.append((a, b, b + n))
        faces.append((a, b + n, a + n))
    return np.concatenate([bottom, top]), faces


def fitted_logo(
    s: Shape,
    thickness: float,
    point,
    normal,
    rotation_deg: float,
    sink: float,
    fit: str,
    surf,
    style: str = "raised",
) -> Fitted:
    """The logo fitted to the model's surface (``surf`` = ``pick.surface`` around ``point``
    with the axes of ``frame(normal, rotation_deg)``). Every cap point is projected along
    −normal onto the model.

    flat — the top stays one plane ``thickness`` above the highest surface point under the
    logo, the bottom follows the surface (``sink`` below it): flush, nothing floating.
    wrap — a decal: bottom on the surface, top ``thickness`` out along the surface's own
    direction at each point (the normal of the logo's projected bottom).
    engraved (style) — the same shapes made as a cutter: from ``thickness`` below the
    surface to ``ENGRAVE_LIFT_MM`` above it, cut out of the model when saving."""
    x, y, n = frame(normal, rotation_deg)
    o = np.asarray(point, float)
    step = refine_edge(s.width)
    verts: list[np.ndarray] = []
    faces: list[tuple[int, int, int]] = []
    missed = 0
    folded = 0
    parts = []
    for poly in s.polygons:
        caps = _caps(poly)  # first: it may switch the polygon to its unsimplified rings
        xy = [p for r in (poly.outer, *poly.holes) for p in r]
        pts, tris = refine(xy, caps, step)
        uv = np.array(pts, float).reshape(-1, 2)
        h, _nrm, hit = surf.sample(uv)
        missed += int((~hit).sum())
        parts.append((uv, tris, h, hit))
    if fit == "wrap" and missed:
        raise LogoError(
            f"{missed} points of the logo miss the model — move it onto the model or make it "
            "smaller (or choose Flat)."
        )
    hits = [h[hit] for _uv, _t, h, hit in parts if hit.any()]
    lowest = float(min(v.min() for v in hits)) if hits else 0.0
    highest = float(max(v.max() for v in hits)) if hits else 0.0
    engraved = style == "engraved"
    for uv, tris, h, hit in parts:
        h = np.where(hit, h, lowest)
        flat_xy = o + uv[:, :1] * x + uv[:, 1:2] * y
        if fit == "wrap":
            ground = flat_xy + h[:, None] * n
            t = np.array(tris, np.int64).reshape(-1, 3)
            cr = np.cross(ground[t[:, 1]] - ground[t[:, 0]], ground[t[:, 2]] - ground[t[:, 0]])
            vn = np.zeros_like(ground)
            for k in range(3):
                np.add.at(vn, t[:, k], cr)
            length = np.linalg.norm(cr, axis=1)
            folded += int(((length > 0) & (cr @ n < FOLD_COS * length)).sum())
            size = np.linalg.norm(vn, axis=1, keepdims=True)
            vn = np.where(size > 1e-12, vn / np.where(size > 1e-12, size, 1), n)
            if engraved:
                bottom, top = ground - vn * thickness, ground + vn * ENGRAVE_LIFT_MM
            else:
                bottom, top = ground - vn * sink, ground + vn * thickness
        else:
            if engraved:
                bottom = flat_xy + (h - thickness)[:, None] * n
                top = flat_xy + (highest + ENGRAVE_LIFT_MM) * n
            else:
                bottom = flat_xy + (h - sink)[:, None] * n
                top = flat_xy + (highest + thickness) * n
        v, f = _solid(bottom, top, tris)
        base = sum(len(p) for p in verts)
        verts.append(v)
        faces += [(a + base, b + base, c + base) for a, b, c in f]
    mesh = Mesh(np.concatenate(verts).reshape(-1, 3), np.array(faces, np.int64).reshape(-1, 3))
    return Fitted(mesh, missed, folded, s.area)
