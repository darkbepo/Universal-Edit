"""Whole-image operations, layer-list operations and adjustments.

Each function applies its change and returns the Command to push onto the History
(None when nothing changed). Layers are replaced, never changed in place — see history.py.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PIL import Image

from umc.editor.document import Document, check_size, with_opacity
from umc.editor.geometry import Rect
from umc.editor.history import (
    Command,
    DocumentStateCommand,
    LayerListCommand,
    PatchCommand,
)
from umc.editor.layer import Layer
from umc.editor.tools.base import clip_rect

RESAMPLE = {
    "lanczos": Image.Resampling.LANCZOS,
    "bilinear": Image.Resampling.BILINEAR,
    "nearest": Image.Resampling.NEAREST,
}
ANCHORS = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)


def _state(doc: Document):
    return (doc.width, doc.height, list(doc.layers))


def _set_state(doc: Document, label: str, width: int, height: int, layers: list[Layer]):
    before = _state(doc)
    doc.width, doc.height, doc.layers = width, height, layers
    doc.active = min(doc.active, len(layers) - 1)
    doc.selection = None
    return DocumentStateCommand(label, before, _state(doc))


def _moved(
    layer: Layer, image: Image.Image | None = None, x: int | None = None, y: int | None = None
):
    return replace(
        layer,
        image=layer.image if image is None else image,
        x=layer.x if x is None else x,
        y=layer.y if y is None else y,
    )


# ------------------------------------------------------------------ canvas


def crop(doc: Document, rect: Rect) -> Command | None:
    rect = rect.intersect(doc.bounds)
    if rect.empty or rect == doc.bounds:
        return None
    canvas = Rect(0, 0, rect.width, rect.height)
    layers = []
    for layer in doc.layers:
        shifted = layer.bounds.offset(-rect.x0, -rect.y0)
        keep = shifted.intersect(canvas)
        if keep.empty:
            layers.append(_moved(layer, Image.new("RGBA", (1, 1)), 0, 0))
            continue
        img = layer.image.crop(keep.offset(-shifted.x0, -shifted.y0).box)
        layers.append(_moved(layer, img, keep.x0, keep.y0))
    return _set_state(doc, "Crop", rect.width, rect.height, layers)


def resize_image(
    doc: Document, width: int, height: int, resample: str = "lanczos"
) -> Command | None:
    check_size(width, height)
    if (width, height) == (doc.width, doc.height):
        return None
    sx, sy = width / doc.width, height / doc.height
    method = RESAMPLE.get(resample, Image.Resampling.LANCZOS)
    layers = []
    for layer in doc.layers:
        size = (max(1, round(layer.image.width * sx)), max(1, round(layer.image.height * sy)))
        layers.append(
            _moved(
                layer, layer.image.resize(size, method), round(layer.x * sx), round(layer.y * sy)
            )
        )
    return _set_state(doc, "Resize image", width, height, layers)


def canvas_size(doc: Document, width: int, height: int, anchor: str = "center") -> Command | None:
    check_size(width, height)
    if (width, height) == (doc.width, doc.height):
        return None
    row, col = divmod(ANCHORS.index(anchor) if anchor in ANCHORS else 4, 3)
    dx = (0, (width - doc.width) // 2, width - doc.width)[col]
    dy = (0, (height - doc.height) // 2, height - doc.height)[row]
    layers = [_moved(la, x=la.x + dx, y=la.y + dy) for la in doc.layers]
    return _set_state(doc, "Canvas size", width, height, layers)


_ROT = {
    90: Image.Transpose.ROTATE_270,  # Pillow's ROTATE_* are counter-clockwise
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def rotate(doc: Document, degrees: int, layer_only: bool = False) -> Command | None:
    """Rotate clockwise by 90, 180 or 270 degrees."""
    degrees %= 360
    if degrees not in _ROT:
        return None
    if layer_only:
        layer = doc.active_layer
        img = layer.image.transpose(_ROT[degrees])
        x = layer.x + (layer.image.width - img.width) // 2
        y = layer.y + (layer.image.height - img.height) // 2
        return _replace_active(doc, f"Rotate layer {degrees}°", _moved(layer, img, x, y))
    w, h = doc.width, doc.height
    layers = []
    for la in doc.layers:
        lw, lh = la.image.size
        if degrees == 90:
            x, y = h - (la.y + lh), la.x
        elif degrees == 180:
            x, y = w - (la.x + lw), h - (la.y + lh)
        else:
            x, y = la.y, w - (la.x + lw)
        layers.append(_moved(la, la.image.transpose(_ROT[degrees]), x, y))
    nw, nh = (h, w) if degrees in (90, 270) else (w, h)
    return _set_state(doc, f"Rotate {degrees}°", nw, nh, layers)


def flip(doc: Document, horizontal: bool, layer_only: bool = False) -> Command | None:
    method = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
    label = "Flip horizontal" if horizontal else "Flip vertical"
    if layer_only:
        layer = doc.active_layer
        return _replace_active(
            doc, label + " (layer)", _moved(layer, layer.image.transpose(method))
        )
    layers = []
    for la in doc.layers:
        lw, lh = la.image.size
        x = doc.width - (la.x + lw) if horizontal else la.x
        y = la.y if horizontal else doc.height - (la.y + lh)
        layers.append(_moved(la, la.image.transpose(method), x, y))
    return _set_state(doc, label, doc.width, doc.height, layers)


# ------------------------------------------------------------------ adjustments


def brightness_contrast(image: Image.Image, brightness: int, contrast: int) -> Image.Image:
    """Brightness and contrast, each −100…+100; alpha untouched."""
    arr = np.asarray(image.convert("RGBA"), dtype=np.float32).copy()
    rgb = arr[..., :3] + brightness * 2.55
    factor = (100 + contrast) / 100 if contrast >= 0 else 1 + contrast / 100
    rgb = (rgb - 128.0) * factor + 128.0
    arr[..., :3] = rgb
    return Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGBA")


def apply_brightness_contrast(doc: Document, brightness: int, contrast: int) -> Command | None:
    if brightness == 0 and contrast == 0:
        return None
    layer = doc.active_layer
    region = clip_rect(doc, layer)
    if region.empty:
        return None
    before = layer.image.copy()
    part = brightness_contrast(layer.image.crop(region.box), brightness, contrast)
    layer.image.paste(part, (region.x0, region.y0))
    return PatchCommand.capture("Brightness / contrast", doc, doc.active, region, before)


# ------------------------------------------------------------------ layer list


def _change(doc: Document, label: str, layers: list[Layer], active: int) -> Command:
    before, active_before = list(doc.layers), doc.active
    doc.layers, doc.active = layers, active
    return LayerListCommand(label, before, list(layers), active_before, active)


def _replace_active(doc: Document, label: str, new: Layer) -> Command:
    return _replace(doc, doc.active, label, new)


def _replace(doc: Document, index: int, label: str, new: Layer) -> Command:
    layers = list(doc.layers)
    layers[index] = new
    return _change(doc, label, layers, doc.active)


def _unique_name(doc: Document, base: str) -> str:
    names = {la.name for la in doc.layers}
    n = len(doc.layers) + 1
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


def add_layer(doc: Document) -> Command:
    layers = list(doc.layers)
    new = Layer(_unique_name(doc, "Layer"), Image.new("RGBA", (doc.width, doc.height)))
    layers.insert(doc.active + 1, new)
    return _change(doc, "Add layer", layers, doc.active + 1)


def add_layers(doc: Document, new_layers: list[Layer], label: str = "Add images") -> Command | None:
    if not new_layers:
        return None
    layers = list(doc.layers)
    layers[doc.active + 1 : doc.active + 1] = new_layers
    return _change(doc, label, layers, doc.active + len(new_layers))


def duplicate_layer(doc: Document) -> Command:
    layers = list(doc.layers)
    copy = doc.active_layer.copy()
    copy.name = f"{copy.name} copy"
    layers.insert(doc.active + 1, copy)
    return _change(doc, "Duplicate layer", layers, doc.active + 1)


def delete_layer(doc: Document) -> Command | None:
    if len(doc.layers) <= 1:
        return None
    layers = list(doc.layers)
    del layers[doc.active]
    return _change(doc, "Delete layer", layers, max(0, doc.active - 1))


def merge_down(doc: Document) -> Command | None:
    i = doc.active
    if i == 0:
        return None
    lower, upper = doc.layers[i - 1], doc.layers[i]
    box = lower.bounds.union(upper.bounds) if upper.visible else lower.bounds
    img = Image.new("RGBA", (box.width, box.height))
    for la in (lower, upper):
        if la is lower or la.visible:
            img.alpha_composite(with_opacity(la.image, la.opacity), (la.x - box.x0, la.y - box.y0))
    merged = Layer(lower.name, img, box.x0, box.y0, 1.0, lower.visible)
    layers = list(doc.layers)
    layers[i - 1 : i + 1] = [merged]
    return _change(doc, "Merge down", layers, i - 1)


def move_layer(doc: Document, delta: int) -> Command | None:
    i = doc.active
    j = max(0, min(len(doc.layers) - 1, i + delta))
    if i == j:
        return None
    layers = list(doc.layers)
    layers.insert(j, layers.pop(i))
    return _change(doc, "Move layer", layers, j)


def select_layer(doc: Document, index: int) -> None:
    """Changing the active layer isn't an undo step."""
    doc.active = max(0, min(len(doc.layers) - 1, index))


def rename_layer(doc: Document, index: int, name: str) -> Command | None:
    name = name.strip()
    if not name or name == doc.layers[index].name:
        return None
    return _replace(doc, index, "Rename layer", replace(doc.layers[index], name=name))


def set_opacity(doc: Document, index: int, opacity: float) -> Command | None:
    opacity = max(0.0, min(1.0, opacity))
    if abs(opacity - doc.layers[index].opacity) < 1e-6:
        return None
    return _replace(doc, index, "Layer opacity", replace(doc.layers[index], opacity=opacity))


def set_visible(doc: Document, index: int, visible: bool) -> Command | None:
    if doc.layers[index].visible == visible:
        return None
    label = "Show layer" if visible else "Hide layer"
    return _replace(doc, index, label, replace(doc.layers[index], visible=visible))
