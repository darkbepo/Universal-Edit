"""Editor GUI (offscreen): canvas, editor page, Convert | Edit switch."""

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402

from umc.editor.document import Document  # noqa: E402
from umc.editor.history import History  # noqa: E402
from umc.editor.tools.base import ToolContext  # noqa: E402
from umc.editor.tools.paint import BrushOptions, BrushTool  # noqa: E402


@pytest.fixture
def canvas(qtbot):
    from umc.gui.editor.canvas import EditorCanvas

    doc = Document.new(100, 100)
    h = History(doc)
    c = EditorCanvas()
    qtbot.addWidget(c)
    c.resize(200, 200)
    c.set_document(doc, h)
    c.set_tool(BrushTool())
    c.context_provider = lambda: ToolContext(
        (255, 0, 0, 255), (255, 255, 255, 255), BrushOptions(size=8)
    )
    c.set_zoom(1.0)
    return c, doc, h


def test_canvas_stroke_pushes_history(canvas, qtbot):
    c, doc, h = canvas
    p = c.image_to_widget(50, 50)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
    qtbot.mouseMove(c, p + QPoint(10, 0))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p + QPoint(10, 0))
    assert h.can_undo and doc.active_layer.image.getpixel((55, 50))[3] > 0
    assert c.cached_pixel(55, 50)[3] > 0  # the cached composite was refreshed


def test_zoom_limits(canvas):
    c, _, _ = canvas
    c.set_zoom(1000)
    assert c.zoom == 32
    c.set_zoom(0.0001)
    assert c.zoom == 0.05
    c.fit()
    assert 0.05 <= c.zoom <= 32


def test_widget_image_round_trip(canvas):
    c, _, _ = canvas
    c.set_zoom(2.5)
    c.pan_by(QPointF(13, -7))
    x, y = c.widget_to_image(QPointF(120, 80))
    back = c.image_to_widget(x, y)
    assert abs(back.x() - 120) <= 1 and abs(back.y() - 80) <= 1


def test_undo_refreshes_cache(canvas, qtbot):
    c, doc, h = canvas
    p = c.image_to_widget(20, 20)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p)
    c.undo()
    assert c.cached_pixel(20, 20)[3] == 0 and not h.can_undo
