"""What the 3D viewer shows and saves once logos are added: the opened model plus logos, with
undo (Qt-free; port: web/src/editor3d/scene.ts).

A ``Logo`` keeps its picture, settings and where it sits, and its meshes, built from those by
``build_logo``. The scene is immutable: every edit returns a new one, and ``History`` keeps
the older ones for undo. ``combined`` is one ViewModel for the view and the info panel;
``export`` writes everything as one object (slicers merge the overlapping parts).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from umc.editor3d import arrange as A
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
    fit: str = "flat"  # on the model: flat (flat top, bottom follows the surface) / wrap
    style: str = "raised"  # on the model: raised / engraved (cut into it when saving)
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
    overhang: int = 0  # logo points whose projection misses the model
    overlap: float = 0.0  # mm³ inside the model / plate (sunk in), so the volume counts it once
    folded: int = 0  # wrapped triangles where the surface turns away (> 75°)

    @property
    def engraved(self) -> bool:
        return self.settings.where == "face" and self.settings.style == "engraved"


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
    face_tris: list[int] | None = None,  # unused since logos follow the surface (old callers)
    shp: L.Shape | None = None,
    quick: bool = False,
) -> Logo:
    """Trace (unless ``shp`` is given: dragging re-places without tracing again) + extrude +
    place. On a model (``base``) the logo is fitted to its surface (``s.fit``); ``quick``
    (while dragging) skips that and stands it on the tangent plane like before. Raises
    logo.LogoError in plain words."""
    if shp is None:
        shp = shape_of(pic, s)
    overhang = 0
    folded = 0
    sunk = 0.0
    overlap: float | None = None
    if s.where == "face":
        sunk = s.sink
        if point is None or normal is None:
            raise L.LogoError("Click the model to place the logo.")
        if base is not None and not quick:
            from umc.editor3d import pick

            x, y, n = L.frame(normal, s.rotation)
            surf = pick.surface(base.vertices, base.faces, point, np.array([x, y, n]), reach(shp))
            fitted = L.fitted_logo(
                shp, s.thickness, point, normal, s.rotation, s.sink, s.fit, surf, s.style
            )
            meshes = (fitted.mesh,)
            overhang, folded = fitted.missed, fitted.folded
            if s.style == "engraved":  # a cut: the info volume loses area × depth
                overlap = abs(fitted.mesh.volume) + shp.area * s.thickness
        else:  # no model, or dragging: on the tangent plane (instant)
            meshes = (L.placed_logo(shp, s.thickness, point, normal, s.rotation, s.sink),)
    else:
        parts = L.badge(shp, s.thickness, s.plate, s.margin, s.plate_thickness, s.hole)
        if s.plate != "none":
            sunk = min(L.SINK_MM, s.plate_thickness / 2)
        x = 0.0
        if base is not None:
            _lo, hi = base.bounds
            left = shp.width / 2  # how far the badge reaches left of its centre
            if s.plate != "none":
                outline, _hole = L.plate_outline(shp, s.margin, s.plate, s.hole)
                left = -min(x for x, _y in outline)
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
        shp.area * sunk if overlap is None else overlap,
        folded,
    )


def reach(shp: L.Shape) -> float:
    """How far the logo reaches from its centre (mm), whichever way it is turned."""
    return max((math.hypot(x, y) for p in shp.polygons for x, y in p.outer), default=0.0)


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

    def with_base(self, base: ViewModel | None) -> Scene:
        """The model replaced (Repair), logos kept."""
        return replace(self, base=base)

    def arranged(self, m: A.Matrix) -> Scene:
        """Everything moved / turned / scaled by ``m``: the model and every logo. A logo keeps
        what Change… needs (point, direction, its turn on the face, sizes scaled with it); when
        ``m`` mirrors, logos are made again the right way round (text never reads backwards)."""
        det = A.determinant3(m)
        k = abs(det) ** (1 / 3)
        base = self.base
        if base is not None:
            v, f = A.apply(base.vertices, base.faces, m)
            volume = base.volume_mm3 * abs(det) if base.volume_mm3 else None
            base = ViewModel(base.name, v, f, base.parts, base.watertight, volume)
        logos = []
        for lg in self.logos:
            s = lg.settings
            point = A.apply_point(m, lg.point) if lg.point is not None else None
            normal = A.apply_normal(m, lg.normal) if lg.normal is not None else None
            wide = _stretch(m, lg.normal or (0.0, 0.0, 1.0), s.rotation, 0)  # along its width
            tall = _stretch(m, lg.normal or (0.0, 0.0, 1.0), s.rotation, 1)
            s = replace(
                s,
                width=s.width * wide,
                thickness=s.thickness * k,
                sink=s.sink * k,
                margin=s.margin * k,
                plate_thickness=s.plate_thickness * k,
                rotation=_turned(m, lg.normal or (0.0, 0.0, 1.0), normal or (0.0, 0.0, 1.0), s),
            )
            if det < 0 and s.where == "face":  # made again where it now is, not mirrored
                try:
                    again = build_logo(lg.picture, s, point, normal, base)
                    logos.append(replace(again, overhang=lg.overhang))
                    continue
                except L.LogoError:
                    pass
            if det < 0 and s.where != "face":
                # a badge: moved to its mirrored place, never turned inside out (two parallel
                # mirrors = a move)
                c = _centre(lg.meshes)
                shift = np.asarray(A.apply_point(m, c)) - np.asarray(c)
                meshes = tuple(L.Mesh(x.vertices + shift, x.faces) for x in lg.meshes)
                logos.append(
                    replace(lg, settings=replace(s, rotation=lg.settings.rotation), meshes=meshes)
                )
                continue
            meshes = tuple(L.Mesh(*A.apply(x.vertices, x.faces, m)) for x in lg.meshes)
            logos.append(
                replace(
                    lg, settings=s, meshes=meshes, point=point, normal=normal,
                    width=lg.width * wide, height=lg.height * tall,
                    overlap=lg.overlap * wide * tall * k,
                )
            )  # fmt: skip
        return Scene(base, tuple(logos))

    @property
    def nbytes(self) -> int:
        """Memory the model's own arrays take (for the undo limit)."""
        b = self.base
        return 0 if b is None else int(b.vertices.nbytes + b.faces.nbytes)

    def positions(self) -> np.ndarray:
        """Every corner of the model and the logos (for bounds)."""
        parts = [v for v, _f in self.meshes()]
        return np.concatenate(parts) if parts else np.zeros((0, 3))

    @property
    def empty(self) -> bool:
        return self.base is None and not self.logos

    def meshes(self) -> list[tuple[np.ndarray, np.ndarray]]:
        out = []
        if self.base is not None:
            out.append((self.base.vertices, self.base.faces))
        out += [(m.vertices, m.faces) for lg in self.logos for m in lg.meshes]
        return out

    def engraved_meshes(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """The engraved logos' cutters (the page draws them tinted, over the model)."""
        return [(m.vertices, m.faces) for lg in self.logos if lg.engraved for m in lg.meshes]

    def combined(self, name: str = "") -> ViewModel | None:
        """One ViewModel of everything (parts = the model's + one per logo piece)."""
        if self.empty:
            return None
        if not self.logos:
            return self.base
        parts = [(self.base.vertices, self.base.faces)] if self.base is not None else []
        parts += [(m.vertices, m.faces) for lg in self.logos if not lg.engraved for m in lg.meshes]
        # engraved cutters are drawn apart, tinted (``engraved_meshes``)
        if not parts:
            parts = [(np.zeros((0, 3)), np.zeros((0, 3), np.int64))]
        offsets = np.cumsum([0] + [len(v) for v, _ in parts[:-1]])
        vertices = np.concatenate([v for v, _ in parts]).astype(np.float64)
        faces = np.concatenate([f + o for (_v, f), o in zip(parts, offsets, strict=True)])
        pieces = sum(len(lg.meshes) for lg in self.logos if not lg.engraved)
        base = self.base
        watertight = base.watertight if base is not None else True
        volume = None
        if watertight:
            volume = (
                (base.volume_mm3 if base is not None else 0.0)
                + sum(abs(m.volume) for lg in self.logos for m in lg.meshes)
                - sum(lg.overlap for lg in self.logos)
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
    return export_with_note(scene, ext, merge=False)[0]


MERGE_FAILED = (
    "Saved as separate overlapping parts: the model isn't one closed solid, so the logos "
    "couldn't be merged into it (Check → Repair may help)."
)
ENGRAVE_FAILED = (
    "Engraving needs a closed model — run Check → Repair first, or make the logo raised."
)


def needs_solid(scene: Scene) -> bool:
    return any(lg.engraved for lg in scene.logos)


def export_with_note(scene: Scene, ext: str, merge: bool = False) -> tuple[bytes, str]:
    """``export``, but with ``merge`` the model and its raised logos become one watertight
    body (boolean union) and engraved logos are cut out of it. A merge that fails falls back
    to the plain save and says why; engraving can't fall back, so it raises LogoError."""
    if ext not in SAVE_EXTS:
        raise ValueError(ext)
    parts = scene.meshes()
    if not parts:
        raise L.LogoError("There is nothing to save yet.")
    note = ""
    if merge or needs_solid(scene):
        from umc.editor3d import solid

        base = scene.base
        try:
            body = solid.combine(
                (base.vertices, base.faces) if base is not None else None,
                [(m.vertices, m.faces) for lg in scene.logos if not lg.engraved for m in lg.meshes],
                [(m.vertices, m.faces) for lg in scene.logos if lg.engraved for m in lg.meshes],
            )
            return _save([body], ext), ""
        except solid.SolidError:
            if needs_solid(scene):
                raise L.LogoError(ENGRAVE_FAILED) from None
            note = MERGE_FAILED
    return _save(parts, ext), note


def _save(parts: list[tuple[np.ndarray, np.ndarray]], ext: str) -> bytes:
    import trimesh

    from umc.converters.model3d import ModelOptions, save_model

    offsets = np.cumsum([0] + [len(v) for v, _ in parts[:-1]])
    mesh = trimesh.Trimesh(
        np.concatenate([v for v, _ in parts]),
        np.concatenate([f + o for (_v, f), o in zip(parts, offsets, strict=True)]),
        process=False,
    )
    opts = ModelOptions(center_on_bed=False, repair=False, merge_parts=True)
    return save_model(trimesh.Scene(mesh), ext, opts)


def _stretch(m: A.Matrix, normal, rotation: float, axis: int) -> float:
    """How much ``m`` lengthens the logo's width (axis 0) or height (1) direction."""
    x = L.frame(normal, rotation)[axis]
    return float(np.linalg.norm(np.array(m, dtype=np.float64)[:3, :3] @ x))


def _centre(meshes) -> tuple[float, float, float]:
    v = np.concatenate([x.vertices for x in meshes])
    return tuple(float(c) for c in (v.min(axis=0) + v.max(axis=0)) / 2)  # type: ignore[return-value]


def _turned(m: A.Matrix, old_normal, new_normal, s: LogoSettings) -> float:
    """The logo's turn on its face after ``m``: its "right" direction moved by ``m``, measured
    against the new face's own starting direction (``logo.frame``)."""
    x_old = L.frame(old_normal, s.rotation)[0]
    a = np.array(m, dtype=np.float64)[:3, :3]
    x = a @ x_old
    n = np.asarray(new_normal, float)
    x = x - n * float(x @ n)
    length = float(np.linalg.norm(x))
    if length < 1e-12:
        return s.rotation
    bx, by, _n = L.frame(n, 0.0)
    return math.degrees(math.atan2(float(x @ by), float(x @ bx)))


def default_save_name(source: Path | None, ext: str, logos: bool = True) -> str:
    """ "<name> with logo.3mf" / "<name> edited.3mf"; "logo.3mf" in the empty viewer."""
    if source is None:
        return f"logo.{ext}"
    return f"{source.stem} {'with logo' if logos else 'edited'}.{ext}"


UNDO_BYTES = 500 * 1024 * 1024  # models kept for undo (Arrange makes a new one each step)


@dataclass
class History:
    """Undo / redo of whole scenes. Logos share their meshes; an arranged model is a new
    copy, so the oldest steps are dropped once the models kept pass ``UNDO_BYTES``."""

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
        self._trim()

    def _trim(self) -> None:
        seen: set[int] = set()
        total = 0
        for i, sc in enumerate(reversed([*self.undo_stack, self.scene])):
            key = id(sc.base)
            if key not in seen:
                seen.add(key)
                total += sc.nbytes
            if total > UNDO_BYTES and i > 0:
                del self.undo_stack[: len(self.undo_stack) - i + 1]
                return

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

    def revert(self) -> None:
        """Back to what was last saved (or opened), forgetting undo / redo."""
        self.scene = self.saved
        self.undo_stack.clear()
        self.redo_stack.clear()
