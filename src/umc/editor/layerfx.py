"""Layer effects: drop shadow, glow, outline and colour overlay — live, kept on the layer.

``Layer.fx`` holds a ``LayerFx`` (None = no effects). The layer's own pixels never change: the
effects are drawn whenever the picture is put together (``Document.composite``), merged, or
rasterised, so they follow every later edit. Drawing order, bottom to top: shadow, glow,
outline (around the outside), then the layer itself with the colour overlay on top of it.
The effects are made from what the layer shows (its mask applied), before its opacity.

``web/src/editor/layerfx.ts`` is the website's port (same fields, numbers and order; the blur
is the website's own Gaussian approximation, so edges can differ by a little).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

import numpy as np
from PIL import Image, ImageFilter

from umc.core.options import opt, option_specs
from umc.editor.geometry import Rect

# id, label (window title / undo step), icon (Tabler) — in the order the rail lists them
EFFECTS: list[tuple[str, str, str]] = [
    ("shadow", "Drop shadow", "shadow"),
    ("glow", "Glow", "sun-high"),
    ("outline", "Outline", "border-outer"),
    ("overlay", "Colour overlay", "color-swatch"),
]
LABELS = {key: label for key, label, _icon in EFFECTS}


@dataclass(frozen=True)
class LayerFx:
    """Every effect's settings; an effect is drawn only while its on/off field is True."""

    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: int = 60  # %
    shadow_angle: int = 45  # the direction the shadow falls, degrees clockwise from the right
    shadow_distance: int = 8  # px
    shadow_blur: int = 8  # px (softness)
    glow: bool = False
    glow_color: str = "#ffe680"
    glow_opacity: int = 80
    glow_size: int = 12
    outline: bool = False
    outline_color: str = "#000000"
    outline_width: int = 3
    overlay: bool = False
    overlay_color: str = "#ff0000"
    overlay_opacity: int = 100

    def any(self) -> bool:
        return self.shadow or self.glow or self.outline or self.overlay

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> LayerFx:
        known = {f.name: f.type for f in fields(cls)}
        clean = {}
        for k, v in d.items():
            if k not in known:
                continue
            kind = known[k]
            try:
                clean[k] = bool(v) if kind == "bool" else int(v) if kind == "int" else _hex(str(v))
            except (TypeError, ValueError):
                continue
            if kind == "int":  # a damaged file must not ask for a million-pixel outline
                lo, hi = LIMITS[k]
                clean[k] = max(lo, min(hi, clean[k]))
        return cls(**clean)


def _hex(value: str) -> str:
    v = value.strip().lower()
    if len(v) == 7 and v[0] == "#":
        int(v[1:], 16)
        return v
    raise ValueError(value)


def _rgb(value: str) -> tuple[int, int, int]:
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _round(v: float) -> int:
    """Halves away from zero for positives (as JavaScript's Math.round), not banker's rounding."""
    return math.floor(v + 0.5)


# ---------------------------------------------------------------- settings windows


@dataclass
class ShadowOptions:
    shadow: bool = opt(True, "Show drop shadow")
    shadow_color: str = opt("#000000", "Colour", kind="color")
    shadow_opacity: int = opt(60, "Opacity", kind="slider", min=0, max=100, unit="%")
    shadow_angle: int = opt(45, "Direction", kind="slider", min=0, max=359, unit="°")
    shadow_distance: int = opt(8, "Distance", kind="slider", min=0, max=200, unit="px")
    shadow_blur: int = opt(8, "Softness", kind="slider", min=0, max=100, unit="px")


@dataclass
class GlowOptions:
    glow: bool = opt(True, "Show glow")
    glow_color: str = opt("#ffe680", "Colour", kind="color")
    glow_opacity: int = opt(80, "Opacity", kind="slider", min=0, max=100, unit="%")
    glow_size: int = opt(12, "Size", kind="slider", min=1, max=100, unit="px")


@dataclass
class OutlineOptions:
    outline: bool = opt(True, "Show outline")
    outline_color: str = opt("#000000", "Colour", kind="color")
    outline_width: int = opt(3, "Width", kind="slider", min=1, max=50, unit="px")


@dataclass
class OverlayOptions:
    overlay: bool = opt(True, "Show colour overlay")
    overlay_color: str = opt("#ff0000", "Colour", kind="color")
    overlay_opacity: int = opt(100, "Strength", kind="slider", min=0, max=100, unit="%")


OPTIONS = {
    "shadow": ShadowOptions,
    "glow": GlowOptions,
    "outline": OutlineOptions,
    "overlay": OverlayOptions,
}


def window_values(fx: LayerFx | None, key: str) -> dict:
    """The settings window's values for effect ``key`` of a layer (``fx`` None = no effects:
    the window shows the defaults, switched off)."""
    names = [f.name for f in fields(OPTIONS[key])]
    current = fx if fx is not None else LayerFx()
    values = {n: getattr(current, n) for n in names}
    if fx is None or not getattr(fx, key):
        # what a fresh effect starts with, off until "Show" is ticked (or the window adds it)
        values[key] = False
    return values


def with_values(fx: LayerFx | None, values: dict) -> LayerFx | None:
    """``fx`` with a window's ``values`` set. An effect switched off keeps its settings (so
    ticking "Show" again brings them back); None only when everything is back to the defaults."""
    names = {f.name for f in fields(LayerFx)}
    new = replace(fx or LayerFx(), **{k: v for k, v in values.items() if k in names})
    return None if new == LayerFx() else new


# (min, max) of every number, from the settings windows; files are clamped to these
LIMITS = {
    spec.name: (spec.min, spec.max)
    for cls in OPTIONS.values()
    for spec in option_specs(cls)
    if spec.kind == "slider"
}


# ---------------------------------------------------------------- drawing


def _sigma(px: int) -> float:
    return px / 2


def _shadow_offset(fx: LayerFx) -> tuple[int, int]:
    a = math.radians(fx.shadow_angle)
    return _round(math.cos(a) * fx.shadow_distance), _round(math.sin(a) * fx.shadow_distance)


def margin(fx: LayerFx | None) -> int:
    """How far the effects reach past the layer's pixels (all sides)."""
    if fx is None:
        return 0
    m = 0
    if fx.shadow:
        dx, dy = _shadow_offset(fx)
        m = max(m, max(abs(dx), abs(dy)) + math.ceil(3 * _sigma(fx.shadow_blur)))
    if fx.glow:
        m = max(m, math.ceil(3 * _sigma(fx.glow_size)))
    if fx.outline:
        m = max(m, fx.outline_width)
    return m


def bounds(layer) -> Rect:
    """Where the layer draws anything, effects included."""
    b = layer.bounds
    m = margin(layer.fx) if layer.fx is not None and layer.fx.any() else 0
    return Rect(b.x0 - m, b.y0 - m, b.x1 + m, b.y1 + m) if m else b


def _blur(alpha: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur of a 0…1 float plane (same numbers as Pillow's GaussianBlur on 8 bit)."""
    if sigma <= 0:
        return alpha
    img = Image.fromarray(np.clip(alpha * 255 + 0.5, 0, 255).astype(np.uint8), "L")
    return np.asarray(img.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32) / 255


def _shift(plane: np.ndarray, dx: int, dy: int) -> np.ndarray:
    out = np.zeros_like(plane)
    h, w = plane.shape
    if abs(dx) >= w or abs(dy) >= h:
        return out
    src = plane[max(0, -dy) : h - max(0, dy), max(0, -dx) : w - max(0, dx)]
    out[max(0, dy) : max(0, dy) + src.shape[0], max(0, dx) : max(0, dx) + src.shape[1]] = src
    return out


def dilate(alpha: np.ndarray, width: int) -> np.ndarray:
    """Grow the shape by ``width`` px: one-pixel steps alternating a cross and a square, which
    makes a near-round (octagonal) outline. Soft edges stay soft (max of neighbours)."""
    try:
        import cv2
    except ImportError:  # the slower numpy steps below
        cv2 = None
    if cv2 is not None and width > 0:
        cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
        square = np.ones((3, 3), np.uint8)
        out = np.ascontiguousarray(alpha, dtype=np.float32)
        for step in range(width):
            out = cv2.dilate(out, square if step % 2 else cross, borderValue=0)
        return out
    out = alpha
    for step in range(width):
        grown = out.copy()
        # every step grows left / right / up / down; odd steps also grow diagonally
        neighbours = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        if step % 2 == 1:
            neighbours += [(1, 1), (1, -1), (-1, 1), (-1, -1)]
        for dx, dy in neighbours:
            np.maximum(grown, _shift(out, dx, dy), out=grown)
        out = grown
    return out


def _over(dst: np.ndarray, rgb: np.ndarray, a: np.ndarray) -> None:
    """Paint colour ``rgb`` (3 floats or an H×W×3 array, 0…255) with coverage ``a`` over the
    straight-alpha float image ``dst`` (H×W×4, alpha 0…1), in place."""
    da = dst[..., 3]
    oa = a + da * (1 - a)
    k = da * (1 - a)
    safe = np.where(oa > 0, oa, 1)
    for c in range(3):
        col = rgb[..., c] if np.ndim(rgb) == 3 else rgb[c]
        dst[..., c] = np.where(oa > 0, (col * a + dst[..., c] * k) / safe, 0)
    dst[..., 3] = oa


def render(pixels: Image.Image, fx: LayerFx | None) -> Image.Image:
    """``pixels`` (RGBA, what the layer shows) with ``fx`` drawn around / over it, same size.
    Pass enough transparent border for the effects to fit (``margin``)."""
    if fx is None or not fx.any():
        return pixels
    src = np.asarray(pixels, dtype=np.float32)
    alpha = src[..., 3] / 255
    out = np.zeros_like(src)
    if fx.shadow:
        dx, dy = _shadow_offset(fx)
        a = _shift(_blur(alpha, _sigma(fx.shadow_blur)), dx, dy) * (fx.shadow_opacity / 100)
        _over(out, np.array(_rgb(fx.shadow_color), np.float32), a)
    if fx.glow:
        a = np.minimum(_blur(alpha, _sigma(fx.glow_size)) * 2, 1) * (fx.glow_opacity / 100)
        _over(out, np.array(_rgb(fx.glow_color), np.float32), a)
    if fx.outline:
        _over(out, np.array(_rgb(fx.outline_color), np.float32), dilate(alpha, fx.outline_width))
    rgb = src[..., :3]
    if fx.overlay:
        k = fx.overlay_opacity / 100
        rgb = rgb * (1 - k) + np.array(_rgb(fx.overlay_color), np.float32) * k
    _over(out, rgb, alpha)
    out[..., 3] *= 255
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA")


# The last few whole-layer renders: repaints of the whole picture (moving the layer, renaming or
# hiding another one…) reuse them. Keyed by the pixels' checksum, so strokes painted into the
# layer in place (and undo patches) are noticed.
_CACHE: list[tuple[tuple, Image.Image]] = []
_CACHE_SIZE = 4


def _checksum(img: Image.Image | None) -> int | None:
    import zlib

    return None if img is None else zlib.crc32(img.tobytes())


def _whole(layer) -> Image.Image:
    """The whole layer with its effects, padded by ``margin`` on every side (cached)."""
    from umc.editor.layer import Layer

    key = (layer.image.size, _checksum(layer.image), _checksum(layer.mask), layer.fx)
    for k, img in _CACHE:
        if k == key:
            return img
    m = margin(layer.fx)
    padded = Image.new("RGBA", (layer.image.width + 2 * m, layer.image.height + 2 * m))
    padded.paste(Layer.pixels(layer), (m, m))
    img = render(padded, layer.fx)
    _CACHE.append((key, img))
    del _CACHE[:-_CACHE_SIZE]
    return img


def region(layer, rect: Rect, image: Image.Image | None = None) -> Image.Image:
    """What ``layer`` shows inside canvas ``rect`` (mask and effects, not its opacity), as an
    image of ``rect``'s size. ``image`` replaces the layer's pixels (live previews)."""
    from umc.editor.layer import Layer

    src_layer = layer if image is None else replace(layer, image=image)
    if src_layer.mask is not None and src_layer.mask.size != src_layer.image.size:
        src_layer = replace(src_layer, mask=None)  # a stale mask (preview of another size)
    m = margin(layer.fx)
    whole = bounds(src_layer)
    if rect.width * rect.height * 4 >= whole.width * whole.height:
        # a big part of it (a full repaint): draw the whole layer once and reuse it
        x, y = rect.x0 - whole.x0, rect.y0 - whole.y0
        return _whole(src_layer).crop((x, y, x + rect.width, y + rect.height))
    need = Rect(rect.x0 - m, rect.y0 - m, rect.x1 + m, rect.y1 + m)
    part = need.intersect(src_layer.bounds)
    padded = Image.new("RGBA", (need.width, need.height))
    if not part.empty:
        box = part.offset(-src_layer.x, -src_layer.y).box
        padded.paste(Layer.pixels(src_layer, box), (part.x0 - need.x0, part.y0 - need.y0))
    return render(padded, layer.fx).crop((m, m, m + rect.width, m + rect.height))


def baked(layer):
    """``layer`` with its effects (and mask) drawn into its pixels: a plain layer, grown to fit
    them. Rasterising and merging use this."""
    if layer.fx is None or not layer.fx.any():
        return replace(layer, fx=None)
    b = bounds(layer)
    return replace(
        layer, image=region(layer, b), x=b.x0, y=b.y0, mask=None, fx=None, text=None, shape=None
    )
