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
