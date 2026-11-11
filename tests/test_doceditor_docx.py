from __future__ import annotations

from io import BytesIO

import docx
import pytest
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from tests.doceditor_samples import CASES, DOCX_CASES, PNG_1PX, docx_package
from umc.core.errors import ConversionError
from umc.doceditor.docx_reader import read
from umc.doceditor.docx_writer import write
from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import (
    MAX_UNPACKED_BYTES,
    TOO_LARGE,
    W_COMMENTS,
    W_EQUATIONS,
    W_FIELDS,
    W_HEADERS,
    W_NOTES,
    W_PICTURES,
    W_RTL_RUBY,
    W_TRACKED,
)


def _doc(tmp_path, html, images=None):
    return docx.Document(write(html, images or {}, tmp_path / "o.docx"))


def test_write_headings_runs_alignment(tmp_path):
    d = _doc(
        tmp_path,
        '<h1>Title</h1><p style="text-align:center">a <b>b</b> <i>c</i> <u>d</u> <s>e</s> '
        '<span style="font-family:Arial;font-size:14pt;color:#ff0000;'
        'background-color:#ffcc00">f</span></p>',
    )
    assert d.paragraphs[0].style.name == "Heading 1"
    para = d.paragraphs[1]
    assert para.alignment == WD_ALIGN_PARAGRAPH.CENTER
    runs = {r.text.strip(): r for r in para.runs if r.text.strip()}
    assert runs["b"].bold and runs["c"].italic and runs["d"].underline and runs["e"].font.strike
    f = runs["f"].font
    assert f.name == "Arial" and f.size.pt == 14 and str(f.color.rgb) == "FF0000"
    assert runs["f"]._r.rPr.find(qn("w:shd")).get(qn("w:fill")) == "FFCC00"


def test_write_lists_restart_numbering(tmp_path):
    d = _doc(tmp_path, "<ol><li>a</li></ol><p>x</p><ol><li>b</li></ol>")
    num_ids = [
        p._p.pPr.numPr.numId.val
        for p in d.paragraphs
        if p._p.pPr is not None and p._p.pPr.numPr is not None
    ]
    assert len(set(num_ids)) == 2
    assert d.paragraphs[0].style.name == "List Number"


def test_write_nested_bullets(tmp_path):
    d = _doc(tmp_path, "<ul><li>a<ul><li>b</li></ul></li></ul>")
    p0, p1 = d.paragraphs
    assert p0.style.name == "List Bullet" and p0.text == "a"
    assert p0._p.pPr.numPr.ilvl.val == 0 and p1._p.pPr.numPr.ilvl.val == 1
    assert p0._p.pPr.numPr.numId.val == p1._p.pPr.numPr.numId.val


def test_write_table_merge(tmp_path):
    d = _doc(
        tmp_path,
        '<table><tr><td colspan="2"><p>a</p></td></tr>'
        "<tr><td><p>b</p></td><td><p>c</p></td></tr></table>",
    )
    t = d.tables[0]
    assert t.style.name == "Table Grid"
    tcs = t.rows[0]._tr.findall(qn("w:tc"))
    assert len(tcs) == 1
    assert tcs[0].tcPr.find(qn("w:gridSpan")).get(qn("w:val")) == "2"
    assert t.rows[1].cells[1].text == "c"


def test_write_table_rowspan(tmp_path):
    d = _doc(
        tmp_path,
        '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr>'
        "<tr><td><p>c</p></td></tr></table>",
    )
    t = d.tables[0]
    assert t.cell(0, 0)._tc is t.cell(1, 0)._tc
    assert t.cell(1, 1).text == "c"


def test_write_picture(tmp_path):
    d = _doc(tmp_path, '<p><img src="img/1.png" width="96"></p>', {"img/1.png": PNG_1PX})
    shapes = d.inline_shapes
    assert len(shapes) == 1 and shapes[0].width == 914400


def test_write_link(tmp_path):
    d = _doc(tmp_path, '<p><a href="https://x.org">x</a></p>')
    link = d.paragraphs[0]._p.find(qn("w:hyperlink"))
    assert link is not None
    rel = d.part.rels[link.get(qn("r:id"))]
    assert rel.target_ref == "https://x.org" and rel.is_external
    assert link.find(qn("w:r")).rPr.rStyle.val == "Hyperlink"


def test_write_page_break_indent_hr_pre_quote(tmp_path):
    d = _doc(
        tmp_path,
        '<p style="page-break-before:always">n</p><hr><p style="margin-left:72pt">i</p>'
        "<pre>a\nb</pre><blockquote><p>q</p></blockquote>",
    )
    ps = d.paragraphs
    assert ps[0].paragraph_format.page_break_before is True
    border = ps[1]._p.pPr.find(qn("w:pBdr")).find(qn("w:bottom"))
    assert ps[1].text == "" and border.get(qn("w:val")) == "single"
    assert ps[2].paragraph_format.left_indent.pt == 72
    assert ps[3].style.name == "Code" and ps[3].text == "a\nb" and len(ps[3].runs) == 1
    assert ps[4].style.name == "Quote"


def test_write_empty_fragment(tmp_path):
    d = _doc(tmp_path, "")
    assert len(d.paragraphs) == 1 and d.paragraphs[0].text == ""


def _num_fmt(d, par):
    num_pr = par._p.pPr.numPr
    numbering = d.part.numbering_part.element
    num = next(
        n for n in numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == str(num_pr.numId.val)
    )
    abstract_id = num.find(qn("w:abstractNumId")).get(qn("w:val"))
    abstract = next(
        a
        for a in numbering.findall(qn("w:abstractNum"))
        if a.get(qn("w:abstractNumId")) == abstract_id
    )
    lvl = next(
        x for x in abstract.findall(qn("w:lvl")) if x.get(qn("w:ilvl")) == str(num_pr.ilvl.val)
    )
    return lvl.find(qn("w:numFmt")).get(qn("w:val"))


def test_write_mixed_nested_lists_keep_their_kind(tmp_path):
    d = _doc(tmp_path, "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul>")
    assert [_num_fmt(d, p) for p in d.paragraphs] == ["bullet", "bullet", "decimal"]
    d = _doc(tmp_path, "<ol><li>x<ul><li>y</li></ul></li></ol>")
    assert [_num_fmt(d, p) for p in d.paragraphs] == ["decimal", "bullet"]
    assert [p._p.pPr.numPr.ilvl.val for p in d.paragraphs] == [0, 1]


def test_write_nested_table_keeps_outer_table_intact(tmp_path):
    d = _doc(
        tmp_path,
        "<table><tr><td><table><tr><td><p>a</p></td></tr><tr><td><p>b</p></td></tr></table>"
        "</td></tr></table>",
    )
    assert len(d.tables) == 1 and len(d.tables[0].rows) == 1
    inner = d.tables[0].rows[0].cells[0].tables[0]
    assert [r.cells[0].text for r in inner.rows] == ["a", "b"]


def test_write_skips_bad_or_missing_pictures(tmp_path, caplog):
    d = _doc(tmp_path, '<p>x<img src="img/1.png" width="10"></p>', {"img/1.png": b"not an image"})
    assert len(d.inline_shapes) == 0 and d.paragraphs[0].text == "x"
    d = _doc(tmp_path, '<p>x<img src="img/9.png"></p>', {})
    assert len(d.inline_shapes) == 0
    assert "img/9.png" in caplog.text


# ----- reader ---------------------------------------------------------------------------


@pytest.mark.parametrize("html", CASES)
def test_round_trip(tmp_path, html):
    expected, _ = normalise_html(html)
    assert read(write(html, {}, tmp_path / "o.docx")).html == expected


def test_nested_lists_round_trip(tmp_path):
    html = "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul><ol><li>d</li></ol>"
    assert read(write(html, {}, tmp_path / "o.docx")).html == normalise_html(html)[0]


def test_list_in_table_cell(tmp_path):
    html = "<table><tr><td><ul><li>a</li><li>b</li></ul></td></tr></table>"
    assert read(write(html, {}, tmp_path / "o.docx")).html == normalise_html(html)[0]


def test_rowspan_round_trip(tmp_path):
    html = (
        '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr>'
        "<tr><td><p>c</p></td></tr></table>"
    )
    assert read(write(html, {}, tmp_path / "o.docx")).html == normalise_html(html)[0]


def test_picture_round_trip(tmp_path):
    html = '<p><img src="img/1.png" width="96"></p>'
    got = read(write(html, {"img/1.png": PNG_1PX}, tmp_path / "o.docx"))
    assert got.images == {"img/1.png": PNG_1PX}
    assert 'src="img/1.png"' in got.html and 'width="96"' in got.html


def test_unicode_round_trip(tmp_path):
    html = "<p>Blåbærgrød — Привет — 你好 — 😀</p>"
    assert read(write(html, {}, tmp_path / "o.docx")).html == html


def test_page_break_run_splits_paragraph(tmp_path):
    d = docx.Document()
    d.add_paragraph("a").add_run().add_break(WD_BREAK.PAGE)
    d.paragraphs[0].add_run("b")
    d.save(tmp_path / "p.docx")
    assert read(tmp_path / "p.docx").html == '<p>a</p><p style="page-break-before:always">b</p>'


def test_word_styles_and_headings(tmp_path):
    d = docx.Document()
    d.add_heading("Top", 4)  # Heading 4 -> h3
    d.add_paragraph("x", style="Title")
    d.save(tmp_path / "s.docx")
    assert read(tmp_path / "s.docx").html == "<h3>Top</h3><h1>x</h1>"


def test_warnings(tmp_path):
    d = docx.Document()
    d.add_paragraph("x")
    d.sections[0].header.paragraphs[0].text = "Head"
    if not hasattr(d, "add_comment"):
        pytest.skip("python-docx without comment support")
    d.add_comment(d.paragraphs[0].runs or [d.paragraphs[0].add_run("y")], text="note")
    p = tmp_path / "w.docx"
    d.save(p)
    assert {W_HEADERS, W_COMMENTS} <= set(read(p).warnings)


def test_tracked_changes_warn(tmp_path):
    d = docx.Document()
    run = d.add_paragraph().add_run("kept")
    ins = OxmlElement("w:ins")
    ins.set(qn("w:id"), "1")
    ins.set(qn("w:author"), "A")
    run._r.addprevious(ins)
    ins.append(run._r)
    d.save(tmp_path / "t.docx")
    got = read(tmp_path / "t.docx")
    assert W_TRACKED in got.warnings and "kept" in got.html


def test_notes_and_fields_warn(tmp_path):
    d = docx.Document()
    par = d.add_paragraph("a")
    ref = OxmlElement("w:footnoteReference")
    ref.set(qn("w:id"), "1")
    par.add_run()._r.append(ref)
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "DATE")
    par._p.append(fld)
    d.save(tmp_path / "n.docx")
    assert {W_NOTES, W_FIELDS} <= set(read(tmp_path / "n.docx").warnings)


def test_unsupported_picture_warns(tmp_path):
    d = docx.Document()
    d.add_paragraph().add_run().add_picture(BytesIO(PNG_1PX))
    part = next(r.target_part for r in d.part.rels.values() if r.reltype.endswith("/image"))
    part._blob = b"not an image"
    part._content_type = "image/x-emf"
    d.save(tmp_path / "e.docx")
    got = read(tmp_path / "e.docx")
    assert W_PICTURES in got.warnings and "<img" not in got.html and got.images == {}


def test_plain_docx_has_no_warnings(tmp_path):
    assert read(write("<p>a</p>", {}, tmp_path / "o.docx")).warnings == []


def test_unreadable_docx_is_a_plain_error(tmp_path):
    (tmp_path / "bad.docx").write_bytes(b"nope")
    with pytest.raises(ConversionError):
        read(tmp_path / "bad.docx")


# ----- reader: wrappers and real-world cases -----------------------------------------------


def _body_xml(tmp_path, xml, name="x.docx"):
    """A document whose body is replaced by ``xml`` (a string of w: elements)."""
    from lxml import etree

    d = docx.Document()
    body = d.element.body
    sect = body.find(qn("w:sectPr"))
    for child in list(body):
        if child is not sect:
            body.remove(child)
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    root = etree.fromstring(f"<root {ns}>{xml}</root>")
    for el in list(root):
        sect.addprevious(el)
    path = tmp_path / name
    d.save(path)
    return path


@pytest.mark.parametrize(
    ("wrapper", "tracked"),
    [("w:customXml", False), ("w:smartTag", False), ("w:ins", True), ("w:moveTo", True)],
)
def test_block_wrappers_keep_text(tmp_path, wrapper, tracked):
    attrs = ' w:id="1" w:author="A"' if tracked else ""
    path = _body_xml(tmp_path, f"<{wrapper}{attrs}><w:p><w:r><w:t>cx</w:t></w:r></w:p></{wrapper}>")
    got = read(path)
    assert got.html == "<p>cx</p>"
    assert (W_TRACKED in got.warnings) is tracked


@pytest.mark.parametrize("wrapper", ["w:del", "w:moveFrom"])
def test_block_deletions_are_dropped(tmp_path, wrapper):
    path = _body_xml(
        tmp_path,
        f'<{wrapper} w:id="1" w:author="A"><w:p><w:r><w:t>gone</w:t></w:r></w:p></{wrapper}>'
        "<w:p><w:r><w:t>kept</w:t></w:r></w:p>",
    )
    assert read(path).html == "<p>kept</p>"


def test_content_controls_keep_text(tmp_path):
    path = _body_xml(
        tmp_path,
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>block</w:t></w:r></w:p></w:sdtContent></w:sdt>"
        "<w:p><w:sdt><w:sdtContent><w:r><w:t>inline</w:t></w:r></w:sdtContent></w:sdt></w:p>",
    )
    assert read(path).html == "<p>block</p><p>inline</p>"


def test_highlight_colour(tmp_path):
    path = _body_xml(
        tmp_path,
        '<w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>h</w:t></w:r></w:p>',
    )
    assert read(path).html == '<p><span style="background-color:#ffff00">h</span></p>'


def test_list_from_paragraph_style_only(tmp_path):
    d = docx.Document()
    d.add_paragraph("a", style="List Bullet")
    d.add_paragraph("b", style="List Number")
    d.save(tmp_path / "ls.docx")
    assert read(tmp_path / "ls.docx").html == "<ul><li>a</li></ul><ol><li>b</li></ol>"


def test_numbering_format_none_is_not_a_list(tmp_path):
    d = docx.Document()
    d.add_paragraph("a", style="List Bullet")
    numbering = d.part.numbering_part.element
    for fmt in numbering.iter(qn("w:numFmt")):
        fmt.set(qn("w:val"), "none")
    d.save(tmp_path / "none.docx")
    assert read(tmp_path / "none.docx").html == "<p>a</p>"


def test_vertical_merge_by_hand(tmp_path):
    def tc(vm, text):
        mark = f"<w:vMerge{vm}/>" if vm is not None else ""
        return f"<w:tc><w:tcPr>{mark}</w:tcPr><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:tc>"

    restart = ' w:val="restart"'
    rows = (
        f"<w:tr>{tc(restart, 'a')}{tc(None, 'b')}</w:tr>"
        f"<w:tr>{tc('', '')}{tc(None, 'c')}</w:tr>"
        f"<w:tr>{tc('', '')}{tc(None, 'd')}</w:tr>"
    )
    path = _body_xml(tmp_path, f"<w:tbl>{rows}</w:tbl><w:p><w:r><w:t>after</w:t></w:r></w:p>")
    assert (
        read(path).html
        == normalise_html(
            # no borders and no table style: a borderless table
            '<table style="border:none"><tr><td rowspan="3"><p>a</p></td><td><p>b</p></td></tr>'
            "<tr><td><p>c</p></td></tr><tr><td><p>d</p></td></tr></table><p>after</p>"
        )[0]
    )


def test_non_web_picture_is_converted_to_png(tmp_path):
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (2, 2), (0, 0, 255)).save(buf, "TIFF")
    d = docx.Document()
    d.add_paragraph().add_run().add_picture(BytesIO(PNG_1PX))
    part = next(r.target_part for r in d.part.rels.values() if r.reltype.endswith("/image"))
    part._blob = buf.getvalue()
    part._content_type = "image/tiff"
    d.save(tmp_path / "tif.docx")
    got = read(tmp_path / "tif.docx")
    assert list(got.images) == ["img/1.png"] and 'src="img/1.png"' in got.html
    assert got.images["img/1.png"].startswith(b"\x89PNG")
    assert W_PICTURES not in got.warnings


# ----- unusual packages: opened leniently, like the website does -------------------------------

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def _w_para(inner: str, ppr: str = "") -> str:
    return f"<w:p>{ppr}{inner}</w:p>"


def _w_run(text: str, rpr: str = "") -> str:
    return f"<w:r>{rpr}<w:t>{text}</w:t></w:r>"


def _w_drawing(rid: str, cx: str = "9525", cy: str = "9525") -> str:
    return (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="{cx}" cy="{cy}"/>'
        f'<a:graphic><a:graphicData><a:blip r:embed="{rid}"/></a:graphicData></a:graphic>'
        "</wp:inline></w:drawing></w:r>"
    )


def _odd_docx(path, body, sect="", rels="", files=None, types=""):
    """A hand-built DOCX with no numbering part, to which ``rels`` / ``files`` are added."""
    doc = (
        f'{_HEAD}<w:document xmlns:w="{_NS_W}" xmlns:r="{_NS_R}" xmlns:wp="{_NS_WP}" '
        f'xmlns:a="{_NS_A}"><w:body>{body}{sect}</w:body></w:document>'
    )
    parts = {
        "[Content_Types].xml": (
            f'{_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            f"{types}</Types>"
        ),
        "_rels/.rels": (
            f'{_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_RT}/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": doc,
        "word/_rels/document.xml.rels": (
            f'{_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships">{rels}</Relationships>'
        ),
    }
    for name, data in (files or {}).items():
        parts[name] = data
    import zipfile

    with zipfile.ZipFile(path, "w") as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return path


_NUM_PR = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
# What web/src/doceditor/docx-read.ts returns for the same package.
WEB_BAD_NUMBERS = (
    "<p>big</p><p>bad id</p><p>bad level</p>"
    '<p><img src="img/1.png" height="1"></p>'
    '<table style="border:none"><tr><td><p>c</p></td></tr></table>'
)
_PNG_TYPES = '<Default Extension="png" ContentType="image/png"/>'


def _png_rel(rid: str, target: str) -> str:
    return f'<Relationship Id="{rid}" Type="{_RT}/image" Target="{target}"/>'


def test_open_list_without_numbering_part(tmp_path):
    p = _odd_docx(tmp_path / "a.docx", _w_para(_w_run("item"), _NUM_PR))
    assert read(p).html == "<ul><li>item</li></ul>"


def test_open_picture_whose_part_is_missing(tmp_path):
    p = _odd_docx(
        tmp_path / "a.docx",
        _w_para(_w_run("x") + _w_drawing("rIdP")),
        rels=_png_rel("rIdP", "media/gone.png"),
        types=_PNG_TYPES,
    )
    got = read(p)
    assert got.html == "<p>x</p>" and got.warnings == [W_PICTURES] and got.images == {}


def test_open_header_whose_part_is_missing(tmp_path):
    p = _odd_docx(
        tmp_path / "a.docx",
        _w_para(_w_run("x")),
        sect='<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/></w:sectPr>',
        rels=f'<Relationship Id="rIdH" Type="{_RT}/header" Target="header1.xml"/>',
    )
    got = read(p)
    assert got.html == "<p>x</p>" and got.warnings == []


def test_open_with_invalid_numbers(tmp_path):
    body = (
        _w_para(_w_run("big", '<w:rPr><w:sz w:val="abc"/></w:rPr>'))
        + _w_para(
            _w_run("bad id"),
            '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="x"/></w:numPr></w:pPr>',
        )
        + _w_para(
            _w_run("bad level"),
            '<w:pPr><w:numPr><w:ilvl w:val="y"/><w:numId w:val="1"/></w:numPr></w:pPr>',
        )
        + _w_para(_w_drawing("rIdP", cx="wide"))
        + '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="two"/></w:tcPr>'
        "<w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
    )
    p = _odd_docx(
        tmp_path / "a.docx",
        body,
        rels=_png_rel("rIdP", "media/p.png"),
        files={"word/media/p.png": PNG_1PX},
        types=_PNG_TYPES,
    )
    got = read(p)
    assert got.html == WEB_BAD_NUMBERS


def test_open_part_name_differing_in_case_and_encoding(tmp_path):
    p = _odd_docx(
        tmp_path / "a.docx",
        _w_para(_w_run("x") + _w_drawing("rIdP")),
        rels=_png_rel("rIdP", "media/MY%20PIC.PNG"),
        files={"word/media/My Pic.png": PNG_1PX},
        types=_PNG_TYPES,
    )
    got = read(p)
    assert got.html == '<p>x<img src="img/1.png" width="1" height="1"></p>'
    assert got.images == {"img/1.png": PNG_1PX}
    assert got.warnings == []


def test_opening_never_changes_the_file(tmp_path):
    p = _odd_docx(
        tmp_path / "a.docx",
        _w_para(_w_drawing("rIdP")),
        rels=_png_rel("rIdP", "media/gone.png"),
    )
    before = p.read_bytes()
    read(p)
    assert p.read_bytes() == before


# ----- content the readers used to drop (shared "docx" vectors) ----------------------------


@pytest.mark.parametrize("case", DOCX_CASES, ids=[c["name"] for c in DOCX_CASES])
def test_shared_docx_case(tmp_path, case):
    path = tmp_path / "case.docx"
    path.write_bytes(docx_package(case))
    got = read(path)
    assert (got.html, got.warnings) == (case["html"], case["warnings"])


def test_equations_warning_text():
    assert W_EQUATIONS == "equations and embedded documents"


# ----- zip bombs ---------------------------------------------------------------------------


def _claimed_size(data: bytes, name: str, size: int) -> bytes:
    """``data`` (a zip) with entry ``name``'s uncompressed size in the central directory set to
    ``size``."""
    out = bytearray(data)
    at = out.find(b"PK\x01\x02")
    while at >= 0:
        name_len = int.from_bytes(out[at + 28 : at + 30], "little")
        if out[at + 46 : at + 46 + name_len] == name.encode():
            out[at + 24 : at + 28] = size.to_bytes(4, "little")
        at = out.find(b"PK\x01\x02", at + 4)
    return bytes(out)


def test_unpacked_size_over_the_limit_is_refused(tmp_path):
    good = docx_package({"body": "<w:p><w:r><w:t>x</w:t></w:r></w:p>"})
    one = _claimed_size(good, "word/document.xml", MAX_UNPACKED_BYTES + 1)
    two = _claimed_size(
        _claimed_size(good, "word/document.xml", MAX_UNPACKED_BYTES // 2 + 1),
        "_rels/.rels",
        MAX_UNPACKED_BYTES // 2 + 1,
    )
    for n, data in enumerate((one, two)):
        path = tmp_path / f"{n}.docx"
        path.write_bytes(data)
        with pytest.raises(ConversionError) as err:
            read(path)
        assert str(err.value) == TOO_LARGE


def test_entry_that_inflates_past_its_size_is_not_unpacked(tmp_path):
    """A lying header: decompression stops at the declared size, so the part is unreadable."""
    filler = "<w:p><w:r><w:t>" + "x" * 2_000_000 + "</w:t></w:r></w:p>"
    data = _claimed_size(docx_package({"body": filler}), "word/document.xml", 100)
    path = tmp_path / "lying.docx"
    path.write_bytes(data)
    with pytest.raises(ConversionError) as err:
        read(path)
    assert str(err.value) == "This Word document could not be opened."


def test_unpacked_size_up_to_the_limit_opens(tmp_path):
    """Claims that add up to exactly MAX_UNPACKED_BYTES open; one byte more is refused."""
    import zipfile

    good = docx_package({"body": "<w:p><w:r><w:t>ok</w:t></w:r></w:p>"})
    with zipfile.ZipFile(BytesIO(good)) as z:
        others = sum(i.file_size for i in z.infolist() if i.filename != "_rels/.rels")
    for extra, opens in ((0, True), (1, False)):
        data = _claimed_size(good, "_rels/.rels", MAX_UNPACKED_BYTES - others + extra)
        path = tmp_path / f"{extra}.docx"
        path.write_bytes(data)
        if opens:
            assert read(path).html == "<p>ok</p>"
        else:
            with pytest.raises(ConversionError) as err:
                read(path)
            assert str(err.value) == TOO_LARGE


# ----- damaged zips ------------------------------------------------------------------------


def _entry(data: bytes, name: str) -> tuple[int, int, int]:
    """Where ``name``'s central-directory record and its data start, and the data's size."""
    at = data.find(b"PK")
    while at >= 0:
        name_len = int.from_bytes(data[at + 28 : at + 30], "little")
        if data[at + 46 : at + 46 + name_len] == name.encode():
            local = int.from_bytes(data[at + 42 : at + 46], "little")
            skip = int.from_bytes(data[local + 26 : local + 28], "little") + int.from_bytes(
                data[local + 28 : local + 30], "little"
            )
            size = int.from_bytes(data[at + 20 : at + 24], "little")
            return at, local + 30 + skip, size
        at = data.find(b"PK", at + 4)
    raise AssertionError(name)


def _damaged_message(tmp_path, data: bytes) -> str:
    path = tmp_path / "damaged.docx"
    path.write_bytes(data)
    with pytest.raises(ConversionError) as err:
        read(path)
    return str(err.value)


def test_flipped_byte_in_a_stored_picture_is_refused(tmp_path):
    path = _odd_docx(
        tmp_path / "pic.docx",
        _w_para(_w_run("x") + _w_drawing("rIdP")),
        rels=_png_rel("rIdP", "media/p.png"),
        files={"word/media/p.png": PNG_1PX},
        types=_PNG_TYPES,
    )
    assert "<img" in read(path).html
    data = bytearray(path.read_bytes())
    _, start, size = _entry(bytes(data), "word/media/p.png")
    data[start + size // 2] ^= 0xFF
    assert _damaged_message(tmp_path, bytes(data)) == "This Word document could not be opened."


def test_wrong_checksum_on_a_deflated_document_xml_is_refused(tmp_path):
    data = bytearray(docx_package({"body": "<w:p><w:r><w:t>hello</w:t></w:r></w:p>"}))
    cd, _, _ = _entry(bytes(data), "word/document.xml")
    data[cd + 16 : cd + 20] = (0x12345678).to_bytes(4, "little")
    assert _damaged_message(tmp_path, bytes(data)) == "This Word document could not be opened."


def test_flipped_byte_in_a_deflated_document_xml_is_refused(tmp_path):
    body = "<w:p><w:r><w:t>" + "hello world " * 200 + "</w:t></w:r></w:p>"
    data = bytearray(docx_package({"body": body}))
    _, start, size = _entry(bytes(data), "word/document.xml")
    data[start + size // 2] ^= 0x01
    assert _damaged_message(tmp_path, bytes(data)) == "This Word document could not be opened."


def test_rtl_ruby_warning_text():
    assert W_RTL_RUBY == "ruby annotations and right-to-left direction"


def test_symbol_font_survives_saving_over_the_original(tmp_path):
    case = next(c for c in DOCX_CASES if c["name"] == "symbols")
    path = tmp_path / "sym.docx"
    path.write_bytes(docx_package(case))
    opened = read(path)
    write(opened.html, opened.images, path, template=path)
    runs = docx.Document(path).paragraphs[0].runs
    sym = next(r for r in runs if "\uf04a" in r.text)
    fonts = sym._r.find(qn("w:rPr")).find(qn("w:rFonts"))
    assert fonts.get(qn("w:ascii")) == "Wingdings"
    assert read(path).html == case["html"]


def test_saving_over_a_file_without_a_numbering_part(tmp_path):
    path = tmp_path / "plain.docx"
    path.write_bytes(docx_package({"body": "<w:p><w:r><w:t>intro</w:t></w:r></w:p>"}))
    opened = read(path)
    edited = opened.html + "<ul><li>a</li><li>b</li></ul><ol><li>c</li></ol>"
    write(edited, opened.images, path, template=path)
    assert read(path).html == edited
    import zipfile

    with zipfile.ZipFile(path) as z:
        types = z.read("[Content_Types].xml").decode()
        names = z.namelist()
    assert "word/numbering.xml" in names
    assert "wordprocessingml.numbering+xml" in types
