"""The viewer's file rules: which view a file gets, the folder list, the Edit button, text."""

from pathlib import Path

from umc.core.detect import detect
from umc.viewer import files as vf
from umc.viewer.preview import read_text


def test_kinds(tmp_path):
    cases = {
        "a.jpg": vf.IMAGE,
        "a.HEIC": vf.IMAGE,
        "a.mp4": vf.VIDEO,
        "a.mp3": vf.AUDIO,
        "a.pdf": vf.PAGES,
        "a.epub": vf.PAGES,
        "a.svg": vf.PAGES,
        "a.docx": vf.CONVERT,
        "a.xlsx": vf.CONVERT,
        "a.eps": vf.CONVERT,
        "a.dxf": vf.CONVERT,
        "a.txt": vf.TEXT,
        "a.json": vf.TEXT,
        "a.srt": vf.TEXT,
        "a.stl": vf.MODEL,
        "a.zip": vf.OTHER,
        "a.ttf": vf.OTHER,
        "a.bin": vf.OTHER,
    }
    for name, kind in cases.items():
        assert vf.kind_of_path(Path(name)) == kind, name


def test_natural_order():
    names = ["img10.png", "Img2.png", "img1.png", "b.png", "A.png"]
    expected = ["A.png", "b.png", "img1.png", "Img2.png", "img10.png"]
    assert sorted(names, key=vf.natural_key) == expected


def test_folder_items(tmp_path):
    for n in ("b2.png", "b10.png", "notes.txt", ".hidden.png", "archive.zip", "x.bin"):
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "sub.png").mkdir()  # a folder named like a picture
    names = [p.name for p in vf.folder_items(tmp_path / "b10.png")]
    assert names == ["b2.png", "b10.png", "notes.txt"]
    # a file it can't show still opens (and is listed) when it's the one asked for
    assert [p.name for p in vf.folder_items(tmp_path / "x.bin")] == [
        "b2.png",
        "b10.png",
        "notes.txt",
        "x.bin",
    ]


def test_edit_button_follows_the_editor_tiles():
    for kind in (vf.IMAGE, vf.MODEL, vf.AUDIO):
        assert vf.editor_for(kind).available
    for kind in (vf.PAGES, vf.CONVERT, vf.TEXT):
        assert vf.editor_for(kind).id == "document" and vf.editor_for(kind).available
    assert not vf.editor_for(vf.VIDEO).available  # "coming soon"
    assert vf.editor_for(vf.OTHER) is None


def test_edit_only_what_the_editor_can_open():
    # the document editor edits DOCX / TXT / MD …, not a PDF or an EPUB the viewer shows
    assert vf.can_edit(vf.TEXT, Path("a.txt")) and vf.can_edit(vf.CONVERT, Path("a.DOCX"))
    assert not vf.can_edit(vf.PAGES, Path("a.pdf"))
    assert not vf.can_edit(vf.PAGES, Path("a.epub"))
    assert vf.can_edit(vf.IMAGE, Path("a.png"))
    assert not vf.can_edit(vf.VIDEO, Path("a.mp4"))  # coming soon
    assert not vf.can_edit(vf.OTHER, Path("a.bin"))


def test_read_text_encodings(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("Blåbær", encoding="utf-8")
    assert read_text(p) == ("Blåbær", False)
    p.write_bytes("Blåbær".encode("cp1252"))
    assert read_text(p)[0] == "Blåbær"
    p.write_text("Blåbær", encoding="utf-16")
    assert read_text(p)[0] == "Blåbær"
    p.write_text("x" * 50, encoding="utf-8")
    assert read_text(p, limit=10) == ("x" * 10, True)


def test_view_kind_of_detected(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# hi")
    assert vf.view_kind(detect(p)) == vf.TEXT
