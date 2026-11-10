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


# ------------------------------------------------------------------ editor page

from PIL import Image  # noqa: E402


@pytest.fixture
def page(qtbot, tmp_path):
    from umc.gui.editor.editor_page import EditorPage

    a, b = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (60, 40), "red").save(a)
    Image.new("RGBA", (30, 30), (0, 0, 255, 128)).save(b)
    pg = EditorPage()
    qtbot.addWidget(pg)
    pg.resize(1100, 720)
    pg.show()
    assert pg.open_paths([a, b])
    return pg


def _stroke(qtbot, pg, x=10, y=10):
    c = pg.canvas
    p = c.image_to_widget(x, y)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
    qtbot.mouseMove(c, p + QPoint(15, 0))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p + QPoint(15, 0))


def test_page_opens_one_layer_per_image(page):
    assert len(page.doc.layers) == 2 and page.layers.list.count() == 2
    assert page.layers.list.item(0).text() == "second"  # top layer first
    assert (page.doc.width, page.doc.height) == (60, 40)


def test_open_paths_reports_bad_file(qtbot, tmp_path):
    from umc.gui.editor.editor_page import EditorPage

    bad = tmp_path / "bad.png"
    bad.write_bytes(b"junk")
    pg = EditorPage()
    qtbot.addWidget(pg)
    assert not pg.open_paths([bad])
    assert "bad.png" in pg.message.text()


def test_shortcut_selects_tool(page, qtbot):
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_E)
    assert page.tool.id == "eraser" and page.tool_bar.current() == "eraser"
    qtbot.keyClick(page.canvas, Qt.Key.Key_U)
    qtbot.keyClick(page.canvas, Qt.Key.Key_U)
    assert page.tool.id == "ellipse"


def test_stroke_enables_undo(page, qtbot):
    assert not page.undo_button.isEnabled()
    _stroke(qtbot, page)
    assert page.undo_button.isEnabled() and page.doc.dirty
    page.undo_button.click()
    assert not page.undo_button.isEnabled()


def test_add_and_delete_layer_buttons_undoable(page):
    page.layers.add_button.click()
    assert len(page.doc.layers) == 3 and page.layers.list.count() == 3
    page.layers.delete_button.click()
    assert len(page.doc.layers) == 2
    page.canvas.undo()
    assert len(page.doc.layers) == 3 and page.layers.list.count() == 3


def test_layer_visibility_checkbox(page):
    item = page.layers.list.item(1)  # bottom layer "first"
    item.setCheckState(Qt.CheckState.Unchecked)
    assert not page.doc.layers[0].visible and page.history.can_undo


def _patch_save_dialog(monkeypatch, page, path):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(path), ""))
    monkeypatch.setattr(page, "_save_options", lambda target_id: {})


@pytest.mark.parametrize("ext", ["png", "ora"])
def test_save_as(page, qtbot, tmp_path, monkeypatch, ext):
    _stroke(qtbot, page)
    out = tmp_path / f"saved.{ext}"
    _patch_save_dialog(monkeypatch, page, out)
    page.save_as()
    qtbot.waitUntil(lambda: out.exists() and not page.saving, timeout=10_000)
    assert not page.doc.dirty
    if ext == "png":
        assert Image.open(out).size == (60, 40)
    else:
        from umc.editor.ora import read_ora

        assert len(read_ora(out).layers) == 2


def test_save_repeats_last_save_as(page, qtbot, tmp_path, monkeypatch):
    out = tmp_path / "again.png"
    _patch_save_dialog(monkeypatch, page, out)
    page.save_as()
    qtbot.waitUntil(lambda: not page.saving, timeout=10_000)
    out.unlink()
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: pytest.fail("asked"))
    _stroke(qtbot, page)
    page.save()
    qtbot.waitUntil(lambda: out.exists() and not page.saving, timeout=10_000)


def test_leave_with_unsaved_changes_asks(page, qtbot, monkeypatch):
    _stroke(qtbot, page)
    monkeypatch.setattr(page, "_ask_save_changes", lambda: "cancel")
    assert not page.maybe_leave()
    monkeypatch.setattr(page, "_ask_save_changes", lambda: "discard")
    assert page.maybe_leave()


def test_leave_without_changes_does_not_ask(page, monkeypatch):
    monkeypatch.setattr(page, "_ask_save_changes", lambda: pytest.fail("asked"))
    assert page.maybe_leave()


def test_brightness_dialog_preview_cancel_restores(page, qtbot):
    from umc.gui.editor.dialogs import BrightnessContrastDialog

    before = page.doc.active_layer.image.tobytes()
    dlg = BrightnessContrastDialog(page)
    dlg.brightness.setValue(60)
    dlg.update_preview()
    assert page.canvas._preview_layer is not None
    dlg.reject()
    assert page.canvas._preview_layer is None
    assert page.doc.active_layer.image.tobytes() == before and not page.history.can_undo


def test_brightness_apply(page):
    page.apply_brightness_contrast(50, 0)
    assert page.history.can_undo and page.history.undo_label == "Brightness / contrast"


def test_text_box_commits_new_layer(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    p = c.image_to_widget(5, 5)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=p)
    assert page.text_box is not None
    page.text_box.setPlainText("Hi")
    page.commit_text()
    assert len(page.doc.layers) == 3 and page.doc.active_layer.name == "Hi"


def test_crop_with_enter(page, qtbot):
    page.select_tool("crop")
    c = page.canvas
    a, b = c.image_to_widget(0, 0), c.image_to_widget(20, 10)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(c, b)
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=b)
    c.setFocus()
    qtbot.keyClick(c, Qt.Key.Key_Return)
    assert (page.doc.width, page.doc.height) == (20, 10)


def test_image_menu_rotate(page):
    page.run_op("rotate_right")
    assert (page.doc.width, page.doc.height) == (40, 60)
