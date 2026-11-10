from __future__ import annotations

import pymupdf
import pytest

from tests.doceditor_samples import PNG_1PX
from umc.core.errors import ConversionError
from umc.doceditor import save as save_mod
from umc.doceditor.docx_reader import read
from umc.doceditor.io import open_document
from umc.doceditor.save import save_document, save_formats
from umc.doceditor.types import SaveContent
from umc.tools import libreoffice, pandoc

CONTENT = SaveContent(
    html='<h1>Hi</h1><p>Blåbær <b>bold</b><img src="img/1.png" width="20"></p>',
    images={"img/1.png": PNG_1PX},
    markdown="# Hi\n\nBlåbær **bold**![](img/1.png)\n",
    plain_text="Hi\nBlåbær bold\n",
)


def raise_(exc):
    def f(*a, **k):
        raise exc

    return f


def test_save_docx_reads_back(tmp_path):
    out = save_document(CONTENT, tmp_path / "a.docx", "docx")
    assert read(out).html.startswith("<h1>Hi</h1>")


def test_save_html_is_self_contained(tmp_path):
    text = save_document(CONTENT, tmp_path / "a.html", "html").read_text("utf-8")
    assert "data:image/png;base64," in text and "<h1>Hi</h1>" in text


def test_save_txt_md_unicode(tmp_path):
    txt = save_document(CONTENT, tmp_path / "a.txt", "txt")
    assert txt.read_text("utf-8") == "Hi\nBlåbær bold\n"
    md = save_document(CONTENT, tmp_path / "a.md", "md").read_text("utf-8")
    assert "![](a_files/1.png)" in md and (tmp_path / "a_files" / "1.png").exists()


def test_save_pdf_without_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(save_mod, "_libreoffice_pdf", lambda: False)
    out = save_document(CONTENT, tmp_path / "a.pdf", "pdf")
    with pymupdf.open(out) as d:
        assert "Blåbær" in d[0].get_text()


def test_save_replaces_existing_file(tmp_path):
    (tmp_path / "a.docx").write_bytes(b"old")
    out = save_document(CONTENT, tmp_path / "a.docx", "docx")
    assert read(out).html.startswith("<h1>Hi</h1>")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.docx"]


def test_save_locked_file_message(tmp_path, monkeypatch):
    monkeypatch.setattr(save_mod.os, "replace", raise_(PermissionError()))
    (tmp_path / "a.docx").write_bytes(b"old")
    with pytest.raises(ConversionError, match="may be open in another program"):
        save_document(CONTENT, tmp_path / "a.docx", "docx")
    assert (tmp_path / "a.docx").read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.docx"]


def test_save_formats_order():
    ids = [e for e, _ in save_formats()]
    assert ids[:8] == ["docx", "odt", "rtf", "doc", "pdf", "md", "txt", "html"]
    assert "epub" in ids


@pytest.mark.skipif(
    not (pandoc.is_available() or libreoffice.is_available()), reason="needs Pandoc or LibreOffice"
)
def test_save_odt(tmp_path):
    out = save_document(CONTENT, tmp_path / "a.odt", "odt")
    assert "<h1>Hi</h1>" in open_document(out).html
