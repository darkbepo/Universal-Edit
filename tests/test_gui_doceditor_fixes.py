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
