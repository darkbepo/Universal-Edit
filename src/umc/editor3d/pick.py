"""Clicking on a model: the ray under the pointer, the triangle it hits, and the flat face
around that triangle (Qt-free; port: web/src/editor3d/pick.ts)."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from umc.editor3d.camera import FOV, OrbitCamera

FLAT_DEGREES = 1.0  # neighbours whose normal differs by less than this belong to the face
FLAT_MM = 0.01  # ... and that lie this close to its plane


def ray(cam: OrbitCamera, x: float, y: float, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """(origin, unit direction) through widget pixel (x, y), y down."""
    t = math.tan(math.radians(FOV) / 2)
    nx = (2 * x / max(w, 1) - 1) * t * (w / max(h, 1))
    ny = (1 - 2 * y / max(h, 1)) * t
    d = -cam.direction() + cam.right() * nx + cam.up() * ny
    return cam.eye(), d / np.linalg.norm(d)


def hit(
    vertices: np.ndarray, faces: np.ndarray, origin, direction
) -> tuple[int, np.ndarray] | None:
    """The nearest triangle the ray hits (either side), and where: Möller–Trumbore."""
    o, d = np.asarray(origin, float), np.asarray(direction, float)
    t0 = vertices[faces[:, 0]]
    e1 = vertices[faces[:, 1]] - t0
    e2 = vertices[faces[:, 2]] - t0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - t0
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    good = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
    if not good.any():
        return None
    t = np.where(good, t, np.inf)
    i = int(np.argmin(t))
    return i, o + d * t[i]


def normals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    t = vertices[faces]
    n = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.divide(n, length, out=np.zeros_like(n), where=length > 0)


@dataclass
class Face:
    triangles: list[int]  # the clicked one first, then in the order they were reached
    normal: np.ndarray  # unit, out of the model
    point: np.ndarray  # a point on the plane (the clicked triangle's first corner)

    def area(self, vertices: np.ndarray, faces: np.ndarray) -> float:
        t = vertices[faces[self.triangles]]
        return float(
            np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1).sum() / 2
        )


def flat_face(vertices: np.ndarray, faces: np.ndarray, tri: int) -> Face:
    """The connected triangles around ``tri`` lying in its plane. Corners are matched by
    position (an STL repeats them), triangles are neighbours when they share an edge. Only
    triangles in the plane are looked at, so a click on a big model stays quick."""
    ns = normals(vertices, faces)
    n0, p0 = ns[tri], vertices[faces[tri, 0]]
    cos_limit = math.cos(math.radians(FLAT_DEGREES))
    flat = (ns @ n0 >= cos_limit) & (np.abs((vertices[faces] - p0) @ n0).max(axis=1) <= FLAT_MM)
    flat[tri] = True
    cand = np.nonzero(flat)[0]
    _, inverse = np.unique(vertices[faces[cand]].reshape(-1, 3), axis=0, return_inverse=True)
    welded = dict(zip(cand.tolist(), inverse.reshape(-1, 3).tolist(), strict=True))
    edge_map: dict[tuple[int, int], list[int]] = {}
    for i in cand.tolist():
        a, b, c = welded[i]
        for e in ((a, b), (b, c), (c, a)):
            edge_map.setdefault((min(e), max(e)), []).append(i)
    seen = {tri}
    order = [tri]
    k = 0
    while k < len(order):
        a, b, c = welded[order[k]]
        k += 1
        for e in ((a, b), (b, c), (c, a)):
            for j in edge_map[(min(e), max(e))]:
                if j not in seen:
                    seen.add(j)
                    order.append(j)
    return Face(order, n0.copy(), p0.copy())


def on_face(points: np.ndarray, vertices: np.ndarray, faces: np.ndarray, face: Face) -> np.ndarray:
    """bool per point: does it lie on the face (projected onto its plane)?"""
    n = face.normal
    # two axes in the plane
    a = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
    a /= np.linalg.norm(a)
    b = np.cross(n, a)
    tri = vertices[faces[face.triangles]]  # (T, 3, 3)
    t2 = np.stack([tri @ a, tri @ b], axis=-1)  # (T, 3, 2)
    p2 = np.stack([points @ a, points @ b], axis=-1)  # (N, 2)
    out = np.zeros(len(points), bool)
    eps = 1e-6
    for x0, x1, x2 in t2:
        d = (x1[1] - x2[1]) * (x0[0] - x2[0]) + (x2[0] - x1[0]) * (x0[1] - x2[1])
        if abs(d) < 1e-15:
            continue
        l1 = ((x1[1] - x2[1]) * (p2[:, 0] - x2[0]) + (x2[0] - x1[0]) * (p2[:, 1] - x2[1])) / d
        l2 = ((x2[1] - x0[1]) * (p2[:, 0] - x2[0]) + (x0[0] - x2[0]) * (p2[:, 1] - x2[1])) / d
        out |= (l1 >= -eps) & (l2 >= -eps) & (1 - l1 - l2 >= -eps)
    return out


def overhang(points: np.ndarray, vertices: np.ndarray, faces: np.ndarray, face: Face) -> int:
    """How many of the logo's outline points lie off the face."""
    return int((~on_face(points, vertices, faces, face)).sum())


# ----------------------------------------------------------------------------- curved surfaces
AREA_COS = 0.5  # triangles facing within 60° of the click count towards the logo's direction
CELL_TRIS = 4  # the surface grid aims at this many triangles per cell
MAX_CELLS = 512  # ... with at most this many cells along a side
SURFACE_MARGIN_MM = 1.0  # triangles this far past the logo's reach are kept too


def area_normal(
    vertices: np.ndarray, faces: np.ndarray, point, normal, radius: float
) -> np.ndarray:
    """The logo's "out" direction on a curved model: the area-weighted normal of the triangles
    with a corner within ``radius`` of ``point`` that face roughly the clicked way
    (``normal``, within 60°). One noisy triangle can't tilt the logo; a flat face (or big
    triangles with no corner that close) gives ``normal`` back."""
    n0 = np.asarray(normal, float)
    n0 = n0 / np.linalg.norm(n0)
    v = np.asarray(vertices, float)
    f = np.asarray(faces, np.int64)
    p = np.asarray(point, float)
    close = ((v - p) ** 2).sum(axis=1) <= radius * radius
    near = close[f[:, 0]] | close[f[:, 1]] | close[f[:, 2]]
    t = v[f[near]]
    cross = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0])  # length = twice the area
    length = np.linalg.norm(cross, axis=1)
    facing = (cross @ n0) >= AREA_COS * length
    total = cross[facing & (length > 0)].sum(axis=0)
    size = float(np.linalg.norm(total))
    return total / size if size > 1e-12 else n0.copy()


@dataclass
class Surface:
    """The model's triangles under a logo, seen along −n from the logo's plane (n = its "out"
    direction, through ``origin``): (u, v) in the logo's x / y, h = height along n. Every
    projection ray of a logo runs along −n, so finding where it meets the model is finding
    the triangle a point lies in, in 2D — a uniform grid over the triangles near the logo."""

    origin: np.ndarray
    axes: np.ndarray  # (3, 3) rows x, y, n
    tris: np.ndarray  # (T, 3, 3) corners as (u, v, h), front-facing only
    normals: np.ndarray  # (T, 3) unit, world
    lo: np.ndarray  # grid corner (u, v)
    cell: float
    size: int  # cells along a side
    starts: np.ndarray  # (size² + 1,) CSR offsets into ``items``
    items: np.ndarray  # triangle indices, by cell

    def sample(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(h, unit world normal, hit) per (u, v) point: of the front-facing triangles the
        point lies in, the one nearest the logo's plane (|h| smallest). Misses: h = 0."""
        uv = np.asarray(uv, float).reshape(-1, 2)
        p = len(uv)
        h = np.zeros(p)
        nrm = np.zeros((p, 3))
        hit = np.zeros(p, bool)
        if not p or not len(self.tris):
            return h, nrm, hit
        g = np.floor((uv - self.lo) / self.cell).astype(np.int64)
        inside = (g >= 0).all(axis=1) & (g < self.size).all(axis=1)
        cid = np.where(inside, g[:, 1] * self.size + g[:, 0], 0)
        counts = np.where(inside, self.starts[cid + 1] - self.starts[cid], 0)
        pt = np.repeat(np.arange(p), counts)
        if not len(pt):
            return h, nrm, hit
        first = np.repeat(self.starts[cid] - np.cumsum(counts) + counts, counts)
        tri = self.items[first + np.arange(len(pt))]
        t = self.tris[tri]
        q = uv[pt]
        x0, y0 = t[:, 0, 0], t[:, 0, 1]
        x1, y1 = t[:, 1, 0], t[:, 1, 1]
        x2, y2 = t[:, 2, 0], t[:, 2, 1]
        d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        l0 = ((y1 - y2) * (q[:, 0] - x2) + (x2 - x1) * (q[:, 1] - y2)) / d
        l1 = ((y2 - y0) * (q[:, 0] - x2) + (x0 - x2) * (q[:, 1] - y2)) / d
        l2 = 1 - l0 - l1
        eps = 1e-9
        ok = (l0 >= -eps) & (l1 >= -eps) & (l2 >= -eps)
        hh = l0 * t[:, 0, 2] + l1 * t[:, 1, 2] + l2 * t[:, 2, 2]
        best = np.full(p, np.inf)
        key = np.where(ok, np.abs(hh), np.inf)
        np.minimum.at(best, pt, key)
        win = ok & (key == best[pt])
        # several triangles can tie (a point on a shared edge): the first listed wins
        idx = np.nonzero(win)[0]
        _, firsts = np.unique(pt[idx], return_index=True)
        idx = idx[firsts]
        h[pt[idx]] = hh[idx]
        nrm[pt[idx]] = self.normals[tri[idx]]
        hit[pt[idx]] = True
        return h, nrm, hit

    def world(self, uvh: np.ndarray) -> np.ndarray:
        """(u, v, h) → world points."""
        return self.origin + np.asarray(uvh, float) @ self.axes


def surface(vertices: np.ndarray, faces: np.ndarray, origin, axes, reach: float) -> Surface:
    """The ``Surface`` of the model around ``origin``: triangles whose (u, v) box comes
    within ``reach`` (+ ``SURFACE_MARGIN_MM``) of it, within ``2 · reach`` along n, and that
    face the logo (n · normal > 0).
    One pass over all triangles, so a big model costs one sweep per placement, not per ray."""
    o = np.asarray(origin, float)
    a = np.asarray(axes, float).reshape(3, 3)
    r = reach + SURFACE_MARGIN_MM
    depth = 2 * reach + SURFACE_MARGIN_MM  # surfaces further along n than this don't count
    local = np.asarray(vertices, float) @ a.T - o @ a.T  # every vertex as (u, v, h)
    f = np.asarray(faces, np.int64)
    f0, f1, f2 = f[:, 0], f[:, 1], f[:, 2]
    near = np.ones(len(f), bool)
    for axis, limit in ((0, r), (1, r), (2, depth)):
        c = local[:, axis]
        for side in (c < -limit, c > limit):  # all three corners past one side: out
            near &= ~(side[f0] & side[f1] & side[f2])
    sel = np.nonzero(near)[0]
    t = local[f[sel]]
    area2 = (t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (
        t[:, 2, 0] - t[:, 0, 0]
    )
    front = area2 > 1e-12 * max(r * r, 1e-9)
    t = t[front]
    sel = sel[front]
    normals_ = normals(np.asarray(vertices, float), f[sel])
    size = int(min(MAX_CELLS, max(1, math.ceil(math.sqrt(len(t) / CELL_TRIS)))))
    lo = np.array([-r, -r])
    cell = 2 * r / size
    if len(t):
        g0 = np.clip(np.floor((t[:, :, :2].min(axis=1) - lo) / cell), 0, size - 1).astype(np.int64)
        g1 = np.clip(np.floor((t[:, :, :2].max(axis=1) - lo) / cell), 0, size - 1).astype(np.int64)
        nx, ny = g1[:, 0] - g0[:, 0] + 1, g1[:, 1] - g0[:, 1] + 1
        counts = nx * ny
        tri = np.repeat(np.arange(len(t)), counts)
        k = np.arange(len(tri)) - np.repeat(np.cumsum(counts) - counts, counts)
        cx = g0[tri, 0] + k % nx[tri]
        cy = g0[tri, 1] + k // nx[tri]
        cid = cy * size + cx
        order = np.argsort(cid, kind="stable")
        items = tri[order]
        starts = np.searchsorted(cid[order], np.arange(size * size + 1), side="left")
    else:
        items = np.zeros(0, np.int64)
        starts = np.zeros(size * size + 1, np.int64)
    return Surface(o, a, t, normals_, lo, cell, size, starts.astype(np.int64), items)
