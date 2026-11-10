"""Selections of any shape (no Qt).

``doc.selection`` is the rectangle around what is selected (canvas coordinates; None = nothing
selected), so everything that only needs "where" keeps working with a Rect. ``doc.selection_mask``
says how much of each pixel in that rectangle is selected (an "L" image the rectangle's size,
0…255, soft edges allowed); None means the whole rectangle — a plain rectangle selection.

Shapes are combined by mode: "replace", "add" (Shift), "subtract" (Alt) or "intersect"
(Shift+Alt). ``web/src/editor/selection.ts`` is the website's port.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import Command, SelectionCommand

MODES = ("replace", "add", "subtract", "intersect")


def mode_for(shift: bool, alt: bool) -> str:
    if shift and alt:
        return "intersect"
    return "add" if shift else "subtract" if alt else "replace"


# ------------------------------------------------------------------ whole-canvas masks


def full_mask(doc: Document) -> Image.Image:
    """The selection as an "L" image the size of the canvas (all 0 when nothing is selected)."""
    out = Image.new("L", (doc.width, doc.height), 0)
    sel = doc.selection
    if sel is not None:
        part = doc.selection_mask or Image.new("L", (sel.width, sel.height), 255)
        out.paste(part, (sel.x0, sel.y0))
    return out


def _normalise(full: Image.Image) -> tuple[Rect | None, Image.Image | None]:
    """(rectangle, mask) for a whole-canvas mask; no mask when it is a plain rectangle."""
    box = full.getbbox()
    if box is None:
        return None, None
    rect = Rect(*box)
    part = full.crop(box)
    lo, hi = part.getextrema()
    return rect, (None if lo == hi == 255 else part)


def set_selection(
    doc: Document, rect: Rect | None, mask: Image.Image | None, label: str
) -> Command | None:
    """Make (rect, mask) the selection; one undo step (None when nothing changed)."""
    before = (doc.selection, doc.selection_mask)
    if rect is not None and rect.empty:
        rect, mask = None, None
    if before[0] == rect and _same(before[1], mask):
        return None
    doc.selection, doc.selection_mask = rect, mask
    return SelectionCommand(before[0], rect, label, before[1], mask)


def _same(a: Image.Image | None, b: Image.Image | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.size == b.size and a.tobytes() == b.tobytes()


def from_full(doc: Document, full: Image.Image, label: str) -> Command | None:
    return set_selection(doc, *_normalise(full), label)


def combine(doc: Document, shape: Image.Image, mode: str, label: str = "Select") -> Command | None:
    """Combine a whole-canvas ``shape`` mask with the selection (see MODES)."""
    if mode == "replace":
        return from_full(doc, shape, label if shape.getbbox() else "Deselect")
    cur = np.asarray(full_mask(doc), dtype=np.uint16)
    new = np.asarray(shape, dtype=np.uint16)
    if mode == "add":
        out = np.maximum(cur, new)
    elif mode == "subtract":
        out = cur * (255 - new) // 255
    else:  # intersect
        out = cur * new // 255
    return from_full(doc, Image.fromarray(out.astype(np.uint8), "L"), label)


# ------------------------------------------------------------------ shapes


def rect_shape(doc: Document, rect: Rect) -> Image.Image:
    out = Image.new("L", (doc.width, doc.height), 0)
    r = rect.intersect(doc.bounds)
    if not r.empty:
        out.paste(255, r.box)
    return out


def ellipse_shape(doc: Document, rect: Rect) -> Image.Image:
    out = Image.new("L", (doc.width, doc.height), 0)
    if rect.width >= 1 and rect.height >= 1:
        ImageDraw.Draw(out).ellipse((rect.x0, rect.y0, rect.x1 - 1, rect.y1 - 1), fill=255)
    return out


def polygon_shape(doc: Document, points: list[tuple[float, float]]) -> Image.Image:
    out = Image.new("L", (doc.width, doc.height), 0)
    if len(points) >= 3:
        ImageDraw.Draw(out).polygon([(round(x), round(y)) for x, y in points], fill=255)
    return out


def wand_shape(
    doc: Document,
    x: float,
    y: float,
    tolerance: int,
    contiguous: bool = True,
    sample_all: bool = False,
) -> Image.Image:
    """Pixels with a colour like the one at (x, y): touching it (``contiguous``) or anywhere."""
    from umc.editor.tools.fill import flood_mask, similar

    out = Image.new("L", (doc.width, doc.height), 0)
    px, py = int(x), int(y)
    if not doc.bounds.contains(px, py):
        return out
    if sample_all:
        src = np.asarray(doc.composite(), dtype=np.uint8)
    else:
        layer = doc.active_layer
        src = np.zeros((doc.height, doc.width, 4), dtype=np.uint8)
        part = layer.bounds.intersect(doc.bounds)
        if not part.empty:
            src[part.y0 : part.y1, part.x0 : part.x1] = np.asarray(
                layer.image.crop(part.offset(-layer.x, -layer.y).box), dtype=np.uint8
            )
    candidates = similar(src, src[py, px], tolerance)
    hit = flood_mask(candidates, px, py) if contiguous else candidates
    return Image.fromarray(hit.astype(np.uint8) * 255, "L")


# ------------------------------------------------------------------ menu actions


def select_all(doc: Document) -> Command | None:
    return set_selection(doc, doc.bounds, None, "Select all")


def deselect(doc: Document) -> Command | None:
    return set_selection(doc, None, None, "Deselect")


def invert(doc: Document) -> Command | None:
    full = full_mask(doc)
    return from_full(doc, Image.eval(full, lambda v: 255 - v), "Invert selection")


def feather(doc: Document, radius: float) -> Command | None:
    """Soften the selection's edge by ``radius`` px (a Gaussian blur of the mask)."""
    if doc.selection is None or radius <= 0:
        return None
    full = full_mask(doc).filter(ImageFilter.GaussianBlur(radius))
    return from_full(doc, full, "Feather selection")


# ------------------------------------------------------------------ for tools and drawing


def weight(doc: Document, rect: Rect) -> np.ndarray | None:
    """How much each pixel of ``rect`` (canvas coordinates, inside the selection rectangle) is
    selected, 0…1 (float32, H×W); None = all of it (no mask)."""
    if doc.selection is None or doc.selection_mask is None:
        return None
    part = rect.offset(-doc.selection.x0, -doc.selection.y0)
    return np.asarray(doc.selection_mask.crop(part.box), dtype=np.float32) / 255.0


def outline(doc: Document) -> list[tuple[int, int, int, int]]:
    """The edges of the selected area (≥ half selected), as merged horizontal and vertical line
    segments (x0, y0, x1, y1) in canvas coordinates — what the marching ants follow."""
    sel = doc.selection
    if sel is None:
        return []
    if doc.selection_mask is None:
        x0, y0, x1, y1 = sel.box
        return [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]
    m = np.asarray(doc.selection_mask) >= 128
    pad = np.zeros((m.shape[0] + 2, m.shape[1] + 2), dtype=bool)
    pad[1:-1, 1:-1] = m
    segs: list[tuple[int, int, int, int]] = []
    rows = pad[1:, 1:-1] != pad[:-1, 1:-1]  # edge above each row (h+1 rows)
    for y in np.flatnonzero(rows.any(axis=1)):
        for a, b in _runs(rows[y]):
            segs.append((sel.x0 + a, sel.y0 + int(y), sel.x0 + b, sel.y0 + int(y)))
    cols = pad[1:-1, 1:] != pad[1:-1, :-1]  # edge left of each column (w+1 columns)
    for x in np.flatnonzero(cols.any(axis=0)):
        for a, b in _runs(cols[:, x]):
            segs.append((sel.x0 + int(x), sel.y0 + a, sel.x0 + int(x), sel.y0 + b))
    return segs


def _runs(line: np.ndarray) -> list[tuple[int, int]]:
    edges = np.diff(np.concatenate(([0], line.view(np.int8), [0])))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]
