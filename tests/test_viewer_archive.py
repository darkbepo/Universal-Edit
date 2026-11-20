"""Archives in the viewer: listing, picking / extracting part of one, changing it in place,
the folder model, and the archive page of the viewer window."""

import shutil
import threading
from datetime import datetime
from pathlib import Path

import pytest

from umc.tools import archive as engine
from umc.viewer import archive as model
from umc.viewer import browse
from umc.viewer import files as vf

NOP = lambda _f: None  # noqa: E731


def _cancel() -> threading.Event:
    return threading.Event()


@pytest.fixture
def tree_src(tmp_path) -> Path:
    src = tmp_path / "src"
    (src / "photos" / "2024").mkdir(parents=True)
    (src / "photos" / "2024" / "a.txt").write_text("a")
    (src / "photos" / "b.txt").write_text("bb")
    (src / "top.txt").write_text("top")
    return src


def _make(tree_src: Path, dest: Path, ext: str, password: str = "") -> Path:
    files = [(p, p.relative_to(tree_src).as_posix()) for p in tree_src.rglob("*") if p.is_file()]
    engine.create(files, dest, ext, "normal", password, bool(password), NOP, _cancel())
    return dest


EXTS = ["zip", "7z", "tar.gz", "tar"]


# ------------------------------------------------------------------ engine


@pytest.mark.parametrize("ext", EXTS)
def test_entries_list_files(tree_src, tmp_path, ext):
    a = _make(tree_src, tmp_path / f"x.{ext}", ext)
    contents = engine.entries(a, ext)
    files = sorted(e.name for e in contents.entries if not e.is_dir)
    assert files == ["photos/2024/a.txt", "photos/b.txt", "top.txt"]
    sizes = {e.name: e.size for e in contents.entries}
    assert sizes["top.txt"] == 3
    assert not contents.encrypted


@pytest.mark.parametrize("ext", EXTS)
def test_extract_part_with_strip_and_unique(tree_src, tmp_path, ext):
    a = _make(tree_src, tmp_path / f"x.{ext}", ext)
    out = tmp_path / "out"
    args = (a, ext, out, "", NOP, _cancel())
    written = engine.extract(*args, only=["photos"], strip="photos")
    assert sorted(p.relative_to(out).as_posix() for p in written) == ["2024/a.txt", "b.txt"]
    again = engine.extract(*args, only=["photos/b.txt"], strip="photos", unique=True)
    assert [p.name for p in again] == ["b (1).txt"]
    assert (out / "b.txt").read_text() == "bb"
    assert not [p for p in out.iterdir() if p.name.startswith(".umc-extract")]


@pytest.mark.parametrize("ext", ["zip", "7z"])
def test_password_needed_and_kept_on_rewrite(tree_src, tmp_path, ext):
    a = _make(tree_src, tmp_path / f"x.{ext}", ext, password="pw")
    if ext == "7z":  # names hidden too
        with pytest.raises(engine.PasswordNeeded):
            engine.entries(a, ext)
        with pytest.raises(engine.PasswordNeeded):
            engine.entries(a, ext, "wrong")
    contents = engine.entries(a, ext, "pw")
    assert contents.encrypted
    with pytest.raises(engine.PasswordNeeded):
        engine.extract(a, ext, tmp_path / "o1", "", NOP, _cancel(), only=["top.txt"])
    new = tmp_path / "new.txt"
    new.write_text("n")
    with pytest.raises(engine.PasswordNeeded):
        engine.rewrite(a, ext, "", [(new, "new.txt")], [], NOP, _cancel())
    engine.rewrite(a, ext, "pw", [(new, "new.txt")], [], NOP, _cancel())
    after = engine.entries(a, ext, "pw")
    assert after.encrypted
    assert "new.txt" in {e.name for e in after.entries}


@pytest.mark.parametrize("ext", EXTS)
def test_rewrite_adds_replaces_and_removes(tree_src, tmp_path, ext):
    a = _make(tree_src, tmp_path / f"x.{ext}", ext)
    new = tmp_path / "top.txt"
    new.write_text("replaced")
    engine.rewrite(a, ext, "", [(new, "top.txt")], ["photos/2024"], NOP, _cancel())
    names = sorted(e.name for e in engine.entries(a, ext).entries if not e.is_dir)
    assert names == ["photos/b.txt", "top.txt"]
    out = tmp_path / "o"
    engine.extract(a, ext, out, "", NOP, _cancel())
    assert (out / "top.txt").read_text() == "replaced"
    assert not [p for p in a.parent.iterdir() if p.name.startswith(".umc-tmp")]


def test_rewrite_cancel_keeps_the_original(tree_src, tmp_path):
    a = _make(tree_src, tmp_path / "x.zip", "zip")
    before = a.read_bytes()
    cancel = _cancel()
    cancel.set()
    with pytest.raises(engine.ConversionCancelled):
        engine.rewrite(a, "zip", "", [], ["top.txt"], NOP, cancel)
    assert a.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src", "x.zip"]


def test_rar_is_read_only(tmp_path):
    assert not engine.can_change("rar")
    with pytest.raises(engine.ConversionError):
        engine.rewrite(tmp_path / "x.rar", "rar", "", [], [], NOP, _cancel())


def test_parse_tv_listing():
    now = datetime(2026, 11, 20, 12, 0)
    text = (
        "drwxr-xr-x  0 0      0           0 Nov 20 10:40 photos/\n"
        "-rw-r--r--  0 user   staff    2000 Nov 20 10:40 photos/my file.txt\n"
        "-rw-r--r--  0 0      0          12 Dec 24  2019 old.txt\n"
        "-rw-r--r--  0 0      0          12 Dec 24 09:00 last-year.txt\n"
        "lrwxrwxrwx  0 0      0           0 Nov 20 10:40 link -> photos\n"
    )
    entries = engine.parse_tv_listing(text, now)
    assert [e.name for e in entries] == ["photos", "photos/my file.txt", "old.txt", "last-year.txt"]
    assert entries[0].is_dir and entries[1].size == 2000
    assert entries[2].modified == datetime(2019, 12, 24)
    assert entries[3].modified.year == 2025


@pytest.mark.skipif(engine.bsdtar_path() is None, reason="needs bsdtar")
def test_bsdtar_reads_and_extracts_part(tree_src, tmp_path):
    a = _make(tree_src, tmp_path / "x.zip", "zip")  # bsdtar reads ZIP the way it reads RAR
    names = sorted(e.name for e in engine._list_bsdtar(a).entries if not e.is_dir)
    assert names == ["photos/2024/a.txt", "photos/b.txt", "top.txt"]
    out = tmp_path / "o"
    out.mkdir()
    pick = engine._picker(["photos/2024"], "photos")
    written = engine._extract_bsdtar(a, out, "", _cancel(), pick, True, ["photos/2024"])
    assert [p.relative_to(out).as_posix() for p in written] == ["2024/a.txt"]


# ------------------------------------------------------------------ the folder model


def _tree(*names: str, dirs: tuple[str, ...] = ()) -> model.Tree:
    entries = [engine.Entry(n, size=len(n)) for n in names]
    entries += [engine.Entry(d, is_dir=True) for d in dirs]
    return model.Tree(engine.Contents(entries))


def test_tree_makes_up_folders_and_sorts():
    t = _tree("b10.txt", "b2.txt", "z/deep/x.png", "a.txt", dirs=("empty",))
    assert [n.name for n in t.listing("")] == ["empty", "z", "a.txt", "b2.txt", "b10.txt"]
    assert t.is_folder("z/deep") and not t.is_folder("a.txt")
    assert t.file_count() == 4
    assert t.find("z").size == len("z/deep/x.png")
    assert t.covered(["z", "a.txt"]) == ["z/deep/x.png", "a.txt"]
    assert t.listing("nope") == []


def test_breadcrumb_and_parents():
    assert model.breadcrumb("x.zip", "a/b") == [("x.zip", ""), ("a", "a"), ("b", "a/b")]
    assert model.parent_of("a/b") == "a" and model.parent_of("a") == ""
    assert model.join("", "f") == "f" and model.join("a", "f") == "a/f"


def test_extract_here_folder(tmp_path):
    a = tmp_path / "photos.tar.gz"
    assert model.archive_stem(a) == "photos"
    assert model.extract_here_folder(a) == tmp_path / "photos"
    (tmp_path / "photos").mkdir()
    assert model.extract_here_folder(a) == tmp_path / "photos (1)"


def test_files_to_add_and_clashes(tmp_path):
    (tmp_path / "dir" / "sub").mkdir(parents=True)
    (tmp_path / "dir" / "sub" / "x.txt").write_text("x")
    (tmp_path / "one.txt").write_text("1")
    adds = model.files_to_add("photos", [tmp_path / "dir", tmp_path / "one.txt"])
    assert [n for _p, n in adds] == ["photos/dir/sub/x.txt", "photos/one.txt"]
    t = _tree("photos/one.txt", "photos/one (1).txt", "f")
    assert model.clashes(t, adds) == ["photos/one.txt"]
    assert [n for _p, n in model.keep_both(t, adds)][1] == "photos/one (2).txt"
    assert model.folder_clash(t, [(tmp_path / "one.txt", "photos")]) == "photos"
    assert model.folder_clash(t, [(tmp_path / "one.txt", "f/x")]) == "f/x"
    assert model.folder_clash(t, adds) is None


def test_archives_are_a_view_kind(tmp_path):
    assert vf.kind_of_path(Path("x.zip")) == vf.ARCHIVE
    assert vf.kind_of_path(Path("x.tar.gz")) == vf.ARCHIVE
    assert browse.category_of(Path("x.7z")) == "archives"
    assert model.kind_of_entry("a/b/c.png") == vf.IMAGE


# ------------------------------------------------------------------ the viewer window

pytest.importorskip("pytestqt")


@pytest.fixture
def viewer(qtbot):
    pytest.importorskip("PySide6.QtMultimedia")
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    from umc.gui.viewer.viewer_window import ViewerWindow

    w = ViewerWindow()
    qtbot.addWidget(w)
    w.answers = []  # what ask() answers, in order (else the last button)
    w.asked = []

    def ask(text, buttons):
        w.asked.append(text)
        return w.answers.pop(0) if w.answers else buttons[-1]

    w.ask = ask
    w.resize(1100, 700)
    w.show()
    yield w
    w.close()


@pytest.fixture
def zipped(tmp_path, media, tree_src):
    shutil.copy(media / "img0.png", tree_src / "photos" / "pic.png")
    folder = tmp_path / "here"
    folder.mkdir()
    shutil.copy(media / "notes.txt", folder / "notes.txt")
    return _make(tree_src, folder / "bundle.zip", "zip")


def _listed(qtbot, w):
    qtbot.waitUntil(lambda: w.archive.tree is not None and not w.archive.busy(), timeout=8000)
    lst = w.archive.list
    return [lst.topLevelItem(i).text(0) for i in range(lst.topLevelItemCount())]


def _idle(qtbot, w):
    qtbot.waitUntil(lambda: not w.archive.busy(), timeout=10000)


def _select(w, *names):
    lst = w.archive.list
    lst.clearSelection()
    for i in range(lst.topLevelItemCount()):
        item = lst.topLevelItem(i)
        if item.text(0) in names:
            item.setSelected(True)


def test_archive_shows_its_contents(qtbot, viewer, zipped):
    viewer.open_path(zipped)
    assert viewer.stack.currentWidget() is viewer.archive
    assert _listed(qtbot, viewer) == ["photos", "top.txt"]
    assert "Archive" in viewer.detail_label.text() and "4 files" in viewer.detail_label.text()
    assert [p.name for p in viewer.items] == ["notes.txt", "bundle.zip"]  # in the side list
    assert not viewer.edit_button.isEnabled() and viewer.convert_button.isEnabled()
    viewer.archive.enter("photos")
    assert _listed(qtbot, viewer) == ["2024", "b.txt", "pic.png"]
    assert viewer.up_button.isEnabled()
    viewer.go_up()  # Backspace / Alt+↑: up inside the archive first
    assert viewer.archive.folder == ""
    viewer.step(-1)  # ← goes on to the folder's other files
    assert viewer.current.name == "notes.txt"


def test_preview_a_file_inside_and_back(qtbot, viewer, zipped):
    viewer.open_path(zipped)
    _listed(qtbot, viewer)
    viewer.archive.enter("photos")
    viewer.archive.open_entry("photos/pic.png")
    qtbot.waitUntil(lambda: viewer.stack.currentWidget() is viewer.canvas, timeout=8000)
    assert viewer.inner == "photos/pic.png" and viewer.inner_bar.isVisible()
    assert viewer.name_label.text() == "pic.png"
    assert not viewer.convert_button.isEnabled()
    assert viewer.back_button.isEnabled()
    viewer.go_back()
    assert viewer.inner is None and viewer.stack.currentWidget() is viewer.archive
    assert viewer.archive.folder == "photos"  # where it was
    assert viewer.name_label.text() == "bundle.zip"
    viewer.archive.open_entry("top.txt")  # text: read-only here
    qtbot.waitUntil(lambda: viewer.stack.currentWidget() is viewer.text, timeout=8000)
    assert viewer.text.isReadOnly() and viewer.text.toPlainText() == "top"


def test_extract_here_and_to(qtbot, viewer, zipped, tmp_path):
    viewer.open_path(zipped)
    _listed(qtbot, viewer)
    viewer.archive.extract_here()
    _idle(qtbot, viewer)
    out = zipped.parent / "bundle"
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()) == [
        "photos/2024/a.txt",
        "photos/b.txt",
        "photos/pic.png",
        "top.txt",
    ]
    assert "Extracted 4 files" in viewer.archive.note.text()
    viewer.archive.enter("photos")
    _select(viewer, "b.txt", "2024")
    target = tmp_path / "chosen"
    target.mkdir()
    (target / "b.txt").write_text("mine")
    viewer.archive.choose_folder = lambda _start: target
    viewer.archive.extract_to()
    _idle(qtbot, viewer)
    assert sorted(p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()) == [
        "2024/a.txt",
        "b (1).txt",
        "b.txt",
    ]
    assert (target / "b.txt").read_text() == "mine"  # never replaced
    viewer.archive.show_button.click()  # "Show": the viewer goes there
    assert viewer.location == target


def test_drag_out_unpacks_the_selection(qtbot, viewer, zipped):
    viewer.open_path(zipped)
    _listed(qtbot, viewer)
    files = viewer.archive.drag_files(["photos", "top.txt"])
    assert sorted(p.name for p in files) == ["photos", "top.txt"]
    assert (files[0] if files[0].is_dir() else files[1]).joinpath("2024", "a.txt").is_file()
    assert all(p.is_relative_to(viewer._preview_dir) for p in files)


def test_drop_in_add_and_delete(qtbot, viewer, zipped, tmp_path):
    viewer.open_path(zipped)
    _listed(qtbot, viewer)
    viewer.archive.enter("photos")
    dropped = tmp_path / "new.txt"
    dropped.write_text("new")
    viewer.answers = ["Add"]
    viewer.archive.add_files([dropped])
    _idle(qtbot, viewer)
    assert "new.txt" in _listed(qtbot, viewer)
    assert viewer.archive.folder == "photos"
    assert "photos/new.txt" in engine.list_names(zipped, "zip")
    # the same name again: keep both
    viewer.answers = ["Keep both"]
    viewer.archive.add_files([dropped])
    _idle(qtbot, viewer)
    assert "new (1).txt" in _listed(qtbot, viewer)
    # delete two items
    _select(viewer, "new.txt", "2024")
    viewer.answers = ["Delete"]
    viewer.archive.delete_selected()
    _idle(qtbot, viewer)
    names = engine.list_names(zipped, "zip")
    assert "photos/new.txt" not in names and "photos/2024/a.txt" not in names
    assert "photos/new (1).txt" in names
    # Cancel changes nothing
    before = zipped.read_bytes()
    _select(viewer, "b.txt")
    viewer.answers = ["Cancel"]
    viewer.archive.delete_selected()
    assert zipped.read_bytes() == before


def test_drop_event_into_a_folder_row(qtbot, viewer, zipped, tmp_path):
    from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    viewer.open_path(zipped)
    _listed(qtbot, viewer)
    dropped = tmp_path / "into.txt"
    dropped.write_text("x")
    lst = viewer.archive.list
    row = lst.visualItemRect(lst.topLevelItem(0)).center()  # the "photos" folder
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dropped))])
    event = QDropEvent(
        QPointF(row),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    viewer.answers = ["Add"]
    lst.dropEvent(event)
    _idle(qtbot, viewer)
    assert "photos/into.txt" in engine.list_names(zipped, "zip")


def test_password_is_asked_once(qtbot, viewer, tree_src, tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    a = _make(tree_src, folder / "secret.7z", "7z", password="pw")
    answers = ["nope", "pw"]
    asked = []

    def ask_password(text):
        asked.append(text)
        return answers.pop(0)

    viewer.archive.ask_password = ask_password
    viewer.open_path(a)
    assert _listed(qtbot, viewer) == ["photos", "top.txt"]
    assert len(asked) == 2 and "didn't work" in asked[1]
    assert "password-protected" in viewer.detail_label.text()
    viewer.archive.extract_here()  # no second question
    _idle(qtbot, viewer)
    assert (folder / "secret" / "top.txt").read_text() == "top"
    assert len(asked) == 2


def test_rar_is_read_only_in_the_view(qtbot, viewer, zipped, monkeypatch):
    rar = zipped.with_name("old.rar")
    shutil.copy(zipped, rar)
    if engine.bsdtar_path() is None:
        pytest.skip("needs bsdtar")
    viewer.open_path(rar)
    assert _listed(qtbot, viewer) == ["photos", "top.txt"]
    assert not viewer.archive.add_button.isEnabled()
    assert "read only" in viewer.detail_label.text()
    viewer.archive.add_files([zipped])
    assert "only be read" in viewer.archive.note.text()


# ------------------------------------------------------------------ review follow-ups


def test_wrong_7z_password_asks_again(tree_src, tmp_path):
    a = tmp_path / "s.7z"  # names readable, contents locked
    files = [(p, p.relative_to(tree_src).as_posix()) for p in tree_src.rglob("*") if p.is_file()]
    engine.create(files, a, "7z", "normal", "pw", False, NOP, _cancel())
    assert engine.entries(a, "7z").encrypted
    with pytest.raises(engine.PasswordNeeded):
        engine.extract(a, "7z", tmp_path / "o", "wrong", NOP, _cancel())
    engine.extract(a, "7z", tmp_path / "o", "pw", NOP, _cancel(), only=["top.txt"])
    assert (tmp_path / "o" / "top.txt").read_text() == "top"


@pytest.mark.parametrize("ext", ["zip", "tar.gz"])
def test_rewrite_keeps_links_modes_and_empty_folders(tmp_path, ext):
    import os
    import stat
    import tarfile
    import zipfile

    a = tmp_path / f"x.{ext}"
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    other = tmp_path / "other.txt"
    other.write_text("o")
    if ext == "zip":
        with zipfile.ZipFile(a, "w") as z:
            z.write(script, "run.sh")
            z.write(other, "other.txt")
            link = zipfile.ZipInfo("ln")
            link.external_attr = 0o120777 << 16
            z.writestr(link, "run.sh")
            z.writestr(zipfile.ZipInfo("empty/"), "")
    else:
        os.symlink("run.sh", tmp_path / "ln")
        (tmp_path / "empty").mkdir()
        with tarfile.open(a, "w:gz") as t:
            t.add(script, "run.sh")
            t.add(other, "other.txt")
            t.add(tmp_path / "ln", "ln")
            t.add(tmp_path / "empty", "empty")
    engine.rewrite(a, ext, "", [], ["other.txt"], NOP, _cancel())
    if ext == "zip":
        with zipfile.ZipFile(a) as z:
            info = {i.filename: i.external_attr >> 16 for i in z.infolist()}
        assert sorted(info) == ["empty/", "ln", "run.sh"]
        assert stat.S_ISLNK(info["ln"]) and info["run.sh"] & 0o111
    else:
        with tarfile.open(a) as t:
            members = {m.name: m for m in t.getmembers()}
        assert sorted(members) == ["empty", "ln", "run.sh"]
        assert members["ln"].issym() and members["run.sh"].mode & 0o111
        assert members["empty"].isdir()


def test_7z_with_links_is_not_changed(tmp_path):
    import os

    import py7zr

    (tmp_path / "f.txt").write_text("f")
    os.symlink("f.txt", tmp_path / "ln")
    a = tmp_path / "l.7z"
    with py7zr.SevenZipFile(a, "w") as z:
        z.write(tmp_path / "f.txt", "f.txt")
        z.write(tmp_path / "ln", "ln")
    if not engine.entries(a, "7z").links:
        pytest.skip("py7zr didn't store the link")
    before = a.read_bytes()
    with pytest.raises(engine.ConversionError, match="links"):
        engine.rewrite(a, "7z", "", [], ["f.txt"], NOP, _cancel())
    assert a.read_bytes() == before


def test_archives_are_never_made_the_default():
    from umc.tools import linux_desktop, linux_integration, shell_integration

    archives = [
        e
        for e in shell_integration.view_extensions()
        if vf.kind_of_path(Path("x." + e)) == vf.ARCHIVE
    ]
    assert archives  # offered under Open with
    assert not set(linux_desktop.mime_types(archives)) & set(linux_integration.viewer_types())


def test_wrong_password_in_the_view_asks_again(qtbot, viewer, tree_src, tmp_path):
    folder = tmp_path / "w"
    folder.mkdir()
    a = folder / "s.7z"
    files = [(p, p.relative_to(tree_src).as_posix()) for p in tree_src.rglob("*") if p.is_file()]
    engine.create(files, a, "7z", "normal", "pw", False, NOP, _cancel())
    answers = ["wrong", "pw"]
    viewer.archive.ask_password = lambda _text: answers.pop(0)
    viewer.open_path(a)
    _listed(qtbot, viewer)
    viewer.archive.extract_here()
    qtbot.waitUntil(lambda: (folder / "s" / "top.txt").is_file(), timeout=10000)
    assert not answers and viewer.archive.password == "pw"
