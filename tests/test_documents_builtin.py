"""DOCX / Markdown / HTML / TXT → PDF with nothing installed (no LibreOffice, no Pandoc)."""

import io
import re

import pymupdf
import pytest
from PIL import Image

from tests.conftest import convert
from umc.tools import calibre, libreoffice, pandoc


@pytest.fixture
def nothing_installed(monkeypatch):
    for tool in (libreoffice, pandoc, calibre):
        monkeypatch.setattr(tool, "is_available", lambda: False)


def _docx(path):
    import docx

    d = docx.Document()
    d.add_heading("Quarterly report", 1)
    d.add_paragraph("Sales went up in Århus.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "Region", "Total"
    t.cell(1, 0).text, t.cell(1, 1).text = "North", "42"
    png = io.BytesIO()
    Image.new("RGB", (120, 60), "red").save(png, "PNG")
    png.seek(0)
    d.add_picture(png)
    d.save(path)
    return path


def test_docx_to_pdf_keeps_text_table_and_picture(nothing_installed, tmp_path):
    [out] = convert([_docx(tmp_path / "report.docx")], "doc:pdf", tmp_path / "out")
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
        assert "Quarterly report" in text and "Århus" in text
        assert "Region" in text and "42" in text
        assert doc[0].get_images()  # the picture made it in
        assert round(doc[0].rect.width) == 595  # A4


@pytest.mark.parametrize(
    "name, content, expect",
    [
        ("notes.md", "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", ["Title", "a", "2"]),
        ("page.html", "<h1>Hello</h1><p>World ÆØÅ</p>", ["Hello", "World ÆØÅ"]),
        ("plain.txt", "line one\nline two æøå\n", ["line one", "line two æøå"]),
    ],
)
def test_text_formats_to_pdf(nothing_installed, tmp_path, name, content, expect):
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    [out] = convert([src], "doc:pdf", tmp_path / "out")
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    for word in expect:
        assert word in text


def test_long_text_flows_onto_more_pages(nothing_installed, tmp_path):
    src = tmp_path / "long.md"
    src.write_text("\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(80)), "utf-8")
    [out] = convert([src], "doc:pdf", tmp_path / "out")
    with pymupdf.open(out) as doc:
        assert doc.page_count > 3
        assert "Paragraph 79" in doc[-1].get_text()


def test_docx_to_html_without_pandoc(nothing_installed, tmp_path):
    [out] = convert([_docx(tmp_path / "r.docx")], "doc:html", tmp_path / "out")
    html = out.read_text(encoding="utf-8")
    assert "<table>" in html and "Århus" in html and "data:image/png" in html


def test_md_to_pdf_two_space_nested_list(nothing_installed, tmp_path):
    from umc.tools import htmlpdf

    src = tmp_path / "list.md"
    src.write_text("- one\n  - two\n- three\n", encoding="utf-8")
    html_out = htmlpdf.markdown_to_html(src, tmp_path / "list.html").read_text(encoding="utf-8")
    assert re.search(r"<li>one\s*<ul>\s*<li>two", html_out)
    [out] = convert([src], "doc:pdf", tmp_path / "out")
    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    assert text.index("one") < text.index("two") < text.index("three")


def test_heading_ids_for_links(tmp_path):
    from umc.tools import htmlpdf

    src = tmp_path / "ids.md"
    src.write_text("# Usage\n\n## Usage\n\n### Q & A: Why?\n\n[see](#usage)\n", encoding="utf-8")
    html_out = htmlpdf.markdown_to_html(src, tmp_path / "ids.html").read_text(encoding="utf-8")
    assert '<h1 id="usage">Usage</h1>' in html_out
    assert '<h2 id="usage-1">Usage</h2>' in html_out
    assert '<h3 id="q-a-why">' in html_out


def _ids(tmp_path, md):
    from umc.tools import htmlpdf

    src = tmp_path / "t.md"
    src.write_text(md, encoding="utf-8")
    return htmlpdf.markdown_to_html(src, tmp_path / "t.html").read_text(encoding="utf-8")


def test_heading_ids_never_collide(tmp_path):
    for md in ("# Usage\n\n## Usage\n\n## usage-1\n", "# usage-1\n\n## Usage\n\n## Usage\n"):
        ids = re.findall(r' id="([^"]+)"', _ids(tmp_path, md))
        assert len(ids) == 3 and len(set(ids)) == 3, ids


def test_link_targets_the_real_usage_1_heading(tmp_path):
    out = _ids(tmp_path, "# Usage\n\n## usage-1\n\n## Usage\n\n[x](#usage-1)\n")
    assert '<h2 id="usage-1">usage-1</h2>' in out
    assert '<h2 id="usage-2">Usage</h2>' in out


def test_toc_marker_becomes_nested_links(tmp_path):
    out = _ids(tmp_path, "[TOC]\n\n# One\n\n## Sub\n\n# Two\n")
    assert "[TOC]" not in out
    toc = out.split("</div>")[0]
    assert '<a href="#one">One</a>' in toc and '<a href="#two">Two</a>' in toc
    assert re.search(r'<a href="#one">One</a>\s*<ul><li><a href="#sub">Sub</a></li></ul></li>', toc)


def test_raw_html_headings_get_or_keep_ids(tmp_path):
    out = _ids(
        tmp_path, '<h2 class="x">Plain</h2>\n\n<h2 id="mine" class="y">Mine</h2>\n\n# Mine\n'
    )
    assert re.search(r'<h2 class="x" id="plain">Plain</h2>', out)
    assert 'id="mine" class="y"' in out
    assert '<h1 id="mine-1">Mine</h1>' in out
