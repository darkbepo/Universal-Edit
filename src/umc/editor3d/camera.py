"""Orbit camera for the 3D viewer (Z is up, like a print bed). Qt-free, numpy matrices.

``yaw`` turns around Z (0° = looking from +X), ``pitch`` is the height above the bed
(90° = straight down). Matrices are column-vector style (``clip = P @ V @ p``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

FOV = 35.0  # vertical field of view, degrees
PITCH_LIMIT = 90.0
# name → (yaw, pitch); the same presets on the website
VIEWS: dict[str, tuple[float, float]] = {
    "front": (-90.0, 0.0),
    "back": (90.0, 0.0),
    "left": (180.0, 0.0),
    "right": (0.0, 0.0),
    "top": (-90.0, 90.0),
    "bottom": (-90.0, -90.0),
    "iso": (-45.0, 35.264),
}


@dataclass
class OrbitCamera:
    target: np.ndarray = None  # type: ignore[assignment]
    distance: float = 100.0
    yaw: float = VIEWS["iso"][0]
    pitch: float = VIEWS["iso"][1]
    radius: float = 50.0  # size of the model, for clipping planes and zoom limits

    def __post_init__(self) -> None:
        if self.target is None:
            self.target = np.zeros(3)

    # ------------------------------------------------------------ geometry
    def _angles(self) -> tuple[float, float]:
        return math.radians(self.yaw), math.radians(self.pitch)

    def direction(self) -> np.ndarray:
        """Unit vector from the target towards the eye."""
        y, p = self._angles()
        return np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])

    def up(self) -> np.ndarray:
        """Screen-up in world space (exact, also when looking straight down)."""
        y, p = self._angles()
        return np.array([-math.sin(p) * math.cos(y), -math.sin(p) * math.sin(y), math.cos(p)])

    def right(self) -> np.ndarray:
        return np.cross(-self.direction(), self.up())

    def eye(self) -> np.ndarray:
        return self.target + self.direction() * self.distance

    def view_matrix(self) -> np.ndarray:
        f = -self.direction()
        r, u = self.right(), self.up()
        m = np.identity(4)
        m[0, :3], m[1, :3], m[2, :3] = r, u, -f
        m[:3, 3] = -m[:3, :3] @ self.eye()
        return m

    def clip_planes(self) -> tuple[float, float]:
        near = max(self.distance - self.radius * 4, self.distance * 0.01, 1e-3)
        far = self.distance + self.radius * 4
        return near, far

    def projection_matrix(self, aspect: float) -> np.ndarray:
        near, far = self.clip_planes()
        f = 1.0 / math.tan(math.radians(FOV) / 2)
        m = np.zeros((4, 4))
        m[0, 0] = f / max(aspect, 1e-6)
        m[1, 1] = f
        m[2, 2] = (far + near) / (near - far)
        m[2, 3] = 2 * far * near / (near - far)
        m[3, 2] = -1.0
        return m

    # ------------------------------------------------------------ moving
    def orbit(self, d_yaw: float, d_pitch: float) -> None:
        self.yaw = (self.yaw + d_yaw + 180.0) % 360.0 - 180.0
        self.pitch = max(-PITCH_LIMIT, min(PITCH_LIMIT, self.pitch + d_pitch))

    def pan(self, dx_px: float, dy_px: float, viewport_h: int) -> None:
        """Move so the point under the pointer follows it (dy down = positive)."""
        per_px = 2 * self.distance * math.tan(math.radians(FOV) / 2) / max(viewport_h, 1)
        self.target = self.target - self.right() * dx_px * per_px + self.up() * dy_px * per_px

    def zoom(self, factor: float) -> None:
        """factor < 1 moves closer."""
        self.distance = max(self.radius * 0.05, min(self.radius * 50, self.distance * factor))

    def set_view(self, name: str) -> None:
        self.yaw, self.pitch = VIEWS[name]

    def fit(self, lo: np.ndarray, hi: np.ndarray, aspect: float = 1.0) -> None:
        """Look at the whole box (keeps the viewing angle)."""
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        self.target = (lo + hi) / 2
        self.radius = max(float(np.linalg.norm(hi - lo)) / 2, 1e-3)
        half = math.radians(FOV) / 2
        if aspect < 1:  # narrow window: the horizontal angle is the smaller one
            half = math.atan(math.tan(half) * aspect)
        self.distance = self.radius / math.sin(half) * 1.05
