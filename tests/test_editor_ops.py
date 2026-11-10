"""Whole-image operations, layer operations and adjustments, each with undo / redo."""

import pytest
from PIL import Image

from umc.core.errors import ConversionError
from umc.editor import ops
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History, PatchCommand
from umc.editor.layer import Layer


def _solid(w, h, color=(0, 0, 0, 255)):
    return Image.new("RGBA", (w, h), color)


def _state(doc):
    return (
        doc.width,
        doc.height,
        doc.active,
        [(la.name, la.x, la.y, la.opacity, la.visible, la.image.tobytes()) for la in doc.layers],
    )


def _check_undo_redo(doc, fn):
    """Apply ``fn`` through a History, undo → original state, redo → changed state."""
    h = History(doc)
    before = _state(doc)
    cmd = fn(doc)
    assert cmd is not None
    after = _state(doc)
    assert after != before
    h.push(cmd)
    h.undo()
    assert _state(doc) == before
    h.redo()
    assert _state(doc) == after


def _doc():
    marked = _solid(10, 4, (0, 0, 0, 0))
    marked.putpixel((0, 0), (255, 0, 0, 255))  # top-left corner marker
    small = _solid(2, 2, (0, 0, 255, 255))
    small.putpixel((0, 0), (0, 255, 0, 255))  # so flips / rotations show
    return Document(10, 4, [Layer("base", marked), Layer("small", small, x=6, y=1)], active=1)


def test_crop_shifts_offsets():
    doc = _doc()
    ops.crop(doc, Rect(5, 0, 10, 4))
    assert (doc.width, doc.height) == (5, 4)
    assert (doc.layers[1].x, doc.layers[1].y) == (1, 1)
    assert doc.layers[0].image.size == (5, 4)


def test_crop_clips_to_canvas_and_drops_selection():
    doc = _doc()
    doc.selection = Rect(0, 0, 3, 3)
    ops.crop(doc, Rect(-5, -5, 3, 3))
    assert (doc.width, doc.height) == (3, 3) and doc.selection is None


def test_resize_scales_offsets():
    doc = _doc()
    ops.resize_image(doc, 20, 8)
    assert (doc.width, doc.height) == (20, 8)
    assert (doc.layers[1].x, doc.layers[1].y, doc.layers[1].image.size) == (12, 2, (4, 4))


def test_resize_refuses_huge():
    with pytest.raises(ConversionError, match="too large"):
        ops.resize_image(_doc(), 20_000, 10)


def test_canvas_size_anchor_center():
    doc = Document(4, 4, [Layer("a", _solid(4, 4))])
    ops.canvas_size(doc, 8, 8, "center")
    assert (doc.width, doc.height) == (8, 8)
    assert (doc.layers[0].x, doc.layers[0].y, doc.layers[0].image.size) == (2, 2, (4, 4))
    ops.canvas_size(doc, 10, 9, "bottom-right")
    assert (doc.layers[0].x, doc.layers[0].y) == (4, 3)


def test_rotate_90_whole_image():
    doc = _doc()
    ops.rotate(doc, 90)
    assert (doc.width, doc.height) == (4, 10)
    # clockwise: the top-left corner ends up top-right
    assert doc.layers[0].image.getpixel((3, 0)) == (255, 0, 0, 255)
    small = doc.layers[1]  # was x 6..8, y 1..3 → x 4-3=1..3, y 6..8
    assert (small.x, small.y) == (1, 6)


def test_rotate_180_and_270():
    doc = _doc()
    ops.rotate(doc, 180)
    assert doc.layers[0].image.getpixel((9, 3)) == (255, 0, 0, 255)
    assert (doc.layers[1].x, doc.layers[1].y) == (2, 1)
    doc = _doc()
    ops.rotate(doc, 270)
    assert (doc.width, doc.height) == (4, 10)
    assert doc.layers[0].image.getpixel((0, 9)) == (255, 0, 0, 255)


def test_rotate_layer_only():
    doc = Document(10, 10, [Layer("a", _solid(4, 2), x=2, y=3)])
    ops.rotate(doc, 90, layer_only=True)
    la = doc.layers[0]
    assert (doc.width, doc.height) == (10, 10)
    assert la.image.size == (2, 4) and (la.x, la.y) == (3, 2)


def test_flip_horizontal():
    doc = _doc()
    ops.flip(doc, horizontal=True)
    assert doc.layers[0].image.getpixel((9, 0)) == (255, 0, 0, 255)
    assert doc.layers[1].x == 2
    doc = _doc()
    ops.flip(doc, horizontal=False, layer_only=True)
    assert doc.layers[1].x == 6


def test_brightness_contrast_values():
    img = _solid(2, 2, (100, 100, 100, 77))
    assert abs(ops.brightness_contrast(img, 50, 0).getpixel((0, 0))[0] - 228) <= 1
    out = ops.brightness_contrast(img, 0, -100).getpixel((0, 0))
    assert out[0] == 128 and out[3] == 77
    assert ops.brightness_contrast(img, 0, 100).getpixel((0, 0))[0] == 72


def test_apply_brightness_respects_selection():
    doc = Document(4, 4, [Layer("a", _solid(4, 4, (100, 100, 100, 255)))])
    doc.selection = Rect(0, 0, 2, 4)
    ops.apply_brightness_contrast(doc, 50, 0)
    img = doc.layers[0].image
    assert img.getpixel((0, 0))[0] > 200 and img.getpixel((3, 0))[0] == 100
    assert ops.apply_brightness_contrast(doc, 0, 0) is None


def test_merge_down_respects_opacity():
    red = Layer("lower", _solid(4, 4, (255, 0, 0, 255)))
    blue = Layer("upper", _solid(2, 2, (0, 0, 255, 255)), x=1, y=1, opacity=0.5)
    doc = Document(4, 4, [red, blue], active=1)
    ops.merge_down(doc)
    assert len(doc.layers) == 1 and doc.active == 0
    merged = doc.layers[0]
    assert merged.name == "lower" and merged.opacity == 1.0
    px = merged.image.getpixel((1 - merged.x, 1 - merged.y))
    assert abs(px[0] - 128) <= 1 and abs(px[2] - 127) <= 1
    assert ops.merge_down(doc) is None


def test_delete_last_layer_refused():
    doc = Document.new(4, 4)
    assert ops.delete_layer(doc) is None


def test_move_layer_updates_active():
    doc = _doc()
    ops.move_layer(doc, -1)
    assert [la.name for la in doc.layers] == ["small", "base"] and doc.active == 0
    assert ops.move_layer(doc, -1) is None


def test_add_layer_name_and_position():
    doc = _doc()
    doc.active = 0
    ops.add_layer(doc)
    assert doc.layers[1].name == "Layer 3" and doc.active == 1
    assert doc.layers[1].image.size == (10, 4)


@pytest.mark.parametrize(
    "fn",
    [
        lambda d: ops.crop(d, Rect(1, 1, 8, 3)),
        lambda d: ops.resize_image(d, 5, 2, "nearest"),
        lambda d: ops.canvas_size(d, 12, 6, "top-left"),
        lambda d: ops.rotate(d, 90),
        lambda d: ops.rotate(d, 180, layer_only=True),
        lambda d: ops.flip(d, True),
        lambda d: ops.flip(d, False, layer_only=True),
        lambda d: ops.apply_brightness_contrast(d, 20, 30),
        ops.add_layer,
        ops.duplicate_layer,
        ops.delete_layer,
        ops.merge_down,
        lambda d: ops.move_layer(d, -1),
        lambda d: ops.rename_layer(d, 0, "renamed"),
        lambda d: ops.set_opacity(d, 1, 0.25),
        lambda d: ops.set_visible(d, 0, False),
    ],
)
def test_every_op_undo_redo(fn):
    _check_undo_redo(_doc(), fn)


def test_undo_order_mixed_commands():
    doc = Document.new(10, 10)
    h = History(doc)
    before = doc.active_layer.image.copy()
    doc.active_layer.image.putpixel((8, 8), (255, 0, 0, 255))
    h.push(PatchCommand.capture("Brush", doc, 0, Rect(8, 8, 9, 9), before))
    h.push(ops.crop(doc, Rect(0, 0, 5, 5)))
    assert (doc.width, doc.height) == (5, 5)
    h.undo()
    assert (doc.width, doc.height) == (10, 10)
    assert doc.active_layer.image.getpixel((8, 8)) == (255, 0, 0, 255)
    h.undo()
    assert doc.active_layer.image.getpixel((8, 8))[3] == 0
    h.redo()
    h.redo()
    assert (doc.width, doc.height) == (5, 5)
