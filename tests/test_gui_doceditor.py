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
