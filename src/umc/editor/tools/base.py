"""What every editor tool looks like, and shared pixel helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from umc.editor.document import Document
from umc.editor.geometry import EMPTY, Rect
from umc.editor.history import Command
from umc.editor.layer import Layer

RGBA = tuple[int, int, int, int]


@dataclass
class ToolContext:
    primary: RGBA
    secondary: RGBA
    options: Any
    shift: bool = False
    button: str = "left"  # "left" | "right"
    font_family: str = "DejaVu Sans"

    @property
    def color(self) -> RGBA:
        return self.secondary if self.button == "right" else self.primary


class Tool:
    """Mouse-driven tool. Coordinates are canvas pixels (floats).

    ``press`` / ``move`` return the canvas rectangle to repaint (None = nothing);
    ``release`` returns the undoable Command for what the gesture changed.
    """

    id: str = ""
    label: str = ""
    shortcut: str = ""
    options_cls: type | None = None
    cursor: str = "cross"  # "cross" | "brush" | "pick" | "text" | "arrow"

    def press(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        return None

    def move(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Rect | None:
        return None

    def release(self, doc: Document, x: float, y: float, ctx: ToolContext) -> Command | None:
        return None

    def preview(self, doc: Document) -> tuple[Image.Image, int, int] | None:
        return None

    def cancel(self, doc: Document) -> Rect | None:
        return None


def clip_rect(doc: Document, layer: Layer) -> Rect:
    """Where a tool may change ``layer``, in layer coordinates (canvas ∩ layer ∩ selection)."""
    r = layer.bounds.intersect(doc.bounds)
    if doc.selection is not None:
        r = r.intersect(doc.selection)
    return r.offset(-layer.x, -layer.y) if not r.empty else EMPTY


def blend_over(base: np.ndarray, color: RGBA, coverage: np.ndarray) -> np.ndarray:
    """Source-over ``color`` onto ``base`` (uint8 HxWx4) with per-pixel ``coverage`` 0..1."""
    b = base.astype(np.float32) / 255.0
    ca = (color[3] / 255.0) * coverage
    ba = b[..., 3]
    out_a = ca + ba * (1.0 - ca)
    c = np.array(color[:3], dtype=np.float32) / 255.0
    safe = np.where(out_a > 0, out_a, 1.0)
    rgb = (c * ca[..., None] + b[..., :3] * (ba * (1.0 - ca))[..., None]) / safe[..., None]
    out = np.empty_like(b)
    out[..., :3] = rgb
    out[..., 3] = out_a
    return np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)


def paste_array(img: Image.Image, arr: np.ndarray, rect: Rect) -> None:
    img.paste(Image.fromarray(arr, "RGBA"), (rect.x0, rect.y0))


def array_of(img: Image.Image, rect: Rect) -> np.ndarray:
    return np.asarray(img.crop(rect.box), dtype=np.uint8)
