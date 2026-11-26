"""Checking and repairing a model for printing (Qt-free; port: web/src/editor3d/meshcheck.ts).

``weld`` merges corners at the same position (first-appearance numbering, the same in both
apps); ``check`` counts what would trouble a slicer and finds overhangs; ``repair`` removes
zero-area / duplicate triangles, turns every part to face outwards, fills holes flat and drops
tiny loose bits. Same steps, same order in both apps (``tests/fixtures/print3d_cases.json``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

OVERHANG_DEG = 45.0  # faces leaning further than this from vertical need supports
BED_EPS = 0.05  # mm: faces this close to the lowest point lie on the bed
TINY_MM3 = 1.0  # loose bits smaller than this …
TINY_SHARE = 0.01  # … and than this share of the largest part are dropped by Repair
MAX_HOLE_EDGES = 5000  # bigger holes are left alone (likely a missing side, not a hole)


def weld(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Corners at exactly the same position become one vertex; vertices are numbered in the
    order their first corner appears (faces row by row)."""
    corners = np.asarray(vertices, np.float64)[np.asarray(faces, np.int64).reshape(-1)]
    if not len(corners):
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    corners = corners + 0.0  # -0.0 → 0.0, as JavaScript's keys see them
    uniq, first, inverse = np.unique(corners, axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first, kind="stable")
    rank = np.empty(len(order), np.int64)
    rank[order] = np.arange(len(order))
    return uniq[order], rank[inverse.reshape(-1)].reshape(-1, 3)


def _areas2(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Twice each triangle's area."""
    t = v[f]
    return np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)


def _scale(v: np.ndarray) -> float:
    return float(np.abs(v).max()) if len(v) else 1.0


def _clean(v: np.ndarray, f: np.ndarray) -> tuple[list[int], int, int]:
    """Indices of the triangles kept (not zero-area, not a repeat of an earlier one), and how
    many of each kind were left out."""
    eps = (_scale(v) * 1e-9) ** 2
    a2 = _areas2(v, f).tolist() if len(f) else []
    seen: set[tuple[int, ...]] = set()
    keep: list[int] = []
    zero = dup = 0
    for i, (a, b, c) in enumerate(f.tolist()):
        if a2[i] <= eps or a in (b, c) or b == c:
            zero += 1
            continue
        # the same corners the same way round (two parts touching face to face have the
        # same corners the other way round: that's fine)
        key = min((a, b, c), (b, c, a), (c, a, b))
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        keep.append(i)
    return keep, zero, dup


def _edges(f: list[list[int]]) -> dict[tuple[int, int], list[int]]:
    """Undirected edge (lo, hi) → the triangles using it, in order."""
    out: dict[tuple[int, int], list[int]] = {}
    for i, (a, b, c) in enumerate(f):
        for p, q in ((a, b), (b, c), (c, a)):
            out.setdefault((p, q) if p < q else (q, p), []).append(i)
    return out


def _kinds(f: list[list[int]], edges) -> dict[tuple[int, int], str]:
    """Each edge: "ok" (two triangles running it opposite ways, or 2k triangles with k each
    way — closed parts touching along it, which prints fine), "open" (one triangle: a hole),
    "flipped" (two triangles running it the same way) or "shared" (anything else)."""
    forward: dict[tuple[int, int], int] = {}
    for a, b, c in f:
        for p, q in ((a, b), (b, c), (c, a)):
            if p < q:
                forward[(p, q)] = forward.get((p, q), 0) + 1
    out = {}
    for key, tris in edges.items():
        n, fw = len(tris), forward.get(key, 0)
        if n == 1:
            out[key] = "open"
        elif n % 2 == 0 and fw * 2 == n:
            out[key] = "ok"
        elif n == 2:
            out[key] = "flipped"
        else:
            out[key] = "shared"
    return out


def _parts(n: int, edges: dict[tuple[int, int], list[int]]) -> list[int]:
    """Part number per triangle (triangles sharing an edge are one part), numbered in order of
    each part's first triangle."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for tris in edges.values():
        r0 = find(tris[0])
        for t in tris[1:]:
            r = find(t)
            if r != r0:
                if r < r0:
                    r, r0 = r0, r
                parent[r] = r0
    label: dict[int, int] = {}
    out = []
    for i in range(n):
        r = find(i)
        if r not in label:
            label[r] = len(label)
        out.append(label[r])
    return out


def _volumes(v: np.ndarray, f: list[list[int]], part: list[int], parts: int) -> list[float]:
    """Signed volume per part (positive = facing outwards, when closed)."""
    vol = [0.0] * parts
    if not f:
        return vol
    t = v[np.array(f, np.int64)]
    six = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).tolist()
    for i, s in enumerate(six):
        vol[part[i]] += s / 6
    return vol


@dataclass
class Report:
    triangles: int = 0
    zero_area: int = 0
    duplicates: int = 0
    open_edges: int = 0  # edges of one triangle only: holes
    shared_edges: int = 0  # edges of three or more triangles
    flipped_edges: int = 0  # two triangles meeting the same way round: one faces wrongly
    parts: int = 0
    inside_out: int = 0  # closed parts facing inwards
    tiny_parts: int = 0
    holes: int = 0
    volume: float = 0.0  # mm³ (closed, consistently facing models; else 0)
    area: float = 0.0  # mm², surface
    overhang_faces: list[int] = field(default_factory=list)  # indices into the checked faces
    overhang_area: float = 0.0

    @property
    def closed(self) -> bool:
        return self.open_edges == 0 and self.shared_edges == 0

    @property
    def problems(self) -> bool:
        return bool(
            self.zero_area or self.duplicates or self.open_edges or self.shared_edges
            or self.flipped_edges or self.inside_out or self.tiny_parts
        )  # fmt: skip


def check(vertices: np.ndarray, faces: np.ndarray, overhang_deg: float = OVERHANG_DEG) -> Report:
    v, f = weld(vertices, faces)
    r = Report(triangles=len(f))
    keep, r.zero_area, r.duplicates = _clean(v, f)
    good = f[keep].tolist()
    edges = _edges(good)
    kinds = _kinds(good, edges)
    for k in kinds.values():
        if k == "open":
            r.open_edges += 1
        elif k == "shared":
            r.shared_edges += 1
        elif k == "flipped":
            r.flipped_edges += 1
    part = _parts(len(good), edges)
    r.parts = max(part) + 1 if part else 0
    vols = _volumes(v, good, part, r.parts)
    open_part = [False] * r.parts
    for key, tris in edges.items():
        if kinds[key] != "ok":
            for t in tris:
                open_part[part[t]] = True
    biggest = max((abs(x) for x in vols), default=0.0)
    for p in range(r.parts):
        if not open_part[p] and vols[p] < 0:
            r.inside_out += 1
        if abs(vols[p]) < TINY_MM3 and abs(vols[p]) < TINY_SHARE * biggest:
            r.tiny_parts += 1
    r.holes = len(_hole_loops(good, edges)[0])
    r.volume = abs(sum(vols)) if r.closed and not r.flipped_edges else 0.0
    if good:
        g = np.array(good, np.int64)
        a2 = _areas2(v, g)
        r.area = float(a2.sum() / 2)
        r.overhang_faces, r.overhang_area = _overhangs(v, f, keep, overhang_deg)
    return r


def _overhangs(v, f, keep, overhang_deg) -> tuple[list[int], float]:
    """Faces leaning further than ``overhang_deg`` from vertical that face down, except those
    lying on the bed (all corners within BED_EPS of the lowest point)."""
    g = f[keep]
    t = v[g]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    length = np.linalg.norm(n, axis=1)
    nz = n[:, 2] / np.where(length > 0, length, 1)
    floor = float(v[:, 2].min())
    on_bed = (t[:, :, 2] <= floor + BED_EPS).all(axis=1)
    steep = nz < -math.sin(math.radians(overhang_deg))
    hit = np.nonzero(steep & ~on_bed)[0]
    return [int(keep[i]) for i in hit.tolist()], float(length[hit].sum() / 2)


def _hole_loops(f: list[list[int]], edges) -> tuple[list[list[int]], int]:
    """Closed loops of hole edges (each in the direction its triangle runs it), and how many
    hole edges couldn't be made into a simple loop."""
    nxt: dict[int, list[int]] = {}
    order: list[tuple[int, int]] = []
    for a, b, c in f:
        for p, q in ((a, b), (b, c), (c, a)):
            if len(edges[(p, q) if p < q else (q, p)]) == 1:
                nxt.setdefault(p, []).append(q)
                order.append((p, q))
    used: set[tuple[int, int]] = set()
    loops: list[list[int]] = []
    stray = 0
    for start in order:
        if start in used:
            continue
        loop = [start[0]]
        used.add(start)
        cur = start[1]
        ok = True
        while cur != start[0]:
            if cur in loop:  # touches itself: not a simple hole
                ok = False
                break
            loop.append(cur)
            step = next((q for q in nxt.get(cur, []) if (cur, q) not in used), None)
            if step is None:
                ok = False
                break
            used.add((cur, step))
            cur = step
        if ok and len(loop) >= 3:
            loops.append(loop)
        else:
            stray += len(loop)
    return loops, stray


@dataclass
class Repaired:
    vertices: np.ndarray
    faces: np.ndarray
    zero_area: int = 0
    duplicates: int = 0
    turned: int = 0  # triangles turned to face outwards
    holes_filled: int = 0
    holes_left: int = 0
    tiny_removed: int = 0

    @property
    def changed(self) -> bool:
        return bool(
            self.zero_area or self.duplicates or self.turned or self.holes_filled
            or self.tiny_removed
        )  # fmt: skip


def repair(vertices: np.ndarray, faces: np.ndarray) -> Repaired:
    v, f = weld(vertices, faces)
    keep, zero, dup = _clean(v, f)
    good = f[keep].tolist()
    out = Repaired(v, f, zero, dup)
    original = len(good)
    # 1. every part facing one way round
    edges = _edges(good)
    flip = _orient(good, edges)
    good = [[a, c, b] if fl else [a, b, c] for (a, b, c), fl in zip(good, flip, strict=True)]
    # 2. fill holes flat (the caps follow the parts' way round)
    loops, _stray = _hole_loops(good, edges)
    for loop in loops:
        if len(loop) > MAX_HOLE_EDGES:
            out.holes_left += 1
            continue
        good += _cap(v, loop)
        out.holes_filled += 1
    # 3. closed parts facing inwards: turn them all
    edges = _edges(good)
    kinds = _kinds(good, edges)
    part = _parts(len(good), edges)
    parts = max(part) + 1 if part else 0
    open_part = [False] * parts
    for key, tris in edges.items():
        if kinds[key] != "ok":
            for t in tris:
                open_part[part[t]] = True
    vols = _volumes(v, good, part, parts)
    inward = [not open_part[p] and vols[p] < 0 for p in range(parts)]
    for i, (a, b, c) in enumerate(good):
        if inward[part[i]]:
            good[i] = [a, c, b]
    out.turned = sum(
        1 for i in range(original) if flip[i] != inward[part[i]]
    )  # facing differently from before
    # 4. tiny loose bits
    edges = _edges(good)
    part = _parts(len(good), edges)
    parts = max(part) + 1 if part else 0
    vols = _volumes(v, good, part, parts)
    biggest = max((abs(x) for x in vols), default=0.0)
    tiny = [abs(x) < TINY_MM3 and abs(x) < TINY_SHARE * biggest for x in vols]
    out.tiny_removed = sum(tiny)
    good = [t for i, t in enumerate(good) if not tiny[part[i]]]
    # 5. only the vertices still used, in first-use order
    remap: dict[int, int] = {}
    for t in good:
        for x in t:
            if x not in remap:
                remap[x] = len(remap)
    used = sorted(remap, key=remap.__getitem__)
    out.vertices = v[used] if used else np.zeros((0, 3))
    out.faces = np.array([[remap[x] for x in t] for t in good], np.int64).reshape(-1, 3)
    return out


def _orient(f: list[list[int]], edges) -> list[bool]:
    """Which triangles to turn so neighbours across every two-triangle edge run it opposite
    ways (first triangle of each part keeps its way)."""
    flip: list[bool | None] = [None] * len(f)
    for seed in range(len(f)):
        if flip[seed] is not None:
            continue
        flip[seed] = False
        todo = [seed]
        while todo:
            t = todo.pop()
            a, b, c = f[t]
            if flip[t]:
                a, b, c = a, c, b
            for p, q in ((a, b), (b, c), (c, a)):
                tris = edges[(p, q) if p < q else (q, p)]
                if len(tris) != 2:
                    continue
                g = tris[0] if tris[1] == t else tris[1]
                if flip[g] is not None:
                    continue
                x, y, z = f[g]
                same = (x, y) == (p, q) or (y, z) == (p, q) or (z, x) == (p, q)
                flip[g] = same  # it must run q → p
                todo.append(g)
    return [bool(x) for x in flip]


def _cap(v: np.ndarray, loop: list[int]) -> list[list[int]]:
    """Triangles closing one hole: the loop (run as its triangles run it) reversed, triangulated
    flat on its best-fit plane, facing the way that closes the surface."""
    import mapbox_earcut

    ring = loop[::-1]
    p = v[ring]
    nx = ny = nz = 0.0
    k = len(ring)
    for i in range(k):
        x1, y1, z1 = p[i].tolist()
        x2, y2, z2 = p[(i + 1) % k].tolist()
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    n = np.array([nx, ny, nz])
    length = float(np.linalg.norm(n))
    if length == 0:
        return []
    n /= length
    u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    w = np.cross(n, u)
    xy = np.stack([p @ u, p @ w], axis=1)
    idx = mapbox_earcut.triangulate_float64(xy, np.array([k], np.uint32)).reshape(-1, 3).tolist()
    out = []
    for a, b, c in idx:
        (ax, ay), (bx, by), (cx, cy) = xy[a], xy[b], xy[c]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
            b, c = c, b
        out.append([ring[a], ring[b], ring[c]])
    return out


def _n(count: int, word: str) -> str:
    """'1 triangle', '3 triangles'."""
    return f"{count} {word}{'' if count == 1 else 's'}"


def notes(r: Report) -> list[str]:
    """The check in plain words (empty problems = ready)."""
    out = []
    if r.open_edges:
        out.append(
            f"{r.holes or 'Some'} hole{'s' if r.holes != 1 else ''} in the surface "
            f"({_n(r.open_edges, 'open edge')}) — a slicer may print it hollow or skip parts."
        )
    if r.shared_edges:
        out.append(f"{_n(r.shared_edges, 'edge')} shared by more than two triangles — the surface "
                   "folds into itself there.")  # fmt: skip
    if r.flipped_edges:
        out.append(f"Some triangles face the wrong way ({_n(r.flipped_edges, 'edge')}).")
    if r.inside_out:
        out.append(f"{r.inside_out} part{'s are' if r.inside_out != 1 else ' is'} inside out.")
    if r.zero_area:
        out.append(f"{_n(r.zero_area, 'triangle')} with no area.")
    if r.duplicates:
        out.append(
            f"{_n(r.duplicates, 'triangle')} appear{'s' if r.duplicates == 1 else ''} twice."
        )
    if r.tiny_parts:
        out.append(f"{r.tiny_parts} tiny loose bit{'s' if r.tiny_parts != 1 else ''} "
                   "(too small to print).")  # fmt: skip
    if not out:
        out.append("No problems found — ready for the slicer.")
    return out


def repaired_note(x: Repaired, after: Report | None = None) -> str:
    """What Repair did; with the check made afterwards, also whether anything is left."""
    left = after is not None and after.problems
    if not x.changed:
        if left:
            return "Nothing could be repaired automatically — see the check for what's left."
        return "Nothing needed repairing."
    done = []
    if x.holes_filled:
        done.append(f"closed {x.holes_filled} hole{'s' if x.holes_filled != 1 else ''}")
    if x.turned:
        done.append(f"turned {x.turned} triangle{'s' if x.turned != 1 else ''} to face outwards")
    if x.zero_area or x.duplicates:
        done.append(f"removed {_n(x.zero_area + x.duplicates, 'empty or repeated triangle')}")
    if x.tiny_removed:
        done.append(f"removed {x.tiny_removed} tiny loose bit{'s' if x.tiny_removed != 1 else ''}")
    note = "Repaired: " + ", ".join(done) + "."
    if x.holes_left:
        were = "s were" if x.holes_left != 1 else " was"
        note += f" {x.holes_left} very large opening{were} left as is."
    if left:
        note += " Some problems couldn't be repaired here — see the check."
    return note
