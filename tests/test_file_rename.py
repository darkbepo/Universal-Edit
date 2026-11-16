"""Properties step 6 (desktop only): the file's name and "modified" date."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from umc.properties import fileinfo as F


def _file(path: Path, data: bytes = b"hello") -> Path:
    path.write_bytes(data)
    return path


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("holiday.txt", "holiday.txt"),
        ("  holiday.txt  ", "holiday.txt"),
        ("holiday", "holiday.txt"),  # the extension is added back
        ("Holiday.TXT", "Holiday.TXT"),  # another spelling of the same extension is fine
        ("my notes v2.txt", "my notes v2.txt"),
    ],
)
def test_clean_name_accepts(typed, expected):
    assert F.clean_name(typed, Path("/x/notes.txt")) == expected


@pytest.mark.parametrize(
    ("typed", "words"),
    [
        ("", "Type a name"),
        ("a/b.txt", "can't contain /"),
        ('a"b?.txt', "can't contain \" ?"),
        ("a\nb.txt", "a line break"),
        ("notes.", "end with a dot"),
        ("notes.pdf", "Keep the file's extension (.txt)"),
        ("con.txt", "Windows doesn't allow"),
        ("LPT1", "Windows doesn't allow"),
        ("x" * 300 + ".txt", "too long"),
    ],
)
def test_clean_name_refuses(typed, words):
    with pytest.raises(F.FileError, match=None) as exc:
        F.clean_name(typed, Path("/x/notes.txt"))
    assert words in str(exc.value)


def test_names_without_an_extension():
    assert F.clean_name("readme", Path("/x/notes")) == "readme"
    assert F.suffix_of(".gitignore") == "" and F.suffix_of("a.tar.gz") == ".gz"
    with pytest.raises(F.FileError, match="no extension"):
        F.clean_name("readme.md", Path("/x/notes"))


def test_parse_modified():
    assert F.parse_modified("2024-02-29 08:05") == datetime(2024, 2, 29, 8, 5)
    assert F.parse_modified(" 2024-03-01 ") == datetime(2024, 3, 1)
    for bad in ("yesterday", "2023-02-29", "2024-01-01 25:00", "1970-01-01", "2150-01-01"):
        with pytest.raises(F.FileError):
            F.parse_modified(bad)


def test_apply_renames_and_sets_the_date(tmp_path):
    src = _file(tmp_path / "notes.txt")
    atime = src.stat().st_atime_ns
    when = datetime(2021, 6, 1, 12, 30, 15)
    out = F.apply(src, "plans.txt", when)
    assert out == tmp_path / "plans.txt" and not src.exists()
    assert out.stat().st_atime_ns == atime  # (checked before reading the file updates it)
    assert out.stat().st_mtime == when.timestamp()
    assert out.read_bytes() == b"hello"
    assert F.shown_modified(out) == "2021-06-01 12:30:15"
    assert F.apply(out, None, None) == out  # nothing asked, nothing done


def test_apply_never_replaces_another_file(tmp_path):
    src = _file(tmp_path / "a.txt", b"a")
    other = _file(tmp_path / "b.txt", b"b")
    with pytest.raises(F.FileError, match="already a file called “b.txt”"):
        F.apply(src, "b.txt")
    assert src.read_bytes() == b"a" and other.read_bytes() == b"b"


def test_move_never_replaces_a_file_made_meanwhile(tmp_path):
    src = _file(tmp_path / "a.txt", b"a")
    late = _file(tmp_path / "b.txt", b"b")  # appeared after the check
    with pytest.raises(FileExistsError):
        F._move(src, late)
    assert src.read_bytes() == b"a" and late.read_bytes() == b"b"


def test_a_failed_rename_leaves_name_and_date_as_they_were(tmp_path, monkeypatch):
    src = _file(tmp_path / "a.txt")
    before = src.stat().st_mtime_ns

    def refuse(path, target):
        raise PermissionError("held open")

    monkeypatch.setattr(F, "_move", refuse)
    with pytest.raises(F.FileError, match="open in another program"):
        F.apply(src, "b.txt", datetime(2020, 1, 1))
    assert src.exists() and not (tmp_path / "b.txt").exists()
    assert src.stat().st_mtime_ns == before


def test_apply_reports_a_missing_file(tmp_path):
    with pytest.raises(F.FileError, match="isn't there any more"):
        F.apply(tmp_path / "gone.txt", "new.txt")


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs a read-only folder")
def test_apply_reports_a_locked_folder(tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    src = _file(folder / "a.txt")
    folder.chmod(0o500)
    try:
        with pytest.raises(F.FileError, match="open in another program"):
            F.apply(src, "b.txt")
    finally:
        folder.chmod(0o700)


# ---------------------------------------------------------------- the Properties card
@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    monkeypatch.setenv("UMC_REDUCE_MOTION", "1")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _card(qtbot, w):
    panel = w.properties_panel
    qtbot.waitUntil(
        lambda: panel.status.isHidden() and not panel.file_form.isHidden(), timeout=10000
    )
    return panel.file_form


def _type(box, text):
    box.setText(text)
    box.textEdited.emit(text)


def test_card_renames_and_redates_and_the_queue_follows(qtbot, window, tmp_path):
    a = _file(tmp_path / "a.txt")
    b = _file(tmp_path / "b.txt")
    window.add_files([str(a), str(b)])
    window.enter_properties()
    form = _card(qtbot, window)
    assert form.name_box.text() == "a.txt"
    assert form.modified_box.text() == F.shown_modified(a)
    assert not form.save_button.isEnabled()
    _type(form.name_box, "first")
    _type(form.modified_box, "2020-01-02 03:04")
    assert form.save_button.isEnabled()
    form.save_button.click()
    new = tmp_path / "first.txt"
    qtbot.waitUntil(lambda: "renamed" in form.note.text(), timeout=10000)
    assert form.note.text() == "Saved — renamed to “first.txt” and date changed."
    assert new.exists() and not a.exists()
    assert new.stat().st_mtime == datetime(2020, 1, 2, 3, 4).timestamp()
    assert [f.path for f in window.files] == [new, b]
    assert window.file_list.paths() == [str(new), str(b)]
    panel = window.properties_panel
    assert panel.picker.itemText(0) == "first.txt"
    assert panel.rows()["File"]["Name"] == "first.txt"
    assert form.name_box.text() == "first.txt" and not form.save_button.isEnabled()


def test_card_shows_problems_and_changes_nothing(qtbot, window, tmp_path):
    a = _file(tmp_path / "a.txt")
    _file(tmp_path / "b.txt")
    window.add_files([str(a)])
    window.enter_properties()
    form = _card(qtbot, window)
    before = a.stat().st_mtime
    _type(form.name_box, "b.txt")
    form.save_button.click()
    assert "already a file" in form.note.text() and form.note.objectName() == "Error"
    _type(form.name_box, "a.txt")
    _type(form.modified_box, "soon")
    form.save_button.click()
    assert "Write the date like" in form.note.text()
    assert a.exists() and a.stat().st_mtime == before
    assert [f.path for f in window.files] == [a]
