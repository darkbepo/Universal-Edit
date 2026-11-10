"""Tone and colour adjustments (no Qt): exposure, brightness / contrast, temperature / tint,
levels, curves, saturation and vibrance.

``Adjustments`` holds every setting (all neutral by default). ``apply_array`` runs them in a fixed
order: exposure → temperature / tint → brightness / contrast → levels (channel, then all) →
curves (channel, then all) → saturation / vibrance. Everything up to curves works on one colour
channel at a time, so it is folded into one 256-entry table per channel first (fast on big
photos); saturation and vibrance then mix the channels per pixel.

``web/src/editor/adjust.ts`` is a line-by-line port — keep the two in step; both test suites
check the same pixels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from PIL import Image

from umc.editor import effects
from umc.editor.document import Document
from umc.editor.history import Command

CHANNELS = ("rgb", "r", "g", "b")  # "rgb" = all colours together


@dataclass(frozen=True)
class Levels:
    black: int = 0  # input black point (0…254)
    white: int = 255  # input white point (black+1…255)
    gamma: float = 1.0  # mid-tones (0.1…10; > 1 brightens)
    out_black: int = 0
    out_white: int = 255

    @property
    def identity(self) -> bool:
        return self == Levels()


IDENTITY_CURVE: tuple[tuple[int, int], ...] = ((0, 0), (255, 255))


@dataclass(frozen=True)
class Adjustments:
    exposure: float = 0.0  # stops, −3…+3
    brightness: int = 0  # −100…+100
    contrast: int = 0  # −100…+100
    temperature: int = 0  # −100 (cooler) … +100 (warmer)
    tint: int = 0  # −100 (greener) … +100 (more magenta)
    saturation: int = 0  # −100 (grey) … +100
    vibrance: int = 0  # −100…+100: mostly the dull colours, skin tones half as much
    levels: dict[str, Levels] = field(default_factory=dict)
    curves: dict[str, tuple[tuple[int, int], ...]] = field(default_factory=dict)

    @property
    def identity(self) -> bool:
        return (
            self.exposure == 0
            and self.brightness == self.contrast == self.temperature == self.tint == 0
            and self.saturation == self.vibrance == 0
            and all(lv.identity for lv in self.levels.values())
            and all(tuple(c) == IDENTITY_CURVE for c in self.curves.values())
        )

    def with_levels(self, channel: str, levels: Levels) -> Adjustments:
        return replace(self, levels={**self.levels, channel: levels})

    def with_curve(self, channel: str, points) -> Adjustments:
        return replace(self, curves={**self.curves, channel: tuple(points)})

    def __call__(self, image: Image.Image, scale: float = 1.0) -> Image.Image:
        return apply(image, self)  # an Effect (effects.py); nothing here depends on the size

    @classmethod
    def from_settings(cls, settings: dict) -> Adjustments:
        """From the JSON form used by the shared test cases and the looks file."""
        s = dict(settings)
        levels = {k: Levels(**v) for k, v in s.pop("levels", {}).items()}
        curves = {k: tuple(map(tuple, v)) for k, v in s.pop("curves", {}).items()}
        return cls(**s, levels=levels, curves=curves)


# ------------------------------------------------------------------ per-channel pieces


def _to_linear(v: np.ndarray) -> np.ndarray:
    v = v / 255.0
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _to_srgb(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    return 255.0 * np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def _levels(v: np.ndarray, lv: Levels) -> np.ndarray:
    t = np.clip((v - lv.black) / max(1, lv.white - lv.black), 0.0, 1.0)
    return lv.out_black + (lv.out_white - lv.out_black) * t ** (1.0 / lv.gamma)


def curve_table(points) -> np.ndarray:
    """256 output values for a curve through ``points`` (x, y in 0…255): a monotone cubic
    (Fritsch–Carlson), so it never overshoots between points; flat beyond the end points."""
    pts: dict[int, int] = {}
    for x, y in points:
        pts[int(x)] = int(y)  # the last point at an x wins
    xs = sorted(pts)
    ys = [float(pts[x]) for x in xs]
    n = len(xs)
    out = np.empty(256)
    if n == 0:
        return np.arange(256, dtype=np.float64)
    if n == 1:
        out[:] = ys[0]
        return out
    d = [(ys[k + 1] - ys[k]) / (xs[k + 1] - xs[k]) for k in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for k in range(1, n - 1):
        m[k] = 0.0 if d[k - 1] * d[k] <= 0 else (d[k - 1] + d[k]) / 2
    for k in range(n - 1):
        if d[k] == 0:
            m[k] = m[k + 1] = 0.0
            continue
        a, b = m[k] / d[k], m[k + 1] / d[k]
        s = a * a + b * b
        if s > 9:
            t = 3 / math.sqrt(s)
            m[k], m[k + 1] = t * a * d[k], t * b * d[k]
    k = 0
    for x in range(256):
        if x <= xs[0]:
            out[x] = ys[0]
            continue
        if x >= xs[-1]:
            out[x] = ys[-1]
            continue
        while xs[k + 1] < x:
            k += 1
        h = xs[k + 1] - xs[k]
        t = (x - xs[k]) / h
        t2, t3 = t * t, t * t * t
        out[x] = (
            (2 * t3 - 3 * t2 + 1) * ys[k]
            + (t3 - 2 * t2 + t) * h * m[k]
            + (-2 * t3 + 3 * t2) * ys[k + 1]
            + (t3 - t2) * h * m[k + 1]
        )
    return out


def tables(adj: Adjustments) -> np.ndarray:
    """3 × 256 uint8: what every per-channel step does to each value of R, G and B."""
    v = np.arange(256, dtype=np.float64)
    if adj.exposure:
        v = _to_srgb(_to_linear(v) * 2.0**adj.exposure)
    k, m = adj.temperature / 100, adj.tint / 100
    gains = (1 + 0.3 * k, 1 - 0.3 * m, 1 - 0.3 * k)  # warmer: more red, less blue
    contrast = 1 + adj.contrast / 100
    out = np.empty((3, 256), dtype=np.uint8)
    for c, name in enumerate("rgb"):
        x = np.clip(v * gains[c], 0, 255)
        if adj.brightness or adj.contrast:
            x = np.clip((x + adj.brightness * 2.55 - 128.0) * contrast + 128.0, 0, 255)
        for ch in (name, "rgb"):
            if ch in adj.levels and not adj.levels[ch].identity:
                x = _levels(x, adj.levels[ch])
        for ch in (name, "rgb"):
            if ch in adj.curves and tuple(adj.curves[ch]) != IDENTITY_CURVE:
                table = curve_table(adj.curves[ch])
                x = table[np.clip(np.floor(x + 0.5), 0, 255).astype(np.int64)]
        out[c] = np.clip(np.floor(x + 0.5), 0, 255).astype(np.uint8)
    return out


# ------------------------------------------------------------------ whole pixels


def apply_array(arr: np.ndarray, adj: Adjustments) -> np.ndarray:
    """``arr`` (H×W×4 uint8 RGBA) with ``adj`` applied; alpha is never changed."""
    if adj.identity:
        return arr.copy()
    t = tables(adj)
    out = arr.copy()
    for c in range(3):
        out[..., c] = t[c][arr[..., c]]
    if adj.saturation or adj.vibrance:
        # Strips of rows keep the temporary float arrays small (a 24 MP photo needed ~3 GB).
        for y in range(0, out.shape[0], BAND):
            _mix(out[y : y + BAND], adj)
    return out


BAND = 256  # rows per strip in the saturation / vibrance step

_LUMA = np.array([0.299, 0.587, 0.114])


def _mix(part: np.ndarray, adj: Adjustments) -> None:
    """Saturation / vibrance on ``part`` (rows of RGBA uint8), in place."""
    rgb = part[..., :3].astype(np.float64)
    luma = rgb @ _LUMA
    factor = np.full(luma.shape, 1 + adj.saturation / 100)
    if adj.vibrance:
        hi, lo = rgb.max(axis=-1), rgb.min(axis=-1)
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        weight = np.where((r > g) & (g > b), 0.5, 1.0)  # orange-ish hues: protect skin tones
        factor *= 1 + (adj.vibrance / 100) * (1 - (hi - lo) / 255.0) * weight
    rgb -= luma[..., None]
    rgb *= factor[..., None]
    rgb += luma[..., None] + 0.5
    np.floor(rgb, out=rgb)
    np.clip(rgb, 0, 255, out=rgb)
    part[..., :3] = rgb


def apply(image: Image.Image, adj: Adjustments) -> Image.Image:
    return Image.fromarray(apply_array(np.asarray(image.convert("RGBA")), adj), "RGBA")


def histogram(image: Image.Image) -> dict[str, np.ndarray]:
    """256 counts per channel ("rgb" = brightness) of the pixels that aren't fully transparent."""
    arr = np.asarray(image.convert("RGBA"))
    seen = arr[..., 3] > 0
    px = arr[seen][:, :3].astype(np.int64)
    luma = np.floor(px @ np.array([0.299, 0.587, 0.114]) + 0.5).astype(np.int64)
    hist = {"rgb": np.bincount(luma, minlength=256)[:256]}
    for c, name in enumerate("rgb"):
        hist[name] = np.bincount(px[:, c], minlength=256)[:256]
    return hist


def auto_levels(image: Image.Image, channel: str = "rgb", clip: float = 0.005) -> Levels:
    """Black / white points that stretch ``channel``'s tones (ignoring the darkest and
    brightest 0.5 %); "rgb" uses the brightness."""
    counts = histogram(image)[channel]
    total = int(counts.sum())
    if total == 0:
        return Levels()
    cum = np.cumsum(counts)
    black = int(np.searchsorted(cum, total * clip, side="right"))
    white = int(np.searchsorted(cum, total * (1 - clip), side="left"))
    black, white = min(black, 254), max(white, 0)
    if white <= black:
        return Levels()
    return Levels(black=black, white=white)


# ------------------------------------------------------------------ on the document
# (the generic live-preview / apply code lives in effects.py; these keep the old names)


def region_image(doc: Document) -> Image.Image | None:
    return effects.region_image(doc)


def preview(
    doc: Document, adj: Adjustments, max_pixels: int = effects.PREVIEW_PIXELS
) -> Image.Image:
    return effects.preview(doc, adj, max_pixels)


def apply_to_document(doc: Document, adj: Adjustments, label: str = "Adjust") -> Command | None:
    return effects.apply_to_document(doc, adj, label)
