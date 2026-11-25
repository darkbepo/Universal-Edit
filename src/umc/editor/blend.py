"""Layer blend modes (port: web/src/editor/blend.ts; shared cases tests/fixtures/blend_cases.json).

The separable modes of the W3C compositing spec, mixed with source-over:
``co = αs·(1−αb)·Cs + αs·αb·B(Cb, Cs) + (1−αs)·αb·Cb``, ``αo = αs + αb·(1−αs)``. "normal" is
plain source-over (Pillow's ``alpha_composite``, as before blend modes existed).
OpenRaster stores the mode as ``composite-op`` (``svg:multiply`` …), which GIMP and Krita read.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

# (id, label) in menu order; ids are the CSS / SVG names
MODES: tuple[tuple[str, str], ...] = (
    ("normal", "Normal"),
    ("multiply", "Multiply"),
    ("screen", "Screen"),
    ("overlay", "Overlay"),
    ("darken", "Darken"),
    ("lighten", "Lighten"),
    ("color-dodge", "Colour dodge"),
    ("color-burn", "Colour burn"),
    ("hard-light", "Hard light"),
    ("soft-light", "Soft light"),
    ("difference", "Difference"),
    ("exclusion", "Exclusion"),
)
IDS = tuple(m for m, _ in MODES)


def ora_op(mode: str) -> str:
    return "svg:src-over" if mode == "normal" else f"svg:{mode}"


def from_ora(op: str | None) -> str:
    """``composite-op`` → a mode; anything we don't do (or none) is normal."""
    name = (op or "").removeprefix("svg:")
    return name if name in IDS and name != "normal" else "normal"


def mix(mode: str, cb: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """B(Cb, Cs) for colour values in 0…1 (float64 arrays)."""
    if mode == "multiply":
        return cb * cs
    if mode == "screen":
        return cb + cs - cb * cs
    if mode == "overlay":
        return mix("hard-light", cs, cb)
    if mode == "darken":
        return np.minimum(cb, cs)
    if mode == "lighten":
        return np.maximum(cb, cs)
    if mode == "color-dodge":
        safe = np.where(cs < 1, 1 - cs, 1.0)
        out = np.minimum(1.0, cb / safe)
        return np.where(cb == 0, 0.0, np.where(cs >= 1, 1.0, out))
    if mode == "color-burn":
        safe = np.where(cs > 0, cs, 1.0)
        out = 1 - np.minimum(1.0, (1 - cb) / safe)
        return np.where(cb >= 1, 1.0, np.where(cs <= 0, 0.0, out))
    if mode == "hard-light":
        return np.where(cs <= 0.5, cb * (2 * cs), mix("screen", cb, 2 * cs - 1))
    if mode == "soft-light":
        d = np.where(cb <= 0.25, ((16 * cb - 12) * cb + 4) * cb, np.sqrt(cb))
        return np.where(cs <= 0.5, cb - (1 - 2 * cs) * cb * (1 - cb), cb + (2 * cs - 1) * (d - cb))
    if mode == "difference":
        return np.abs(cb - cs)
    if mode == "exclusion":
        return cb + cs - 2 * cb * cs
    raise ValueError(mode)


def blend_over(dst: Image.Image, src: Image.Image, pos: tuple[int, int], mode: str) -> None:
    """Draw RGBA ``src`` onto RGBA ``dst`` at ``pos`` (in place), mixed with ``mode``."""
    if mode == "normal" or mode not in IDS:
        dst.alpha_composite(src, pos)
        return
    x0, y0 = pos
    left, top = max(0, -x0), max(0, -y0)
    right = min(src.width, dst.width - x0)
    bottom = min(src.height, dst.height - y0)
    if right <= left or bottom <= top:
        return
    s = np.asarray(src.crop((left, top, right, bottom)), dtype=np.float64) / 255
    box = (x0 + left, y0 + top, x0 + right, y0 + bottom)
    b = np.asarray(dst.crop(box), dtype=np.float64) / 255
    sa, ba = s[..., 3:4], b[..., 3:4]
    cs, cb = s[..., :3], b[..., :3]
    co = sa * (1 - ba) * cs + sa * ba * mix(mode, cb, cs) + (1 - sa) * ba * cb
    ao = sa + ba * (1 - sa)
    colour = np.where(ao > 0, co / np.where(ao > 0, ao, 1.0), 0.0)
    out = np.concatenate([colour, ao], axis=-1)
    px = np.clip(np.floor(out * 255 + 0.5), 0, 255).astype(np.uint8)
    dst.paste(Image.fromarray(px, "RGBA"), box[:2])
