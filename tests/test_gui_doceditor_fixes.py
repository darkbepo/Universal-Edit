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
