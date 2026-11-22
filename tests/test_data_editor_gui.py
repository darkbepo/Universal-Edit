"""The data editor page: which files Edit opens there, typing, rows / columns, sort, header,
copy / paste, find & replace, saving (same format, another format, UTF-8 fallback), leaving."""

from __future__ import annotations

import io as _io
import json

import pytest

pytest.importorskip("pytestqt")

CSV = "name;age\r\nAnn;3\r\nBob;10\r\n"


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
    if w.data_editor is not None:
        w.data_editor.wait_idle()
        w.data_editor.history.mark_saved()


def open_file(qtbot, window, path):
    window.add_files([str(path)])
    window.enter_edit()
    page = window.data_editor
    assert window.stack.currentWidget() is page
    qtbot.waitUntil(lambda: not page.busy and page.source == path, timeout=10000)
    return page


def save_and_wait(qtbot, page, fn) -> None:
    assert fn()
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)


from PySide6.QtCore import Qt  # noqa: E402


def cell(page, r, c):
    return page.model.index(r, c).data()


def test_which_files_open_in_the_data_editor(window, tmp_path):
    for name, choices in (
        ("a.csv", ["data", "code"]),
        ("a.tsv", ["data", "code"]),
        ("a.json", ["code", "data"]),
        ("a.yaml", ["code", "data"]),
        ("a.xml", ["code", "data"]),
    ):
        f = tmp_path / name
        f.write_text("a\n1\n" if name.endswith("sv") else "{}", encoding="utf-8")
        window.clear_files()
        window.add_files([str(f)])
        assert window.edit_choices() == choices, name
    from umc.viewer import files as vf

    f = tmp_path / "a.csv"
    kind = vf.kind_of_path(f)
    assert vf.editor_for(kind, f).id == "data" and vf.can_edit(kind, f)


def test_type_rows_columns_sort_and_save_back(qtbot, window, tmp_path):
    f = tmp_path / "people.csv"
    f.write_bytes(CSV.encode())
    page = open_file(qtbot, window, f)
    assert (page.model.rowCount(), page.model.columnCount()) == (2, 2)
    assert page.model.headerData(0, Qt.Orientation.Horizontal) == "name"
    assert page.header_box.isVisible() and page.header_box.isChecked()
    # untouched: saved byte for byte
    save_and_wait(qtbot, page, page.save)
    assert f.read_bytes() == CSV.encode()
    # typing in a cell (the grid's editor ends in setData)
    page.model.setData(page.model.index(0, 1), "4")
    assert cell(page, 0, 1) == "4" and page.dirty
    # rows, columns, rename
    page.table.setCurrentIndex(page.model.index(1, 0))
    assert page.add_row() and page.current() == (2, 0)
    page.set_cell(2, 0, "Cy")
    page.set_cell(2, 1, "2")
    page.table.setCurrentIndex(page.model.index(0, 1))
    assert page.add_column() and page.model.columnCount() == 3
    page.ask_text = lambda *_a: "town"
    assert page.ask_rename(2)
    assert page.sheet.columns == ("name", "age", "town")
    # sort by age, numbers by value
    page.table.setCurrentIndex(page.model.index(0, 1))
    assert page.sort(False)
    assert [cell(page, r, 0) for r in range(3)] == ["Cy", "Ann", "Bob"]
    save_and_wait(qtbot, page, page.save)
    assert f.read_bytes() == b"name;age;town\r\nCy;2;\r\nAnn;4;\r\nBob;10;\r\n"
    assert not page.dirty
    # undo goes back step by step
    page.undo()
    assert [cell(page, r, 0) for r in range(3)] == ["Ann", "Bob", "Cy"] and page.dirty


def test_copy_paste_clear_and_header_switch(qtbot, window, tmp_path):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QApplication

    f = tmp_path / "t.csv"
    f.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    page = open_file(qtbot, window, f)
    sm = page.table.selectionModel()
    page.table.setCurrentIndex(page.model.index(0, 0))
    sm.select(page.model.index(1, 1), QItemSelectionModel.SelectionFlag.Select)
    assert page.copy()
    assert QApplication.clipboard().text() == "1\t2\n3\t4"
    # paste past the edges: rows and columns are added, one undo step
    page.table.setCurrentIndex(page.model.index(1, 1))
    page.table.clearSelection()
    assert page.paste("x\ty\nz\tw")
    assert (page.model.rowCount(), page.model.columnCount()) == (3, 3)
    assert [cell(page, 2, c) for c in range(3)] == ["", "z", "w"]
    page.undo()
    assert (page.model.rowCount(), page.model.columnCount()) == (2, 2)
    # Delete empties the chosen cells
    page.table.setCurrentIndex(page.model.index(0, 0))
    assert page.clear_cells() and cell(page, 0, 0) == ""
    # the header switch: the names become the first row
    page.header_box.setChecked(False)
    assert page.model.rowCount() == 3 and cell(page, 0, 0) == "a"
    assert page.model.headerData(0, Qt.Orientation.Horizontal) == "A"


def test_find_and_replace(qtbot, window, tmp_path):
    f = tmp_path / "t.json"
    f.write_text('[{"pet": "cat", "n": 12}, {"pet": "Cat", "n": 2}]\n', encoding="utf-8")
    window.add_files([str(f)])
    window.enter_edit("data")
    page = window.data_editor
    qtbot.waitUntil(lambda: not page.busy and page.source == f, timeout=10000)
    assert not page.header_box.isVisible()  # JSON: columns are keys
    page.find_bar.open(True)
    page.find_bar.query.setText("cat")
    assert page.find(1) and page.current() == (1, 0)  # the chosen cell (0, 0) is skipped
    assert page.find(1) and page.current() == (0, 0)  # … then round again
    page.find_bar.query.setText("2")
    page.find_bar.replacement.setText("7")
    page.replace_all()
    assert page.find_bar.count.text() == "Replaced 2"
    save_and_wait(qtbot, page, page.save)
    assert json.loads(f.read_text("utf-8")) == [{"pet": "cat", "n": 17}, {"pet": "Cat", "n": 7}]


def test_save_as_another_format_then_edits_that_file(qtbot, window, tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("name,a.b\nAnn,1\n", encoding="utf-8")
    page = open_file(qtbot, window, f)
    out = tmp_path / "t.json"
    page.choose_save_path = lambda *_a: out
    save_and_wait(qtbot, page, page.save_as)
    assert json.loads(out.read_text("utf-8")) == [{"name": "Ann", "a": {"b": 1}}]
    assert page.source == out and page.doc.format == "json" and not page.dirty


def test_lost_parts_and_utf8_questions(qtbot, window, tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("- a: 1  # note\n", encoding="utf-8")
    window.add_files([str(f)])
    window.enter_edit("data")
    page = window.data_editor
    qtbot.waitUntil(lambda: not page.busy and page.source == f, timeout=10000)
    asked = []
    page.ask = lambda title, *_a: asked.append(title) or "no"
    page.set_cell(0, 0, "2")
    assert not page.save() and asked == ["Some parts will be left out"]
    page.ask = lambda title, *_a: asked.append(title) or "yes"
    save_and_wait(qtbot, page, page.save)
    assert f.read_text("utf-8") == "- a: 2\n"
    # a Windows-1252 file that can't hold the new text: asked, then saved as UTF-8
    g = tmp_path / "old.csv"
    g.write_bytes("a\ncaf\xe9\n".encode("cp1252"))
    assert page.open_paths([g])
    qtbot.waitUntil(lambda: not page.busy and page.source == g, timeout=10000)
    page.set_cell(0, 0, "日本")
    save_and_wait(qtbot, page, page.save)
    assert asked[-1] == "Save as UTF-8?" and g.read_bytes() == "a\n日本\n".encode()


def test_workbook_tabs_and_formulas_survive(qtbot, window, tmp_path):
    from openpyxl import Workbook, load_workbook

    wb = Workbook()
    wb.active.title = "One"
    wb.active.append(["n", "twice"])
    wb.active.append([2, "=A2*2"])
    wb.create_sheet("Two").append(["x"])
    f = tmp_path / "book.xlsx"
    buf = _io.BytesIO()
    wb.save(buf)
    f.write_bytes(buf.getvalue())
    page = open_file(qtbot, window, f)
    assert page.tabs.isVisible() and page.tabs.count() == 2
    page.set_cell(0, 0, "5")
    page.tabs.setCurrentIndex(1)
    assert page.sheet.name == "Two" and page.sheet.columns == ("x",)
    save_and_wait(qtbot, page, page.save)
    ws = load_workbook(f)["One"]
    assert ws["A2"].value == 5 and ws["B2"].value == "=A2*2"


def test_hub_tile_new_table_and_leaving(qtbot, window, tmp_path):
    assert window.open_editor("data")
    page = window.data_editor
    assert page.path is None and page.model.columnCount() == 3
    page.set_cell(0, 0, "hello")
    asked = []
    page.ask = lambda title, *_a: asked.append(title) or "cancel"
    window.leave_edit()
    assert asked == ["Unsaved changes"] and window.stack.currentWidget() is page
    out = tmp_path / "new.csv"
    page.choose_save_path = lambda *_a: out
    page.ask = lambda *_a: "save"
    window.leave_edit()
    assert window.stack.currentIndex() == 0
    assert out.read_text("utf-8").startswith("column1,column2,column3\nhello,,\n")


def test_real_keys_in_the_grid_and_the_box(qtbot, window, tmp_path):
    f = tmp_path / "k.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    page = open_file(qtbot, window, f)
    page.table.setFocus()
    page.table.setCurrentIndex(page.model.index(0, 0))
    qtbot.keyClicks(page.table, "9")  # typing starts editing the cell
    from PySide6.QtWidgets import QLineEdit

    editor = page.table.viewport().findChild(QLineEdit)
    assert editor is not None and editor.text() == "9"
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: cell(page, 0, 0) == "9", timeout=3000)  # Qt commits it queued
    page.table.setFocus()
    page.table.setCurrentIndex(page.model.index(0, 1))
    qtbot.keyClick(page.table, Qt.Key.Key_Delete)
    assert cell(page, 0, 1) == ""
    qtbot.keyClick(page.table, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert cell(page, 0, 1) == "2"
    # the box above the grid: Shift+Enter is a new line, Enter keeps it
    page.cell_box.setFocus()
    page.cell_box.selectAll()
    qtbot.keyClicks(page.cell_box, "two")
    qtbot.keyClick(page.cell_box, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    qtbot.keyClicks(page.cell_box, "lines")
    qtbot.keyClick(page.cell_box, Qt.Key.Key_Return)
    assert page.sheet.rows[0][1] == "two\nlines" and cell(page, 0, 1) == "two ⏎ lines"
    assert not page.model.flags(page.model.index(0, 1)) & Qt.ItemFlag.ItemIsEditable
