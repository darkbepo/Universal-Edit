"""Content-aware fill: paint over the selected area from what surrounds it (no Qt).

Uses OpenCV's Telea inpainting (``cv2.inpaint``, fast marching from the edge inwards) on the
active layer — good for removing blemishes, wires, small objects and text. Soft selection edges
blend the result in. ``web/src/editor/inpaint.ts`` is the website's own Telea port.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from umc.core.errors import ConversionError
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, PatchCommand

RADIUS = 5  # how far around each pixel Telea looks (px)


def fill_mask(doc: Document, region: Rect) -> np.ndarray:
    """0…1 weights of the selection over ``region`` (canvas coordinates; may reach past it)."""
    out = np.zeros((region.height, region.width), dtype=np.float32)
    sel = doc.selection
    part = sel.intersect(region) if sel is not None else region
    if part.empty:
        return out
    ys, xs = (
        slice(part.y0 - region.y0, part.y1 - region.y0),
        slice(part.x0 - region.x0, part.x1 - region.x0),
    )
    if sel is not None and doc.selection_mask is not None:
        box = part.offset(-sel.x0, -sel.y0).box
        out[ys, xs] = np.asarray(doc.selection_mask.crop(box), dtype=np.float32) / 255.0
    else:
        out[ys, xs] = 1.0
    return out


def inpaint(rgba: np.ndarray, hole: np.ndarray, radius: int = RADIUS) -> np.ndarray:
    """``rgba`` (H×W×4 uint8) with the pixels where ``hole`` is true painted in."""
    import cv2

    mask = hole.astype(np.uint8) * 255
    rgb = cv2.inpaint(np.ascontiguousarray(rgba[..., :3]), mask, radius, cv2.INPAINT_TELEA)
    alpha = cv2.inpaint(np.ascontiguousarray(rgba[..., 3]), mask, radius, cv2.INPAINT_TELEA)
    return np.dstack([rgb, alpha])


def content_aware_fill(doc: Document, radius: int = RADIUS) -> Command | None:
    """Fill the selection on the active layer from its surroundings (one undo step)."""
    if doc.selection is None:
        raise ConversionError("Select what should disappear first (lasso, rectangle…).")
    layer = doc.active_layer
    sel = doc.selection.intersect(layer.bounds).intersect(doc.bounds)
    if sel.empty:
        return None
    pad = radius * 3 + 8  # enough surroundings to paint from
    region = Rect(sel.x0 - pad, sel.y0 - pad, sel.x1 + pad, sel.y1 + pad).intersect(layer.bounds)
    weight = fill_mask(doc, region)
    hole = weight > 0
    if not hole.any():
        return None
    if hole.all():
        raise ConversionError("Leave some of the picture unselected around it to fill from.")
    local = region.offset(-layer.x, -layer.y)
    before = layer.image.crop(local.box)
    base = np.asarray(before, dtype=np.uint8)
    filled = inpaint(base, hole, radius).astype(np.float32)
    # soft edges: mix on premultiplied colour, like every other selection-limited change
    t = weight[..., None]
    b = base.astype(np.float32)
    a0, a1 = b[..., 3:] / 255.0, filled[..., 3:] / 255.0
    alpha = a0 + (a1 - a0) * t
    rgb = (b[..., :3] * a0 + (filled[..., :3] * a1 - b[..., :3] * a0) * t) / np.where(
        alpha > 0, alpha, 1
    )
    out = np.dstack([rgb, alpha * 255.0])
    layer.image.paste(
        Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGBA"), local.box[:2]
    )
    idx = doc.active
    return PatchCommand.from_region("Content-aware fill", doc, idx, local, before)
