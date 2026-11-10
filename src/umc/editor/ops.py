"""Whole-image operations, layer-list operations and adjustments.

Each function applies its change and returns the Command to push onto the History
(None when nothing changed). Layers are replaced, never changed in place — see history.py.
"""

from __future__ import annotations

from dataclasses import replace

from PIL import Image

from umc.editor.document import Document, check_size, with_opacity
from umc.editor.geometry import Rect
from umc.editor.history import (
    Command,
    DocumentStateCommand,
    LayerListCommand,
)
from umc.editor.layer import Layer

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
    doc.selection, doc.selection_mask = None, None
    return DocumentStateCommand(label, before, _state(doc))


def _moved(
    layer: Layer,
    image: Image.Image | None = None,
    x: int | None = None,
    y: int | None = None,
    op=None,
):
    """``layer`` moved and/or with its pixels changed by ``op`` (applied to the mask too, so a
    layer mask follows crops, resizes, rotations and flips)."""
    mask = layer.mask
    if op is not None:
        image = op(layer.image)
        mask = None if mask is None else op(mask)
    elif image is not None and mask is not None and mask.size != image.size:
        mask = None  # a replaced image that the old mask doesn't fit
    return replace(
        layer,
        image=layer.image if image is None else image,
        x=layer.x if x is None else x,
        y=layer.y if y is None else y,
        mask=mask,
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
            layers.append(replace(layer, image=Image.new("RGBA", (1, 1)), x=0, y=0, mask=None))
            continue
        box = keep.offset(-shifted.x0, -shifted.y0).box
        layers.append(_moved(layer, x=keep.x0, y=keep.y0, op=lambda im, b=box: im.crop(b)))
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
                layer,
                x=round(layer.x * sx),
                y=round(layer.y * sy),
                op=lambda im, sz=size: im.resize(sz, method),
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
        t = _ROT[degrees]
        w, h = (layer.image.height, layer.image.width) if degrees in (90, 270) else layer.image.size
        x = layer.x + (layer.image.width - w) // 2
        y = layer.y + (layer.image.height - h) // 2
        moved = _moved(layer, x=x, y=y, op=lambda im: im.transpose(t))
        return _replace_active(doc, f"Rotate layer {degrees}°", moved)
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
        layers.append(_moved(la, x=x, y=y, op=lambda im: im.transpose(_ROT[degrees])))
    nw, nh = (h, w) if degrees in (90, 270) else (w, h)
    return _set_state(doc, f"Rotate {degrees}°", nw, nh, layers)


def flip(doc: Document, horizontal: bool, layer_only: bool = False) -> Command | None:
    method = Image.Transpose.FLIP_LEFT_RIGHT if horizontal else Image.Transpose.FLIP_TOP_BOTTOM
    label = "Flip horizontal" if horizontal else "Flip vertical"
    if layer_only:
        layer = doc.active_layer
        return _replace_active(
            doc, label + " (layer)", _moved(layer, op=lambda im: im.transpose(method))
        )
    layers = []
    for la in doc.layers:
        lw, lh = la.image.size
        x = doc.width - (la.x + lw) if horizontal else la.x
        y = la.y if horizontal else doc.height - (la.y + lh)
        layers.append(_moved(la, x=x, y=y, op=lambda im: im.transpose(method)))
    return _set_state(doc, label, doc.width, doc.height, layers)


# ------------------------------------------------------------------ layer list


def _change(
    doc: Document,
    label: str,
    layers: list[Layer],
    active: int,
    selected: set[int] | None = None,
) -> Command:
    before, active_before, selected_before = list(doc.layers), doc.active, set(doc.selected)
    doc.layers, doc.active = layers, active
    doc.selected = set(selected or ())
    return LayerListCommand(
        label, before, list(layers), active_before, active, selected_before, set(doc.selected)
    )


def _replace_active(doc: Document, label: str, new: Layer) -> Command:
    return _replace(doc, doc.active, label, new)


def _replace(doc: Document, index: int, label: str, new: Layer) -> Command:
    return _replace_many(doc, {index: new}, label)


def _replace_many(doc: Document, new: dict[int, Layer], label: str) -> Command:
    layers = list(doc.layers)
    for index, layer in new.items():
        layers[index] = layer
    return _change(doc, label, layers, doc.active, set(doc.selected))


def _clean(doc: Document, indices) -> list[int]:
    n = len(doc.layers)
    return sorted({i for i in indices if 0 <= i < n})


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _reselect(
    doc: Document, layers: list[Layer], keep: list[Layer], active: Layer
) -> tuple[int, set[int]]:
    """Helper for reorders: find the kept/active Layer objects again in the new order."""
    where = {id(la): i for i, la in enumerate(layers)}
    return where[id(active)], {where[id(la)] for la in keep}


def unique_name(doc: Document, base: str) -> str:
    names = {la.name for la in doc.layers}
    n = len(doc.layers) + 1
    while f"{base} {n}" in names:
        n += 1
    return f"{base} {n}"


def add_layer(doc: Document) -> Command:
    layers = list(doc.layers)
    new = Layer(unique_name(doc, "Layer"), Image.new("RGBA", (doc.width, doc.height)))
    layers.insert(doc.active + 1, new)
    return _change(doc, "Add layer", layers, doc.active + 1)


def add_layers(doc: Document, new_layers: list[Layer], label: str = "Add images") -> Command | None:
    if not new_layers:
        return None
    layers = list(doc.layers)
    layers[doc.active + 1 : doc.active + 1] = new_layers
    return _change(doc, label, layers, doc.active + len(new_layers))


def duplicate_layer(doc: Document) -> Command:
    return duplicate_layers(doc, [doc.active])


def duplicate_layers(doc: Document, indices) -> Command | None:
    """Each layer gets a copy right above it; the copies become the selection."""
    chosen = set(_clean(doc, indices))
    if not chosen:
        return None
    layers: list[Layer] = []
    copies: dict[int, int] = {}
    for i, layer in enumerate(doc.layers):
        layers.append(layer)
        if i in chosen:
            copy = layer.copy()
            copy.name = f"{layer.name} copy"
            copies[i] = len(layers)
            layers.append(copy)
    active = copies.get(doc.active, copies[max(chosen)])
    label = _plural(len(chosen), "Duplicate layer", "Duplicate layers")
    return _change(doc, label, layers, active, set(copies.values()))


def delete_layer(doc: Document) -> Command | None:
    return delete_layers(doc, [doc.active])


def delete_layers(doc: Document, indices) -> Command | None:
    """Delete the given layers; at least one layer always stays."""
    chosen = _clean(doc, indices)
    if not chosen or len(chosen) >= len(doc.layers):
        return None
    gone = set(chosen)
    layers = [la for i, la in enumerate(doc.layers) if i not in gone]
    label = _plural(len(chosen), "Delete layer", "Delete layers")
    return _change(doc, label, layers, max(0, chosen[0] - 1))


def merge_down(doc: Document) -> Command | None:
    i = doc.active
    if i == 0:
        return None
    lower, upper = doc.layers[i - 1], doc.layers[i]
    box = lower.bounds.union(upper.bounds) if upper.visible else lower.bounds
    img = Image.new("RGBA", (box.width, box.height))
    for la in (lower, upper):
        if la is lower or la.visible:
            img.alpha_composite(
                with_opacity(la.pixels(), la.opacity), (la.x - box.x0, la.y - box.y0)
            )
    merged = Layer(lower.name, img, box.x0, box.y0, 1.0, lower.visible)
    layers = list(doc.layers)
    layers[i - 1 : i + 1] = [merged]
    return _change(doc, "Merge down", layers, i - 1)


def merge_layers(doc: Document, indices) -> Command | None:
    """Flatten several layers into one, placed where the top one was and named after it.

    Hidden layers are dropped (like Merge down does), unless all of them are hidden.
    """
    chosen = _clean(doc, indices)
    if len(chosen) < 2:
        return None
    group = [doc.layers[i] for i in chosen]
    shown = [la for la in group if la.visible] or group
    box = shown[0].bounds
    for la in shown[1:]:
        box = box.union(la.bounds)
    img = Image.new("RGBA", (box.width, box.height))
    for la in shown:
        img.alpha_composite(with_opacity(la.pixels(), la.opacity), (la.x - box.x0, la.y - box.y0))
    top = group[-1]
    merged = Layer(top.name, img, box.x0, box.y0, 1.0, any(la.visible for la in group))
    gone = set(chosen)
    position = chosen[-1] - (len(chosen) - 1)
    layers = [la for i, la in enumerate(doc.layers) if i not in gone]
    layers.insert(position, merged)
    return _change(doc, "Merge layers", layers, position)


def move_layer(doc: Document, delta: int) -> Command | None:
    return move_layers_by(doc, [doc.active], delta)


def move_layers_by(doc: Document, indices, delta: int) -> Command | None:
    """Move each layer one step up (+1) or down (−1); a layer stops at the top / bottom
    or against another moving layer that can't go further."""
    chosen = _clean(doc, indices)
    if not chosen or delta == 0:
        return None
    step = 1 if delta > 0 else -1
    layers = list(doc.layers)
    keep = [layers[i] for i in chosen]
    active = layers[doc.active]
    for _ in range(abs(delta)):
        moving = {id(la) for la in keep}
        order = range(len(layers) - 1, -1, -1) if step > 0 else range(len(layers))
        for i in order:
            j = i + step
            if id(layers[i]) in moving and 0 <= j < len(layers) and id(layers[j]) not in moving:
                layers[i], layers[j] = layers[j], layers[i]
    if all(a is b for a, b in zip(layers, doc.layers, strict=True)):
        return None
    new_active, selected = _reselect(doc, layers, keep, active)
    label = _plural(len(chosen), "Move layer", "Move layers")
    return _change(doc, label, layers, new_active, selected)


def move_layers_to(doc: Document, indices, gap: int) -> Command | None:
    """Move layers (keeping their order) into the gap below ``doc.layers[gap]``.

    ``gap`` counts in the current list: 0 = bottom, ``len(doc.layers)`` = top. This is what
    dragging rows in the layers list does.
    """
    chosen = _clean(doc, indices)
    if not chosen:
        return None
    gap = max(0, min(len(doc.layers), gap))
    gone = set(chosen)
    keep = [doc.layers[i] for i in chosen]
    rest = [la for i, la in enumerate(doc.layers) if i not in gone]
    at = gap - sum(1 for i in chosen if i < gap)
    layers = rest[:at] + keep + rest[at:]
    if all(a is b for a, b in zip(layers, doc.layers, strict=True)):
        return None
    active = doc.layers[doc.active]
    if id(active) not in {id(la) for la in keep}:
        active = keep[-1]
    new_active, selected = _reselect(doc, layers, keep, active)
    label = _plural(len(chosen), "Move layer", "Move layers")
    return _change(doc, label, layers, new_active, selected)


def select_layer(doc: Document, index: int) -> None:
    """Changing the active layer isn't an undo step."""
    doc.active = max(0, min(len(doc.layers) - 1, index))
    doc.selected = {doc.active}


def select_layers(doc: Document, indices, active: int | None = None) -> None:
    """Select several layers; ``active`` (default: the top one) is the one tools paint on."""
    chosen = _clean(doc, indices)
    if not chosen:
        return
    doc.active = active if active in chosen else chosen[-1]
    doc.selected = set(chosen)


def rename_layer(doc: Document, index: int, name: str) -> Command | None:
    name = name.strip()
    if not name or name == doc.layers[index].name:
        return None
    return _replace(doc, index, "Rename layer", replace(doc.layers[index], name=name))


def set_opacity(doc: Document, index: int, opacity: float) -> Command | None:
    return set_opacity_layers(doc, [index], opacity)


def set_opacity_layers(doc: Document, indices, opacity: float) -> Command | None:
    opacity = max(0.0, min(1.0, opacity))
    new = {
        i: replace(doc.layers[i], opacity=opacity)
        for i in _clean(doc, indices)
        if abs(opacity - doc.layers[i].opacity) >= 1e-6
    }
    return _replace_many(doc, new, "Layer opacity") if new else None


def set_visible(doc: Document, index: int, visible: bool) -> Command | None:
    return set_visible_layers(doc, [index], visible)


def set_visible_layers(doc: Document, indices, visible: bool) -> Command | None:
    new = {
        i: replace(doc.layers[i], visible=visible)
        for i in _clean(doc, indices)
        if doc.layers[i].visible != visible
    }
    if not new:
        return None
    label = ("Show " if visible else "Hide ") + _plural(len(new), "layer", "layers")
    return _replace_many(doc, new, label)


def offset_layers(doc: Document, base: list[Layer], indices, dx: int, dy: int) -> list[Layer]:
    """``base`` with the given layers shifted by (dx, dy) — new Layer objects, images shared."""
    layers = list(base)
    for i in _clean(doc, indices):
        layers[i] = replace(base[i], x=base[i].x + dx, y=base[i].y + dy)
    return layers


def nudge_layers(doc: Document, indices, dx: int, dy: int) -> Command | None:
    """Move layers by (dx, dy) pixels (arrow keys with the Move tool)."""
    if (dx, dy) == (0, 0) or not _clean(doc, indices):
        return None
    return _change(
        doc, "Move", offset_layers(doc, doc.layers, indices, dx, dy), doc.active, set(doc.selected)
    )
