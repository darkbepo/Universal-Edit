from __future__ import annotations

import docx
import pytest

from tests.doceditor_samples import PNG_1PX
from umc.core.errors import ConversionError, ToolMissingError
from umc.doceditor import io as io_mod
from umc.doceditor.docx_reader import read
from umc.doceditor.docx_writer import write
from umc.doceditor.io import open_document
from umc.doceditor.routes import convert_file
from umc.tools import libreoffice, pandoc


def test_open_markdown(media):
    d = open_document(media / "doc.md")
    assert d.source_format == "md"
    assert "<h1>Title</h1>" in d.html and "<b>world</b>" in d.html and "<ul>" in d.html


def test_open_txt_cp1252(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("Café\nline 2\n".encode("cp1252"))
    assert open_document(p).html == "<p>Café</p><p>line 2</p>"


def test_open_txt_escapes(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("a < b & c")
    assert open_document(p).html == "<p>a &lt; b &amp; c</p>"


def test_open_html_with_local_picture(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    p = tmp_path / "a.html"
    p.write_text('<html><body><p>Hi</p><p><img src="pic.png"></p></body></html>')
    d = open_document(p)
    assert d.source_format == "html"
    assert list(d.images) == ["img/1.png"] and 'src="img/1.png"' in d.html


def test_open_docx(tmp_path):
    p = write("<h1>T</h1><p>Hello <b>x</b></p>", {}, tmp_path / "a.docx")
    d = open_document(p)
    assert d.source_format == "docx"
    assert d.html == read(p).html


def test_open_empty_files(tmp_path):
    (tmp_path / "e.txt").write_bytes(b"")
    docx.Document().save(tmp_path / "e.docx")
    for name in ("e.txt", "e.docx"):
        assert open_document(tmp_path / name).html == "<p></p>"


def test_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "MAX_CHARS", 10)
    p = tmp_path / "big.txt"
    p.write_text("x" * 11)
    with pytest.raises(ConversionError, match="too large to edit here"):
        open_document(p)


def test_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(io_mod, "MAX_FILE_BYTES", 5)
    p = tmp_path / "big.txt"
    p.write_text("x" * 11)
    with pytest.raises(ConversionError, match="too large to edit here"):
        open_document(p)


def test_unsupported_type(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(ConversionError, match="can't be edited"):
        open_document(p)


def test_convert_file_unknown_target(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    with pytest.raises(ConversionError, match="Can't save as NOPE"):
        convert_file(p, "nope", tmp_path / "o.nope")


def test_open_odt_needs_a_tool(tmp_path, monkeypatch):
    monkeypatch.setattr(libreoffice, "soffice_path", lambda: None)
    monkeypatch.setattr(pandoc, "pandoc_path", lambda: None)
    p = tmp_path / "a.odt"
    p.write_bytes(b"PK\x03\x04 not really")
    with pytest.raises(ToolMissingError):
        open_document(p)


@pytest.mark.skipif(
    not (pandoc.is_available() or libreoffice.is_available()),
    reason="needs Pandoc or LibreOffice",
)
def test_open_odt_round_trip(tmp_path):
    src = write("<h1>Title</h1><p>Body</p>", {}, tmp_path / "a.docx")
    odt = convert_file(src, "odt", tmp_path / "a.odt")
    d = open_document(odt)
    assert d.source_format == "odt" and "<h1>Title</h1>" in d.html
