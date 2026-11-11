"""Annotation: arrow tool, highlighter and built-in stickers."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from umc.editor import ops
from umc.editor.document import Document
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.stickers import STICKERS, as_json, render_sticker
from umc.editor.tools.base import ToolContext
from umc.editor.tools.paint import HighlighterTool
from umc.editor.tools.shapes import ArrowTool, arrow_head

ROOT = Path(__file__).resolve().parents[1]


def _doc(w=120, h=80, color=(255, 255, 255, 255)) -> Document:
    return Document(w, h, [Layer("bg", Image.new("RGBA", (w, h), color))])


def _ctx(tool, color=(255, 0, 0, 255), **opts) -> ToolContext:
    return ToolContext(color, (255, 255, 255, 255), tool.options_cls(**opts))


def _stroke(tool, doc, pts, ctx):
    tool.press(doc, *pts[0], ctx)
    for p in pts[1:]:
        tool.move(doc, *p, ctx)
    return tool.release(doc, *pts[-1], ctx)


def test_stickers_match_the_website_copy():
    web = json.loads((ROOT / "web/src/editor/stickers.json").read_text())
    web.pop("_note")
    ours = json.loads(as_json())
    ours.pop("_note")
    assert web == ours and set(web) == set(STICKERS)


def test_every_sticker_renders_something():
    for name in STICKERS:
        img = render_sticker(name, 48, (0, 0, 255, 255), (255, 255, 0, 255))
        assert img.size == (48, 48)
        assert img.getchannel("A").getbbox() is not None, name


def test_swatch_colours_are_used():
    img = render_sticker("arrow", 50, (0, 0, 255, 255))
    assert img.getpixel((25, 25)) == (0, 0, 255, 255)


def test_add_sticker_is_a_centred_layer_and_undoes():
    doc = _doc(200, 100)
    h = History(doc)
    h.push(ops.add_sticker(doc, "star", 40, (100, 50)))
    assert len(doc.layers) == 2 and doc.active == 1
    la = doc.layers[1]
    assert la.name == "Star 2" and (la.x, la.y) == (80, 30) and la.image.size == (40, 40)
    h.undo()
    assert len(doc.layers) == 1


def test_arrow_head_points_at_the_end():
    tip, (bx, by) = arrow_head(0, 0, 100, 0, 4)
    assert tip[0] == (100, 0)
    assert bx < 100 and by == 0
    assert {round(p[1]) for p in tip[1:]} == {9, -9}


def test_arrow_draws_line_and_head():
    doc = _doc()
    t = ArrowTool()
    _stroke(t, doc, [(10, 40), (100, 40)], _ctx(t, width=4))
    px = doc.composite()
    assert px.getpixel((50, 40))[:3] == (255, 0, 0)  # the shaft
    assert px.getpixel((88, 45))[:3] == (255, 0, 0)  # the head is wider than the shaft
    assert px.getpixel((50, 46))[:3] == (255, 255, 255)
    assert px.getpixel((10, 46))[:3] == (255, 255, 255)  # no head at the start


def test_arrow_with_two_heads():
    doc = _doc()
    t = ArrowTool()
    _stroke(t, doc, [(10, 40), (100, 40)], _ctx(t, width=4, heads="both"))
    assert doc.composite().getpixel((22, 45))[:3] == (255, 0, 0)


def test_highlighter_is_see_through_and_does_not_build_up():
    doc = _doc(100, 40, (0, 0, 0, 0))
    t = HighlighterTool()
    _stroke(t, doc, [(10, 20), (90, 20), (10, 20), (90, 20)], _ctx(t, color=(255, 255, 0, 255)))
    a = doc.layers[0].image.getpixel((50, 20))[3]
    assert 90 <= a <= 115  # 40 %, once


# ------------------------------------------------------------------ shape layers


def test_each_shape_is_its_own_layer_with_its_spec():
    from umc.editor.tools.shapes import RectTool, canvas_points

    doc = _doc()
    h = History(doc)
    t = RectTool()
    h.push(_stroke(t, doc, [(10, 10), (40, 30)], _ctx(t, width=2, mode="both")))
    assert len(doc.layers) == 2 and doc.active == 1
    la = doc.active_layer
    assert la.shape.kind == "rect" and la.shape.mode == "both"
    assert canvas_points(la) == (10, 10, 40, 30)
    assert doc.layers[0].image.getpixel((20, 20)) == (255, 255, 255, 255)  # the photo untouched
    h.undo()
    assert len(doc.layers) == 1


def test_dragging_a_corner_reshapes_and_is_one_undo_step():
    from umc.editor.tools.shapes import EllipseTool, canvas_points

    doc = _doc()
    h = History(doc)
    t = EllipseTool()
    h.push(_stroke(t, doc, [(10, 10), (40, 30)], _ctx(t)))
    assert t.handles(doc)[2] == (40, 30)
    c = _ctx(t)
    t.press(doc, 40, 30, c)  # the bottom-right handle
    t.move(doc, 60, 50, c)
    assert canvas_points(doc.active_layer) == (10, 10, 60, 50)  # shown while dragging
    h.push(t.release(doc, 60, 50, c))
    assert canvas_points(doc.active_layer) == (10, 10, 60, 50) and h.undo_label == "Edit shape"
    assert len(doc.layers) == 2  # reshaped, not a new layer
    h.undo()
    assert canvas_points(doc.active_layer) == (10, 10, 40, 30)


def test_moving_a_shape_layer_keeps_its_handles_on_it():
    from umc.editor import ops
    from umc.editor.tools.shapes import LineTool

    doc = _doc()
    t = LineTool()
    _stroke(t, doc, [(10, 10), (50, 20)], _ctx(t))
    ops.nudge_layers(doc, [doc.active], 5, 7)
    assert t.handles(doc) == [(15, 17), (55, 27)]


def test_painted_shapes_and_transforms_become_pixels():
    from umc.editor import ops
    from umc.editor.tools.shapes import RectTool, shape_painted_over

    doc = _doc()
    t = RectTool()
    _stroke(t, doc, [(10, 10), (40, 30)], _ctx(t))
    assert not shape_painted_over(doc.active_layer)
    doc.active_layer.image.putpixel((0, 0), (1, 2, 3, 255))
    assert shape_painted_over(doc.active_layer)
    t.press(doc, 10, 10, _ctx(t))  # on a handle: refused, it's pixels now
    assert t.notice and "painted on" in t.notice
    h = History(doc)
    h.push(ops.rasterize_layer(doc))
    assert doc.active_layer.shape is None and h.undo_label == "Rasterize layer"
    _stroke(t, doc, [(60, 10), (90, 30)], _ctx(t))
    ops.rotate(doc, 90, layer_only=True)
    assert doc.active_layer.shape is None


def test_shape_layers_stay_editable_through_an_ora_file(tmp_path):
    from umc.editor.ora import read_ora, write_ora
    from umc.editor.tools.shapes import ArrowTool, canvas_points

    doc = _doc()
    t = ArrowTool()
    _stroke(t, doc, [(10, 40), (100, 40)], _ctx(t, width=5, heads="both"))
    write_ora(doc, tmp_path / "s.ora")
    back = read_ora(tmp_path / "s.ora")
    la = back.layers[1]
    assert la.shape.kind == "arrow" and la.shape.heads == "both" and la.shape.width == 5
    assert canvas_points(la) == (10, 40, 100, 40)
