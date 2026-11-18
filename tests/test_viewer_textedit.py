"""Quick text edits in the viewer (no Qt): byte-exact saves, changed-on-disk, encodings."""

import pytest

from umc.viewer import textedit


def test_save_keeps_encoding_bom_and_line_endings(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(b"\xef\xbb\xbfone\r\ntwo\r\n")  # UTF-8 with BOM, CRLF
    opened = textedit.open_text(p)
    assert opened.file.text == "one\ntwo\n"
    again = textedit.save(opened, "one\nTWO\n")
    assert p.read_bytes() == b"\xef\xbb\xbfone\r\nTWO\r\n"
    assert not textedit.changed_on_disk(again)
    assert [x.name for x in tmp_path.iterdir()] == ["a.txt"]  # no temp file left


def test_changed_on_disk_and_limits(tmp_path, monkeypatch):
    p = tmp_path / "b.ini"
    p.write_text("a=1\n")
    opened = textedit.open_text(p)
    p.write_text("a=22\n")
    assert textedit.changed_on_disk(opened)
    monkeypatch.setattr(textedit, "EDIT_LIMIT", 2)
    assert textedit.open_text(p) is None  # too big: read only
    binary = tmp_path / "c.bin"
    binary.write_bytes(b"\x00\x01\x02")
    monkeypatch.setattr(textedit, "EDIT_LIMIT", 1000)
    assert textedit.open_text(binary) is None


def test_text_the_encoding_cant_hold(tmp_path):
    p = tmp_path / "old.txt"
    p.write_bytes("caf\xe9\n".encode("cp1252"))
    opened = textedit.open_text(p)
    with pytest.raises(textedit.SaveProblem, match="UTF-8"):
        textedit.save(opened, "café ✓\n")
    assert p.read_bytes() == "caf\xe9\n".encode("cp1252")  # untouched
