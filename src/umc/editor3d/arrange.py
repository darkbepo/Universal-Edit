"""Arranging a model on the print bed: move, rotate, scale, mirror, lay flat, units, printer beds
(Qt-free; port: web/src/editor3d/arrange.ts). Matrices are 4×4, row-major, column vectors
(``p' = M @ p``), as plain nested lists so both apps compute the same numbers."""

from __future__ import annotations

import math

import numpy as np

Matrix = list[list[float]]

# name → (width X, depth Y, height Z) in mm; "custom" is typed by the user
BEDS: list[tuple[str, float, float, float]] = [
    ("Prusa MK4", 250.0, 210.0, 220.0),
    ("Prusa MINI", 180.0, 180.0, 180.0),
    ("Bambu Lab A1 / P1 / X1", 256.0, 256.0, 256.0),
    ("Bambu Lab A1 mini", 180.0, 180.0, 180.0),
    ("Creality Ender-3", 220.0, 220.0, 250.0),
    ("Creality K1", 220.0, 220.0, 250.0),
    ("Elegoo Neptune 4", 225.0, 225.0, 265.0),
    ("Anycubic Kobra 2", 220.0, 220.0, 250.0),
]
DEFAULT_BED = BEDS[2]
UNIT_FIXES = {"inch": 25.4, "cm": 10.0, "to_inch": 1 / 25.4, "to_cm": 0.1}


def identity() -> Matrix:
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def multiply(a: Matrix, b: Matrix) -> Matrix:
    """a @ b (b first, then a)."""
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def translate(x: float, y: float, z: float) -> Matrix:
    m = identity()
    m[0][3], m[1][3], m[2][3] = x, y, z
    return m


def _about(m: Matrix, centre) -> Matrix:
    cx, cy, cz = centre
    return multiply(translate(cx, cy, cz), multiply(m, translate(-cx, -cy, -cz)))


def rotate(axis: str, degrees: float, centre=(0.0, 0.0, 0.0)) -> Matrix:
    """Rotation about the X / Y / Z axis through ``centre`` (right hand: +90° about Z turns X to
    Y). Multiples of 90° are exact (no 6e-17 left over)."""
    q = degrees % 360
    if q in (0, 90, 180, 270):
        c, s = {0: (1.0, 0.0), 90: (0.0, 1.0), 180: (-1.0, 0.0), 270: (0.0, -1.0)}[int(q)]
    else:
        t = math.radians(degrees)
        c, s = math.cos(t), math.sin(t)
    m = identity()
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m[i][i], m[i][j], m[j][i], m[j][j] = c, -s, s, c
    return _about(m, centre)


def scale(sx: float, sy: float, sz: float, centre=(0.0, 0.0, 0.0)) -> Matrix:
    m = identity()
    m[0][0], m[1][1], m[2][2] = sx, sy, sz
    return _about(m, centre)


def mirror(axis: str, centre=(0.0, 0.0, 0.0)) -> Matrix:
    f = {"x": (-1.0, 1.0, 1.0), "y": (1.0, -1.0, 1.0), "z": (1.0, 1.0, -1.0)}[axis]
    return scale(*f, centre=centre)


def lay_flat(normal, centre=(0.0, 0.0, 0.0)) -> Matrix:
    """Rotation turning a face with this outward normal to face straight down (onto the bed)."""
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    down = np.array([0.0, 0.0, -1.0])
    c = float(n @ down)
    if c > 1 - 1e-12:
        return identity()
    if c < -1 + 1e-12:  # facing straight up: half a turn about X
        return rotate("x", 180, centre)
    v = np.cross(n, down)  # axis × sin
    s = float(np.linalg.norm(v))
    k = v / s
    kx, ky, kz = k.tolist()
    t = 1 - c
    r = [
        [c + kx * kx * t, kx * ky * t - kz * s, kx * kz * t + ky * s],
        [ky * kx * t + kz * s, c + ky * ky * t, ky * kz * t - kx * s],
        [kz * kx * t - ky * s, kz * ky * t + kx * s, c + kz * kz * t],
    ]
    m = identity()
    for i in range(3):
        for j in range(3):
            m[i][j] = r[i][j]
    return _about(m, centre)


def determinant3(m: Matrix) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def apply(vertices: np.ndarray, faces: np.ndarray, m: Matrix) -> tuple[np.ndarray, np.ndarray]:
    """Transformed copies; a mirroring matrix reverses the triangles so they still face out."""
    a = np.array(m, dtype=np.float64)
    v = vertices @ a[:3, :3].T + a[:3, 3]
    f = faces[:, [0, 2, 1]].copy() if determinant3(m) < 0 else faces.copy()
    return v, f


def apply_point(m: Matrix, p) -> tuple[float, float, float]:
    x, y, z = (float(v) for v in p)
    return tuple(m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3] for r in range(3))  # type: ignore[return-value]


def apply_normal(m: Matrix, n) -> tuple[float, float, float]:
    """A surface normal under ``m`` (inverse transpose of the 3×3 part), unit length."""
    a = np.array(m, dtype=np.float64)[:3, :3]
    v = np.linalg.inv(a).T @ np.asarray(n, float)
    v = v / np.linalg.norm(v)
    return tuple(float(x) for x in v)  # type: ignore[return-value]


def bounds(vertices: np.ndarray) -> tuple[list[float], list[float]]:
    return vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()


def centre_of(vertices: np.ndarray) -> tuple[float, float, float]:
    lo, hi = bounds(vertices)
    return tuple((a + b) / 2 for a, b in zip(lo, hi, strict=True))  # type: ignore[return-value]


def settle(vertices: np.ndarray, m: Matrix) -> Matrix:
    """``m`` followed by the move that puts the result back on the bed (lowest point Z = 0)
    with its X / Y centre where the model's centre was before."""
    v, _ = apply(vertices, np.zeros((0, 3), np.int64), m)
    (cx, cy, _cz) = centre_of(vertices)
    lo, hi = bounds(v)
    nx, ny = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    return multiply(translate(cx - nx, cy - ny, -lo[2]), m)


def centred(vertices: np.ndarray) -> Matrix:
    """Move to the middle of the bed (X / Y centre 0), standing on it."""
    lo, hi = bounds(vertices)
    return translate(-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])


def fits(size, bed) -> list[float]:
    """How much (mm) the model is too big per axis (0 = fits)."""
    return [max(0.0, float(s) - float(b)) for s, b in zip(size, bed, strict=True)]


def fit_note(size, bed) -> str:
    over = fits(size, bed)
    words = ("wide", "deep", "tall")
    parts = [f"{o:.1f} mm too {w}" for o, w in zip(over, words, strict=True) if o > 0]
    if not parts:
        return "Fits the printer's bed."
    return "Too big for the bed: " + ", ".join(parts) + "."
