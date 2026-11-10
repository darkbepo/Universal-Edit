"""Background removal: model download checks, the mask it gives, and the layer mask it sets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from umc.editor import ops
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.tools import bgremove

# a real run needs the 179 MB model: point UMC_BG_MODEL at a copy to test it
MODEL = os.environ.get("UMC_BG_MODEL")


def test_model_is_pinned():
    assert bgremove.MODEL_URL.startswith("https://github.com/danielgatis/rembg/releases/")
    assert len(bgremove.MODEL_SHA256) == 64 and bgremove.MODEL_SIZE > 100_000_000


def test_model_ready_checks_the_size(tmp_path, monkeypatch):
    monkeypatch.setenv("UMC_TOOLS_DIR", str(tmp_path))
    assert not bgremove.model_ready()
    bgremove.model_path().write_bytes(b"x")
    assert not bgremove.model_ready()


def test_mask_from_sets_and_combines_the_layer_mask():
    doc = Document(4, 2, [Layer("bg", Image.new("RGBA", (4, 2), (9, 9, 9, 255)))])
    h = History(doc)
    m = Image.new("L", (4, 2), 255)
    m.putpixel((0, 0), 0)
    h.push(ops.mask_from(doc, m))
    assert doc.active_layer.mask.getpixel((0, 0)) == 0
    assert doc.active_layer.image.getpixel((0, 0)) == (9, 9, 9, 255)  # pixels kept
    m2 = Image.new("L", (4, 2), 255)
    m2.putpixel((1, 0), 0)
    h.push(ops.mask_from(doc, m2))
    assert doc.active_layer.mask.getpixel((0, 0)) == 0  # still hidden
    assert doc.active_layer.mask.getpixel((1, 0)) == 0
    h.undo()
    h.undo()
    assert doc.active_layer.mask is None


@pytest.mark.skipif(not MODEL, reason="set UMC_BG_MODEL to the downloaded model")
def test_subject_mask_finds_the_subject():
    img = Image.new("RGB", (400, 300), (90, 160, 220))
    ImageDraw.Draw(img).ellipse((130, 60, 270, 240), fill=(200, 60, 40))
    m = bgremove.subject_mask(img, model=Path(MODEL))
    assert m.size == img.size and m.mode == "L"
    assert m.getpixel((200, 150)) > 200 and m.getpixel((20, 20)) < 50


def test_a_failed_or_cancelled_download_leaves_nothing(tmp_path, monkeypatch):
    import threading

    from umc.core.errors import ConversionCancelled, ConversionError
    from umc.tools import installer

    monkeypatch.setenv("UMC_TOOLS_DIR", str(tmp_path))
    part = bgremove.model_path().with_suffix(".part")

    def broken(url, dest, **kw):
        dest.write_bytes(b"half")
        raise ConnectionResetError("reset")

    monkeypatch.setattr(installer, "download", broken)
    with pytest.raises(ConversionError, match="internet connection"):
        bgremove.ensure_model(lambda f, m: None, threading.Event())
    assert not part.exists()

    def cancelled(url, dest, **kw):
        dest.write_bytes(b"half")
        raise ConversionCancelled()

    monkeypatch.setattr(installer, "download", cancelled)
    with pytest.raises(ConversionCancelled):
        bgremove.ensure_model(lambda f, m: None, threading.Event())
    assert not part.exists()
