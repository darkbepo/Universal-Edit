"""The vector editor page: which files Edit opens there, drawing with the tools, the selection
and its handles, layers, appearance, clipboard, undo, saving (byte-exact, SVGZ), export."""

from __future__ import annotations

import gzip

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402

SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!-- made by hand -->\n"
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">\n'
    '  <rect id="box" x="10" y="10" width="40" height="20" style="fill:#ff0000"/>\n'
    '  <g id="grp" transform="translate(100 0)">\n'
    '    <circle id="dot" cx="20" cy="20" r="10" fill="blue"/>\n'
    "  </g>\n"
    '  <text id="hi" x="10" y="80" font-size="20">Hello</text>\n'
    "</svg>\n"
)


@pytest.fixture
def window(qtbot):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1280, 800)
    w.show()
    yield w
    if w.vector_editor is not None:
        w.vector_editor.wait_idle()
        w.vector_editor.history.mark_saved()


def open_file(qtbot, window, path):
    window.add_files([str(path)])
    window.enter_edit()
    page = window.vector_editor
    assert window.stack.currentWidget() is page
    qtbot.waitUntil(lambda: not page.busy and page.source == path, timeout=10000)
    page.canvas.set_zoom(2.0)  # whole units under the pointer (see VectorCanvas.step)
    return page


@pytest.fixture
def page(qtbot, window, tmp_path):
    f = tmp_path / "drawing.svg"
    f.write_bytes(SVG.encode())
    return open_file(qtbot, window, f)


def save_and_wait(qtbot, page, fn) -> None:
    assert fn()
    qtbot.waitUntil(lambda: not page.busy, timeout=20000)


def screen(page, x, y) -> QPoint:
    return page.canvas.to_screen(x, y).toPoint()


def drag(qtbot, page, a, b, modifier=Qt.KeyboardModifier.NoModifier):
    c = page.canvas
    qtbot.mousePress(c, Qt.MouseButton.LeftButton, modifier, screen(page, *a))
    for t in (0.3, 0.6, 1.0):
        p = QPointF(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        qtbot.mouseMove(c, screen(page, p.x(), p.y()))
    qtbot.mouseRelease(c, Qt.MouseButton.LeftButton, modifier, screen(page, *b))


def test_which_files_open_in_the_vector_editor(window, tmp_path):
    f = tmp_path / "a.svg"
    f.write_text(SVG, encoding="utf-8")
    window.add_files([str(f)])
    assert window.edit_choices() == ["vector", "code"]
    assert window.hub.tiles["vector"].isEnabled()
    from umc.viewer import files as vf

    kind = vf.kind_of_path(f)
    assert vf.editor_for(kind, f).id == "vector" and vf.can_edit(kind, f)


def test_untouched_saves_byte_for_byte_and_a_move_changes_one_line(qtbot, page):
    f = page.source
    assert [i.name for i in __import__("umc.vectoredit.doc").vectoredit.doc.items(page.doc)] == [
        "box", "grp", "dot", "hi",
    ]  # fmt: skip
    save_and_wait(qtbot, page, page.save)
    assert f.read_bytes() == SVG.encode()
    drag(qtbot, page, (20, 20), (30, 25))
    assert page.dirty and page.selection == [(0,)]
    save_and_wait(qtbot, page, page.save)
    out = f.read_text(encoding="utf-8")
    assert '<rect id="box" x="20" y="15" width="40" height="20" style="fill:#ff0000"/>' in out
    assert out.replace('x="20" y="15"', 'x="10" y="10"') == SVG
    assert not page.dirty
    page.undo()
    assert page.dirty and 'x="10"' in __import__("umc.vectoredit.doc").vectoredit.doc.write(
        page.doc
    )


def test_click_selects_a_group_and_handles_resize(qtbot, page):
    from umc.vectoredit import doc as vdoc

    qtbot.mouseClick(page.canvas, Qt.MouseButton.LeftButton, pos=screen(page, 120, 20))
    assert page.selection == [(1,)]
    assert page.layers.selectedItems()[0].text(0) == "grp"
    box = page.canvas.selection_box()
    drag(qtbot, page, (box.x1, box.y1), (box.x1 + 20, box.y1 + 20))  # the se handle
    new = vdoc.bounds(page.doc, (1,))
    assert round(new.width) == 40 and round(new.height) == 40
    # Shift+click adds, a rubber band picks what's inside
    qtbot.mouseClick(
        page.canvas,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        screen(page, 20, 20),
    )
    assert sorted(page.selection) == [(0,), (1,)]
    qtbot.mouseClick(page.canvas, Qt.MouseButton.LeftButton, pos=screen(page, 190, 95))
    assert page.selection == []
    drag(qtbot, page, (1, 1), (60, 40))
    assert page.selection == [(0,)]


def test_draw_shapes_pen_and_text(qtbot, page):
    from umc.vectoredit import doc as vdoc

    page.set_tool("rect")
    drag(qtbot, page, (60, 50), (90, 70))
    page.set_tool("ellipse")
    drag(qtbot, page, (100, 50), (120, 60))
    page.set_tool("line")
    drag(qtbot, page, (0, 0), (50, 50))
    kinds = [i.kind for i in vdoc.items(page.doc)]
    assert kinds[-3:] == ["rect", "ellipse", "line"]
    rect = vdoc.get(page.doc, (3,))
    assert (rect.get("x"), rect.get("width")) == ("60", "30")
    page.set_tool("pen")
    c = page.canvas
    for pt in ((10, 90), (40, 90), (40, 60)):
        qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=screen(page, *pt))
    qtbot.keyClick(c, Qt.Key.Key_Return)
    pen = vdoc.get(page.doc, (6,))
    assert pen.name == "path" and pen.get("d") == "M10 90 L40 90 L40 60"
    page.ask_text = lambda *a: "New words"
    page.set_tool("text")
    qtbot.mouseClick(c, Qt.MouseButton.LeftButton, pos=screen(page, 150, 90))
    assert vdoc.get(page.doc, (7,)).text() == "New words"
    assert page.canvas.tool == "select" and page.selection == [(7,)]
    for _ in range(5):
        page.undo()
    assert len(vdoc.items(page.doc)) == 4  # back to box, grp, dot, hi


def test_node_tool_moves_and_adds_points(qtbot, page):
    from umc.vectoredit import doc as vdoc

    page.canvas.set_selection([(0,)])
    page.set_tool("node")
    page.to_path()
    el = vdoc.get(page.doc, (0,))
    assert el.name == "path" and el.get("d") == "M10 10 L50 10 L50 30 L10 30 Z"
    drag(qtbot, page, (50, 10), (60, 0))
    assert vdoc.get(page.doc, (0,)).get("d") == "M10 10 L60 0 L50 30 L10 30 Z"
    qtbot.mouseDClick(page.canvas, Qt.MouseButton.LeftButton, pos=screen(page, 30, 30))
    assert vdoc.get(page.doc, (0,)).get("d").count("L") == 4
    page.canvas.node = (1, "end")
    assert page.delete()
    assert vdoc.get(page.doc, (0,)).get("d").count("L") == 3


def test_appearance_layers_and_arrange(qtbot, page):
    from umc.vectoredit import doc as vdoc
    from umc.vectoredit import shapes

    page.canvas.set_selection([(0,)])
    assert page.appearance.fill_button.value == "#ff0000"
    page.ask_colour = lambda current: "#00ff00"
    assert page.choose_colour("fill")
    assert shapes.style_get(vdoc.get(page.doc, (0,)), "fill") == "#00ff00"
    assert page.set_stroke_width(3) and page.set_opacity(0.5)
    el = vdoc.get(page.doc, (0,))
    assert el.get("style") == "fill:#00ff00;stroke-width:3;opacity:0.5"
    assert page.set_geometry(0, 0, 80, 40)
    assert vdoc.bounds(page.doc, (0,)).width == 80
    page.ask_text = lambda *a: "big"
    assert page.ask_rename((0,))
    assert vdoc.get(page.doc, (0,)).get("id") == "big"
    page.layers.visibility.emit((0,), False)
    assert vdoc.get(page.doc, (0,)).get("style").endswith("display:none")
    page.canvas.set_selection([(0,), (2,)])
    assert page.group()
    assert [i.kind for i in vdoc.items(page.doc)][:2] == ["g", "circle"]
    assert page.ungroup() and len(page.selection) == 2
    page.canvas.set_selection([page.selection[0]])
    assert page.reorder("front")
    text = next(i.path for i in vdoc.items(page.doc) if i.kind == "text")
    page.canvas.set_selection([text])
    assert page.appearance.text_box.isVisibleTo(page) and page.appearance.text_box.text() == "Hello"
    assert page.set_text_content("Bye")
    assert vdoc.get(page.doc, text).text() == "Bye"


def test_keys_clipboard_duplicate_delete(qtbot, page):
    from umc.vectoredit import doc as vdoc

    c = page.canvas
    c.setFocus()
    page.canvas.set_selection([(0,)])
    qtbot.keyClick(c, Qt.Key.Key_Right)
    qtbot.keyClick(c, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert vdoc.get(page.doc, (0,)).get("x") == "21"
    page.undo()  # the nudges are one step
    assert vdoc.get(page.doc, (0,)).get("x") == "10"
    assert page.copy()
    assert page.paste()
    pasted = vdoc.get(page.doc, page.selection[0])
    assert pasted.get("id") == "box1" and pasted.get("x") == "20"
    qtbot.keyClick(c, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    assert len(vdoc.items(page.doc)) == 6
    qtbot.keyClick(c, Qt.Key.Key_Delete)
    assert len(vdoc.items(page.doc)) == 5
    qtbot.keyClick(c, Qt.Key.Key_R)
    assert page.canvas.tool == "rect" and page.tool_buttons["rect"].isChecked()
    qtbot.keyClick(c, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(page.selection) == 4  # box, grp, hi, pasted (layers looked through)


def test_new_drawing_save_as_svgz_and_export(qtbot, window, tmp_path):
    from umc.vectoredit import doc as vdoc

    assert window.hub.tiles["vector"].isEnabled()
    window.open_editor  # noqa: B018
    from umc.gui.vectoreditor.vector_editor_page import VectorEditorPage

    page = window._page("vector")
    assert isinstance(page, VectorEditorPage)
    page.ask_size = lambda: (210, 297, "mm")
    assert window.open_editor("vector")
    assert window.stack.currentWidget() is page
    assert vdoc.canvas(page.doc)[2].width == 210
    page.set_tool("rect")
    drag(qtbot, page, (20, 20), (100, 80))
    target = tmp_path / "new.svgz"
    page.choose_save_path = lambda suggested, filters: target
    save_and_wait(qtbot, page, page.save)
    text = gzip.decompress(target.read_bytes()).decode()
    assert 'width="210mm"' in text and "<rect" in text and not page.dirty
    pdf = tmp_path / "new.pdf"
    page.choose_save_path = lambda suggested, filters: pdf
    save_and_wait(qtbot, page, page.export)
    assert pdf.read_bytes().startswith(b"%PDF")
    # reopen the .svgz: Save keeps it compressed
    page.open_paths([target])
    qtbot.waitUntil(lambda: not page.busy and page.source == target, timeout=10000)
    assert page.opened.gz


def test_leaving_asks_about_unsaved_changes(qtbot, window, page):
    page.canvas.set_selection([(0,)])
    page.nudge(1, 0)
    asked = []
    page.ask = lambda *a: asked.append(a[0]) or "cancel"
    window.leave_edit()
    assert asked == ["Unsaved changes"] and window.stack.currentWidget() is page
    page.ask = lambda *a: "discard"
    window.leave_edit()
    assert window.stack.currentIndex() == 0


def test_shortcut_table_has_actions_and_no_clashes(page):
    from umc.gui.vectoreditor.shortcuts import SHORTCUTS

    seen = set()
    for s in SHORTCUTS:
        assert s.id in page.actions or s.id.startswith("nudge_"), s.id
        for k in s.keys:
            assert k not in seen, k
            seen.add(k)


def test_layers_list_nests_groups_topmost_first(page):
    rows = [page.layers.topLevelItem(i).text(0) for i in range(page.layers.topLevelItemCount())]
    assert rows == ["hi", "grp", "box"]
    grp = page.layers.topLevelItem(1)
    assert grp.childCount() == 1 and grp.child(0).text(0) == "dot"
