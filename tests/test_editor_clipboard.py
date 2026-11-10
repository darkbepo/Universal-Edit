"""Copy / cut / paste, the Move tool and nudging (model only, no Qt)."""

import pytest
from PIL import Image

from umc.core.errors import ConversionError
from umc.editor import clipboard, ops
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.tools.base import ToolContext
from umc.editor.tools.move import MoveTool


def _doc():
    """20×10 canvas: red background, a 4×4 blue layer at (2, 3) on top (active)."""
    red = Layer("bg", Image.new("RGBA", (20, 10), (255, 0, 0, 255)))
    blue = Layer("blue", Image.new("RGBA", (4, 4), (0, 0, 255, 255)), x=2, y=3)
    return Document(20, 10, [red, blue], active=1)


def _ctx(shift=False):
    return ToolContext((0, 0, 0, 255), (255, 255, 255, 255), None, shift=shift)


def test_copy_whole_active_layer_without_selection():
    clip = clipboard.copy(_doc())
    assert (clip.x, clip.y, clip.image.size) == (2, 3, (4, 4))
    assert clip.image.getpixel((0, 0)) == (0, 0, 255, 255)


def test_copy_inside_selection_only():
    doc = _doc()
    doc.selection = Rect(4, 4, 10, 10)
    clip = clipboard.copy(doc)
    assert (clip.x, clip.y, clip.image.size) == (4, 4, (2, 3))  # layer ∩ selection
    doc.selection = Rect(15, 0, 20, 2)  # misses the layer
    assert clipboard.copy(doc) is None


def test_copy_merged_is_what_you_see():
    doc = _doc()
    doc.selection = Rect(1, 3, 3, 4)
    clip = clipboard.copy(doc, merged=True)
    assert (clip.x, clip.y) == (1, 3)
    assert [clip.image.getpixel((i, 0)) for i in range(2)] == [(255, 0, 0, 255), (0, 0, 255, 255)]


def test_cut_clears_and_undoes():
    doc = _doc()
    h = History(doc)
    clip, cmd = clipboard.cut(doc)
    h.push(cmd)
    assert clip.image.getpixel((1, 1)) == (0, 0, 255, 255)
    assert doc.layers[1].image.getpixel((1, 1))[3] == 0
    h.undo()
    assert doc.layers[1].image.getpixel((1, 1)) == (0, 0, 255, 255)


def test_paste_goes_back_in_place_as_a_new_selected_layer():
    doc = _doc()
    h = History(doc)
    h.push(clipboard.paste(doc, clipboard.copy(doc)))
    assert [la.name for la in doc.layers] == ["bg", "blue", "Pasted 3"]
    pasted = doc.layers[2]
    assert (pasted.x, pasted.y) == (2, 3) and doc.active == 2 and doc.selected_indices == [2]
    assert pasted.image is not doc.layers[1].image
    h.undo()
    assert len(doc.layers) == 2 and doc.active == 1


def test_paste_from_outside_is_centred():
    doc = _doc()
    clipboard.paste(doc, clipboard.Clip(Image.new("RGB", (6, 2), "white")))
    assert (doc.active_layer.x, doc.active_layer.y) == (7, 4)
    clipboard.paste(doc, clipboard.Clip(Image.new("RGBA", (2, 2))), center=(0, 0))
    assert (doc.active_layer.x, doc.active_layer.y) == (-1, -1)
    assert doc.active_layer.image.mode == "RGBA"


def test_paste_refuses_huge_images(monkeypatch):
    monkeypatch.setattr("umc.editor.document.MAX_SIDE", 8)
    with pytest.raises(ConversionError):
        clipboard.paste(_doc(), clipboard.Clip(Image.new("RGBA", (9, 2))))


def test_move_tool_moves_selected_layers_and_undoes():
    doc = _doc()
    doc.layers.append(Layer("top", Image.new("RGBA", (2, 2), (0, 255, 0, 255))))
    ops.select_layers(doc, [1, 2], active=2)
    h = History(doc)
    tool = MoveTool()
    tool.press(doc, 5.2, 5.0, _ctx())
    dirty = tool.move(doc, 8.4, 4.0, _ctx())
    assert dirty.contains(2, 3) and dirty.contains(8, 5)  # old and new places repaint
    cmd = tool.release(doc, 8.4, 4.0, _ctx())
    h.push(cmd)
    assert [(la.x, la.y) for la in doc.layers] == [(0, 0), (5, 2), (3, -1)]
    assert doc.layers[1].image is h._undo[-1].before[1].image  # images are shared, not copied
    h.undo()
    assert [(la.x, la.y) for la in doc.layers] == [(0, 0), (2, 3), (0, 0)]


def test_move_tool_shift_keeps_one_axis_and_cancel_restores():
    doc = _doc()
    tool = MoveTool()
    tool.press(doc, 0, 0, _ctx())
    tool.move(doc, 5, 2, _ctx(shift=True))
    assert (doc.layers[1].x, doc.layers[1].y) == (7, 3)
    tool.cancel(doc)
    assert (doc.layers[1].x, doc.layers[1].y) == (2, 3)
    tool.press(doc, 0, 0, _ctx())
    assert tool.release(doc, 0.3, 0.2, _ctx()) is None  # no real move → no undo step


def test_nudge():
    doc = _doc()
    h = History(doc)
    h.push(ops.nudge_layers(doc, doc.selected_indices, -10, 1))
    assert (doc.layers[1].x, doc.layers[1].y) == (-8, 4)
    assert ops.nudge_layers(doc, [1], 0, 0) is None
    h.undo()
    assert (doc.layers[1].x, doc.layers[1].y) == (2, 3)
