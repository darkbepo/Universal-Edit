"""Editor tools on small documents."""

import time

from PIL import Image

from umc.editor.document import Document
from umc.editor.geometry import Rect
from umc.editor.history import History
from umc.editor.layer import Layer
from umc.editor.tools.base import ToolContext
from umc.editor.tools.fill import FillOptions, FillTool, PickerOptions, PickerTool
from umc.editor.tools.paint import BrushOptions, BrushTool, EraserOptions, EraserTool


def ctx(opts, color=(255, 0, 0, 255), **kw):
    return ToolContext(primary=color, secondary=(255, 255, 255, 255), options=opts, **kw)


def run_stroke(tool, doc, pts, c):
    tool.press(doc, *pts[0], c)
    for p in pts[1:]:
        tool.move(doc, *p, c)
    return tool.release(doc, *pts[-1], c)


def test_brush_paints_and_undoes():
    doc = Document.new(50, 50)
    h = History(doc)
    cmd = run_stroke(BrushTool(), doc, [(10, 10), (40, 10)], ctx(BrushOptions(size=6)))
    assert doc.active_layer.image.getpixel((25, 10)) == (255, 0, 0, 255)
    assert doc.active_layer.image.getpixel((25, 30))[3] == 0
    h.push(cmd)
    h.undo()
    assert doc.active_layer.image.getpixel((25, 10))[3] == 0


def test_brush_returns_dirty_rect():
    doc = Document.new(50, 50)
    tool, c = BrushTool(), ctx(BrushOptions(size=6))
    r = tool.press(doc, 20, 20, c)
    assert r is not None and r.contains(20, 20) and r.width < 15
    tool.release(doc, 20, 20, c)


def test_brush_opacity_does_not_stack_within_stroke():
    doc = Document.new(50, 50)
    opts = BrushOptions(size=10, opacity=50, hardness=100)
    run_stroke(BrushTool(), doc, [(10, 10), (10, 10.5), (10, 11), (10, 10)], ctx(opts))
    assert 120 <= doc.active_layer.image.getpixel((10, 10))[3] <= 135


def test_brush_respects_selection():
    doc = Document.new(50, 50)
    doc.selection = Rect(0, 0, 20, 50)
    run_stroke(BrushTool(), doc, [(5, 10), (45, 10)], ctx(BrushOptions(size=4)))
    assert doc.active_layer.image.getpixel((30, 10))[3] == 0
    assert doc.active_layer.image.getpixel((10, 10))[3] == 255


def test_brush_on_offset_layer():
    doc = Document(50, 50, [Layer("s", Image.new("RGBA", (20, 20)), x=30, y=30)])
    run_stroke(BrushTool(), doc, [(35, 35), (35, 35)], ctx(BrushOptions(size=4)))
    assert doc.layers[0].image.getpixel((5, 5)) == (255, 0, 0, 255)
    assert run_stroke(BrushTool(), doc, [(5, 5), (5, 5)], ctx(BrushOptions(size=4))) is None


def test_soft_brush_fades_at_edge():
    doc = Document.new(60, 60)
    run_stroke(BrushTool(), doc, [(30, 30)], ctx(BrushOptions(size=40, hardness=0)))
    img = doc.active_layer.image
    assert img.getpixel((30, 30))[3] > 200 > img.getpixel((47, 30))[3] > 0


def test_right_button_paints_secondary():
    doc = Document.new(20, 20)
    c = ctx(BrushOptions(size=4), button="right")
    run_stroke(BrushTool(), doc, [(10, 10)], c)
    assert doc.active_layer.image.getpixel((10, 10)) == (255, 255, 255, 255)


def test_eraser_clears():
    doc = Document(30, 30, [Layer("bg", Image.new("RGBA", (30, 30), (0, 0, 0, 255)))])
    run_stroke(EraserTool(), doc, [(5, 15), (25, 15)], ctx(EraserOptions(size=4)))
    img = doc.active_layer.image
    assert img.getpixel((15, 15))[3] == 0 and img.getpixel((15, 2))[3] == 255


def _two_halves():
    img = Image.new("RGBA", (20, 10), (10, 10, 10, 255))
    img.paste((40, 40, 40, 255), (10, 0, 20, 10))
    return Document(20, 10, [Layer("bg", img)])


def test_fill_tolerance():
    doc = _two_halves()
    tool = FillTool()
    c = ctx(FillOptions(tolerance=5))
    tool.press(doc, 2, 2, c)
    cmd = tool.release(doc, 2, 2, c)
    img = doc.active_layer.image
    assert img.getpixel((5, 5)) == (255, 0, 0, 255) and img.getpixel((15, 5)) == (40, 40, 40, 255)
    History(doc).push(cmd)
    doc2 = _two_halves()
    c = ctx(FillOptions(tolerance=40))
    tool.press(doc2, 2, 2, c)
    tool.release(doc2, 2, 2, c)
    assert doc2.active_layer.image.getpixel((15, 5)) == (255, 0, 0, 255)


def test_fill_is_connected_only():
    img = Image.new("RGBA", (21, 5), (0, 0, 0, 255))
    img.paste((255, 255, 255, 255), (10, 0, 11, 5))  # a wall in the middle
    doc = Document(21, 5, [Layer("bg", img)])
    tool, c = FillTool(), ctx(FillOptions(tolerance=0), color=(0, 0, 255, 255))
    tool.press(doc, 1, 1, c)
    tool.release(doc, 1, 1, c)
    assert doc.active_layer.image.getpixel((5, 2)) == (0, 0, 255, 255)
    assert doc.active_layer.image.getpixel((15, 2)) == (0, 0, 0, 255)


def test_fill_sample_all_layers():
    wall = Image.new("RGBA", (21, 5))
    wall.paste((0, 0, 0, 255), (10, 0, 11, 5))
    doc = Document(21, 5, [Layer("wall", wall), Layer("paint", Image.new("RGBA", (21, 5)))])
    doc.active = 1
    tool, c = FillTool(), ctx(FillOptions(tolerance=0, sample_all=True))
    tool.press(doc, 1, 1, c)
    tool.release(doc, 1, 1, c)
    paint = doc.layers[1].image
    assert paint.getpixel((5, 2)) == (255, 0, 0, 255) and paint.getpixel((15, 2))[3] == 0


def test_fill_respects_selection():
    doc = Document.new(20, 20)
    doc.selection = Rect(0, 0, 10, 10)
    tool, c = FillTool(), ctx(FillOptions())
    tool.press(doc, 2, 2, c)
    tool.release(doc, 2, 2, c)
    assert doc.active_layer.image.getpixel((15, 15))[3] == 0
    assert doc.active_layer.image.getpixel((5, 5))[3] == 255


def test_fill_transparent_layer_fast():
    doc = Document.new(4000, 4000)
    t = time.perf_counter()
    tool = FillTool()
    c = ctx(FillOptions(tolerance=0))
    tool.press(doc, 5, 5, c)
    tool.release(doc, 5, 5, c)
    assert time.perf_counter() - t < 2.0
    assert doc.active_layer.image.getpixel((3999, 3999)) == (255, 0, 0, 255)


def test_picker_sets_color():
    img = Image.new("RGBA", (10, 10), (0, 0, 255, 255))
    doc = Document(10, 10, [Layer("bg", img)])
    tool = PickerTool()
    tool.press(doc, 3, 3, ctx(PickerOptions()))
    assert tool.picked == (0, 0, 255, 255) and tool.picked_button == "left"
    assert tool.release(doc, 3, 3, ctx(PickerOptions())) is None


def test_tools_registry_order():
    from umc.editor.tools import TOOLS

    ids = [t.id for t in TOOLS]
    assert ids[:4] == ["brush", "eraser", "fill", "picker"]
    assert len(set(ids)) == len(ids)


def test_fill_semi_transparent_color_blends():
    doc = Document(4, 4, [Layer("bg", Image.new("RGBA", (4, 4), (0, 0, 255, 255)))])
    tool, c = FillTool(), ctx(FillOptions(), color=(255, 0, 0, 128))
    tool.press(doc, 1, 1, c)
    tool.release(doc, 1, 1, c)
    r, g, b, a = doc.active_layer.image.getpixel((1, 1))
    assert a == 255 and 120 <= r <= 135 and 120 <= b <= 135
