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
    assert ids[:5] == ["move", "brush", "eraser", "fill", "picker"]
    assert len(set(ids)) == len(ids)


def test_fill_semi_transparent_color_blends():
    doc = Document(4, 4, [Layer("bg", Image.new("RGBA", (4, 4), (0, 0, 255, 255)))])
    tool, c = FillTool(), ctx(FillOptions(), color=(255, 0, 0, 128))
    tool.press(doc, 1, 1, c)
    tool.release(doc, 1, 1, c)
    r, g, b, a = doc.active_layer.image.getpixel((1, 1))
    assert a == 255 and 120 <= r <= 135 and 120 <= b <= 135


# ------------------------------------------------------------------ shapes, select, text

from umc.editor.history import History as _History  # noqa: E402
from umc.editor.tools.select import (  # noqa: E402
    CropTool,
    SelectTool,
    clear_selection,
    deselect,
    select_all,
)
from umc.editor.tools.shapes import (  # noqa: E402
    EllipseTool,
    LineOptions,
    LineTool,
    RectTool,
    ShapeOptions,
)
from umc.editor.tools.text import add_text_layer, render_text  # noqa: E402


def test_rect_outline_vs_filled():
    doc = Document.new(40, 40)
    run_stroke(RectTool(), doc, [(5, 5), (35, 35)], ctx(ShapeOptions(width=2, mode="outline")))
    img = doc.active_layer.image
    assert img.getpixel((20, 20))[3] == 0 and img.getpixel((5, 20))[3] > 200
    doc2 = Document.new(40, 40)
    run_stroke(RectTool(), doc2, [(5, 5), (35, 35)], ctx(ShapeOptions(mode="filled")))
    assert doc2.active_layer.image.getpixel((20, 20)) == (255, 255, 255, 255)


def test_ellipse_filled_centre_not_corner():
    doc = Document.new(40, 40)
    run_stroke(EllipseTool(), doc, [(0, 0), (40, 40)], ctx(ShapeOptions(mode="both")))
    img = doc.active_layer.image
    assert img.getpixel((20, 20)) == (255, 255, 255, 255) and img.getpixel((1, 1))[3] == 0


def test_line_shift_snaps():
    doc = Document.new(50, 50)
    run_stroke(LineTool(), doc, [(10, 10), (40, 12)], ctx(LineOptions(width=3), shift=True))
    img = doc.active_layer.image
    assert img.getpixel((39, 10))[3] > 200 and img.getpixel((39, 13))[3] == 0


def test_shape_preview_then_commit_undo():
    doc = Document.new(40, 40)
    tool, c = RectTool(), ctx(ShapeOptions(mode="filled"))
    tool.press(doc, 5, 5, c)
    tool.move(doc, 30, 30, c)
    prev = tool.preview(doc)
    assert prev is not None and doc.active_layer.image.getpixel((15, 15))[3] == 0
    h = _History(doc)
    h.push(tool.release(doc, 30, 30, c))
    assert tool.preview(doc) is None and doc.active_layer.image.getpixel((15, 15))[3] == 255
    h.undo()
    assert doc.active_layer.image.getpixel((15, 15))[3] == 0


def test_select_and_clear():
    doc = Document(20, 20, [Layer("bg", Image.new("RGBA", (20, 20), (0, 0, 0, 255)))])
    h = _History(doc)
    h.push(run_stroke(SelectTool(), doc, [(0, 0), (10, 10)], ctx(None)))
    assert doc.selection == Rect(0, 0, 10, 10)
    h.push(clear_selection(doc))
    img = doc.active_layer.image
    assert img.getpixel((5, 5))[3] == 0 and img.getpixel((15, 15))[3] == 255
    h.undo()
    assert doc.active_layer.image.getpixel((5, 5))[3] == 255
    h.undo()
    assert doc.selection is None


def test_selection_clipped_to_canvas_and_click_clears():
    doc = Document.new(20, 20)
    run_stroke(SelectTool(), doc, [(10, 10), (50, -5)], ctx(None))
    assert doc.selection == Rect(10, 0, 20, 10)
    run_stroke(SelectTool(), doc, [(3, 3), (3, 3)], ctx(None))
    assert doc.selection is None


def test_select_all_and_deselect():
    doc = Document.new(8, 6)
    h = _History(doc)
    h.push(select_all(doc))
    assert doc.selection == Rect(0, 0, 8, 6)
    h.push(deselect(doc))
    assert doc.selection is None and deselect(doc) is None
    assert clear_selection(doc) is None


def test_crop_tool_pending_rect():
    doc = Document.new(30, 30)
    tool = CropTool()
    run_stroke(tool, doc, [(2, 3), (12, 20)], ctx(None))
    assert tool.pending == Rect(2, 3, 12, 20) and doc.selection is None


def test_text_layer_added():
    doc = Document.new(200, 100)
    h = _History(doc)
    cmd = add_text_layer(doc, "Hello", 10, 10, "DejaVu Sans", 40, False, False, (255, 0, 0, 255))
    h.push(cmd)
    assert len(doc.layers) == 2 and doc.active == 1 and doc.active_layer.name == "Hello"
    assert (doc.active_layer.x, doc.active_layer.y) == (10, 10)
    assert (255, 0, 0, 255) in [c for _, c in doc.active_layer.image.getcolors(100000)]
    h.undo()
    assert len(doc.layers) == 1
    assert add_text_layer(doc, "   ", 0, 0, "DejaVu Sans", 40, False, False, (0, 0, 0, 255)) is None


def test_find_font_dejavu():
    import pytest

    from umc.converters.subtitles import fonts_dir
    from umc.editor.fonts import find_font

    if fonts_dir() is None:
        pytest.skip("bundled font not fetched")
    assert find_font("DejaVu Sans", False, False).name == "DejaVuSans.ttf"


def test_render_text_unknown_font_falls_back():
    img = render_text("Hi\nthere", "No Such Font 123", 30, True, True, (0, 0, 0, 255))
    assert img.width > 10 and img.height > 30 and img.getbbox() is not None


def test_registry_has_all_tools():
    from umc.editor.tools import TOOLS

    assert [t.id for t in TOOLS] == [
        "move",
        "brush",
        "eraser",
        "fill",
        "picker",
        "line",
        "rect",
        "ellipse",
        "text",
        "select",
        "crop",
    ]


def test_stroke_does_not_copy_whole_layer(monkeypatch):
    import tracemalloc

    from PIL import Image as PILImage

    doc = Document.new(3000, 3000)
    sizes = []
    real_copy = PILImage.Image.copy
    monkeypatch.setattr(
        PILImage.Image, "copy", lambda self: sizes.append(self.size) or real_copy(self)
    )
    tracemalloc.start()
    cmd = run_stroke(BrushTool(), doc, [(100, 100), (200, 150)], ctx(BrushOptions(size=20)))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert (3000, 3000) not in sizes
    assert peak < 20 * 1024**2
    assert cmd.rect.width < 200 and cmd.before.getpixel((10, 10))[3] == 0
    sizes.clear()
    run_stroke(RectTool(), doc, [(10, 10), (60, 60)], ctx(ShapeOptions()))
    assert (3000, 3000) not in sizes
    # fill inside a small hole of an opaque layer: only the hole's area is remembered
    wall = Document(3000, 3000, [Layer("bg", Image.new("RGBA", (3000, 3000), (0, 0, 0, 255)))])
    wall.layers[0].image.paste((0, 0, 0, 0), (100, 100, 150, 150))
    sizes.clear()
    cmd = run_stroke(FillTool(), wall, [(120, 120), (120, 120)], ctx(FillOptions(tolerance=0)))
    assert (3000, 3000) not in sizes and cmd.rect == Rect(100, 100, 150, 150)


def test_shape_preview_is_bounded_on_large_canvas():
    doc = Document.new(4000, 3000)
    tool, c = RectTool(), ctx(ShapeOptions())
    tool.press(doc, -1000, -1000, c)
    t = time.perf_counter()
    tool.move(doc, 5000, 4000, c)
    elapsed = time.perf_counter() - t
    img, x, y = tool.preview(doc)
    assert img.width <= 4000 + 20 and img.height <= 3000 + 20
    assert elapsed < 1.0
    tool.release(doc, 5000, 4000, c)
    assert doc.active_layer.image.getpixel((0, 1500))[3] == 0  # outline is off-canvas


def test_every_tool_cancels_mid_gesture():
    """An interrupted gesture (Esc, Ctrl+Z while the mouse is down) must leave no trace."""
    from umc.core import options as opts
    from umc.editor.tools import TOOLS

    for cls in TOOLS:
        doc = Document(40, 40, [Layer("bg", Image.new("RGBA", (40, 40), (10, 20, 30, 255)))])
        before = doc.layers[0].image.tobytes()
        tool = cls()
        o = opts.build(cls.options_cls, opts.defaults(cls.options_cls)) if cls.options_cls else None
        c = ctx(o)
        tool.press(doc, 10, 10, c)
        tool.move(doc, 30, 25, c)
        tool.cancel(doc)
        assert doc.layers[0].image.tobytes() == before, cls.id
        assert tool.release(doc, 30, 25, c) is None, cls.id
