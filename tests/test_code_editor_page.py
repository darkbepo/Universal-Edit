"""The desktop code editor page: tabs, byte-exact saving, questions, find & replace, shortcuts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtGui import QTextCursor  # noqa: E402

from umc.gui.codeeditor import code_editor_page as cep  # noqa: E402
from umc.gui.codeeditor.code_editor_page import CodeEditorPage  # noqa: E402


@pytest.fixture
def page(qtbot):
    p = CodeEditorPage()
    qtbot.addWidget(p)
    p.resize(900, 600)
    p.show()
    p.questions = []

    def ask(title: str, text: str, buttons: tuple[str, ...]) -> str:
        p.questions.append(text)
        return p.answers.pop(0)

    p.answers = []
    p.ask = ask
    yield p
    qtbot.waitUntil(lambda: not p.busy, timeout=10000)


def _open(qtbot, page, *paths: Path) -> None:
    assert page.open_paths(list(paths))
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)


def _type(page, text: str) -> None:
    cursor = page.view.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(text)
    page.view.setTextCursor(cursor)


def _save(qtbot, page) -> bool:
    started = page.save()
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    return started


def test_open_edit_save_keeps_bytes(qtbot, page, tmp_path):
    f = tmp_path / "a.css"
    data = b"\xef\xbb\xbfa {\r\n  color: red;\r\n}\r\n"
    f.write_bytes(data)
    _open(qtbot, page, f)
    assert page.current.language_id == "css" and page.current.indent == "  "
    assert _save(qtbot, page)
    assert f.read_bytes() == data
    _type(page, "b {}")
    assert page.current.dirty
    _save(qtbot, page)
    assert f.read_bytes() == data + b"b {}"
    assert not page.current.dirty


def test_tabs_and_duplicates(qtbot, page, tmp_path):
    a, b = tmp_path / "a.css", tmp_path / "b.js"
    a.write_text("a {}")
    b.write_text("let b = 1")
    _open(qtbot, page, a, b)
    assert page.tabs.count() == 2
    _open(qtbot, page, a)
    assert page.tabs.count() == 2 and page.current.path == a


def test_tabs_keep_the_order_the_files_came_in(qtbot, page, tmp_path):
    paths = []
    for i, size in enumerate((400_000, 10, 200_000, 5, 100_000)):  # big ones take longer
        p = tmp_path / f"f{i}.txt"
        p.write_text("x" * size)
        paths.append(p)
    _open(qtbot, page, *paths)
    assert [page.tabs.tabText(i) for i in range(5)] == [p.name for p in paths]


def test_binary_and_huge_are_refused(qtbot, page, tmp_path, monkeypatch):
    blob = tmp_path / "blob.css"
    blob.write_bytes(b"\x00\x01\x02")
    page.open_paths([blob])
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    assert page.tabs.count() == 0 and "binary" in page.message.text()
    monkeypatch.setattr(cep, "MAX_OPEN_BYTES", 10)
    big = tmp_path / "big.txt"
    big.write_text("x" * 11)
    page.open_paths([big])
    qtbot.waitUntil(lambda: not page.busy, timeout=10000)
    assert page.tabs.count() == 0 and "too big" in page.message.text()


def test_big_file_has_no_colours(qtbot, page, tmp_path, monkeypatch):
    monkeypatch.setattr(cep, "MAX_HIGHLIGHT_BYTES", 10)
    f = tmp_path / "a.css"
    f.write_text("a { color: red; }")
    _open(qtbot, page, f)
    assert page.current.highlighter.done() and not page.current.colours
    assert "Colours are off" in page.note.text()


def test_encoding_menu_marks_dirty_and_saves_converted(qtbot, page, tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("café\n".encode())
    _open(qtbot, page, f)
    page.set_encoding("windows-1252", False)
    assert page.current.dirty
    _save(qtbot, page)
    assert f.read_bytes() == "café\n".encode("cp1252")


def test_unencodable_offers_utf8(qtbot, page, tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"caf\xe9\n")  # Windows-1252
    _open(qtbot, page, f)
    _type(page, "日本")
    page.answers = ["no"]
    assert not _save(qtbot, page)
    assert f.read_bytes() == b"caf\xe9\n"
    page.answers = ["yes"]
    _save(qtbot, page)
    assert f.read_bytes() == "café\n日本".encode()


def test_changed_on_disk_asks(qtbot, page, tmp_path):
    f = tmp_path / "a.css"
    f.write_text("a {}")
    _open(qtbot, page, f)
    f.write_text("someone else's change")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    _type(page, "x")
    page.answers = ["no"]
    assert not _save(qtbot, page)
    assert f.read_text() == "someone else's change"
    assert "changed by another program" in page.questions[-1]


def test_maybe_leave_asks_for_hidden_dirty_tab(qtbot, page, tmp_path):
    a, b = tmp_path / "a.css", tmp_path / "b.css"
    a.write_text("a {}")
    b.write_text("b {}")
    _open(qtbot, page, a, b)
    page.tabs.setCurrentIndex(0)
    _type(page, "x")
    page.tabs.setCurrentIndex(1)
    page.answers = ["cancel"]
    assert not page.maybe_leave()
    page.answers = ["discard"]
    assert page.maybe_leave()
    assert a.read_text() == "a {}"


def test_maybe_leave_can_save_all(qtbot, page, tmp_path):
    a = tmp_path / "a.css"
    a.write_text("a {}")
    _open(qtbot, page, a)
    _type(page, "x")
    page.answers = ["save"]
    assert page.maybe_leave()
    assert a.read_text() == "a {}x"


def test_untitled_saves_through_save_as(qtbot, page, tmp_path, monkeypatch):
    target = tmp_path / "new.txt"
    monkeypatch.setattr(page, "choose_save_path", lambda _suggested: target)
    assert page.open_empty()
    assert page.current.title == "Untitled"
    _type(page, "hello\nworld")
    _save(qtbot, page)
    assert target.read_bytes() == b"hello\nworld"
    assert page.current.path == target and page.current.title == "new.txt"


def test_closing_a_dirty_tab_asks(qtbot, page, tmp_path):
    a = tmp_path / "a.css"
    a.write_text("a {}")
    _open(qtbot, page, a)
    _type(page, "x")
    page.answers = ["cancel"]
    assert not page.close_tab(0) and page.tabs.count() == 1
    page.answers = ["discard"]
    assert page.close_tab(0)
    assert page.tabs.count() == 1 and page.current.title == "Untitled"  # never empty


def test_find_and_replace_all_one_undo(qtbot, page, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a-b-c")
    _open(qtbot, page, f)
    bar = page.find_bar
    bar.open(replace=True)
    bar.query.setText("-")
    assert bar.count.text() == "2 matches"
    bar.replacement.setText("+")
    bar.replace_all()
    assert page.view.toPlainText() == "a+b+c" and bar.count.text() == "2 replaced"
    page.view.undo()
    assert page.view.toPlainText() == "a-b-c"
    bar.regex.setChecked(True)
    bar.query.setText("(")
    assert "isn't valid" in bar.count.text()
    bar.replace_all()
    assert page.view.toPlainText() == "a-b-c"


def test_find_next_selects_the_match(qtbot, page, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one two one")
    _open(qtbot, page, f)
    bar = page.find_bar
    bar.open(replace=False)
    bar.query.setText("one")
    bar.find_next()
    assert page.view.textCursor().selectedText() == "one"
    first = page.view.textCursor().selectionStart()
    bar.find_next()
    assert page.view.textCursor().selectionStart() != first


def test_every_shortcut_has_an_action_and_no_clash(page):
    from umc.gui.codeeditor.shortcuts import SHORTCUTS

    keys = [k for s in SHORTCUTS if s.bound for k in s.keys]
    assert len(keys) == len(set(keys))
    for s in SHORTCUTS:
        if s.bound:
            assert s.id in page.actions, s.id
