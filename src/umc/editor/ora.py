"""OpenRaster (.ora): a ZIP of PNG layers plus stack.xml — read by GIMP, Krita, MyPaint."""

from __future__ import annotations

import io
import json
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PIL import Image

from umc.core.errors import ConversionError
from umc.editor.document import Document, check_size
from umc.editor.layer import Layer

MIMETYPE = b"image/openraster"


TEXT_ATTR = "umc-text"  # JSON of tools.text.TextSpec
SHAPE_ATTR = "umc-shape"  # JSON of tools.shapes.ShapeSpec
FX_ATTR = "umc-fx"  # JSON of layerfx.LayerFx (other programs show the layer without its effects)


def _text_spec(raw: str | None):
    if not raw:
        return None
    from umc.editor.tools.text import TextSpec

    try:
        d = json.loads(raw)
        return TextSpec.from_dict(d) if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None  # a damaged or newer attribute: keep the pixels, lose the editing


def _shape_spec(raw: str | None):
    if not raw:
        return None
    from umc.editor.tools.shapes import ShapeSpec

    try:
        d = json.loads(raw)
        return ShapeSpec.from_dict(d) if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None  # a damaged or newer attribute: keep the pixels, lose the editing


def _layer_fx(raw: str | None):
    if not raw:
        return None
    from umc.editor.layerfx import LayerFx

    try:
        d = json.loads(raw)
        fx = LayerFx.from_dict(d) if isinstance(d, dict) else None
    except (ValueError, TypeError):
        return None  # a damaged or newer attribute: the layer without its effects
    return fx if fx is not None and fx.any() else None


def _redrawn(layer: Layer, size: tuple[int, int]) -> Layer | None:
    """A text / shape layer drawn again from its recipe (None when it has none)."""
    from dataclasses import replace

    if layer.shape is not None:
        from umc.editor.tools.shapes import canvas_points, shape_layer

        spec = replace(layer.shape, points=canvas_points(layer))
        return shape_layer(layer.name, spec, layer)
    if layer.text is not None:
        from umc.editor.tools.text import anchor_of, live_text_layer

        return live_text_layer(Document(*size, []), layer.text, *anchor_of(layer), layer)
    return None


def _same(a: Layer, b: Layer) -> bool:
    return (a.x, a.y, a.image.size) == (b.x, b.y, b.image.size) and a.image.tobytes() == (
        b.image.tobytes()
    )


def _close(a: Layer, b: Layer) -> bool:
    """Nearly the same pixels: another app's (or the browser's) anti-aliasing and rounding, not
    painting. At most 2 % of the drawn pixels may differ by more than a little."""
    import numpy as np

    box = a.bounds.union(b.bounds)
    planes = []
    for la in (a, b):
        img = Image.new("RGBA", (box.width, box.height))
        img.paste(la.image, (la.x - box.x0, la.y - box.y0))
        planes.append(np.asarray(img, dtype=np.int16))
    diff = np.abs(planes[0] - planes[1]).max(axis=2)
    drawn = max(int((planes[0][..., 3] > 0).sum()), int((planes[1][..., 3] > 0).sum()))
    return int((diff > 96).sum()) <= max(4, drawn // 50)


def _keeps_recipe(layer: Layer, size: tuple[int, int]) -> bool:
    """Save a text / shape recipe only while the pixels are still exactly what it draws (and
    there is no mask, which the file bakes into the pixels)."""
    if layer.mask is not None:
        return False
    try:
        again = _redrawn(layer, size)
    except Exception:
        return False
    return again is not None and _same(again, layer)


def _from_recipe(layer: Layer, size: tuple[int, int]) -> Layer:
    """A text / shape layer read from a file: drawn again by this app when the stored pixels
    match (so it stays editable here, even if another app drew it), else plain pixels."""
    from dataclasses import replace

    try:
        again = _redrawn(layer, size)
    except Exception:
        again = None
    if again is not None and _close(again, layer):
        return again
    return replace(layer, text=None, shape=None)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_ora(doc: Document, path: Path) -> None:
    """Write ``doc`` to ``path`` (written to a temp file next to it, then moved into place)."""
    root = ET.Element("image", version="0.0.3", w=str(doc.width), h=str(doc.height))
    stack = ET.SubElement(root, "stack")
    files: list[tuple[str, bytes]] = []
    for i, layer in enumerate(reversed(doc.layers)):  # top layer first
        src = f"data/layer{len(doc.layers) - 1 - i}.png"
        keep = _keeps_recipe(layer, (doc.width, doc.height))
        ET.SubElement(
            stack,
            "layer",
            name=layer.name,
            src=src,
            x=str(layer.x),
            y=str(layer.y),
            opacity=f"{layer.opacity:.4f}",
            visibility="visible" if layer.visible else "hidden",
            **({"selected": "true"} if doc.layers.index(layer) == doc.active else {}),
            # our own extra: how a text layer was made, so it stays editable (others ignore it)
            **({TEXT_ATTR: json.dumps(layer.text.to_dict())} if keep and layer.text else {}),
            **({SHAPE_ATTR: json.dumps(layer.shape.to_dict())} if keep and layer.shape else {}),
            **({FX_ATTR: json.dumps(layer.fx.to_dict())} if layer.fx is not None else {}),
        )
        files.append((src, _png(layer.pixels())))  # ORA has no masks: bake them in
    merged = doc.composite()
    thumb = merged.copy()
    thumb.thumbnail((256, 256))

    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".umc-", suffix=".ora", dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
            z.writestr("stack.xml", ET.tostring(root, encoding="utf-8", xml_declaration=True))
            for name, data in files:
                z.writestr(name, data)
            z.writestr("mergedimage.png", _png(merged))
            z.writestr("Thumbnails/thumbnail.png", _png(thumb))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _layers_of(stack: ET.Element):
    """Layer elements top-first, nested stacks flattened in order."""
    for el in stack:
        if el.tag == "layer":
            yield el
        elif el.tag == "stack":
            yield from _layers_of(el)


def read_ora(path: Path) -> Document:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("stack.xml"))
            width, height = int(root.get("w", "0")), int(root.get("h", "0"))
            check_size(width, height)
            stack = root.find("stack")
            layers: list[Layer] = []
            active = None
            for el in _layers_of(stack if stack is not None else root):
                src = el.get("src", "")
                if not src.lower().endswith(".png") or src not in z.namelist():
                    continue
                with z.open(src) as f:
                    img = Image.open(f)
                    img.load()
                layer = Layer(
                    el.get("name", Path(src).stem),
                    img,
                    x=int(float(el.get("x", "0"))),
                    y=int(float(el.get("y", "0"))),
                    opacity=max(0.0, min(1.0, float(el.get("opacity", "1")))),
                    visible=el.get("visibility", "visible") != "hidden",
                    text=_text_spec(el.get(TEXT_ATTR)),
                    shape=_shape_spec(el.get(SHAPE_ATTR)),
                    fx=_layer_fx(el.get(FX_ATTR)),
                )
                if layer.text is not None or layer.shape is not None:
                    layer = _from_recipe(layer, (width, height))
                if el.get("selected") == "true":
                    active = layer
                layers.append(layer)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Couldn't open “{path.name}” as an OpenRaster file.") from exc
    if not layers:
        raise ConversionError(f"“{path.name}” has no layers.")
    layers.reverse()  # our order: bottom first
    idx = layers.index(active) if active is not None else len(layers) - 1
    return Document(width, height, layers, active=idx)
