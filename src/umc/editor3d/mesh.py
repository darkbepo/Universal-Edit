"""A 3D model as the viewer shows it: one triangle soup in millimetres, standing on the bed.

Loading reuses the converter's ``load_model`` (so every input format and the mm rules are the
same as when converting); the model is then moved like "Centre on the print bed" does: X/Y
centred on 0 and the lowest point on Z = 0. Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import numpy as np

from umc.converters.model3d import ModelOptions, load_model
from umc.core.errors import ConversionError


@dataclass
class ViewModel:
    name: str
    vertices: np.ndarray  # (V, 3) float64, mm
    faces: np.ndarray  # (F, 3) int64
    parts: int = 1
    watertight: bool = False
    volume_mm3: float | None = None  # only when watertight
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def triangles(self) -> int:
        return len(self.faces)

    @cached_property
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    @property
    def size(self) -> tuple[float, float, float]:
        lo, hi = self.bounds
        w, d, h = (hi - lo).tolist()
        return w, d, h

    def triangle_buffer(self) -> np.ndarray:
        """Interleaved float32 (3·F, 6): position xyz + flat face normal xyz, per corner."""
        tri = self.vertices[self.faces]  # (F, 3, 3)
        n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        length = np.linalg.norm(n, axis=1, keepdims=True)
        n = np.divide(n, length, out=np.zeros_like(n), where=length > 0)
        out = np.empty((len(self.faces), 3, 6), dtype=np.float32)
        out[:, :, :3] = tri
        out[:, :, 3:] = n[:, None, :]
        return out.reshape(-1, 6)

    def edge_buffer(self) -> np.ndarray:
        """float32 (2·E, 3): every unique triangle edge once, for the wireframe."""
        if "edges" not in self._cache:
            e = self.faces[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
            e = np.unique(np.sort(e, axis=1), axis=0)
            self._cache["edges"] = self.vertices[e].reshape(-1, 3).astype(np.float32)
        return self._cache["edges"]


def load_view_model(path: Path, ext: str) -> ViewModel:
    """Read any supported 3D file into a ViewModel (raises ConversionError)."""
    import trimesh

    scene = load_model(path, ext, ModelOptions())
    meshes = [g for g in scene.dump() if isinstance(g, trimesh.Trimesh) and len(g.faces)]
    if not meshes:
        raise ConversionError(f"“{path.name}” doesn't contain any triangles to show.")
    watertight = all(m.is_watertight for m in meshes)
    volume = float(sum(abs(m.volume) for m in meshes)) if watertight else None
    offsets = np.cumsum([0] + [len(m.vertices) for m in meshes[:-1]])
    vertices = np.concatenate([m.vertices for m in meshes]).astype(np.float64)
    faces = np.concatenate([m.faces + o for m, o in zip(meshes, offsets, strict=True)])
    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
    vertices -= [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]]
    return ViewModel(
        name=path.name,
        vertices=vertices,
        faces=faces.astype(np.int64),
        parts=len(meshes),
        watertight=watertight,
        volume_mm3=volume,
    )


def fmt_mm(v: float) -> str:
    return f"{v:.1f} mm" if abs(v) < 1000 else f"{v / 1000:.2f} m"


def describe(m: ViewModel) -> list[tuple[str, str]]:
    """The info panel rows (label, value), same in both apps."""
    w, d, h = m.size
    rows = [
        ("Width (X)", fmt_mm(w)),
        ("Depth (Y)", fmt_mm(d)),
        ("Height (Z)", fmt_mm(h)),
        ("Triangles", f"{m.triangles:,}"),
        ("Parts", str(m.parts)),
        ("Watertight", "Yes" if m.watertight else "No — may not print cleanly"),
    ]
    if m.volume_mm3 is not None:
        rows.append(("Volume", f"{m.volume_mm3 / 1000:.2f} cm³"))
    return rows
