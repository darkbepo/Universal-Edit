"""What the 3D viewer shows and saves once logos are added: the opened model plus logos, with
undo (Qt-free; port: web/src/editor3d/scene.ts).

A ``Logo`` keeps its picture, settings and where it sits, and its meshes, built from those by
``build_logo``. The scene is immutable: every edit returns a new one, and ``History`` keeps
the older ones for undo. ``combined`` is one ViewModel for the view and the info panel;
``export`` writes everything as one object (slicers merge the overlapping parts).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from umc.editor3d import logo as L
from umc.editor3d.mesh import ViewModel

BADGE_GAP_MM = 10.0  # a separate badge stands this far to the right of the model
SAVE_EXTS = ("3mf", "stl", "obj", "ply", "glb")


@dataclass(frozen=True)
class Picture:
    name: str
    rgba: bytes  # scaled so the longer side is at most logo.MAX_SIDE
    w: int
    h: int


@dataclass(frozen=True)
class LogoSettings:
    mode: str = "alpha"  # alpha / dark / light
    threshold: int = 128
    invert: bool = False
    detail: str = "normal"
    width: float = 40.0  # mm
    thickness: float = 2.0  # mm above the surface / the plate
    where: str = "face"  # face (on the model) / badge (a separate piece)
    rotation: float = 0.0  # degrees
    sink: float = L.SINK_MM
    plate: str = "none"  # badge only: none / rectangle / rounded
    margin: float = 3.0
    plate_thickness: float = 2.0
    hole: bool = False


@dataclass(frozen=True, eq=False)
class Logo:
    picture: Picture
    settings: LogoSettings
    point: tuple[float, float, float] | None = None  # on the face (where == "face")
    normal: tuple[float, float, float] | None = None
    meshes: tuple[L.Mesh, ...] = ()
    width: float = 0.0  # mm, as built
    height: float = 0.0
    dropped: int = 0
    overhang: int = 0  # outline points off the face


def picture_from_image(image, name: str) -> Picture:
    """A Pillow image → Picture (RGBA, at most MAX_SIDE on its longer side)."""
    from PIL import Image

    im = image.convert("RGBA")
    if max(im.size) > L.MAX_SIDE:
        s = L.MAX_SIDE / max(im.size)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
    return Picture(name, im.tobytes(), im.width, im.height)


def load_picture(path: Path) -> Picture:
    """Raster pictures through the converter's opener (so HEIC / RAW / PSD work too)."""
    from umc.converters.imaging import open_image

    with open_image(path) as im:
        im.load()
        return picture_from_image(im, path.name)


def shape_of(pic: Picture, s: LogoSettings) -> L.Shape:
    return L.shape_from_picture(
        pic.rgba, pic.w, pic.h, s.width, s.mode, s.threshold, s.invert, s.detail
    )


def build_logo(
    pic: Picture,
    s: LogoSettings,
    point=None,
    normal=None,
    base: ViewModel | None = None,
    face_tris: list[int] | None = None,
    shp: L.Shape | None = None,
) -> Logo:
    """Trace (unless ``shp`` is given: dragging re-places without tracing again) + extrude +
    place. Raises logo.LogoError in plain words."""
    if shp is None:
        shp = shape_of(pic, s)
    overhang = 0
    if s.where == "face":
        if point is None or normal is None:
            raise L.LogoError("Click a flat face of the model to place the logo.")
        meshes = (L.placed_logo(shp, s.thickness, point, normal, s.rotation, s.sink),)
        if base is not None and face_tris:
            from umc.editor3d import pick

            face = pick.Face(list(face_tris), np.asarray(normal, float), np.asarray(point, float))
            pts = L.footprint(shp, point, normal, s.rotation)
            overhang = pick.overhang(pts, base.vertices, base.faces, face)
    else:
        parts = L.badge(shp, s.thickness, s.plate, s.margin, s.plate_thickness, s.hole)
        x = 0.0
        if base is not None:
            _lo, hi = base.bounds
            left = shp.width / 2  # how far the badge reaches left of its centre
            if s.plate != "none":
                left += s.margin + (2 * L.HOLE_RADIUS_MM + s.margin if s.hole else 0)
            x = float(hi[0]) + BADGE_GAP_MM + left
        m = L.placement((x, 0.0, 0.0), (0.0, 0.0, 1.0), s.rotation, 0.0)
        meshes = tuple(L.transform(p, m) for p in parts)
        point, normal = None, None
    return Logo(
        pic,
        s,
        tuple(map(float, point)) if point is not None else None,
        tuple(map(float, normal)) if normal is not None else None,
        meshes,
        shp.width,
        shp.height,
        shp.dropped,
        overhang,
    )


@dataclass(frozen=True)
class Scene:
    base: ViewModel | None = None
    logos: tuple[Logo, ...] = ()

    def with_logo(self, logo: Logo, index: int | None = None) -> Scene:
        """Add (index None) or replace a logo."""
        logos = list(self.logos)
        if index is None:
            logos.append(logo)
        else:
            logos[index] = logo
        return replace(self, logos=tuple(logos))

    def without(self, index: int) -> Scene:
        return replace(self, logos=self.logos[:index] + self.logos[index + 1 :])

    @property
    def empty(self) -> bool:
        return self.base is None and not self.logos

    def meshes(self) -> list[tuple[np.ndarray, np.ndarray]]:
        out = []
        if self.base is not None:
            out.append((self.base.vertices, self.base.faces))
        out += [(m.vertices, m.faces) for lg in self.logos for m in lg.meshes]
        return out

    def combined(self, name: str = "") -> ViewModel | None:
        """One ViewModel of everything (parts = the model's + one per logo piece)."""
        if self.empty:
            return None
        if not self.logos:
            return self.base
        parts = self.meshes()
        offsets = np.cumsum([0] + [len(v) for v, _ in parts[:-1]])
        vertices = np.concatenate([v for v, _ in parts]).astype(np.float64)
        faces = np.concatenate([f + o for (_v, f), o in zip(parts, offsets, strict=True)])
        pieces = sum(len(lg.meshes) for lg in self.logos)
        base = self.base
        watertight = base.watertight if base is not None else True
        volume = None
        if watertight:
            volume = (base.volume_mm3 if base is not None else 0.0) + sum(
                abs(m.volume) for lg in self.logos for m in lg.meshes
            )
        return ViewModel(
            name=name or (base.name if base is not None else "Logo"),
            vertices=vertices,
            faces=faces.astype(np.int64),
            parts=(base.parts if base is not None else 0) + pieces,
            watertight=watertight,
            volume_mm3=volume,
        )


def export(scene: Scene, ext: str) -> bytes:
    """The whole scene as one object in ``ext`` (millimetres; nothing moved or repaired)."""
    import trimesh

    from umc.converters.model3d import ModelOptions, save_model

    if ext not in SAVE_EXTS:
        raise ValueError(ext)
    parts = scene.meshes()
    if not parts:
        raise L.LogoError("There is nothing to save yet.")
    offsets = np.cumsum([0] + [len(v) for v, _ in parts[:-1]])
    mesh = trimesh.Trimesh(
        np.concatenate([v for v, _ in parts]),
        np.concatenate([f + o for (_v, f), o in zip(parts, offsets, strict=True)]),
        process=False,
    )
    opts = ModelOptions(center_on_bed=False, repair=False, merge_parts=True)
    return save_model(trimesh.Scene(mesh), ext, opts)


def default_save_name(source: Path | None, ext: str) -> str:
    stem = source.stem if source is not None else "logo"
    return f"{stem} with logo.{ext}" if source is not None else f"{stem}.{ext}"


@dataclass
class History:
    """Undo / redo of whole scenes (scenes share their meshes, so this is cheap)."""

    scene: Scene = field(default_factory=Scene)
    undo_stack: list[Scene] = field(default_factory=list)
    redo_stack: list[Scene] = field(default_factory=list)
    saved: Scene | None = None

    def __post_init__(self) -> None:
        self.saved = self.scene

    def push(self, scene: Scene) -> None:
        if scene is self.scene:
            return
        self.undo_stack.append(self.scene)
        self.redo_stack.clear()
        self.scene = scene

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append(self.scene)
        self.scene = self.undo_stack.pop()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append(self.scene)
        self.scene = self.redo_stack.pop()
        return True

    @property
    def dirty(self) -> bool:
        return self.scene is not self.saved

    def mark_saved(self) -> None:
        self.saved = self.scene
