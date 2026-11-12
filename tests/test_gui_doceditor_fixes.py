"""Final review fixes, through the real editor page and real files (offscreen Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

import docx  # noqa: E402

from umc.doceditor.io import open_document  # noqa: E402
from umc.gui.doceditor.doc_editor_page import DocEditorPage  # noqa: E402

NBSP = "\xa0"


@pytest.fixture
def page(qtbot):
    pg = DocEditorPage()
    qtbot.addWidget(pg)
    pg.resize(1100, 720)
    pg.show()
    pg._ask_lose_parts = lambda: "save"
    yield pg
    pg._ask_save_changes = lambda: "discard"
    qtbot.waitUntil(lambda: not pg.busy, timeout=20000)


def gui_cycle(qtbot, page, path: Path, n: int = 3) -> list[str]:
    """Open ``path`` in the page and Save over it ``n`` times; returns the file each time."""
    seen = []
    for _ in range(n):
        with qtbot.waitSignal(page.opened, timeout=20000):
            page.open_path(path)
        page.view.document().setModified(True)  # Save writes even an unchanged document
        with qtbot.waitSignal(page.save_done, timeout=30000):
            assert page.save()
        assert page._last_save_ok, page.message.text()
        seen.append(path.read_bytes())
    return seen


def test_txt_whitespace_survives_three_saves(qtbot, page, tmp_path):
    text = "def f():\n    return 1\n\tTabbed\nA    B\n"
    p = tmp_path / "a.txt"
    p.write_text(text, encoding="utf-8")
    gui_cycle(qtbot, page, p)
    assert p.read_text(encoding="utf-8") == text


def test_docx_tab_and_spaces_survive_three_saves_in_the_editor(qtbot, page, tmp_path):
    p = tmp_path / "a.docx"
    d = docx.Document()
    d.add_paragraph("Name:\tJohn    Smith")
    d.save(p)
    gui_cycle(qtbot, page, p)
    assert [par.text for par in docx.Document(p).paragraphs] == ["Name:\tJohn    Smith"]
    assert open_document(p).html == f"<p>Name:\tJohn {NBSP * 3}Smith</p>"


def test_markdown_round_trip_through_the_editor(qtbot, page, tmp_path):
    from tests.doceditor_samples import PNG_1PX
    from tests.test_doceditor_fixes import MD_SAMPLE

    p = tmp_path / "a.md"
    p.write_bytes(MD_SAMPLE.encode("utf-8"))
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    original = open_document(p)
    saves = gui_cycle(qtbot, page, p)
    assert len(set(saves)) == 1  # the first save is already stable
    text = saves[0].decode("utf-8")
    assert text.startswith("---\ntitle: x\ntags: [a, b]\n---\n\n# Notes\n")
    assert "```\ndef f():\n    return 1\n\nprint(f())\n```" in text
    assert "> A quote that goes on." in text
    assert "<u>under</u>" in text and "**bold**" in text and "*italic*" in text
    assert open_document(p).html == original.html


def test_html_title_and_warnings_through_the_editor(qtbot, page, tmp_path, monkeypatch):
    from tests.test_doceditor_fixes import WEB_PAGE
    from umc.doceditor.types import W_PAGESTYLE, W_RAWHTML, W_WEBPICS

    p = tmp_path / "a.html"
    p.write_text(WEB_PAGE, encoding="utf-8")
    asked = []
    page._ask_lose_parts = lambda: asked.append(list(page.warnings)) or "save"
    gui_cycle(qtbot, page, p, n=2)
    assert asked == [[W_PAGESTYLE, W_WEBPICS, W_RAWHTML]]  # only the original had them
    assert "<title>T</title>" in p.read_text(encoding="utf-8")
    assert page.banner.isHidden()


def test_word_look_kept_through_the_editor(qtbot, page, tmp_path):
    from tests.test_doceditor_fixes import _borders, make_styled_docx

    p = make_styled_docx(tmp_path / "styled.docx")
    before = docx.Document(p).sections[0]
    size = (before.page_width, before.page_height)
    original = open_document(p)
    gui_cycle(qtbot, page, p, n=2)
    d = docx.Document(p)
    assert (d.sections[0].page_width, d.sections[0].page_height) == size
    assert d.styles["Normal"].font.name == "Arial"
    plain, grid = d.tables
    assert set(_borders(plain).values()) == {"none"}
    assert set(_borders(grid).values()) == {"single"}
    assert open_document(p).html == original.html
    assert page.view.document().rootFrame().childFrames()[0].format().toTableFormat().border() == 0


# ----- Important 4: pasted pictures are kept ----------------------------------------------
import base64  # noqa: E402
import io  # noqa: E402

from PIL import Image  # noqa: E402
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QImage, QTextCursor  # noqa: E402

from tests.doceditor_samples import PNG_1PX  # noqa: E402
from umc.doceditor.save import save_document  # noqa: E402
from umc.doceditor.types import OpenedDocument  # noqa: E402
from umc.gui.doceditor.text_view import DocTextView  # noqa: E402


def _blue_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (0, 0, 255)).save(buf, "PNG")
    return buf.getvalue()


def _view_with_picture(qtbot) -> DocTextView:
    v = DocTextView()
    qtbot.addWidget(v)
    v.load(OpenedDocument('<p>x<img src="img/1.png"></p>', {"img/1.png": PNG_1PX}, "docx"))
    c = v.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    v.setTextCursor(c)
    return v


def _paste(view, html=None, text=None, image=None):
    mime = QMimeData()
    if html is not None:
        mime.setHtml(html)
    if text is not None:
        mime.setText(text)
    if image is not None:
        mime.setImageData(image)
    view.insertFromMimeData(mime)


def test_paste_data_uri_picture_gets_its_own_name(qtbot, tmp_path):
    v = _view_with_picture(qtbot)
    blue = _blue_png()
    _paste(v, html=f'<p>y<img src="data:image/png;base64,{base64.b64encode(blue).decode()}"></p>')
    content = v.content()
    assert sorted(content.images.values(), key=len) == sorted([PNG_1PX, blue], key=len)
    assert len(content.images) == 2
    p = save_document(content, tmp_path / "a.docx", "docx")
    assert sorted(open_document(p).images.values(), key=len) == sorted([PNG_1PX, blue], key=len)


def test_paste_local_file_picture_is_kept(qtbot, tmp_path):
    pic = tmp_path / "clip image.png"
    pic.write_bytes(_blue_png())
    v = _view_with_picture(qtbot)
    url = QUrl.fromLocalFile(str(pic)).toString()
    _paste(v, html=f'<p>z<img src="{url}"><img src="https://example.com/r.png"></p>')
    content = v.content()
    assert len(content.images) == 2 and _blue_png() in content.images.values()
    assert "example.com" not in content.html


def test_paste_image_and_plain_text(qtbot):
    v = _view_with_picture(qtbot)
    _paste(v, image=QImage.fromData(_blue_png()))
    assert len(v.content().images) == 2
    _paste(v, text="<b>not bold</b>")
    assert "&lt;b&gt;not bold&lt;/b&gt;" in v.content().html


# ----- smaller fixes: size box, Save as, leaving ------------------------------------------
from umc.gui.doceditor.format_bar import FormatBar  # noqa: E402

MIXED = '<p><span style="font-size:10pt">a</span><span style="font-size:20pt">b</span></p>'


def test_size_box_focus_in_and_out_changes_nothing(qtbot):
    v = DocTextView()
    qtbot.addWidget(v)
    bar = FormatBar(v)
    qtbot.addWidget(bar)
    v.load(OpenedDocument(MIXED, {}, "docx"))
    c = v.textCursor()
    c.select(QTextCursor.SelectionType.Document)
    v.setTextCursor(c)
    bar.refresh()
    steps = v.document().availableUndoSteps()
    bar.size_box.lineEdit().editingFinished.emit()  # clicked in and away, nothing typed
    assert v.document().availableUndoSteps() == steps
    assert v.content().html == MIXED
    bar.size_box.setCurrentText("14")
    bar.size_box.activated.emit(0)
    bar.size_box.lineEdit().editingFinished.emit()  # focus leaves after choosing
    assert v.content().html == '<p><span style="font-size:14pt">ab</span></p>'
    v.undo()  # one step undoes the whole choice
    assert v.content().html == MIXED and not v.document().isUndoAvailable()


def _txt(tmp_path, text="x\n"):
    p = tmp_path / "a.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_save_as_added_suffix_asks_before_replacing(qtbot, page, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_path(_txt(tmp_path, "new\n"))
    existing = tmp_path / "notes.v2.txt"
    existing.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(tmp_path / "notes.v2"), "Plain text (TXT) (*.txt)"),
    )
    asked = []

    def answer(box, button):
        asked.append(box.text())
        return button

    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: answer(box, QMessageBox.StandardButton.Cancel)
    )
    assert not page.save_as()
    assert asked == ["“notes.v2.txt” already exists. Replace it?"]
    assert existing.read_text(encoding="utf-8") == "keep me"
    monkeypatch.setattr(
        QMessageBox, "exec", lambda box: answer(box, QMessageBox.StandardButton.Yes)
    )
    with qtbot.waitSignal(page.save_done, timeout=30000):
        assert page.save_as()
    assert existing.read_text(encoding="utf-8") == "new\n"


def test_nested_maybe_leave_returns_false_at_once(qtbot, page, tmp_path):
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_path(_txt(tmp_path))
    page.view.document().setModified(True)
    inner = []

    def ask():
        inner.append(page.maybe_leave())  # e.g. a second close while the question is open
        return "discard"

    page._ask_save_changes = ask
    assert page.maybe_leave() is True
    assert inner == [False]


# ===== Fix round 2 ========================================================================


def _type_at_end(page, text):
    c = page.view.textCursor()
    c.movePosition(QTextCursor.MoveOperation.End)
    c.insertText(text)
    page.view.setTextCursor(c)


@pytest.mark.parametrize("fmt", ["docx", "md"])
def test_typed_spaces_survive_saving(qtbot, page, tmp_path, fmt):
    p = tmp_path / f"a.{fmt}"
    if fmt == "docx":
        d = docx.Document()
        d.add_paragraph("x")
        d.save(p)
    else:
        p.write_text("x\n", encoding="utf-8")
    with qtbot.waitSignal(page.opened, timeout=20000):
        page.open_path(p)
    _type_at_end(page, "    typed    gap")
    with qtbot.waitSignal(page.save_done, timeout=30000):
        assert page.save()
    assert open_document(p).html == f"<p>x {NBSP * 3}typed {NBSP * 3}gap</p>"
    gui_cycle(qtbot, page, p)
    assert open_document(p).html == f"<p>x {NBSP * 3}typed {NBSP * 3}gap</p>"
    if fmt == "docx":
        assert docx.Document(p).paragraphs[0].text == "x    typed    gap"


def test_plain_text_paste_keeps_spaces(qtbot):
    v = _view_with_picture(qtbot)
    _paste(v, text="  a   b")
    assert f"> {NBSP}a {NBSP * 2}b</p>" in v.content().html  # after the picture


def test_unreadable_pasted_html_falls_back_to_text(qtbot, monkeypatch):
    from umc.core.errors import ConversionError
    from umc.gui.doceditor import text_view

    def boom(*a, **k):
        raise ConversionError("This file couldn't be read as HTML.")

    v = _view_with_picture(qtbot)
    monkeypatch.setattr(text_view, "normalise_html", boom)
    _paste(v, html="<p>bad</p>", text="plain")
    monkeypatch.undo()
    assert "plain" in v.content().html


def test_empty_table_cells_stay_one_paragraph(qtbot, page, tmp_path):
    p = tmp_path / "t.docx"
    d = docx.Document()
    t = d.add_table(2, 2)
    t.style = d.styles["Table Grid"]
    t.cell(0, 0).text = "a"
    d.save(p)
    original = open_document(p).html
    gui_cycle(qtbot, page, p)
    cells = docx.Document(p).tables[0]._cells
    assert [len(c.paragraphs) for c in cells] == [1, 1, 1, 1]
    assert open_document(p).html == original


@pytest.mark.parametrize(
    "html",
    [
        '<h2 style="page-break-before:always">H</h2><pre>code</pre>',
        "<pre>a</pre><pre>b</pre><p>x</p>",
    ],
)
def test_code_saved_as_markdown_comes_back_the_same_in_the_editor(qtbot, tmp_path, html):
    """Markdown reads a code block as one <pre> with a final line break
    (``<pre>code\n</pre>``); the editor gives that back, and the file doesn't change on later
    saves."""
    from umc.doceditor.save import save_document

    v = DocTextView()
    qtbot.addWidget(v)
    v.load(OpenedDocument(html, {}, "docx"))
    first = v.content()
    p = tmp_path / "a.md"
    save_document(first, p, "md")
    saved = p.read_bytes()
    assert first.html == html
    reopened = open_document(p)
    v.load(reopened)
    assert v.content().html == reopened.html
    save_document(v.content(), p, "md")
    assert p.read_bytes() == saved


# ----- code blocks keep their boundaries (CODE_GROUP) -------------------------------------


def _code_view(qtbot, html: str) -> DocTextView:
    v = DocTextView()
    qtbot.addWidget(v)
    v.load(OpenedDocument(html, {}, "md"))
    return v


def _cursor_at_block(v: DocTextView, n: int) -> QTextCursor:
    c = QTextCursor(v.document().findBlockByNumber(n))
    c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    return c


def test_two_code_blocks_in_a_row_stay_two_through_markdown(qtbot, tmp_path):
    p = tmp_path / "a.md"
    p.write_text("```\na\n```\n\n```\nb\nc\n```\n", encoding="utf-8")
    original = open_document(p)
    v = _code_view(qtbot, original.html)
    assert v.content().html == original.html == "<pre>a\n</pre><pre>b\nc\n</pre>"
    save_document(v.content(), p, "md")
    assert p.read_text(encoding="utf-8") == "```\na\n```\n\n```\nb\nc\n```\n"
    assert open_document(p).html == original.html


def test_enter_inside_a_code_block_keeps_the_blocks_apart(qtbot, tmp_path):
    v = _code_view(qtbot, "<pre>a\n</pre><pre>b\n</pre>")
    c = _cursor_at_block(v, 0)
    c.insertBlock()
    c.insertText("z")
    assert v.content().html == "<pre>a\nz\n</pre><pre>b\n</pre>"
    p = tmp_path / "a.md"
    save_document(v.content(), p, "md")
    assert p.read_text(encoding="utf-8") == "```\na\nz\n```\n\n```\nb\n```\n"


def test_code_lines_from_word_stay_one_block(qtbot):
    html = "<p>x</p><pre>d</pre><pre>e</pre><p>y</p>"
    assert _code_view(qtbot, html).content().html == html


def test_making_a_paragraph_code_joins_the_block_above_only(qtbot):
    from umc.gui.doceditor.formatting import set_block_style

    v = _code_view(qtbot, "<pre>a\n</pre><p>b</p><p>c</p><pre>d\n</pre>")
    set_block_style(_cursor_at_block(v, 1), "code")  # right below a code block: extends it
    set_block_style(_cursor_at_block(v, 2), "code")  # ... and again
    assert v.content().html == "<pre>a\nb\nc\n</pre><pre>d\n</pre>"
    v = _code_view(qtbot, "<p>b</p><pre>d\n</pre>")
    set_block_style(_cursor_at_block(v, 0), "code")  # no code above: a block of its own
    assert v.content().html == "<pre>b\n</pre><pre>d\n</pre>"


@pytest.mark.parametrize(
    "html",
    [
        "<p>x</p><pre></pre><p>y</p>",
        "<p>x</p><pre>a</pre><pre></pre><pre>b</pre>",
        "<pre></pre><p>x</p>",
    ],
)
def test_empty_code_lines_stay_code(qtbot, html):
    assert _code_view(qtbot, html).content().html == html


def test_code_starting_with_an_empty_line_keeps_it(qtbot):
    html = "<p>x</p><pre>\nx\n</pre><p>y</p>"
    assert _code_view(qtbot, html).content().html == html


# ----- a README saved from the editor without edits doesn't change (Task 5d) ---------------


def _view_keeps(qtbot, tmp_path, md: str) -> DocTextView:
    """Open ``md`` in the editor's view and save it back as Markdown: the view gives back
    the opened HTML, and the file doesn't change."""
    p = tmp_path / "a.md"
    p.write_text(md, encoding="utf-8")
    opened = open_document(p)
    v = DocTextView()
    qtbot.addWidget(v)
    v.load(opened)
    assert v.content().html == opened.html
    save_document(v.content(), p, "md")
    assert p.read_text(encoding="utf-8") == md
    return v


@pytest.mark.parametrize(
    "md",
    [
        "```\na\nb\n```\n",  # one Markdown code block of two lines
        "```\na\n```\n\n```\nb\nc\n```\n",  # two in a row
        "```\n\n```\n",  # a code block of one empty line
        "x\n\n```\n\na\n```\n",  # starting with an empty line
    ],
)
def test_markdown_code_blocks_come_back_whole(qtbot, tmp_path, md):
    _view_keeps(qtbot, tmp_path, md)


@pytest.mark.parametrize(
    "md",
    [
        "[](https://u)\n",  # a badge whose web picture was dropped
        "[](https://u)[](https://u)\n",  # two of them, to the same address
        "# Title [](https://u)\n",
        "a [](https://u)b [](https://v)\n",
    ],
)
def test_empty_links_are_kept(qtbot, tmp_path, md):
    v = _view_keeps(qtbot, tmp_path, md)
    assert "⁣" not in v.content().plain_text


@pytest.mark.parametrize(
    "md",
    [
        "**[x](https://u)**\n",  # bold around a link ...
        "[**x**](https://u)\n",  # ... and inside one
        "***x***\n",  # italic around bold
        "*zero or **one***\n",
        "**Apply now at [x](https://u)**\n",
        "*a [b](https://u)*\n",
    ],
)
def test_bold_italic_and_links_keep_their_nesting(qtbot, tmp_path, md):
    _view_keeps(qtbot, tmp_path, md)


def test_space_after_no_break_spaces_is_kept(qtbot, tmp_path):
    _view_keeps(qtbot, tmp_path, "a\xa0\xa0 b [x](https://u) \xa0\xa0•\xa0\xa0 [y](https://v)\n")


def test_two_quotes_in_a_row_stay_two(qtbot, tmp_path):
    _view_keeps(qtbot, tmp_path, "- a\n\n> b\n\n<!-- -->\n\n> c\n")


def test_ragged_table_gets_no_empty_cells(qtbot, tmp_path):
    md = "<table><tr><td><p>a</p></td><td><p>b</p></td></tr><tr><td><p>c</p></td></tr></table>\n"
    v = _view_keeps(qtbot, tmp_path, md)
    # a cell the user types into is kept
    table = QTextCursor(v.document().findBlockByNumber(1)).currentTable()
    QTextCursor(table.cellAt(1, 1).firstCursorPosition()).insertText("d")
    assert v.content().html.endswith("<td><p>c</p></td><td><p>d</p></td></tr></table>")


def test_document_of_one_empty_code_line_is_kept(qtbot):
    v = _code_view(qtbot, "<pre></pre>")
    assert v.content().html == "<pre></pre>"


@pytest.mark.parametrize(
    ("start", "line", "pasted", "html"),
    [
        ("<pre>a\nb\n</pre>", 0, "<pre>y</pre><pre>z</pre>", "<pre>ay\nz\nb\n</pre>"),
        (
            "<pre>a</pre><pre>b</pre>",
            0,
            "<pre>y</pre><pre>z</pre>",
            "<pre>ay</pre><pre>z</pre><pre>b</pre>",
        ),
        ("<pre>a\nb\n</pre>", 0, "<p>q</p><pre>y</pre>", "<pre>aq\ny\nb\n</pre>"),
    ],
)
def test_pasted_code_lines_join_the_code_block(qtbot, start, line, pasted, html):
    v = _code_view(qtbot, start)
    v.setTextCursor(_cursor_at_block(v, line))
    mime = QMimeData()
    mime.setHtml(pasted)
    v.insertFromMimeData(mime)
    assert v.content().html == html


@pytest.mark.parametrize(
    "md",
    [
        "[![](a_files/1.png)](https://u)\n",  # a linked picture
        "[a ![](a_files/1.png) b](https://u)\n",
        "**![](a_files/1.png) x**\n",
    ],
)
def test_linked_pictures_keep_their_link(qtbot, tmp_path, md):
    (tmp_path / "a_files").mkdir()
    (tmp_path / "a_files" / "1.png").write_bytes(PNG_1PX)  # where saving puts pictures
    _view_keeps(qtbot, tmp_path, md)


@pytest.mark.parametrize(
    "html",
    [
        "<table><tr><td><ol><li></li><li>x</li></ol></td></tr></table>",  # from a README
        "<ol><li></li><li>x</li></ol>",
        "<ul><li><ul><li>x</li></ul></li></ul>",  # an item that holds only a nested list
        "<p>a</p><ul><li><ul><li>x</li></ul></li></ul>",
        "<ol><li></li></ol>",
        "<blockquote><p></p></blockquote>",
    ],
)
def test_empty_list_items_and_quotes_stay(qtbot, html):
    assert _code_view(qtbot, html).content().html == html


def test_nesting_tool_tips_are_never_shown(qtbot):
    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QToolTip

    v = _code_view(qtbot, "<p><b>bold bold bold bold</b></p>")
    v.resize(900, 600)
    v.show()
    pos = v.cursorRect(QTextCursor(v.document().begin())).center() + QPoint(30, 0)
    assert v.cursorForPosition(pos).charFormat().toolTip()  # the nesting is recorded ...
    QToolTip.hideText()
    QApplication.sendEvent(
        v.viewport(), QHelpEvent(QEvent.Type.ToolTip, pos, v.viewport().mapToGlobal(pos))
    )
    assert not QToolTip.isVisible()  # ... but not shown


def test_copied_text_has_no_link_placeholder(qtbot, tmp_path):
    v = _view_keeps(qtbot, tmp_path, "a [](https://u)b\n")
    v.selectAll()
    assert v.createMimeDataFromSelection().text() == "a b"


@pytest.mark.parametrize("pasted", ["<ul><li></li><li>x</li></ul>", "<pre></pre><pre>y</pre>"])
def test_pasted_empty_items_leave_no_placeholder(qtbot, pasted):
    v = _code_view(qtbot, "<p>a</p><p>b</p>")
    v.setTextCursor(_cursor_at_block(v, 1))
    mime = QMimeData()
    mime.setHtml(pasted)
    v.insertFromMimeData(mime)
    html = v.content().html
    assert "​" not in html and "⁠" not in html, html
