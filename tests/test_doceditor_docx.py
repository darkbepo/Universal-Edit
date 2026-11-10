from __future__ import annotations

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from tests.doceditor_samples import PNG_1PX
from umc.doceditor.docx_writer import write


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
