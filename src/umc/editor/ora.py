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


def _text_spec(raw: str | None):
    if not raw:
        return None
    from umc.editor.tools.text import TextSpec

    try:
        return TextSpec.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None  # a damaged or newer attribute: keep the pixels, lose the editing


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
            **({TEXT_ATTR: json.dumps(layer.text.to_dict())} if layer.text is not None else {}),
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
                )
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
