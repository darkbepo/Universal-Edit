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


def _accept_window(page, key, fill=None):
    """Open the floating window for ``key``, let ``fill`` set it up, and press its OK."""
    page.run_op(key)
    dlg = page.panels.panels[key].content
    if fill is not None:
        fill(dlg)
    dlg.accept()
    assert key not in page.panels.panels  # OK closes it


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
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")  # rail width etc. never touch real settings
    pg = EditorPage()
    qtbot.addWidget(pg)
    pg.resize(1300, 720)
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


def test_quick_fix_preview_cancel_restores(page, qtbot):
    from umc.gui.editor.dialogs import QuickFixDialog

    before = page.doc.active_layer.image.tobytes()
    dlg = QuickFixDialog(page)
    dlg.sliders["brightness"].set_value(60)
    dlg.update_preview()
    assert page.canvas._preview_layer is not None
    dlg.original_button.pressed.emit()  # "hold to see the original"
    assert page.canvas._preview_layer is None
    dlg.original_button.released.emit()
    assert page.canvas._preview_layer is not None
    dlg.reject()
    assert page.canvas._preview_layer is None
    assert page.doc.active_layer.image.tobytes() == before and not page.history.can_undo


def test_quick_fix_apply_is_one_undo_step(page, qtbot, monkeypatch):

    def ok(self):
        self.sliders["exposure"].set_value(1.0)
        self.sliders["saturation"].set_value(-100)
        self.auto.setChecked(True)
        return 1

    _accept_window(page, "quick_fix", ok)
    assert page.history.undo_label == "Quick fix"
    r, g, b, _ = page.doc.active_layer.image.getpixel((5, 5))
    assert r == g == b  # saturation −100 → grey
    page.canvas.undo()
    assert not page.history.can_undo


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


def test_edit_disabled_for_non_images(window, media):
    assert window.mode_bar.isVisible() and not window.edit_mode.isEnabled()
    window.add_files([str(media / "doc.md")])
    assert not window.edit_mode.isEnabled()
    window.clear_files()
    assert not window.edit_mode.isEnabled()


def test_edit_enabled_for_images(window, media):
    window.add_files([str(media / "img0.png"), str(media / "img1.png")])
    assert window.edit_mode.isEnabled() and window.convert_mode.isChecked()
    window.add_files([str(media / "doc.md")])  # mixed → no editing
    assert not window.edit_mode.isEnabled()


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


# ------------------------------------------------------------------ crop / selection bar


def test_place_bar_beside_the_release_corner():
    from umc.gui.editor.crop_bar import GAP, MARGIN, nearest_corner, place_bar

    rect = (100, 100, 300, 200)
    assert nearest_corner(rect, 290, 195) == (True, True)
    assert nearest_corner(rect, 105, 110) == (False, False)
    # bottom-right corner: below, right edges aligned
    assert place_bar(rect, (True, True), (120, 30), (800, 600)) == (180, 200 + GAP)
    # top-left corner: above, left edges aligned
    assert place_bar(rect, (False, False), (120, 30), (800, 600)) == (100, 100 - GAP - 30)
    # no room below → above
    assert place_bar(rect, (True, True), (120, 30), (800, 230)) == (180, 100 - GAP - 30)
    # no room above or below → inside the rectangle
    assert place_bar((0, 0, 300, 200), (True, True), (120, 30), (300, 200)) == (
        300 - 120 - MARGIN,
        200 - GAP - 30,
    )
    # never outside the canvas
    x, y = place_bar((-50, 10, 20, 40), (False, True), (120, 30), (800, 600))
    assert x == MARGIN and y == 40 + GAP


def _drag_rect(qtbot, pg, a, b):
    c = pg.canvas
    pa, pb = c.image_to_widget(*a), c.image_to_widget(*b)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=pa)
    qtbot.mouseMove(c, pb)
    c.repaint()
    assert not pg.crop_bar.isVisible()  # hidden while dragging
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=pb)
    c.repaint()


def test_crop_bar_appears_after_drag_and_applies(page, qtbot):
    page.select_tool("crop")
    page.canvas.repaint()
    assert not page.crop_bar.isVisible() and "Drag a rectangle" in page.message.text()
    _drag_rect(qtbot, page, (0, 0), (20, 10))
    bar = page.crop_bar
    assert bar.isVisible() and bar.size_label.text() == "20 × 10 px"
    assert bar.apply_button.text() == "Apply crop"
    corner = page.canvas.image_to_widget(20, 10)
    assert bar.y() >= corner.y() and abs(bar.geometry().right() - corner.x()) <= 2
    qtbot.mouseClick(bar.apply_button, Qt.MouseButton.LeftButton)
    page.canvas.repaint()
    assert (page.doc.width, page.doc.height) == (20, 10) and not bar.isVisible()
    page.select_tool("brush")
    assert "Drag a rectangle" not in page.message.text()


def test_crop_bar_cancel_and_escape(page, qtbot):
    page.select_tool("crop")
    _drag_rect(qtbot, page, (5, 5), (30, 25))
    qtbot.mouseClick(page.crop_bar.cancel_button, Qt.MouseButton.LeftButton)
    page.canvas.repaint()
    assert not page.crop_bar.isVisible() and page.tool.pending is None
    _drag_rect(qtbot, page, (5, 5), (30, 25))
    assert page.crop_bar.isVisible()
    qtbot.keyClick(page.canvas, Qt.Key.Key_Escape)
    page.canvas.repaint()
    assert not page.crop_bar.isVisible() and (page.doc.width, page.doc.height) == (60, 40)


def test_selection_bar_crops_or_deselects(page, qtbot):
    page.select_tool("select")
    _drag_rect(qtbot, page, (30, 20), (10, 5))  # released at the top-left corner
    bar = page.crop_bar
    assert bar.isVisible() and bar.apply_button.text() == "Crop to selection"
    assert bar.geometry().bottom() <= page.canvas.image_to_widget(10, 5).y()  # above it
    qtbot.mouseClick(bar.cancel_button, Qt.MouseButton.LeftButton)
    page.canvas.repaint()
    assert page.doc.selection is None and not bar.isVisible()
    _drag_rect(qtbot, page, (10, 5), (30, 20))
    qtbot.mouseClick(bar.apply_button, Qt.MouseButton.LeftButton)
    assert (page.doc.width, page.doc.height) == (20, 15)
    page.canvas.undo()
    assert (page.doc.width, page.doc.height) == (60, 40)


# ------------------------------------------------------------------ clipboard, Move tool, shortcuts

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier


def _names(pg):
    return [la.name for la in pg.doc.layers]


def test_ctrl_c_ctrl_v_pastes_in_place_through_the_system_clipboard(page, qtbot):
    from PySide6.QtWidgets import QApplication

    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_C, CTRL)
    assert not QApplication.clipboard().image().isNull()
    assert "Copied 30 × 30 px" in page.message.text()
    qtbot.keyClick(page.canvas, Qt.Key.Key_V, CTRL)
    assert _names(page) == ["first", "second", "Pasted 3"]
    pasted = page.doc.active_layer
    assert (pasted.x, pasted.y, pasted.image.size) == (0, 0, (30, 30))
    assert pasted.image.getpixel((5, 5)) == page.doc.layers[1].image.getpixel((5, 5))
    assert page.layers.list.item(0).text() == "Pasted 3"
    page.canvas.undo()
    assert _names(page) == ["first", "second"]


def test_paste_from_another_program_is_centred_in_view(page, qtbot):
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    img = QImage(10, 6, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.green)
    QApplication.clipboard().setImage(img)
    page.paste()
    layer = page.doc.active_layer
    cx, cy = page.view_center()
    assert (layer.x, layer.y) == (round(cx - 5), round(cy - 3))
    assert layer.image.getpixel((0, 0)) == (0, 255, 0, 255)


def test_paste_without_a_picture_says_so(page):
    from PySide6.QtWidgets import QApplication

    QApplication.clipboard().setText("just text")
    page.paste()
    assert len(page.doc.layers) == 2 and "no picture" in page.message.text()


def test_ctrl_x_cuts_the_selection(page, qtbot):
    from umc.editor.geometry import Rect

    page.doc.selection = Rect(0, 0, 10, 10)
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_X, CTRL)
    top = page.doc.layers[1]
    assert top.image.getpixel((5, 5))[3] == 0 and top.image.getpixel((15, 15))[3] > 0
    qtbot.keyClick(page.canvas, Qt.Key.Key_V, CTRL)
    assert (page.doc.active_layer.x, page.doc.active_layer.image.size) == (0, (10, 10))


def test_move_tool_drag_and_arrow_nudge(page, qtbot):
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_V)
    assert page.tool.id == "move"
    c = page.canvas
    a, b = c.image_to_widget(5, 5), c.image_to_widget(15, 9)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(c, b)
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=b)
    top = page.doc.layers[1]
    assert (top.x, top.y) == (10, 4) and page.history.undo_label == "Move"
    qtbot.keyClick(c, Qt.Key.Key_Left)
    qtbot.keyClick(c, Qt.Key.Key_Down, SHIFT)
    assert (page.doc.layers[1].x, page.doc.layers[1].y) == (9, 14)
    for _ in range(3):
        page.canvas.undo()
    assert (page.doc.layers[1].x, page.doc.layers[1].y) == (0, 0)


def test_layer_shortcuts(page, qtbot):
    c = page.canvas
    c.setFocus()
    qtbot.keyClick(c, Qt.Key.Key_N, CTRL | SHIFT)
    assert _names(page) == ["first", "second", "Layer 3"]
    qtbot.keyClick(c, Qt.Key.Key_J, CTRL)
    assert _names(page) == ["first", "second", "Layer 3", "Layer 3 copy"]
    qtbot.keyClick(c, Qt.Key.Key_BracketLeft, CTRL)
    assert _names(page)[2] == "Layer 3 copy"
    qtbot.keyClick(c, Qt.Key.Key_BracketRight, CTRL | SHIFT)  # to the top
    assert _names(page)[-1] == "Layer 3 copy"
    qtbot.keyClick(c, Qt.Key.Key_E, CTRL)  # merge down
    assert _names(page) == ["first", "second", "Layer 3"]
    qtbot.keyClick(c, Qt.Key.Key_A, CTRL | Qt.KeyboardModifier.AltModifier)
    assert page.doc.selected_indices == [0, 1, 2]
    qtbot.keyClick(c, Qt.Key.Key_E, CTRL)  # merge all selected
    assert len(page.doc.layers) == 1


def test_delete_in_layers_list_deletes_layers_and_on_canvas_clears(page, qtbot):
    from umc.editor.geometry import Rect

    page.doc.selection = Rect(0, 0, 4, 4)
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_Delete)
    assert len(page.doc.layers) == 2 and page.doc.layers[1].image.getpixel((1, 1))[3] == 0
    page.layers.list.setFocus()
    qtbot.keyClick(page.layers.list, Qt.Key.Key_Delete)
    assert _names(page) == ["first"]


def test_zoom_shortcuts(page, qtbot):
    page.canvas.set_zoom(1.0)
    qtbot.keyClick(page.canvas, Qt.Key.Key_Equal, CTRL)
    assert page.canvas.zoom > 1.0
    qtbot.keyClick(page.canvas, Qt.Key.Key_Minus, CTRL)
    qtbot.keyClick(page.canvas, Qt.Key.Key_Minus, CTRL)
    assert page.canvas.zoom < 1.0


def test_shortcut_table_has_no_clashes_and_help_lists_everything(page, qtbot, monkeypatch):
    from PySide6.QtGui import QKeySequence

    from umc.editor.tools import TOOLS
    from umc.gui.editor import dialogs
    from umc.gui.editor.shortcuts import SHORTCUTS

    keys = [QKeySequence(k).toString() for s in SHORTCUTS for k in s.keys]
    keys += sorted({t.shortcut for t in TOOLS if t.shortcut})  # a shared letter cycles its tools
    assert len(keys) == len(set(keys)), "two actions share a key"
    rows = dialogs.shortcut_rows(TOOLS)
    labels = {label for group in rows.values() for _, label in group}
    assert {s.label for s in SHORTCUTS} <= labels and "Move" in labels
    tool_keys = [k for k, _ in rows["Tools"]]
    assert tool_keys.count("U") == 1
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_F1)
    assert isinstance(page.panels.panels["shortcuts"].content, dialogs.ShortcutsDialog)


def test_ctrl_alt_shortcuts_leave_text_fields_alone(page, qtbot):
    """AltGr is Ctrl+Alt on Windows: Ctrl+Alt+A must not fire while typing (e.g. "ą")."""
    page.select_tool("text")
    edit = page.tool_settings.font_box.lineEdit()
    edit.setFocus()
    qtbot.keyClick(edit, Qt.Key.Key_A, CTRL | Qt.KeyboardModifier.AltModifier)
    assert page.doc.selected_indices == [1]
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_A, CTRL | Qt.KeyboardModifier.AltModifier)
    assert page.doc.selected_indices == [0, 1]
    from umc.editor import ops

    ops.select_layer(page.doc, 1)
    page.layers.refresh(page.doc)
    page.layers.list.setFocus()
    qtbot.keyClick(page.layers.list, Qt.Key.Key_A, CTRL | Qt.KeyboardModifier.AltModifier)
    assert page.doc.selected_indices == [0, 1]


# ------------------------------------------------------------------ levels / curves windows


def test_levels_dialog_fields_markers_and_channels(page, qtbot):
    from umc.gui.editor.dialogs import LevelsDialog

    dlg = LevelsDialog(page)
    qtbot.addWidget(dlg)
    dlg.show()
    assert dlg.hist["rgb"].sum() > 0
    dlg.black.setValue(40)
    assert dlg.adjustments().levels["rgb"].black == 40
    # drag the white marker on the histogram
    hist = dlg.histogram
    white_x = int(hist._x(255))
    y = hist.height() - 6
    qtbot.mousePress(hist, Qt.MouseButton.LeftButton, pos=QPoint(white_x, y))
    qtbot.mouseMove(hist, QPoint(int(hist._x(200)), y))
    qtbot.mouseRelease(hist, Qt.MouseButton.LeftButton, pos=QPoint(int(hist._x(200)), y))
    assert abs(dlg.white.value() - 200) <= 2
    # red channel is separate
    dlg.channel.setCurrentIndex(1)
    assert dlg.black.value() == 0
    dlg.gamma.setValue(2.0)
    adj = dlg.adjustments()
    assert set(adj.levels) == {"rgb", "r"} and adj.levels["r"].gamma == 2.0
    dlg.reset()
    assert dlg.adjustments().identity


def test_levels_auto_and_apply(page, monkeypatch):

    def ok(self):
        self.auto_button.click()
        return 1

    _accept_window(page, "levels", ok)
    # one flat colour: nothing to stretch → no change, no undo step
    assert not page.history.can_undo


def test_curves_editor_add_move_remove(page, qtbot):
    from umc.gui.editor.dialogs import CurvesDialog

    dlg = CurvesDialog(page)
    qtbot.addWidget(dlg)
    dlg.resize(400, 480)
    dlg.show()
    ed = dlg.editor
    at = ed.to_widget(128, 128).toPoint()
    qtbot.mousePress(ed, Qt.MouseButton.LeftButton, pos=at)
    up = ed.to_widget(128, 180).toPoint()
    qtbot.mouseMove(ed, up)
    qtbot.mouseRelease(ed, Qt.MouseButton.LeftButton, pos=up)
    pts = dlg.points()
    assert len(pts) == 3 and pts[0] == (0, 0) and pts[-1] == (255, 255)
    assert abs(pts[1][0] - 128) <= 2 and abs(pts[1][1] - 180) <= 2
    assert not dlg.adjustments().identity
    # drag it far outside the box → removed
    qtbot.mousePress(ed, Qt.MouseButton.LeftButton, pos=up)
    qtbot.mouseMove(ed, QPoint(up.x(), ed.height() + 80))
    qtbot.mouseRelease(ed, Qt.MouseButton.LeftButton, pos=QPoint(up.x(), ed.height() + 80))
    assert dlg.points() == [(0, 0), (255, 255)]
    # the two end points can't be removed
    end = ed.to_widget(255, 255).toPoint()
    qtbot.mousePress(ed, Qt.MouseButton.LeftButton, pos=end)
    qtbot.mouseMove(ed, QPoint(-200, -200))
    qtbot.mouseRelease(ed, Qt.MouseButton.LeftButton, pos=QPoint(-200, -200))
    assert len(dlg.points()) == 2


def test_curves_apply_to_selection(page, monkeypatch):
    from umc.editor.geometry import Rect

    page.doc.selection = Rect(0, 0, 10, 10)

    def ok(self):
        self.channel.setCurrentIndex(1)  # red: lift the darks (0 → 100)
        self.add_point(0, 100)
        return 1

    before = page.doc.active_layer.image.getpixel((20, 20))
    _accept_window(page, "curves", ok)
    assert page.history.undo_label == "Curves"
    assert page.doc.active_layer.image.getpixel((20, 20)) == before  # outside the selection
    assert before[0] == 0 and page.doc.active_layer.image.getpixel((5, 5))[0] == 100


def test_curve_end_points_stay_and_clicks_beside_the_graph_do_nothing(page, qtbot):
    from umc.gui.editor.dialogs import CurvesDialog

    dlg = CurvesDialog(page)
    qtbot.addWidget(dlg)
    dlg.resize(560, 480)  # wider than the square graph
    dlg.show()
    ed = dlg.editor
    dlg.add_point(128, 160)
    end = ed.to_widget(255, 255).toPoint()
    far = QPoint(ed.width() + 200, -200)
    qtbot.mousePress(ed, Qt.MouseButton.LeftButton, pos=end)
    qtbot.mouseMove(ed, far)
    qtbot.mouseRelease(ed, Qt.MouseButton.LeftButton, pos=far)
    assert len(dlg.points()) == 3 and dlg.points()[-1][0] == 255  # end point kept
    pts = list(dlg.points())
    beside = QPoint(ed.to_widget(255, 0).toPoint().x() + 30, ed.to_widget(0, 128).toPoint().y())
    assert beside.x() < ed.width()
    qtbot.mouseClick(ed, Qt.MouseButton.LeftButton, pos=beside)
    qtbot.mouseClick(ed, Qt.MouseButton.RightButton, pos=ed.to_widget(64, 64).toPoint())
    assert dlg.points() == pts


def test_levels_fields_show_what_is_applied_and_auto_uses_the_channel(page, qtbot):
    from umc.gui.editor.dialogs import LevelsDialog

    dlg = LevelsDialog(page)
    qtbot.addWidget(dlg)
    dlg.white.setValue(100)
    dlg.black.setValue(200)
    assert dlg.white.value() == 201 and dlg.adjustments().levels["rgb"].white == 201
    dlg.reset()
    dlg.channel.setCurrentIndex(3)  # blue
    dlg.out_black.setValue(20)
    dlg._image = Image.new("RGBA", (10, 10), (0, 0, 40, 255))
    dlg._image.paste((0, 0, 200, 255), (0, 0, 10, 5))
    dlg.auto_button.click()
    blue = dlg.adjustments().levels["b"]
    assert (blue.black, blue.white, blue.out_black) == (40, 200, 20)


# ------------------------------------------------------------------ filters menu


def test_blur_and_sharpen_dialogs_preview_and_apply(page, qtbot, monkeypatch):
    from umc.gui.editor.dialogs import BlurDialog

    dlg = BlurDialog(page)
    qtbot.addWidget(dlg)
    dlg.update_preview()  # starts at 2 px
    assert page.canvas._preview_layer is not None and not dlg.effect().identity
    dlg.reset()
    assert dlg.effect().identity
    dlg.reject()
    assert page.canvas._preview_layer is None and not page.history.can_undo

    def ok(self):
        self.radius.set_value(3)
        return 1

    _accept_window(page, "blur", ok)
    assert page.history.undo_label == "Blur"
    _accept_window(page, "sharpen")
    assert page.history.undo_label == "Sharpen"


def test_looks_dialog_tiles_choose_and_strength(page, qtbot, monkeypatch):
    from umc.editor import filters
    from umc.gui.editor.dialogs import LooksDialog

    dlg = LooksDialog(page)
    qtbot.addWidget(dlg)
    assert set(dlg.tiles) == set(filters.LOOKS)
    assert all(not b.icon().isNull() for b in dlg.tiles.values())
    assert dlg.effect().identity  # nothing chosen yet
    qtbot.mouseClick(dlg.tiles["bw"], Qt.MouseButton.LeftButton)
    assert dlg.chosen() == "bw" and dlg.effect() == filters.Look("bw", 1.0)
    dlg.strength.set_value(40)
    assert dlg.effect().strength == 0.4
    dlg.reset()
    assert dlg.chosen() == ""

    def ok(self):
        self.choose("bw")
        return 1

    _accept_window(page, "looks", ok)
    r, g, b, _ = page.doc.active_layer.image.getpixel((5, 5))
    assert r == g == b and page.history.undo_label == "Look"


# ------------------------------------------------------------------ selection tools


def _canvas_drag(qtbot, pg, a, b, modifier=Qt.KeyboardModifier.NoModifier):
    c = pg.canvas
    pa, pb = c.image_to_widget(*a), c.image_to_widget(*b)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, modifier, pa)
    qtbot.mouseMove(c, pb)
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, modifier, pb)


def test_tool_letters_cycle_shared_tools(page, qtbot):
    c = page.canvas
    c.setFocus()
    for key, ids in (
        (Qt.Key.Key_M, ("select", "ellipse_select", "select")),
        (Qt.Key.Key_Q, ("lasso", "polygon_lasso", "lasso")),
        (Qt.Key.Key_U, ("rect", "ellipse", "rect")),
    ):
        for want in ids:
            qtbot.keyClick(c, key)
            assert page.tool.id == want
    qtbot.keyClick(c, Qt.Key.Key_W)
    assert page.tool.id == "wand"


def test_shift_and_alt_drags_combine_selections(page, qtbot):
    import numpy as np

    from umc.editor import selection

    page.select_tool("select")
    _canvas_drag(qtbot, page, (0, 0), (10, 10))
    _canvas_drag(qtbot, page, (20, 0), (30, 10), Qt.KeyboardModifier.ShiftModifier)
    m = np.asarray(selection.full_mask(page.doc)) > 127
    assert m[5, 5] and m[5, 25] and not m[5, 15]
    page.select_tool("ellipse_select")
    _canvas_drag(qtbot, page, (0, 0), (30, 10), Qt.KeyboardModifier.AltModifier)
    m = np.asarray(selection.full_mask(page.doc)) > 127
    assert not m[5, 5] and m[0, 0]  # the middle of the ellipse was taken away
    page.canvas.repaint()  # the shaped marching ants paint without errors
    assert page.crop_bar.isVisible() and page.crop_bar.apply_button.text() == "Crop to selection"


def test_polygon_lasso_double_click_and_enter_finish(page, qtbot):
    c = page.canvas
    page.select_tool("polygon_lasso")
    for p in ((2, 2), (40, 2), (20, 30)):
        qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(*p))
    assert page.doc.selection is None and len(page.tool.points) == 3
    c.repaint()
    qtbot.mouseDClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(20, 30))
    assert page.doc.selection is not None and page.tool.points == []
    page.canvas.undo()
    for p in ((2, 2), (40, 2), (20, 30)):
        qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(*p))
    c.setFocus()
    qtbot.keyClick(c, Qt.Key.Key_Return)
    assert page.doc.selection is not None


def test_select_menu_invert_and_feather(page, qtbot, monkeypatch):
    from umc.editor.geometry import Rect

    page.select_tool("select")
    _canvas_drag(qtbot, page, (0, 0), (20, 40))
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_I, CTRL | SHIFT)
    assert page.doc.selection == Rect(20, 0, 60, 40)
    qtbot.keyClick(page.canvas, Qt.Key.Key_F6, SHIFT)
    page.panels.panels["feather"].content.accept()
    assert page.doc.selection_mask is not None and page.history.undo_label == "Feather selection"


# ------------------------------------------------------------- masks, gradient, background blur


def test_layer_mask_menu_thumbnail_and_mask_brush(page, qtbot):
    from umc.gui.editor.layers_panel import MASK_ROLE

    page.layers.list.setCurrentRow(0)
    actions = {a.text(): a for a in page.layers.build_menu(0).actions() if a.text()}
    actions["Add layer mask"].trigger()
    assert page.doc.active_layer.mask is not None
    assert page.layers.list.item(0).data(MASK_ROLE) is not None
    page.layers.list.repaint()
    actions = {a.text(): a for a in page.layers.build_menu(0).actions() if a.text()}
    assert {"Invert mask", "Apply mask (bake it in)", "Delete mask"} <= set(actions)
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_K)
    assert page.tool.id == "mask_brush"
    c = page.canvas
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    assert (
        page.doc.active_layer.mask.getpixel((5, 5)) < 128
        and page.history.undo_label == "Mask brush"
    )
    actions = {a.text(): a for a in page.layers.build_menu(0).actions() if a.text()}
    actions["Delete mask"].trigger()
    assert page.doc.active_layer.mask is None


def test_g_cycles_fill_and_gradient_and_gradient_paints(page, qtbot):
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_G)
    assert page.tool.id == "fill"
    qtbot.keyClick(page.canvas, Qt.Key.Key_G)
    assert page.tool.id == "gradient"
    c = page.canvas
    a, b = c.image_to_widget(0, 10), c.image_to_widget(29, 10)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(c, b)
    c.repaint()  # the gradient line is drawn while dragging
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=b)
    assert page.history.undo_label == "Gradient"


def test_background_blur_needs_a_selection_then_applies(page, monkeypatch):
    from umc.editor.geometry import Rect

    page.run_op("background_blur")
    assert "Select the subject first" in page.message.text() and not page.history.can_undo
    page.doc.selection = Rect(0, 0, 10, 10)
    _accept_window(page, "background_blur")
    assert page.history.undo_label == "Background blur"
    assert page.doc.selection == Rect(0, 0, 10, 10)


# ------------------------------------------------------------------ text v2


def test_text_with_styles_then_click_it_to_edit(page, qtbot):
    page.select_tool("text")
    page.tool_settings.set_values(page.tools["text"], {"box": True, "outline": 2, "size": 20})
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("Hi")
    page.commit_text()
    layer = page.doc.active_layer
    assert layer.text is not None and layer.text.box and layer.text.outline == 2
    n = len(page.doc.layers)
    # clicking inside the text layer opens it again, prefilled
    bx, by = layer.x + layer.image.width // 2, layer.y + layer.image.height // 2
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(bx, by))
    assert page.text_box is not None and page.text_box.toPlainText() == "Hi"
    page.text_box.setPlainText("Hi there")
    page.commit_text()
    assert len(page.doc.layers) == n and page.doc.active_layer.text.text == "Hi there"
    assert page.history.undo_label == "Edit text"


def test_layer_menu_edit_text(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("Menu")
    page.commit_text()
    page.select_tool("brush")
    actions = {a.text(): a for a in page.layers.build_menu(0).actions() if a.text()}
    actions["Edit text…"].trigger()
    assert page.tool.id == "text" and page.text_box.toPlainText() == "Menu"
    label = page.history.undo_label
    page.commit_text()
    assert page.history.undo_label == label  # unchanged text: no extra undo step


def test_re_editing_boxed_text_keeps_the_outline_colour(page, qtbot):
    from dataclasses import replace

    from umc.editor.tools.text import edit_text_layer

    page.select_tool("text")
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("Hi")
    page.commit_text()
    i = page.doc.active
    spec = replace(
        page.doc.layers[i].text,
        box=True,
        outline=2,
        outline_color=(255, 0, 0, 255),
        box_color=(0, 0, 255, 255),
    )
    page._push(edit_text_layer(page.doc, i, spec))
    page.edit_text(i)
    page.text_box.setPlainText("Hi there")
    page.commit_text()
    t = page.doc.layers[i].text
    assert t.text == "Hi there" and t.outline_color == (255, 0, 0, 255)
    assert t.box_color == (0, 0, 255, 255)


def test_stickers_dialog_adds_a_layer(page, qtbot, monkeypatch):
    from umc.editor.stickers import STICKERS
    from umc.gui.editor.dialogs import StickersDialog

    dlg = StickersDialog(page)
    qtbot.addWidget(dlg)
    assert set(dlg.tiles) == set(STICKERS) and not dlg.ok.isEnabled()
    qtbot.mouseClick(dlg.tiles["heart"], Qt.MouseButton.LeftButton)
    assert dlg.chosen() == "heart" and dlg.ok.isEnabled()

    def ok(self):
        self.tiles["star"].setChecked(True)
        self.size.setValue(30)
        return 1

    n = len(page.doc.layers)
    _accept_window(page, "stickers", ok)
    assert len(page.doc.layers) == n + 1
    assert page.doc.active_layer.image.size == (30, 30)


def test_canvas_shows_layers_on_the_pasteboard(canvas):
    from PIL import Image

    from umc.editor.layer import Layer

    c, doc, _ = canvas
    doc.layers.append(Layer("off", Image.new("RGBA", (30, 30), (0, 0, 255, 255)), x=90, y=-20))
    c.refresh()
    assert c.cached_pixel(110, -10) == (0, 0, 255, 255)
    assert c.cached_pixel(50, 50)[3] == 0
    top_left = c.image_to_widget(0, -20)
    assert top_left.x() >= 0 and top_left.y() >= 0  # moved into view
    c.grab()  # paints without errors


def test_clone_stamp_and_content_fill_in_the_page(page, qtbot):
    from umc.editor.geometry import Rect

    page.select_tool("clone")
    msgs = []
    page.canvas.tool_notice.connect(msgs.append)
    p = page.canvas.image_to_widget(5, 5)
    qtbot.mousePress(page.canvas, Qt.MouseButton.LeftButton, pos=p)
    qtbot.mouseRelease(page.canvas, Qt.MouseButton.LeftButton, pos=p)
    assert msgs and "Alt+click" in msgs[0]
    page.doc.selection = Rect(4, 4, 8, 8)
    page.run_op("content_fill")
    assert page.history.undo_label == "Content-aware fill"


def test_remove_background_sets_a_layer_mask(page, qtbot, monkeypatch):
    from PIL import Image

    from umc.tools import bgremove

    monkeypatch.setattr(bgremove, "model_ready", lambda: True)
    monkeypatch.setattr(
        bgremove, "subject_mask", lambda img, model=None: Image.new("L", img.size, 128)
    )
    with qtbot.waitSignal(page.bg_done, timeout=5000):
        page.run_op("remove_background")
    qtbot.waitUntil(lambda: page.doc.active_layer.mask is not None)
    assert page.doc.active_layer.mask.getpixel((0, 0)) == 128
    assert page.history.undo_label == "Remove background"


def test_colour_picker_shows_a_loupe_while_held(canvas, qtbot):
    from umc.editor.tools.fill import PickerOptions, PickerTool

    c, doc, _ = canvas
    c.resize(400, 400)
    c.set_zoom(1.0)
    c.set_tool(PickerTool())
    c.context_provider = lambda: ToolContext((0, 0, 0, 255), (255, 255, 255, 255), PickerOptions())
    p = c.image_to_widget(90, 90)
    middle = QPoint(p.x() - 24 - 61, p.y() - 24 - 61)  # the loupe sits up and to the left
    before = c.grab().toImage().pixelColor(middle)
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, pos=p)
    qtbot.mouseMove(c, p)
    assert c.grab().toImage().pixelColor(middle) != before
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, pos=p)
    assert c.grab().toImage().pixelColor(middle) == before  # gone again


def test_arrows_move_the_selection_with_a_selection_tool(page, qtbot):
    from umc.editor.geometry import Rect

    page.select_tool("select")
    page.doc.selection = Rect(2, 2, 10, 10)
    page.canvas.setFocus()
    qtbot.keyClick(page.canvas, Qt.Key.Key_Right)
    qtbot.keyClick(page.canvas, Qt.Key.Key_Down, SHIFT)
    assert page.doc.selection == Rect(3, 12, 11, 20)
    assert page.history.undo_label == "Move selection"


def test_tool_rail_groups_names_narrowing_and_actions(page, qtbot, monkeypatch):
    from PySide6.QtWidgets import QToolButton

    from umc.editor.geometry import Rect

    rail = page.tool_bar
    b = rail.buttons["select"]
    assert b.text() == "Rectangle select" and not rail.narrow
    assert b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon
    qtbot.mouseClick(rail.buttons["lasso"], Qt.MouseButton.LeftButton)
    assert page.tool.id == "lasso" and rail.current() == "lasso"
    qtbot.mouseClick(rail.toggle, Qt.MouseButton.LeftButton)
    assert rail.narrow and b.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    rail.set_narrow(False)
    # actions run at once; the Select group's ones too
    qtbot.mouseClick(rail.buttons["select_all"], Qt.MouseButton.LeftButton)
    assert page.doc.selection == Rect(0, 0, 60, 40)
    qtbot.mouseClick(rail.buttons["deselect"], Qt.MouseButton.LeftButton)
    assert page.doc.selection is None
    menu = rail.buttons["rotate"].menu()
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Rotate 90° right" in texts and "Layer: flip vertical" in texts
    next(a for a in menu.actions() if a.text() == "Rotate 90° right").trigger()
    assert (page.doc.width, page.doc.height) == (40, 60)
    # the old top-bar menus are gone
    assert not [w for w in page.findChildren(QToolButton) if w.text().endswith("▾")]


def test_the_rail_never_takes_focus_from_the_canvas(page, qtbot):
    page.canvas.setFocus()
    rail = page.tool_bar
    qtbot.mouseClick(rail.buttons["move"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(rail._labels[0], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(rail.viewport(), Qt.MouseButton.LeftButton)
    assert page.tool.id == "move" and page.focusWidget() is page.canvas


# ------------------------------------------------------------------ floating windows


def test_rail_icons_open_minimise_and_bring_back_windows(page, qtbot):
    from PySide6.QtCore import QPoint

    rail, host = page.tool_bar, page.panels
    qtbot.mouseClick(rail.buttons["quick_fix"], Qt.MouseButton.LeftButton)
    qtbot.mouseClick(rail.buttons["blur"], Qt.MouseButton.LeftButton)
    assert host.state("quick_fix") == "open" and host.state("blur") == "open"  # several at once
    assert rail.buttons["blur"].property("panel") == "open"
    qtbot.mouseClick(rail.buttons["blur"], Qt.MouseButton.LeftButton)  # again: minimise
    assert host.state("blur") == "minimised"
    assert rail.buttons["blur"].property("panel") == "minimised"
    qtbot.mouseClick(rail.buttons["blur"], Qt.MouseButton.LeftButton)  # and back
    assert host.state("blur") == "open"
    panel = host.panels["blur"]
    panel.move_to(QPoint(120, 90))
    panel.remember()
    host.close("blur")
    assert host.state("blur") == "closed" and rail.buttons["blur"].property("panel") == "closed"
    qtbot.mouseClick(rail.buttons["blur"], Qt.MouseButton.LeftButton)
    assert host.panels["blur"].pos() == QPoint(120, 90)  # where it was left
    host.panels["blur"].move_to(QPoint(-500, 99999))
    p = host.panels["blur"].pos()
    assert p.x() >= 0 and p.y() < page.canvas.height()  # kept inside the editor


def test_tool_options_float_and_follow_the_tool(page, qtbot):
    host, rail = page.panels, page.tool_bar
    tool_panel = host.panels["tool"]
    assert tool_panel.isVisible() and page.tool_settings.window() is page.window()
    assert page.tool_settings.parentWidget() is not None
    assert tool_panel.title_bar.label.text() == "Brush"
    qtbot.mouseClick(rail.buttons["eraser"], Qt.MouseButton.LeftButton)
    assert tool_panel.title_bar.label.text() == "Eraser"
    qtbot.mouseClick(rail.buttons["eraser"], Qt.MouseButton.LeftButton)  # the chosen tool again
    assert host.state("tool") == "minimised"
    qtbot.mouseClick(rail.buttons["brush"], Qt.MouseButton.LeftButton)  # another tool: back
    assert host.state("tool") == "open" and tool_panel.title_bar.label.text() == "Brush"
    host.close("tool")  # can't be closed, only minimised
    assert host.panels["tool"] is tool_panel and host.state("tool") == "minimised"
    host.close_all()
    assert host.panels["tool"] is tool_panel


def test_a_minimised_adjustment_hides_its_preview_and_closing_cancels(page, qtbot):
    page.run_op("quick_fix")
    dlg = page.panels.panels["quick_fix"].content
    dlg.sliders["exposure"].set_value(2.0)
    dlg.update_preview()
    assert page.canvas._preview_layer is not None
    page.panels.minimise("quick_fix")
    assert page.canvas._preview_layer is None
    page.panels.restore("quick_fix")
    dlg.update_preview()
    assert page.canvas._preview_layer is not None
    page.panels.close("quick_fix")  # ×: like Cancel
    assert page.canvas._preview_layer is None and not page.history.can_undo


def test_keys_typed_into_a_window_belong_to_it(page, qtbot):
    page.run_op("resize")
    dlg = page.panels.panels["resize"].content
    dlg.width.setValue(30)
    dlg.width.setFocus()
    qtbot.keyClick(dlg.width.lineEdit(), Qt.Key.Key_E)  # not the eraser shortcut
    assert page.tool.id == "brush"
    qtbot.keyClick(dlg.width.lineEdit(), Qt.Key.Key_Return)  # Enter = OK, not "apply crop"
    assert (page.doc.width, page.doc.height) == (30, 20) and "resize" not in page.panels.panels


def test_size_windows_close_when_the_picture_changes_size(page):
    from umc.editor.geometry import Rect

    page.run_op("resize")
    page.run_op("canvas_size")
    page.doc.selection = Rect(0, 0, 30, 20)
    page.run_op("crop_selection")
    assert "resize" not in page.panels.panels and "canvas_size" not in page.panels.panels


def test_a_window_minimised_before_its_preview_shows_stays_parked(page, qtbot):
    page.run_op("quick_fix")
    dlg = page.panels.panels["quick_fix"].content
    dlg.sliders["exposure"].set_value(2.0)  # the preview waits 60 ms…
    page.panels.minimise("quick_fix")  # …but the window is tucked away first
    qtbot.wait(150)
    assert page.canvas._preview_layer is None and page._preview_owner is not dlg


# ------------------------------------------------------------------ live text layers


def test_text_is_a_layer_while_it_is_typed_and_one_undo_step(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    n = len(page.doc.layers)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    assert len(page.doc.layers) == n + 1  # a layer at once, before anything is typed
    assert page.layers.list.count() == n + 1
    page.text_box.setPlainText("Hi")
    live = page.doc.active_layer
    assert live.text is not None and live.text.text == "Hi" and live.image.width > 1
    x0 = live.x
    page._text_grip.dragged.emit(20 * c.zoom, 0)  # drag the grip 20 picture pixels right
    assert page.doc.active_layer.x == x0 + 20
    page.commit_text()
    assert len(page.doc.layers) == n + 1 and page.doc.active_layer.text.text == "Hi"
    assert page.doc.active_layer.x == x0 + 20 and page.history.undo_label == "Text"
    page.canvas.undo()
    assert len(page.doc.layers) == n  # the whole typing is one step


def test_an_empty_text_box_leaves_no_layer(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    n = len(page.doc.layers)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.commit_text()
    assert len(page.doc.layers) == n and not page.history.can_undo


def test_editing_text_updates_live_and_can_move_it(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("Hello")
    page.commit_text()
    i = page.doc.active
    page.edit_text(i)
    page.text_box.setPlainText("Hello there")
    assert page.doc.layers[i].text.text == "Hello there"  # shown before it's committed
    page._text_grip.dragged.emit(0, 10 * c.zoom)
    page.commit_text()
    assert page.doc.layers[i].text.text == "Hello there" and page.history.undo_label == "Edit text"
    page.canvas.undo()
    assert page.doc.layers[i].text.text == "Hello"


def test_shape_handles_drawn_and_dragged_on_the_canvas(page, qtbot):
    from umc.editor.tools.shapes import canvas_points

    page.select_tool("rect")
    c = page.canvas
    n = len(page.doc.layers)
    _canvas_drag(qtbot, page, (5, 5), (20, 15))
    assert len(page.doc.layers) == n + 1 and page.doc.active_layer.shape is not None
    c.grab()  # paints its handles without errors
    _canvas_drag(qtbot, page, (20, 15), (30, 25))  # the bottom-right handle
    assert [round(v) for v in canvas_points(page.doc.active_layer)] == [5, 5, 30, 25]
    assert len(page.doc.layers) == n + 1 and page.history.undo_label == "Edit shape"
    actions = {a.text(): a for a in page.layers.build_menu(page.doc.active).actions() if a.text()}
    actions["Rasterize layer (plain pixels)"].trigger()
    assert page.doc.active_layer.shape is None


def test_changes_made_while_typing_finish_the_text_first(page, qtbot, tmp_path):
    from PIL import Image

    page.select_tool("text")
    c = page.canvas
    n = len(page.doc.layers)

    def type_hi():
        qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
        page.text_box.setPlainText("Hi")

    type_hi()
    page.rail_action("rotate_right")  # a rail button (it never takes the focus)
    assert page.text_box is None and (page.doc.width, page.doc.height) == (40, 60)
    assert all(la.image.size[0] <= 60 for la in page.doc.layers)
    page.canvas.undo()
    page.canvas.undo()
    assert len(page.doc.layers) == n and all(la.text is None for la in page.doc.layers)

    page.select_tool("text")
    type_hi()
    extra = tmp_path / "extra.png"
    Image.new("RGB", (10, 10), "green").save(extra)
    page.add_paths([extra])  # a file dropped while typing
    assert len(page.doc.layers) == n + 2
    page.canvas.undo()
    page.canvas.undo()
    assert len(page.doc.layers) == n and all(la.text is None for la in page.doc.layers)


def test_an_empty_commit_leaves_the_list_in_step(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    n = len(page.doc.layers)
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("abc")
    page.text_box.setPlainText("")
    page.commit_text()
    assert len(page.doc.layers) == n and page.layers.list.count() == n
    assert page.layers.in_sync(page.doc)


def test_style_controls_take_the_focus_without_ending_the_typing(page, qtbot):
    page.select_tool("text")
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("Hi")
    page.tool_settings.font_box.setFocus()  # the font list, in the tool options window
    qtbot.wait(20)
    assert page.text_box is not None  # still typing
    page.tool_settings.set_values(page.tools["text"], {"size": 30})
    assert page.doc.active_layer.text.size == 30  # the text follows
    qtbot.mouseClick(page.tool_bar.buttons["brush"], Qt.MouseButton.LeftButton)
    assert page.text_box is None and page.doc.active_layer.text.text == "Hi"  # done


def test_clicking_elsewhere_with_text_opens_a_new_box_that_stays(page, qtbot):
    page.show()
    page.select_tool("text")
    c = page.canvas
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(5, 5))
    page.text_box.setPlainText("One")
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=c.image_to_widget(40, 40))
    for _ in range(3):
        qtbot.wait(1)  # the old box's late focus-out runs here
    assert page.text_box is not None  # the new box is still open
    assert any(la.text is not None and la.text.text == "One" for la in page.doc.layers)


def test_duplicating_a_shape_layer_keeps_it_editable(page, qtbot):
    from umc.editor import ops
    from umc.editor.tools.shapes import ShapeSpec, shape_layer

    layer = shape_layer("Box", ShapeSpec("rect", (4, 4, 30, 20), (255, 0, 0, 255)))
    page._push(ops.add_layers(page.doc, [layer], "Rectangle"))
    page._push(ops.duplicate_layers(page.doc, [page.doc.active]))
    assert page.doc.active_layer.shape is not None
    assert all(la.shape is not None for la in [la.copy() for la in page.doc.layers[-2:]])
