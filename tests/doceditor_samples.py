"""Shared sample data for the document editor tests."""

from __future__ import annotations

import base64
import io

from PIL import Image


def _png_1px() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


PNG_1PX: bytes = _png_1px()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
