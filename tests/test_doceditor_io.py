from __future__ import annotations

import docx
import pytest

from tests.doceditor_samples import PNG_1PX, b64
from umc.core.errors import ConversionError, ToolMissingError
from umc.doceditor import io as io_mod
from umc.doceditor.docx_reader import read
from umc.doceditor.docx_writer import write
from umc.doceditor.html_subset import normalise_html, text_length
from umc.doceditor.io import open_document
from umc.doceditor.routes import convert_file
from umc.doceditor.types import (
    W_COMMENTS,
    W_HEADERS,
    next_image_name,
    warning_text,
)
from umc.tools import libreoffice, pandoc

SAMPLE = (
    '<html><body><h1 class="x">T</h1><script>bad()</script>'
    '<p style="text-align:center;color:red;margin-top:3px">a <strong>b</strong> '
    '<span style="color:RGB(255,0,0);font-size:12pt;position:absolute">c</span>'
    '<font face="Arial">d</font></p><div>e</div><h4>f</h4></body></html>'
)


def test_warning_text():
    assert warning_text([W_HEADERS, W_COMMENTS]) == (
        "This document has parts the editor can't keep: headers and footers, comments. "
        "They will be lost if you save over the original — use Save as to keep a copy."
    )
    assert warning_text([]) == ""


def test_next_image_name():
    assert next_image_name({}, "png") == "img/1.png"
    assert next_image_name({"img/1.png": b""}, "jpg") == "img/2.jpg"


def test_normalise_keeps_subset_and_drops_the_rest():
    html, imgs = normalise_html(SAMPLE)
    assert "<script" not in html and "bad()" not in html and "class=" not in html
    assert "<h1>T</h1>" in html
    assert '<p style="text-align:center">' in html
    assert "<b>b</b>" in html
    assert 'style="font-size:12pt;color:#ff0000"' in html
    assert '<span style="font-family:Arial">d</span>' in html
    assert "<p>e</p>" in html and "<h3>f</h3>" in html
    assert imgs == {}


def test_normalise_idempotent():
    once, _ = normalise_html(SAMPLE)
    twice, _ = normalise_html(once)
    assert once == twice


def test_normalise_unicode_as_characters():
    text = "<p>Blåbærgrød — Привет — 你好 — 😀</p>"
    assert normalise_html(text)[0] == text


def test_normalise_images(tmp_path):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    html, imgs = normalise_html(
        f'<p><img src="pic.png" width="10"><img src="data:image/png;base64,{b64(PNG_1PX)}">'
        '<img src="https://example.com/x.png"></p>',
        base_dir=tmp_path,
    )
    assert list(imgs) == ["img/1.png", "img/2.png"] and "example.com" not in html


def test_images_never_read_outside_base_dir(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    (tmp_path / "secret.png").write_bytes(PNG_1PX)
    html, imgs = normalise_html('<p><img src="../secret.png"></p>', base_dir=base)
    assert imgs == {} and "secret" not in html


def test_jpeg_extension_and_roundtrip_of_existing_img_names(tmp_path):
    (tmp_path / "a.jpeg").write_bytes(PNG_1PX)
    html, imgs = normalise_html('<p><img src="a.jpeg"></p>', base_dir=tmp_path)
    assert list(imgs) == ["img/1.jpg"]


def test_text_length():
    assert text_length("<p>ab <b>c</b></p><p>d</p>") == 5


def test_blocks_inside_inline_tags_are_separated():
    html, _ = normalise_html('<a href="http://a"><div>one</div><div>two</div></a>')
    assert html == '<p><a href="http://a">one<br>two</a></p>'
    html, _ = normalise_html("<span><div>one</div><div>two</div></span>")
    assert html == "<p>one<br>two</p>"
    assert normalise_html(html)[0] == html


def test_deeply_nested_content_survives():
    html, _ = normalise_html("<div>" * 600 + "x" + "</div>" * 600)
    assert "x" in html


def test_unparseable_html_raises():
    from umc.core.errors import ConversionError

    with pytest.raises(ConversionError, match="couldn't be read as HTML"):
        normalise_html("<div>" * 100000 + "x")


def test_empty_input_is_empty():
    assert normalise_html("  \n") == ("", {})


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


def test_two_space_nested_list(tmp_path):
    from tests.test_doceditor_fixes import content_of
    from umc.doceditor.save import save_document

    p = tmp_path / "a.md"
    p.write_text("- one\n  - two\n    - three\n", encoding="utf-8")
    want = "<ul><li>one<ul><li>two<ul><li>three</li></ul></li></ul></li></ul>"
    assert open_document(p).html == want
    for _ in range(3):
        save_document(content_of(open_document(p), p), p, "md")
        assert open_document(p).html == want


def test_md_to_html_is_the_shared_configuration():
    assert io_mod.md_to_html("~~x~~\n").strip() == "<p><s>x</s></p>"
    assert "<table>" in io_mod.md_to_html("| a |\n|---|\n| 1 |\n")
