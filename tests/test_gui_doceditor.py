"""Document editor GUI (offscreen): the text view and its QTextDocument → HTML export."""

import pytest

pytest.importorskip("pytestqt")

from tests.doceditor_samples import CASES, PNG_1PX  # noqa: E402
from umc.doceditor.html_subset import normalise_html  # noqa: E402
from umc.doceditor.types import OpenedDocument  # noqa: E402
from umc.gui.doceditor.html_export import document_to_html  # noqa: E402
from umc.gui.doceditor.text_view import (  # noqa: E402
    PAGE_WIDTH,
    QUOTE_PROPERTY,
    DocTextView,
)

SUBSET_CASES = CASES + [
    "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul>",
    '<p>x<img src="img/1.png" width="20" height="20"></p>',
    '<p style="text-align:center">c</p><p style="text-align:right">r</p>',
    "<p>a<br>b</p><p></p><p>&lt;&amp;&gt; blåbær</p>",
    '<p><a href="https://x.org/"><b>x</b></a> <span style="color:#00ff00">g</span></p>',
    '<blockquote><p style="text-align:center">q1</p><p>q2</p></blockquote>',
    '<ol><li style="text-align:right">one</li><li>two</li></ol><p>after</p>',
    '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr>'
    "<tr><td><p>c</p></td></tr></table>",
    '<h2 style="text-align:center;page-break-before:always">H</h2>',
    '<p><span style="font-family:Courier New">m</span></p><pre>x &lt; 1<br>y</pre>',
    '<p style="margin-left:36pt">margin-left:72pt text-align:center</p><p>&lt;p&gt;</p>',
]


def _view(qtbot, html: str, images=None) -> DocTextView:
    v = DocTextView()
    qtbot.addWidget(v)
    v.load(OpenedDocument(html, {"img/1.png": PNG_1PX} if images is None else images, "docx"))
    return v


@pytest.mark.parametrize("html", SUBSET_CASES)
def test_load_then_export_is_identity(qtbot, html):
    v = _view(qtbot, html)
    assert v.content().html == normalise_html(html)[0]


def test_export_nested_list_in_table(qtbot):
    html = "<table><tr><td><ul><li>a<ul><li>b</li></ul></li></ul></td></tr></table>"
    v = _view(qtbot, html)
    assert v.content().html == html
    assert document_to_html(v.document()) == html


def test_quote_blocks_are_marked(qtbot):
    v = _view(qtbot, "<blockquote><p>q</p></blockquote><p>n</p>")
    first = v.document().begin()
    assert first.blockFormat().boolProperty(QUOTE_PROPERTY)
    assert not first.next().blockFormat().boolProperty(QUOTE_PROPERTY)


def test_indent_becomes_block_indent(qtbot):
    v = _view(qtbot, '<p style="margin-left:72pt">i</p>')
    fmt = v.document().begin().blockFormat()
    assert fmt.indent() == 2 and fmt.leftMargin() == 0


def test_heading_display_format(qtbot):
    v = _view(qtbot, "<h1>T</h1><pre>c</pre>")
    head = v.document().begin()
    fmt = head.begin().fragment().charFormat()
    assert fmt.fontPointSize() == 20 and fmt.fontWeight() >= 700
    code = head.next().begin().fragment().charFormat()
    assert code.fontFamilies() == ["Consolas"] and code.fontPointSize() == 10


def test_load_resets_undo_and_modified(qtbot):
    v = _view(qtbot, "<h1>T</h1><blockquote><p>q</p></blockquote>")
    assert not v.document().isModified()
    assert not v.document().isUndoAvailable()
    v.textCursor().insertText("more")
    assert v.document().isModified()
    v.load(OpenedDocument("<p>x</p>", {}, "docx"))
    assert not v.document().isModified() and not v.document().isUndoAvailable()


def test_insert_image_adds_resource(qtbot, tmp_path):
    png = tmp_path / "dot.png"
    png.write_bytes(PNG_1PX)
    v = _view(qtbot, "<p>x</p>", images={})
    v.insert_image(png)
    assert list(v.images) == ["img/1.png"]
    content = v.content()
    assert '<img src="img/1.png"' in content.html
    assert content.images == {"img/1.png": PNG_1PX}


def test_content_keeps_only_referenced_pictures(qtbot):
    v = _view(qtbot, "<p>no picture</p>", images={"img/1.png": PNG_1PX})
    assert v.content().images == {}


def test_content_has_markdown_and_text(qtbot):
    v = _view(qtbot, "<h1>T</h1><p>x</p>")
    content = v.content()
    assert content.plain_text == "T\nx\n"
    assert content.markdown.startswith("# T")


def test_empty_document_exports_nothing(qtbot):
    v = _view(qtbot, "")
    content = v.content()
    assert content.html == "" and content.plain_text == ""


def test_page_column_width(qtbot):
    v = _view(qtbot, "<p>x</p>")
    v.resize(1400, 800)
    v.show()
    qtbot.waitExposed(v)
    assert abs(v.viewport().width() - PAGE_WIDTH) <= v.verticalScrollBar().sizeHint().width()
    assert v.viewport().objectName() == "DocPage"


def test_page_break_is_painted(qtbot):
    v = _view(qtbot, '<p>a</p><p style="page-break-before:always">b</p>')
    v.resize(1000, 600)
    v.show()
    qtbot.waitExposed(v)
    assert v.page_break_lines() and len(v.page_break_lines()) == 1
    v.viewport().grab()  # paints without errors


# ===== formatting operations and the formatting bar (Task 7) =====
from PySide6.QtGui import QColor, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QColorDialog, QInputDialog  # noqa: E402

from umc.gui.doceditor.format_bar import FormatBar  # noqa: E402
from umc.gui.doceditor.formatting import (  # noqa: E402
    block_style,
    change_indent,
    clear_formatting,
    insert_hr,
    insert_link,
    insert_page_break,
    insert_table,
    is_on,
    set_alignment,
    set_block_style,
    set_color,
    set_font_family,
    set_font_size,
    set_highlight,
    table_op,
    toggle,
    toggle_list,
)


@pytest.fixture
def view(qtbot):
    return _view(qtbot, "<p>hello world</p>")


def _select_word(view):
    c = view.textCursor()
    c.select(QTextCursor.SelectionType.WordUnderCursor)
    view.setTextCursor(c)
    return c


def _select_all(view):
    c = view.textCursor()
    c.select(QTextCursor.SelectionType.Document)
    view.setTextCursor(c)
    return c


def test_bold_and_heading_export(view):
    c = _select_word(view)
    toggle(c, "bold")
    set_block_style(c, "h1")
    assert view.content().html == "<h1>hello world</h1>"
    set_block_style(c, "normal")
    assert view.content().html == "<p>hello world</p>"


def test_inline_formats_export(view):
    c = _select_word(view)
    toggle(c, "bold")
    toggle(c, "italic")
    toggle(c, "underline")
    toggle(c, "strike")
    assert is_on(c, "bold") and is_on(c, "strike")
    html = view.content().html
    for tag in ("<b>", "<i>", "<u>", "<s>"):
        assert tag in html
    toggle(c, "bold")
    assert not is_on(c, "bold")
    set_font_family(c, "Courier New")
    set_font_size(c, 14)
    set_color(c, QColor("#ff0000"))
    set_highlight(c, QColor("#ffff00"))
    html = view.content().html
    assert "font-family:Courier New" in html and "font-size:14pt" in html
    assert "color:#ff0000" in html and "background-color:#ffff00" in html
    set_highlight(c, None)
    assert "background-color" not in view.content().html


def test_block_styles(view):
    c = view.textCursor()
    for style, html in [
        ("h2", "<h2>hello world</h2>"),
        ("quote", "<blockquote><p>hello world</p></blockquote>"),
        ("code", "<pre>hello world</pre>"),
        ("normal", "<p>hello world</p>"),
    ]:
        set_block_style(c, style)
        assert view.content().html == html
        assert block_style(c) == style


def test_single_undo_step(view):
    c = view.textCursor()
    set_block_style(c, "h1")
    view.undo()
    assert view.content().html == "<p>hello world</p>"
    toggle_list(view.textCursor(), "bullet")
    view.undo()
    assert view.content().html == "<p>hello world</p>"


def test_alignment(view):
    set_alignment(view.textCursor(), "center")
    assert view.content().html == '<p style="text-align:center">hello world</p>'
    set_alignment(view.textCursor(), "left")
    assert view.content().html == "<p>hello world</p>"
    set_alignment(view.textCursor(), "justify")
    assert "text-align:justify" in view.content().html


def test_lists_and_indent(qtbot):
    v = _view(qtbot, "<p>a</p><p>b</p><p>c</p>")
    toggle_list(_select_all(v), "bullet")
    assert v.content().html == "<ul><li>a</li><li>b</li><li>c</li></ul>"
    c = v.textCursor()
    c.setPosition(v.document().findBlockByNumber(1).position())
    change_indent(c, 1)
    assert v.content().html == "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
    change_indent(c, -1)
    assert v.content().html == "<ul><li>a</li><li>b</li><li>c</li></ul>"
    toggle_list(_select_all(v), "number")
    assert v.content().html == "<ol><li>a</li><li>b</li><li>c</li></ol>"
    toggle_list(_select_all(v), "number")
    assert v.content().html == "<p>a</p><p>b</p><p>c</p>"


def test_list_cannot_nest_deeper_than_the_item_above(qtbot):
    v = _view(qtbot, "<p>a</p><p>b</p>")
    toggle_list(_select_all(v), "bullet")
    c = v.textCursor()
    c.setPosition(0)
    change_indent(c, 1)  # first item: nothing above it to nest under
    assert v.content().html == "<ul><li>a</li><li>b</li></ul>"
    c.setPosition(v.document().findBlockByNumber(1).position())
    change_indent(c, 1)
    change_indent(c, 1)
    assert v.content().html == "<ul><li>a<ul><li>b</li></ul></li></ul>"
    html = "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>"
    v.load(OpenedDocument(html, {}, "docx"))
    c = v.textCursor()
    c.setPosition(v.document().findBlockByNumber(1).position())
    change_indent(c, -1)
    out = v.content().html
    assert "<ul><ul>" not in out and out.count("<ul>") == out.count("</ul>")


def test_block_indent_outside_lists(view):
    c = view.textCursor()
    change_indent(c, 2)
    assert view.content().html == '<p style="margin-left:72pt">hello world</p>'
    change_indent(c, -5)
    assert view.content().html == "<p>hello world</p>"
    change_indent(c, 20)
    assert "margin-left:288pt" in view.content().html


def test_links(view):
    c = _select_word(view)
    insert_link(c, "https://x.org/", None)
    assert view.content().html == '<p><a href="https://x.org/">hello</a> world</p>'
    c = view.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    insert_link(c, "https://y.org/", "why")
    assert view.content().html.endswith('<a href="https://y.org/">why</a></p>')


def test_table_ops(view):
    c = view.textCursor()
    insert_table(c, 2, 2)
    view.setTextCursor(c)
    table_op(view.textCursor(), "row_below")
    assert view.content().html.count("<tr>") == 3
    table_op(view.textCursor(), "col_right")
    assert view.content().html.count("<td") == 9
    table_op(view.textCursor(), "row_above")
    table_op(view.textCursor(), "col_left")
    assert view.content().html.count("<tr>") == 4
    table_op(view.textCursor(), "del_row")
    table_op(view.textCursor(), "del_col")
    assert view.content().html.count("<tr>") == 3 and view.content().html.count("<td") == 9
    table_op(view.textCursor(), "del_table")
    assert view.content().html == "<p>hello world</p>"


def test_table_merge_and_split(view):
    c = view.textCursor()
    insert_table(c, 2, 2)
    view.setTextCursor(c)
    c = view.textCursor()
    table = c.currentTable()
    c.setPosition(table.cellAt(0, 0).firstPosition())
    c.setPosition(table.cellAt(0, 1).firstPosition(), QTextCursor.MoveMode.KeepAnchor)
    table_op(c, "merge")
    assert 'colspan="2"' in view.content().html
    c = view.textCursor()
    c.setPosition(table.cellAt(0, 0).firstPosition())
    table_op(c, "split")
    assert "colspan" not in view.content().html
    assert view.content().html.count("<td") == 4


def test_table_op_outside_table_is_noop(view):
    table_op(view.textCursor(), "row_below")
    assert view.content().html == "<p>hello world</p>"


def test_page_break_and_rule(view):
    c = view.textCursor()
    c.setPosition(5)
    insert_page_break(c)
    assert view.content().html == '<p>hello</p><p style="page-break-before:always">world</p>'
    c = view.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    insert_hr(c)
    assert "<hr>" in view.content().html


def test_clear_formatting_keeps_list(qtbot):
    v = _view(qtbot, '<ul><li><b>bold</b> <a href="https://x.org/">link</a></li></ul><h1>T</h1>')
    clear_formatting(_select_all(v))
    assert v.content().html == "<ul><li>bold link</li></ul><p>T</p>"


def test_clear_formatting_keeps_pictures_and_table(qtbot):
    html = (
        '<table><tr><td><p><b>x</b><img src="img/1.png" width="2" height="2"></p></td></tr></table>'
    )
    v = _view(qtbot, html)
    clear_formatting(_select_all(v))
    out = v.content().html
    assert "<table>" in out and "<img" in out and "<b>" not in out


def test_format_bar_follows_cursor(qtbot, view):
    bar = FormatBar(view)
    qtbot.addWidget(bar)
    c = _select_word(view)
    toggle(c, "bold")
    c = view.textCursor()
    c.setPosition(2)
    view.setTextCursor(c)
    assert bar.buttons["bold"].isChecked()
    c.setPosition(9)
    view.setTextCursor(c)
    assert not bar.buttons["bold"].isChecked()
    set_alignment(view.textCursor(), "center")
    bar.refresh()
    assert bar.buttons["center"].isChecked() and not bar.buttons["left"].isChecked()
    set_block_style(view.textCursor(), "h2")
    bar.refresh()
    assert bar.style_box.currentText() == "Heading 2"
    toggle_list(view.textCursor(), "bullet")
    bar.refresh()
    assert bar.buttons["bullet"].isChecked() and not bar.buttons["number"].isChecked()


def test_format_bar_buttons_edit_the_document(qtbot, view):
    bar = FormatBar(view)
    qtbot.addWidget(bar)
    _select_word(view)
    bar.buttons["italic"].click()
    assert view.content().html == "<p><i>hello</i> world</p>"
    bar.style_box.setCurrentIndex(1)
    bar.style_box.activated.emit(1)
    assert view.content().html.startswith("<h1>")
    bar.buttons["right"].click()
    assert "text-align:right" in view.content().html


def test_format_bar_colour_and_link_dialogs(qtbot, view, monkeypatch):
    bar = FormatBar(view)
    qtbot.addWidget(bar)
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **k: QColor("#00ff00"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("https://x.org/", True))
    _select_word(view)
    bar.color_button.click()
    assert "color:#00ff00" in view.content().html
    _select_word(view)
    bar.link_button.click()
    assert '<a href="https://x.org/"' in view.content().html


def test_table_menu_only_in_table(qtbot, view):
    bar = FormatBar(view)
    qtbot.addWidget(bar)
    assert not bar.table_menu.isEnabled()
    c = view.textCursor()
    insert_table(c, 2, 2)
    view.setTextCursor(c)
    bar.refresh()
    assert bar.table_menu.isEnabled()
    bar.table_menu.actions()[1].trigger()  # row below
    assert view.content().html.count("<tr>") == 3


# ----- find & replace -----------------------------------------------------------
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from umc.gui.doceditor.find_bar import FindBar  # noqa: E402


@pytest.fixture
def view_text(qtbot):
    return _view(qtbot, "<p>a cat. A cat.</p>")


def test_find_wraps(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert bar.find("cat") and bar.find("cat") and bar.find("cat")  # third wraps to the first
    assert not bar.find("dog") and bar.status.text() == "No matches."


def test_find_backward_and_case(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert bar.find("cat", backward=True)
    assert view_text.textCursor().selectionStart() == 9
    assert not bar.find("CAT", case=True)


def test_replace_all_case_and_whole_word(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert bar.replace_all("a", "the", case=True, whole=True) == 1
    assert view_text.toPlainText() == "the cat. A cat."
    assert bar.status.text() == "Replaced 1."
    view_text.undo()
    assert view_text.toPlainText() == "a cat. A cat."


def test_replace_all_is_one_undo_step_and_zero(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert bar.replace_all("cat", "dog") == 2
    assert view_text.toPlainText() == "a dog. A dog."
    view_text.undo()
    assert view_text.toPlainText() == "a cat. A cat."
    assert bar.replace_all("zzz", "y") == 0
    assert bar.status.text() == "No matches."


def test_replace_current_then_next(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert not bar.replace("cat", "dog")  # nothing selected: only finds the first
    assert view_text.toPlainText() == "a cat. A cat."
    assert bar.replace("cat", "dog")  # replaces it, selects the next
    assert view_text.toPlainText() == "a dog. A cat."
    assert view_text.textCursor().selectedText() == "cat"


def test_bar_open_and_escape(qtbot, view_text):
    bar = FindBar(view_text)
    qtbot.addWidget(bar)
    assert bar.isHidden()
    c = view_text.textCursor()
    c.setPosition(2)
    c.setPosition(5, c.MoveMode.KeepAnchor)
    view_text.setTextCursor(c)
    bar.show()
    bar.open(True)
    assert bar.find_edit.text() == "cat"
    assert not bar.replace_edit.isHidden()
    bar.open(False)
    assert bar.replace_edit.isHidden()
    QTest.keyClick(bar.find_edit, Qt.Key.Key_Escape)
    assert bar.isHidden()


# ===== the document editor page (Task 9) =====
import threading  # noqa: E402

import docx as python_docx  # noqa: E402
from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from umc.core.errors import ConversionError, ToolMissingError  # noqa: E402
from umc.doceditor import save as save_mod  # noqa: E402
from umc.doceditor.docx_writer import write  # noqa: E402
from umc.doceditor.types import W_HEADERS, warning_text  # noqa: E402
from umc.gui.doceditor import doc_editor_page as page_mod  # noqa: E402
from umc.gui.doceditor.doc_editor_page import DocEditorPage  # noqa: E402
from umc.gui.tool_dialog import ToolMissingDialog  # noqa: E402


@pytest.fixture
def page(qtbot):
    pg = DocEditorPage()
    qtbot.addWidget(pg)
    pg.resize(1100, 720)
    pg.show()
    yield pg
    # pytest-qt closes widgets before fixture teardown: never block on "Save changes?"
    pg._ask_save_changes = lambda: "discard"
    qtbot.waitUntil(lambda: not pg.busy, timeout=20000)


@pytest.fixture
def docx_with_header(tmp_path):
    d = python_docx.Document()
    d.sections[0].header.paragraphs[0].text = "Top secret"
    d.add_paragraph("Body text")
    p = tmp_path / "header.docx"
    d.save(p)
    return p


def _open(qtbot, page, path):
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_path(path)


def _type(page, text):
    c = page.view.textCursor()
    c.movePosition(c.MoveOperation.End)
    c.insertText(text)
    page.view.setTextCursor(c)


def _save(qtbot, page, how="save"):
    with qtbot.waitSignal(page.save_done, timeout=30000):
        assert getattr(page, how)()


def _txt(tmp_path, text="x"):
    p = tmp_path / "a.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_open_docx_shows_text(qtbot, page, tmp_path):
    p = write("<h1>Hi</h1><p>there</p>", {}, tmp_path / "a.docx")
    _open(qtbot, page, p)
    assert page.view.toPlainText() == "Hi\nthere" and not page.banner.isVisible()
    assert page.status.text() == "2 words · 7 characters"
    assert page.source == p and page.source_format == "docx" and page.warnings == []
    assert page.view.isEnabled() and page.message.text() == ""


def test_status_marks_unsaved_changes(qtbot, page, tmp_path):
    _open(qtbot, page, _txt(tmp_path, "one\n"))
    _type(page, " two")
    qtbot.waitUntil(lambda: page.status.text() == "2 words · 7 characters · Unsaved changes")


def test_banner_for_header(qtbot, page, docx_with_header):
    _open(qtbot, page, docx_with_header)
    assert page.warnings == [W_HEADERS]
    assert page.banner.isVisible()
    assert page.banner_label.text() == warning_text([W_HEADERS])
    page.banner_close.click()
    assert not page.banner.isVisible()


def test_open_failure_shows_message(qtbot, page, tmp_path, monkeypatch):
    def boom(path, cancel_event=None):
        raise ConversionError("This document is too large to edit here.")

    monkeypatch.setattr(page_mod, "open_document", boom)
    page.open_path(_txt(tmp_path))
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    assert page.message.text() == "This document is too large to edit here."
    assert page.message.objectName() == "Error" and page.source is None


def test_save_writes_back_same_format(qtbot, page, tmp_path, monkeypatch):
    p = tmp_path / "a.md"
    p.write_text("# Title\n\nHello\n", encoding="utf-8")
    _open(qtbot, page, p)

    def no_dialog(*a, **k):
        raise AssertionError("no dialog expected")

    monkeypatch.setattr(QMessageBox, "exec", no_dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", no_dialog)
    _type(page, " world")
    _save(qtbot, page)
    assert "Hello world" in p.read_text(encoding="utf-8")
    assert not page.view.document().isModified()
    assert page.message.text() == "Saved “a.md”."
    assert "Unsaved" not in page.status.text()


def test_save_with_warnings_asks(qtbot, page, docx_with_header, monkeypatch):
    _open(qtbot, page, docx_with_header)
    before = docx_with_header.read_bytes()
    asked = []

    def cancel(box):
        asked.append(box.text())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    _type(page, " more")
    assert not page.save()
    assert asked == [f"Parts of this document will be lost ({W_HEADERS}). Save anyway?"]
    assert docx_with_header.read_bytes() == before
    assert page.view.document().isModified()

    # Answering Save writes it, and the question isn't asked again.
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: asked.append(1) or QMessageBox.StandardButton.Save
    )
    _save(qtbot, page)
    assert docx_with_header.read_bytes() != before and len(asked) == 2
    _type(page, " again")
    _save(qtbot, page)
    assert len(asked) == 2


def test_save_warning_offers_save_as(qtbot, page, docx_with_header, tmp_path, monkeypatch):
    _open(qtbot, page, docx_with_header)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: QMessageBox.StandardButton.SaveAll)
    out = tmp_path / "copy.docx"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "Word (DOCX) (*.docx)")
    )
    _save(qtbot, page)
    assert out.exists()


def test_save_as_pdf(qtbot, page, tmp_path, monkeypatch):
    import pymupdf

    monkeypatch.setattr(save_mod, "_libreoffice_pdf", lambda: False)
    p = write("<p>Hello pdf</p>", {}, tmp_path / "a.docx")
    _open(qtbot, page, p)
    seen = {}

    def dialog(parent, title, default, filters, selected):
        seen.update(default=default, filters=filters, selected=selected)
        return str(tmp_path / "out"), "PDF (*.pdf)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", dialog)
    _save(qtbot, page, "save_as")
    out = tmp_path / "out.pdf"
    with pymupdf.open(out) as pdf:
        assert "Hello pdf" in pdf[0].get_text()
    assert seen["default"] == str(p) and seen["selected"] == "Word (DOCX) (*.docx)"
    assert "PDF (*.pdf)" in seen["filters"].split(";;")

    # Save now repeats that path and format, without asking.
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    _type(page, " again")
    _save(qtbot, page)
    with pymupdf.open(out) as pdf:
        assert "again" in pdf[0].get_text()


def test_save_as_cancelled(qtbot, page, tmp_path, monkeypatch):
    _open(qtbot, page, _txt(tmp_path))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not page.save_as() and not page.saving


def test_failed_save_stays_unsaved(qtbot, page, tmp_path, monkeypatch):
    _open(qtbot, page, _txt(tmp_path))

    def boom(content, dest, fmt, cancel_event=None):
        raise ConversionError("Couldn't save “a.txt” — it may be open in another program.")

    monkeypatch.setattr(page_mod, "save_document", boom)
    _type(page, "y")
    _save(qtbot, page)
    assert page.message.text() == "Couldn't save “a.txt” — it may be open in another program."
    assert page.message.objectName() == "Error"
    assert page.view.document().isModified()


def test_edits_during_save_stay_unsaved(qtbot, page, tmp_path, monkeypatch):
    p = _txt(tmp_path)
    _open(qtbot, page, p)
    started, release = threading.Event(), threading.Event()
    real = page_mod.save_document

    def slow(content, dest, fmt, cancel_event=None):
        started.set()
        release.wait(20)
        return real(content, dest, fmt, cancel_event)

    monkeypatch.setattr(page_mod, "save_document", slow)
    _type(page, "y")
    with qtbot.waitSignal(page.save_done, timeout=30000):
        assert page.save()
        assert page.saving and not page.save_button.isEnabled()
        qtbot.waitUntil(started.is_set, timeout=10000)
        _type(page, "z")
        release.set()
    assert p.read_text(encoding="utf-8") == "xy\n"
    assert page.view.document().isModified()


def test_back_with_changes_asks(qtbot, page, tmp_path, monkeypatch):
    p = _txt(tmp_path)
    _open(qtbot, page, p)
    assert page.maybe_leave()  # nothing changed: no question
    _type(page, "y")
    texts = []

    def answer(button):
        def exec_(box):
            texts.append(box.text())
            return button

        return exec_

    monkeypatch.setattr(QMessageBox, "exec", answer(QMessageBox.StandardButton.Cancel))
    assert not page.maybe_leave()
    assert texts == ["Save changes to this document?"]
    monkeypatch.setattr(QMessageBox, "exec", answer(QMessageBox.StandardButton.Discard))
    assert page.maybe_leave()
    monkeypatch.setattr(QMessageBox, "exec", answer(QMessageBox.StandardButton.Save))
    assert page.maybe_leave()
    assert p.read_text(encoding="utf-8") == "xy\n" and not page.view.document().isModified()


def test_drop_second_kind_of_file(qtbot, page, media):
    page.drop_paths([media / "img0.png"])
    assert page.message.text() == "Only one document can be edited at a time."
    page.drop_paths([media / "doc.md", media / "notes.txt"])
    assert page.message.text() == "Only one document can be edited at a time."
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.drop_paths([media / "notes.txt"])
    assert page.source == media / "notes.txt"


def test_open_tool_missing_shows_dialog(qtbot, page, tmp_path, monkeypatch):
    calls = []

    def missing(path, cancel_event=None):
        calls.append(path)
        if len(calls) == 1:
            raise ToolMissingError("LibreOffice", "https://example.org/", "open this document")
        return page_mod.OpenedDocument("<p>ok</p>", {}, "odt")

    shown = []

    def exec_(dialog):
        shown.append(dialog.tool)
        return 1

    monkeypatch.setattr(page_mod, "open_document", missing)
    monkeypatch.setattr(ToolMissingDialog, "exec", exec_)
    p = tmp_path / "a.odt"
    p.write_bytes(b"x")
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_path(p)
    assert shown == ["LibreOffice"] and len(calls) == 2
    assert page.view.toPlainText() == "ok"


def test_save_tool_missing_retries(qtbot, page, tmp_path, monkeypatch):
    p = _txt(tmp_path)
    _open(qtbot, page, p)
    calls = []
    real = page_mod.save_document

    def missing(content, dest, fmt, cancel_event=None):
        calls.append(fmt)
        if len(calls) == 1:
            raise ToolMissingError("Pandoc", "https://example.org/", "save this document")
        return real(content, dest, fmt, cancel_event)

    monkeypatch.setattr(page_mod, "save_document", missing)
    monkeypatch.setattr(ToolMissingDialog, "exec", lambda dialog: 1)
    _type(page, "y")
    assert page.save()
    qtbot.waitUntil(lambda: len(calls) == 2 and not page.busy, timeout=30000)
    assert p.read_text(encoding="utf-8") == "xy\n"


def test_shortcuts(qtbot, page, tmp_path, monkeypatch):
    _open(qtbot, page, _txt(tmp_path, "hello"))
    v = page.view
    v.setFocus()
    v.selectAll()
    ctrl = Qt.KeyboardModifier.ControlModifier
    qtbot.keyClick(v, Qt.Key.Key_B, ctrl)
    assert "<b>hello</b>" in v.content().html
    qtbot.keyClick(v, Qt.Key.Key_2, ctrl | Qt.KeyboardModifier.AltModifier)
    assert v.content().html.startswith("<h2")
    qtbot.keyClick(v, Qt.Key.Key_E, ctrl)
    assert "text-align:center" in v.content().html
    qtbot.keyClick(v, Qt.Key.Key_F, ctrl)
    assert page.find_bar.isVisible() and page.find_bar.replace_edit.isHidden()
    page.find_bar.close_bar()
    qtbot.keyClick(v, Qt.Key.Key_H, ctrl)
    assert not page.find_bar.replace_edit.isHidden()
    page.find_bar.close_bar()
    saved = []
    monkeypatch.setattr(page, "save", lambda: saved.append("save") or True)
    monkeypatch.setattr(page, "save_as", lambda: saved.append("as") or True)
    qtbot.keyClick(v, Qt.Key.Key_S, ctrl)
    qtbot.keyClick(v, Qt.Key.Key_S, ctrl | Qt.KeyboardModifier.ShiftModifier)
    assert saved == ["save", "as"]


def test_back_button_and_undo(qtbot, page, tmp_path):
    _open(qtbot, page, _txt(tmp_path))
    assert not page.undo_button.isEnabled()
    _type(page, "y")
    assert page.undo_button.isEnabled()
    page.undo_button.click()
    assert page.view.toPlainText() == "x" and page.redo_button.isEnabled()
    with qtbot.waitSignal(page.back_requested, timeout=1000):
        page.back_button.click()


class _SlowOpen:
    """open_document stand-in: blocks paths named "slow*" until released or cancelled."""

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.cancelled = False

    def __call__(self, path, cancel_event=None):
        if path.name.startswith("slow"):
            self.started.set()
            while not self.release.is_set():
                if cancel_event is not None and cancel_event.wait(0.01):
                    self.cancelled = True
                    raise ConversionError("Cancelled.")
            return page_mod.OpenedDocument("<p>slow content</p>", {}, "txt")
        return page_mod.OpenedDocument(f"<p>{path.stem}</p>", {}, "txt")


def test_leave_during_open_cancels_it(qtbot, page, tmp_path, monkeypatch):
    slow = _SlowOpen()
    monkeypatch.setattr(page_mod, "open_document", slow)
    opened = []
    page.opened.connect(lambda: opened.append(1))
    page.open_path(_txt(tmp_path).with_name("slow.txt"))
    qtbot.waitUntil(slow.started.is_set, timeout=10000)
    assert page.maybe_leave()
    assert slow.cancelled and not page.busy and not page._jobs
    qtbot.wait(50)  # any late queued result would arrive now
    assert opened == [] and page.source is None and "slow" not in page.view.toPlainText()
    assert page.message.objectName() != "Error"


def test_second_open_wins(qtbot, page, tmp_path, monkeypatch):
    slow = _SlowOpen()
    monkeypatch.setattr(page_mod, "open_document", slow)
    a, b = tmp_path / "slow.txt", tmp_path / "b.txt"
    page.open_path(a)
    qtbot.waitUntil(slow.started.is_set, timeout=10000)
    texts = []
    page.opened.connect(lambda: texts.append(page.view.toPlainText()))
    with qtbot.waitSignal(page.opened, timeout=10000):
        page.open_path(b)
        assert page.message.text() == "Opening “b.txt”…"
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    assert texts == ["b"] and page.source == b and slow.cancelled


def test_drop_during_save_opens_after(qtbot, page, tmp_path, monkeypatch, media):
    p = _txt(tmp_path)
    _open(qtbot, page, p)
    started, release = threading.Event(), threading.Event()
    real = page_mod.save_document

    def slow(content, dest, fmt, cancel_event=None):
        started.set()
        release.wait(20)
        return real(content, dest, fmt, cancel_event)

    monkeypatch.setattr(page_mod, "save_document", slow)
    _type(page, "y")
    assert page.save()
    qtbot.waitUntil(started.is_set, timeout=10000)
    page.drop_paths([media / "notes.txt"])
    assert page.message.text() == "Saving… the document will open when it's done."
    assert page.source == p
    with qtbot.waitSignal(page.opened, timeout=20000):
        release.set()
    assert page.source == media / "notes.txt"
    assert p.read_text(encoding="utf-8") == "xy\n"
