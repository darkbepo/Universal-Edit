"""Selections of any shape: modes, tools, invert / feather, outline, and every pixel-changing
operation respecting the selection's shape (model only, no Qt)."""

import numpy as np
from PIL import Image

from umc.core import options as opts
from umc.editor import adjust, clipboard, effects, filters, selection
from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.tools.base import ToolContext
from umc.editor.tools.fill import FillOptions, FillTool
from umc.editor.tools.paint import BrushOptions, BrushTool
from umc.editor.tools.select import (
    EllipseSelectTool,
    LassoTool,
    MagicWandTool,
    PolygonLassoTool,
    SelectTool,
    clear_selection,
)


def _doc(w=40, h=30, color=(10, 20, 30, 255)):
    return Document(w, h, [Layer("bg", Image.new("RGBA", (w, h), color))])


def _ctx(options=None, shift=False, alt=False, zoom=1.0):
    return ToolContext((255, 0, 0, 255), (0, 0, 255, 255), options, shift=shift, alt=alt, zoom=zoom)


def _drag(tool, doc, a, b, **kw):
    c = _ctx(**kw)
    tool.press(doc, *a, c)
    tool.move(doc, *b, c)
    return tool.release(doc, *b, c)


def _mask(doc):
    return np.asarray(selection.full_mask(doc)) > 127


def test_rectangle_add_subtract_intersect_with_undo():
    doc = _doc()
    h = History(doc)
    h.push(_drag(SelectTool(), doc, (0, 0), (10, 10)))
    assert doc.selection == Rect(0, 0, 10, 10) and doc.selection_mask is None  # plain rectangle
    h.push(_drag(SelectTool(), doc, (20, 0), (30, 10), shift=True))  # add
    assert doc.selection == Rect(0, 0, 30, 10) and doc.selection_mask is not None
    assert _mask(doc)[5, 5] and _mask(doc)[5, 25] and not _mask(doc)[5, 15]
    h.push(_drag(SelectTool(), doc, (5, 0), (25, 10), alt=True))  # subtract
    assert _mask(doc)[5, 2] and not _mask(doc)[5, 7] and _mask(doc)[5, 27]
    h.push(_drag(SelectTool(), doc, (0, 0), (3, 10), shift=True, alt=True))  # intersect
    assert doc.selection == Rect(0, 0, 3, 10)
    for _ in range(3):
        h.undo()
    assert doc.selection == Rect(0, 0, 10, 10) and doc.selection_mask is None
    h.redo()
    assert doc.selection == Rect(0, 0, 30, 10) and not _mask(doc)[5, 15]


def test_click_without_drag_deselects_only_in_replace_mode():
    doc = _doc()
    _drag(SelectTool(), doc, (0, 0), (10, 10))
    assert _drag(SelectTool(), doc, (3, 3), (3, 3), shift=True) is None
    assert doc.selection is not None
    _drag(SelectTool(), doc, (3, 3), (3, 3))
    assert doc.selection is None


def test_ellipse_lasso_and_polygon_shapes():
    doc = _doc()
    _drag(EllipseSelectTool(), doc, (0, 0), (20, 20))
    m = _mask(doc)
    assert m[10, 10] and not m[1, 1] and doc.selection == Rect(0, 0, 20, 20)
    lasso = LassoTool()
    c = _ctx()
    lasso.press(doc, 0, 0, c)
    for p in ((30, 0), (30, 20), (0, 20)):
        lasso.move(doc, *p, c)
    lasso.release(doc, 0, 20, c)
    assert _mask(doc)[10, 15] and not _mask(doc)[25, 15]
    poly = PolygonLassoTool()
    for p in ((5, 5), (35, 5), (20, 25)):
        poly.press(doc, *p, c)
        assert poly.release(doc, *p, c) is None
    assert poly.outline is not None
    cmd = poly.release(doc, 5.5, 5.5, _ctx(zoom=4.0))  # back at the first corner closes it
    assert cmd is not None and _mask(doc)[10, 20] and not _mask(doc)[24, 5]
    poly.press(doc, 1, 1, c)
    poly.release(doc, 1, 1, c)
    poly.cancel(doc)
    assert poly.points == [] and poly.finish(doc) is None


def test_magic_wand_contiguous_and_everywhere():
    img = Image.new("RGBA", (30, 10), (255, 255, 255, 255))
    img.paste((0, 0, 0, 255), (10, 0, 12, 10))  # a black bar splits the white
    doc = Document(30, 10, [Layer("bg", img)])
    o = opts.build(MagicWandTool.options_cls, opts.defaults(MagicWandTool.options_cls))
    _drag(MagicWandTool(), doc, (2, 2), (2, 2), options=o)
    assert doc.selection == Rect(0, 0, 10, 10)
    o.contiguous = False
    _drag(MagicWandTool(), doc, (2, 2), (2, 2), options=o)
    assert _mask(doc)[5, 20] and not _mask(doc)[5, 11]


def test_invert_and_feather():
    doc = _doc()
    _drag(SelectTool(), doc, (0, 0), (10, 30))
    selection.invert(doc)
    assert doc.selection == Rect(10, 0, 40, 30) and doc.selection_mask is None
    selection.feather(doc, 3)
    w = selection.weight(doc, doc.selection)
    assert w is not None and 0 < w[15, 0] < 0.6 and w[15, 20] > 0.99
    assert doc.selection.x0 < 10  # the soft edge spreads a little outwards
    selection.deselect(doc)
    assert selection.feather(doc, 3) is None and selection.invert(doc) is not None
    assert doc.selection == doc.bounds  # inverting "nothing" selects everything


def test_outline_follows_the_shape():
    doc = _doc()
    _drag(SelectTool(), doc, (0, 0), (10, 10))
    assert len(selection.outline(doc)) == 4
    _drag(SelectTool(), doc, (20, 0), (30, 10), shift=True)
    segs = selection.outline(doc)
    assert (0, 0, 10, 0) in segs and (20, 0, 30, 0) in segs and (10, 0, 10, 10) in segs
    selection.deselect(doc)
    assert selection.outline(doc) == []


def _two_squares(doc):
    _drag(SelectTool(), doc, (0, 0), (10, 10))
    _drag(SelectTool(), doc, (20, 0), (30, 10), shift=True)


def test_painting_tools_respect_the_shape():
    doc = _doc()
    _two_squares(doc)
    o = opts.build(FillOptions, opts.defaults(FillOptions))
    tool = FillTool()
    tool.press(doc, 5, 5, _ctx(o))
    tool.release(doc, 5, 5, _ctx(o))
    img = doc.layers[0].image
    assert img.getpixel((5, 5))[:3] == (255, 0, 0)
    assert img.getpixel((15, 5)) == (10, 20, 30, 255)  # the gap between the squares
    assert img.getpixel((25, 5))[:3] == (255, 0, 0)
    doc = _doc()
    _two_squares(doc)
    b = BrushTool()
    bo = opts.build(BrushOptions, {**opts.defaults(BrushOptions), "size": 60, "hardness": 100})
    b.press(doc, 15, 5, _ctx(bo))
    b.release(doc, 15, 5, _ctx(bo))
    img = doc.layers[0].image
    assert img.getpixel((5, 5))[:3] == (255, 0, 0) and img.getpixel((15, 5)) == (10, 20, 30, 255)


def test_clear_effects_copy_and_cut_respect_the_shape():
    doc = _doc()
    _two_squares(doc)
    clear_selection(doc)
    img = doc.layers[0].image
    assert img.getpixel((5, 5))[3] == 0 and img.getpixel((15, 5))[3] == 255
    doc = _doc()
    _two_squares(doc)
    effects.apply_to_document(doc, adjust.Adjustments(brightness=100), "Quick fix")
    assert doc.layers[0].image.getpixel((15, 5)) == (10, 20, 30, 255)
    assert doc.layers[0].image.getpixel((5, 5))[0] > 200
    preview = effects.preview(doc, filters.Look("bw"))
    assert preview.getpixel((15, 5)) == (10, 20, 30, 255)
    doc = _doc()
    _two_squares(doc)
    clip = clipboard.copy(doc)
    assert clip.image.size == (30, 10)
    assert clip.image.getpixel((5, 5))[3] == 255 and clip.image.getpixel((15, 5))[3] == 0
    _clip, _cmd = clipboard.cut(doc)
    assert (
        doc.layers[0].image.getpixel((15, 5))[3] == 255
        and doc.layers[0].image.getpixel((5, 5))[3] == 0
    )


def test_soft_selection_mixes_on_premultiplied_colour():
    img = Image.new("RGBA", (20, 1), (0, 0, 0, 0))
    doc = Document(20, 1, [Layer("bg", img)])
    doc.selection, doc.selection_mask = (
        Rect(0, 0, 20, 1),
        Image.fromarray(np.linspace(0, 255, 20).astype(np.uint8)[None, :], "L"),
    )
    o = opts.build(FillOptions, opts.defaults(FillOptions))
    FillTool().press(doc, 10, 0, _ctx(o))
    r, g, b, a = doc.layers[0].image.getpixel((10, 0))
    assert (r, g, b) == (
        255,
        0,
        0,
    ) and 100 < a < 160  # half selected: half see-through red, not dark


# ------------------------------------------------------------------ moving a selection


def test_dragging_inside_the_selection_moves_it_with_undo():
    doc = _doc()
    h = History(doc)
    t = SelectTool()
    h.push(_drag(t, doc, (5, 5), (15, 10)))
    assert doc.selection == Rect(5, 5, 15, 10)
    h.push(_drag(t, doc, (8, 7), (20, 17)))  # starts inside: moves by (12, 10)
    assert doc.selection == Rect(17, 15, 27, 20) and h.undo_label == "Move selection"
    assert doc.layers[0].image.getpixel((17, 15)) == (10, 20, 30, 255)  # pixels untouched
    h.undo()
    assert doc.selection == Rect(5, 5, 15, 10)


def test_a_moved_shaped_selection_keeps_its_shape_and_is_trimmed():
    doc = _doc()
    h = History(doc)
    h.push(_drag(EllipseSelectTool(), doc, (0, 0), (20, 20)))
    before = _mask(doc)
    h.push(_drag(LassoTool(), doc, (10, 10), (15, 12)))  # lasso also moves it
    after = _mask(doc)
    assert (after[2:22, 5:25] == before[0:20, 0:20]).all()
    h.push(selection.move_selection(doc, 30, 0))  # mostly off the canvas: trimmed
    assert doc.selection.x1 == 40 and doc.selection.x0 >= 35


def test_shift_or_a_click_outside_still_selects():
    doc = _doc()
    t = SelectTool()
    _drag(t, doc, (5, 5), (15, 10))
    _drag(t, doc, (8, 7), (30, 25), shift=True)  # Shift inside: adds, doesn't move
    assert doc.selection == Rect(5, 5, 30, 25)
    _drag(t, doc, (8, 7), (8, 7))  # a click inside: deselects as before
    assert doc.selection is None


def test_wand_click_inside_still_picks_and_polygon_drag_moves():
    doc = _doc()
    doc.selection = Rect(0, 0, 10, 10)
    w = MagicWandTool()
    o = opts.build(type(w).options_cls, opts.defaults(type(w).options_cls))
    c = _ctx(o)
    w.press(doc, 5, 5, c)
    w.release(doc, 5, 5, c)
    assert doc.selection == Rect(0, 0, 40, 30)  # the whole single-colour picture
    doc.selection = Rect(0, 0, 10, 10)
    p = PolygonLassoTool()
    _drag(p, doc, (5, 5), (8, 9))
    assert doc.selection == Rect(3, 4, 13, 14) and p.points == []


def test_contains_follows_the_shape():
    doc = _doc()
    _drag(EllipseSelectTool(), doc, (0, 0), (20, 20))
    assert selection.contains(doc, 10, 10) and not selection.contains(doc, 0.5, 0.5)
    assert not selection.contains(doc, 30, 10)
