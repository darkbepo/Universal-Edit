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
    yield pg
    pg._ask_save_changes = lambda: "discard"


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


# ------------------------------------------------------------------ Convert | Edit switch


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    from umc.gui.editor.editor_page import EditorPage

    # pytest-qt closes windows before fixture teardown: never block on "Save changes?"
    monkeypatch.setattr(EditorPage, "_ask_save_changes", lambda self: "discard")

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    w._set_output_dir(tmp_path)
    qtbot.addWidget(w)
    w.show()
    return w


def test_switch_hidden_for_non_images(window, media):
    window.add_files([str(media / "doc.md")])
    assert not window.mode_bar.isVisible()
    window.clear_files()
    assert not window.mode_bar.isVisible()


def test_switch_shown_for_images(window, media):
    window.add_files([str(media / "img0.png"), str(media / "img1.png")])
    assert window.mode_bar.isVisible() and window.convert_mode.isChecked()
    window.add_files([str(media / "doc.md")])  # mixed → no editing
    assert not window.mode_bar.isVisible()


def test_edit_and_back(window, media, monkeypatch):
    window.resize(620, 780)
    window.add_files([str(media / "img0.png"), str(media / "img1.png")])
    window.edit_mode.click()
    assert window.stack.currentIndex() == 1
    assert window.width() >= 1100 and window.height() >= 720
    assert len(window.editor.doc.layers) == 2
    window.editor.back_requested.emit()
    assert window.stack.currentIndex() == 0 and window.convert_mode.isChecked()
    assert (window.width(), window.height()) == (620, 780)


def test_back_cancelled_stays_in_editor(window, media, monkeypatch, qtbot):
    window.add_files([str(media / "img0.png")])
    window.edit_mode.click()
    monkeypatch.setattr(window.editor, "maybe_leave", lambda: False)
    window.convert_mode.click()
    assert window.stack.currentIndex() == 1 and window.edit_mode.isChecked()


def test_drop_in_edit_mode_adds_layer(window, media):
    window.add_files([str(media / "img0.png")])
    window.edit_mode.click()
    window.handle_drop([str(media / "img1.png")])
    assert len(window.editor.doc.layers) == 2 and window.editor.history.can_undo
    assert len(window.files) == 1  # the converter's list is untouched


def test_undo_during_stroke_is_safe(page, qtbot):
    page.layers.add_button.click()
    c = page.canvas
    p = c.image_to_widget(10, 10)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
    c.setFocus()
    qtbot.keyClick(c, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    qtbot.mouseMove(c, p + QPoint(20, 0))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p + QPoint(20, 0))
    assert len(page.doc.layers) == 2


def test_wait_for_save_waits_until_finished(page):
    from PySide6.QtCore import QTimer

    page.saving = True
    QTimer.singleShot(50, page._save_finished)
    page._wait_for_save()
    assert not page.saving


# ------------------------------------------------------------------ layers list


@pytest.fixture
def four(page):
    """The page with four layers: (top) Layer 4, Layer 3, second, first (bottom)."""
    page.layers.add_button.click()
    page.layers.add_button.click()
    assert [la.name for la in page.doc.layers] == ["first", "second", "Layer 3", "Layer 4"]
    return page


def _row_names(pg):
    return [pg.layers.list.item(r).text() for r in range(pg.layers.list.count())]


def _click_row(qtbot, pg, row, modifier=Qt.KeyboardModifier.NoModifier):
    lst = pg.layers.list
    rect = lst.visualItemRect(lst.item(row))
    qtbot.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, modifier, rect.center())


def test_ctrl_and_shift_click_select_several_layers(four, qtbot):
    pg = four
    _click_row(qtbot, pg, 0)
    _click_row(qtbot, pg, 2, Qt.KeyboardModifier.ControlModifier)
    assert pg.doc.selected_indices == [1, 3] and pg.doc.active == 1
    _click_row(qtbot, pg, 1)
    _click_row(qtbot, pg, 3, Qt.KeyboardModifier.ShiftModifier)
    assert pg.doc.selected_indices == [0, 1, 2]
    assert pg.layers.merge_button.text() == "Merge layers"
    _click_row(qtbot, pg, 1)
    assert pg.doc.selected_indices == [2] and pg.layers.merge_button.text() == "Merge down"


def test_ctrl_click_never_leaves_nothing_selected(four, qtbot):
    pg = four
    _click_row(qtbot, pg, 1)
    _click_row(qtbot, pg, 1, Qt.KeyboardModifier.ControlModifier)
    assert pg.doc.selected_indices == [2] and len(pg.layers.list.selectedItems()) == 1


def test_row_arrows_move_layer(four, qtbot):
    from umc.gui.editor.layers_panel import arrow_rects

    pg = four
    lst = pg.layers.list
    up, down = arrow_rects(lst.visualItemRect(lst.item(3)))  # bottom row: "first"
    assert lst.arrow_at(down.center()) is None  # already at the bottom
    qtbot.mouseClick(lst.viewport(), Qt.MouseButton.LeftButton, pos=up.center())
    assert _row_names(pg) == ["Layer 4", "Layer 3", "first", "second"]
    assert pg.doc.active_layer.name == "first" and pg.history.undo_label == "Move layer"
    pg.canvas.undo()
    assert _row_names(pg) == ["Layer 4", "Layer 3", "second", "first"]


def test_drop_moves_selected_layers(four, qtbot):
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QDropEvent

    pg = four
    lst = pg.layers.list
    _click_row(qtbot, pg, 2)
    _click_row(qtbot, pg, 3, Qt.KeyboardModifier.ControlModifier)  # second + first

    class Drop(QDropEvent):
        def source(self):
            return lst

    top = lst.visualItemRect(lst.item(0))
    pos = QPointF(top.center().x(), top.top() + 2)  # upper half of the top row → gap 0
    assert lst.gap_at(pos.toPoint()) == 0
    event = Drop(
        pos,
        Qt.DropAction.MoveAction,
        QMimeData(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    lst.dropEvent(event)
    assert event.dropAction() == Qt.DropAction.IgnoreAction  # Qt must not delete rows itself
    qtbot.waitUntil(lambda: _row_names(pg)[0] != "Layer 4")
    assert _row_names(pg) == ["second", "first", "Layer 4", "Layer 3"]
    assert sorted(lst.row(i) for i in lst.selectedItems()) == [0, 1]
    pg.canvas.undo()
    assert _row_names(pg) == ["Layer 4", "Layer 3", "second", "first"]


def test_context_menu_actions(four, qtbot):
    pg = four
    _click_row(qtbot, pg, 0)
    _click_row(qtbot, pg, 1, Qt.KeyboardModifier.ControlModifier)
    menu = pg.layers.build_menu(0)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert "Rename…" not in actions  # only for one layer
    assert not actions["Move up"].isEnabled()
    actions["Hide 2 layers"].trigger()
    assert [la.visible for la in pg.doc.layers] == [True, True, False, False]
    assert pg.layers.list.item(0).checkState() == Qt.CheckState.Unchecked
    menu = pg.layers.build_menu(0)
    actions = {a.text(): a for a in menu.actions() if a.text()}
    actions["Move to bottom"].trigger()
    assert _row_names(pg) == ["second", "first", "Layer 4", "Layer 3"]
    actions = {a.text(): a for a in pg.layers.build_menu(2).actions() if a.text()}
    actions["Merge 2 layers"].trigger()
    assert _row_names(pg) == ["second", "first", "Layer 4"]


def test_opacity_slider_changes_all_selected(four, qtbot):
    pg = four
    _click_row(qtbot, pg, 0)
    _click_row(qtbot, pg, 3, Qt.KeyboardModifier.ControlModifier)
    pg.layers.opacity.setValue(40)
    assert [la.opacity for la in pg.doc.layers] == [0.4, 1.0, 1.0, 0.4]
    pg.canvas.undo()
    assert all(la.opacity == 1.0 for la in pg.doc.layers)
