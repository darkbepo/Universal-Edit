"""Open the files the user added as one layered document."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import ImageOps

from umc.core.errors import ConversionError
from umc.editor.document import Document, check_size
from umc.editor.layer import Layer

log = logging.getLogger(__name__)


def load_layers(paths: list[Path]) -> tuple[list[Layer], tuple[int, int], list[str]]:
    """Layers for ``paths`` (first file at the bottom), the largest size, and error messages."""
    from umc.converters.imaging import open_image
    from umc.editor.ora import read_ora

    layers: list[Layer] = []
    errors: list[str] = []
    width = height = 0
    for p in map(Path, paths):
        try:
            if p.suffix.lower() == ".ora":
                doc = read_ora(p)
                layers.extend(doc.layers)
                width, height = max(width, doc.width), max(height, doc.height)
                continue
            img = open_image(p)
            img = ImageOps.exif_transpose(img)
            check_size(img.width, img.height)
            layers.append(Layer(p.stem, img))
            width, height = max(width, img.width), max(height, img.height)
        except ConversionError as exc:
            log.warning("editor: couldn't open %s: %s", p, exc)
            msg = str(exc)
            errors.append(msg if p.name in msg else f"Couldn't open “{p.name}”: {msg}")
        except Exception as exc:
            log.exception("editor: couldn't open %s", p)
            errors.append(f"Couldn't open “{p.name}” as an image.")
            del exc
    return layers, (width, height), errors


def open_files(paths: list[Path]) -> tuple[Document, list[str]]:
    layers, (width, height), errors = load_layers(paths)
    if not layers:
        detail = f" {errors[0]}" if len(errors) == 1 else ""
        raise ConversionError("None of the files could be opened as an image." + detail)
    check_size(width, height)
    return Document(width, height, layers, active=len(layers) - 1), errors
