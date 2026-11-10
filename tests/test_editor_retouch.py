"""Clone stamp and content-aware fill."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from umc.core.errors import ConversionError
from umc.editor import selection
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.inpaint import content_aware_fill, fill_mask
from umc.editor.layer import Layer
from umc.editor.tools.base import ToolContext
from umc.editor.tools.paint import CloneOptions, CloneStampTool


def _halves() -> Document:
    img = Image.new("RGBA", (100, 60), (255, 0, 0, 255))
    img.paste((0, 0, 255, 255), (50, 0, 100, 60))
    return Document(100, 60, [Layer("bg", img)])


def _ctx(alt=False, **opts) -> ToolContext:
    return ToolContext((0, 0, 0, 255), (255, 255, 255, 255), CloneOptions(**opts), alt=alt)


def _click(tool, doc, x, y, ctx):
    tool.press(doc, x, y, ctx)
    return tool.release(doc, x, y, ctx)


def test_clone_needs_a_source_first():
    doc, t = _halves(), CloneStampTool()
    assert _click(t, doc, 20, 30, _ctx()) is None
    assert t.notice and "Alt+click" in t.notice


def test_clone_copies_from_the_source_and_undoes():
    doc, t = _halves(), CloneStampTool()
    h = History(doc)
    _click(t, doc, 75, 30, _ctx(alt=True))  # blue side
    h.push(_click(t, doc, 20, 30, _ctx(size=10, hardness=100)))
    assert doc.layers[0].image.getpixel((20, 30)) == (0, 0, 255, 255)
    assert doc.layers[0].image.getpixel((5, 30)) == (255, 0, 0, 255)
    assert t.marker(30, 30) == (85, 30)  # the source follows the brush
    h.undo()
    assert doc.layers[0].image.getpixel((20, 30)) == (255, 0, 0, 255)


def test_clone_aligned_or_not():
    doc, t = _halves(), CloneStampTool()
    _click(t, doc, 75, 30, _ctx(alt=True))
    _click(t, doc, 10, 10, _ctx(size=6, hardness=100))
    assert t.offset == (-65, -20)
    _click(t, doc, 30, 40, _ctx(size=6, hardness=100))
    assert t.offset == (-65, -20)  # aligned: kept
    _click(t, doc, 30, 40, _ctx(size=6, hardness=100, aligned=False))
    assert t.offset == (-45, 10)  # restarts at the source


def test_clone_from_all_layers():
    doc = _halves()
    doc.layers.append(Layer("top", Image.new("RGBA", (100, 60))))
    doc.active = 1
    t = CloneStampTool()
    _click(t, doc, 75, 30, _ctx(alt=True))
    _click(t, doc, 20, 30, _ctx(size=8, hardness=100))
    assert doc.layers[1].image.getpixel((20, 30))[3] == 0  # this (empty) layer
    _click(t, doc, 20, 30, _ctx(size=8, hardness=100, sample="all", aligned=False))
    assert doc.layers[1].image.getpixel((20, 30)) == (0, 0, 255, 255)


def test_fill_mask_reaches_past_the_selection():
    doc = _halves()
    doc.selection = Rect(10, 10, 20, 20)
    m = fill_mask(doc, Rect(5, 5, 25, 25))
    assert m.shape == (20, 20) and m[10, 10] == 1 and m[0, 0] == 0


def test_content_aware_fill_removes_a_spot():
    img = Image.new("RGBA", (80, 60), (40, 160, 60, 255))
    img.paste((250, 250, 250, 255), (30, 20, 46, 36))  # a white spot
    doc = Document(80, 60, [Layer("bg", img)])
    h = History(doc)
    h.push(selection.set_selection(doc, Rect(27, 17, 49, 39), None, "Select"))
    h.push(content_aware_fill(doc))
    px = np.asarray(doc.layers[0].image, dtype=int)
    assert np.abs(px[28, 38, :3] - (40, 160, 60)).max() <= 3  # middle of the old spot
    assert tuple(px[5, 5]) == (40, 160, 60, 255)  # outside untouched
    h.undo()
    assert doc.layers[0].image.getpixel((38, 28)) == (250, 250, 250, 255)


def test_content_aware_fill_needs_a_selection_with_room_around():
    doc = _halves()
    with pytest.raises(ConversionError):
        content_aware_fill(doc)
    doc.selection = Rect(0, 0, 100, 60)
    with pytest.raises(ConversionError):
        content_aware_fill(doc)


def test_content_aware_fill_continues_a_gradient():
    arr = np.zeros((20, 100, 4), dtype=np.uint8)
    arr[..., 0] = (np.arange(100) * 2.5).astype(np.uint8)
    arr[..., 3] = 255
    doc = Document(100, 20, [Layer("bg", Image.fromarray(arr, "RGBA"))])
    doc.selection = Rect(40, 5, 60, 15)
    content_aware_fill(doc)
    assert 100 < doc.layers[0].image.getpixel((50, 10))[0] < 150
