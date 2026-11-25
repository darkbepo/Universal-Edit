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
    position (an STL repeats them), triangles are neighbours when they share an edge."""
    _, inverse = np.unique(vertices, axis=0, return_inverse=True)
    welded = inverse.reshape(-1)[faces]
    edge_map: dict[tuple[int, int], list[int]] = {}
    for i, (a, b, c) in enumerate(welded.tolist()):
        for e in ((a, b), (b, c), (c, a)):
            edge_map.setdefault((min(e), max(e)), []).append(i)
    ns = normals(vertices, faces)
    n0, p0 = ns[tri], vertices[faces[tri, 0]]
    cos_limit = math.cos(math.radians(FLAT_DEGREES))
    seen = {tri}
    order = [tri]
    k = 0
    while k < len(order):
        a, b, c = welded[order[k]].tolist()
        k += 1
        for e in ((a, b), (b, c), (c, a)):
            for j in edge_map[(min(e), max(e))]:
                if j in seen:
                    continue
                if float(ns[j] @ n0) < cos_limit:
                    continue
                if np.abs((vertices[faces[j]] - p0) @ n0).max() > FLAT_MM:
                    continue
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
