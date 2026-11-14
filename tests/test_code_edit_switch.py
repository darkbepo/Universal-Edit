"""Which editor the Edit switch opens for code and text files, and the ▾ second choice."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")


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
    if w.code_editor is not None:
        w.code_editor.wait_idle()


def _files(tmp_path, *names: str) -> list[str]:
    out = []
    for name in names:
        p = tmp_path / name
        if name.endswith(".png"):
            from PIL import Image

            Image.new("RGB", (4, 4)).save(p)
        elif name.endswith(".docx"):
            import docx

            docx.Document().save(p)
        else:
            p.write_text("x = 1\n", encoding="utf-8")
        out.append(str(p))
    return out


def test_a_css_file_opens_in_the_code_editor(qtbot, window, tmp_path):
    window.add_files(_files(tmp_path, "a.css"))
    assert window.edit_choices() == ["code"] and window.edit_kind() == "code"
    assert window.edit_mode.isEnabled() and window.edit_more.isHidden()
    window.enter_edit()
    page = window.code_editor
    assert window.stack.currentWidget() is page
    qtbot.waitUntil(lambda: page.tabs.count() == 1 and not page.busy, timeout=10000)
    assert page.current.path.name == "a.css"


def test_two_code_files_open_as_two_tabs(qtbot, window, tmp_path):
    window.add_files(_files(tmp_path, "a.css", "b.js"))
    assert window.edit_choices() == ["code"]
    window.enter_edit()
    page = window.code_editor
    qtbot.waitUntil(lambda: page.tabs.count() == 2 and not page.busy, timeout=10000)


@pytest.mark.parametrize(
    ("names", "choices"),
    [
        (("a.txt",), ["document", "code"]),
        (("a.md",), ["document", "code"]),
        (("a.html",), ["code", "document"]),
        (("a.json",), ["code"]),
        (("a.docx",), ["document"]),
        (("a.css", "b.png"), []),
    ],
)
def test_choices(window, tmp_path, names, choices):
    window.add_files(_files(tmp_path, *names))
    assert window.edit_choices() == choices
    assert window.edit_more.isVisible() == (len(choices) > 1)


def test_the_second_choice_opens_a_txt_as_code(qtbot, window, tmp_path):
    window.add_files(_files(tmp_path, "notes.txt"))
    window.enter_edit("code")
    page = window.code_editor
    assert window.stack.currentWidget() is page
    qtbot.waitUntil(lambda: page.tabs.count() == 1 and not page.busy, timeout=10000)
    assert page.current.path.name == "notes.txt"
