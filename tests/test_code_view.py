"""The code editor's text view and its chunked highlighter."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QTextCursor  # noqa: E402

from umc.gui import theme  # noqa: E402
from umc.gui.codeeditor.code_view import CodeView  # noqa: E402
from umc.gui.codeeditor.highlight import ChunkedHighlighter  # noqa: E402


@pytest.fixture
def view(qtbot):
    v = CodeView()
    qtbot.addWidget(v)
    v.resize(600, 400)
    v.show()
    return v


def _colours(block) -> set[str]:
    return {r.format.foreground().color().name() for r in block.layout().formats()}


def _colour_at(block, column: int) -> str | None:
    for r in block.layout().formats():
        if r.start <= column < r.start + r.length:
            return r.format.foreground().color().name()
    return None


def _highlight(qtbot, view, text: str, language: str) -> ChunkedHighlighter:
    view.setPlainText(text)
    h = ChunkedHighlighter(view.document(), language)
    qtbot.waitUntil(h.done, timeout=10000)
    return h


def test_highlighter_colours_a_multiline_comment(qtbot, view):
    _highlight(qtbot, view, "a { color: red; }\n/* one\ntwo\nthree */\nb {}", "css")
    comment = QColor(theme.palette()["code_comment"]).name()
    assert _colours(view.document().findBlockByNumber(2)) == {comment}


def test_closing_a_comment_recolours_below(qtbot, view):
    _highlight(qtbot, view, "/* a\nb { color: red }\nc {}", "css")
    comment = QColor(theme.palette()["code_comment"]).name()
    second = view.document().findBlockByNumber(1)
    assert _colour_at(second, 0) == comment
    cursor = QTextCursor(view.document().findBlockByNumber(0))
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertText(" */")
    assert _colour_at(view.document().findBlockByNumber(1), 0) != comment


def test_big_file_stays_responsive(qtbot, view):
    text = "\n".join(f".c{i} {{ color: #{i % 999:03d}; }} /* {i} */" for i in range(40000))
    view.setPlainText(text)
    start = time.perf_counter()
    h = ChunkedHighlighter(view.document(), "css")
    assert time.perf_counter() - start < 1.0
    assert not h.done()
    qtbot.waitUntil(h.done, timeout=60000)


def test_disabled_highlighter_colours_nothing(qtbot, view):
    view.setPlainText("a { color: red }")
    h = ChunkedHighlighter(view.document(), "css")
    h.set_enabled(False)
    qtbot.wait(50)
    assert _colours(view.document().firstBlock()) == set()


def test_tab_indents_the_selection_in_one_undo_step(qtbot, view):
    view.setPlainText("a\nb\nc")
    view.set_indent_unit("    ")
    cursor = view.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)  # "a\nb"
    view.setTextCursor(cursor)
    qtbot.keyClick(view, Qt.Key.Key_Tab)
    assert view.toPlainText() == "    a\n    b\nc"
    qtbot.keyClick(view, Qt.Key.Key_Backtab)
    assert view.toPlainText() == "a\nb\nc"
    view.undo()
    assert view.toPlainText() == "    a\n    b\nc"
    view.undo()
    assert view.toPlainText() == "a\nb\nc"


def test_tab_without_selection_inserts_the_unit(qtbot, view):
    view.setPlainText("x")
    view.set_indent_unit("  ")
    view.moveCursor(QTextCursor.MoveOperation.Start)
    qtbot.keyClick(view, Qt.Key.Key_Tab)
    assert view.toPlainText() == "  x"


def test_enter_keeps_indent(qtbot, view):
    view.setPlainText("  if x:")
    view.set_indent_unit("  ")
    view.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert view.toPlainText() == "  if x:\n    "


def test_toggle_comment_python(view):
    view.setPlainText("x = 1\ny = 2")
    view.set_language("python")
    view.selectAll()
    view.toggle_comment()
    assert view.toPlainText() == "# x = 1\n# y = 2"
    view.selectAll()
    view.toggle_comment()
    assert view.toPlainText() == "x = 1\ny = 2"


def test_gutter_widens_with_more_digits(view):
    view.setPlainText("\n".join("x" * 9))
    narrow = view.gutter_width()
    view.setPlainText("\n".join(["x"] * 10000))
    assert view.gutter_width() > narrow


def test_monospace_and_zoom_survive_the_app_stylesheet(qtbot):
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    old = app.styleSheet()
    app.setStyleSheet(theme.stylesheet())  # "* { font-size: 14px }" like the real app
    try:
        from umc.gui.codeeditor.code_view import _monospace_family

        v = CodeView()
        qtbot.addWidget(v)
        v.show()
        family = _monospace_family()
        assert v.font().family() == family
        before = v.font().pointSize()
        v.zoom(4)
        assert v.font().pointSize() == before + 4
        v.zoom(0)
        assert v.font().pointSize() == before
        # each tab brings its own document: it must get the code font too
        from PySide6.QtGui import QTextDocument
        from PySide6.QtWidgets import QPlainTextDocumentLayout

        doc = QTextDocument()
        doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
        v.setDocument(doc)
        assert doc.defaultFont().family() == family
        v.zoom(2)
        assert doc.defaultFont().pointSize() == v.font().pointSize()
    finally:
        app.setStyleSheet(old)


def test_go_to_line(view):
    view.setPlainText("a\nb\nc")
    view.go_to_line(3)
    assert view.textCursor().blockNumber() == 2


def test_cursor_moved_reports_line_and_column(qtbot, view):
    view.setPlainText("ab\ncd")
    seen = []
    view.cursor_moved.connect(lambda line, col: seen.append((line, col)))
    cursor = view.textCursor()
    cursor.setPosition(4)  # "c|d"
    view.setTextCursor(cursor)
    assert seen[-1] == (2, 2)
