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
