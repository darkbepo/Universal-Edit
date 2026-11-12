"""Round trips through real files for the final review fixes (no Qt)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.doceditor_samples import PNG_1PX
from umc.doceditor.html_subset import normalise_html


@pytest.mark.parametrize(
    "src", [r"\host\share\a.png", "//host/share/a.png", r"C:\x.png", "/etc/x.png", "C:/x.png"]
)
def test_picture_paths_never_touch_other_places(tmp_path, monkeypatch, src):
    def guard(name):
        real = getattr(Path, name)

        def check(self, *a, **k):
            text = str(self)
            if "host" in text or "x.png" in text:
                raise AssertionError(f"{name} called with {text}")
            return real(self, *a, **k)

        return check

    for name in ("resolve", "is_file", "stat", "read_bytes"):
        monkeypatch.setattr(Path, name, guard(name))
    html, imgs = normalise_html(f'<p>a<img src="{src}"></p>', base_dir=tmp_path)
    assert imgs == {} and html == "<p>a</p>"


def test_relative_picture_still_read(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "p.png").write_bytes(PNG_1PX)
    _, imgs = normalise_html('<p><img src="sub/p.png"></p>', base_dir=tmp_path)
    assert list(imgs) == ["img/1.png"]


# ----- helpers: open -> save over -> reopen, like the editor page does ----------------------
import docx  # noqa: E402

from umc.doceditor.io import open_document  # noqa: E402
from umc.doceditor.save import save_document  # noqa: E402
from umc.doceditor.types import SaveContent  # noqa: E402

NBSP = "\xa0"


def content_of(doc, path: Path) -> SaveContent:
    """What the page would hand to save_document for an unedited document."""
    fields = {}
    for name in ("front_matter", "title"):
        if hasattr(doc, name) and hasattr(SaveContent, name):
            fields[name] = getattr(doc, name)
    if "template" in SaveContent.__dataclass_fields__ and doc.source_format == "docx":
        fields["template"] = path
    return SaveContent(doc.html, dict(doc.images), **fields)


def cycle(path: Path, fmt: str, n: int = 3) -> list:
    """Open and save over ``path`` ``n`` times; returns every opened document."""
    opened = []
    for _ in range(n):
        doc = open_document(path)
        opened.append(doc)
        save_document(content_of(doc, path), path, fmt)
    opened.append(open_document(path))
    return opened


# ----- Critical 1: whitespace -------------------------------------------------------------


def test_normaliser_keeps_tabs_and_nbsp():
    html = f"<p>a\tb {NBSP}{NBSP}c</p><p>{NBSP}{NBSP}x</p>"
    assert normalise_html(html)[0] == html
    # source-code formatting (line breaks and the indentation around them) still collapses
    assert normalise_html("<p>\n\t\tHello\n\t\tworld\n</p>")[0] == "<p>Hello world</p>"


def test_txt_reader_marks_significant_spaces(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("def f():\n    return 1\n\tTabbed\nA    B\n", encoding="utf-8")
    assert open_document(p).html == (
        f"<p>def f():</p><p>{NBSP * 4}return 1</p><p>\tTabbed</p><p>A {NBSP * 3}B</p>"
    )


def test_docx_tabs_and_spaces_survive_three_saves(tmp_path):
    p = tmp_path / "a.docx"
    d = docx.Document()
    d.add_paragraph("Name:\tJohn    Smith")
    d.add_paragraph("  indented")
    d.save(p)
    opened = cycle(p, "docx")
    assert len({o.html for o in opened}) == 1
    texts = [par.text for par in docx.Document(p).paragraphs]
    assert texts == ["Name:\tJohn    Smith", "  indented"]
    assert NBSP not in "".join(texts)


# ----- Critical 2: our own Markdown writer ----------------------------------------------

MD_SAMPLE = """---
title: x
tags: [a, b]
---

# Notes

Some **bold**, *italic*, <u>under</u> and <s>struck</s> text with a [link](https://example.com/a).

```
def f():
    return 1

print(f())
```

> A quote
> that goes on.

- one
    - two
        - three
- four

| Name | Age |
| --- | --- |
| Ann | 30 |
| Bob |  |

![](pic.png)

A    B
"""


def test_markdown_survives_three_saves(tmp_path):
    p = tmp_path / "a.md"
    p.write_text(MD_SAMPLE, encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    original = open_document(p)
    assert original.front_matter == "---\ntitle: x\ntags: [a, b]\n---\n"
    assert "title" not in original.html and "<hr>" not in original.html
    save_document(content_of(original, p), p, "md")
    first = p.read_text(encoding="utf-8")
    opened = cycle(p, "md")
    assert p.read_text(encoding="utf-8") == first
    for doc in opened:
        assert doc.html == original.html
        assert doc.front_matter == original.front_matter
        assert list(doc.images.values()) == [PNG_1PX]
    html = original.html
    assert "<pre>def f():\n    return 1\n\nprint(f())\n</pre>" in html
    assert "<blockquote><p>A quote that goes on.</p></blockquote>" in html
    assert "<ul><li>one<ul><li>two<ul><li>three</li></ul></li></ul></li><li>four</li></ul>" in html
    assert "<b>bold</b>" in html and "<i>italic</i>" in html
    assert "<u>under</u>" in html and "<s>struck</s>" in html
    assert '<a href="https://example.com/a">link</a>' in html
    assert "<td><p>Ann</p></td>" in html
    assert first.startswith("---\ntitle: x\ntags: [a, b]\n---\n\n# Notes\n")


def test_markdown_keeps_aligned_spaces_and_leading_indent(tmp_path):
    p = tmp_path / "a.md"
    html = f"<p>A {NBSP * 3}B</p><p>{NBSP * 2}indented</p><p>\tTabbed</p>"
    save_document(SaveContent(html, {}), p, "md")
    for doc in cycle(p, "md"):
        assert doc.html == html
    assert "    indented" not in p.read_text(encoding="utf-8")  # never an indented code block


@pytest.mark.parametrize(
    "html",
    [
        "<p># not a heading</p><p>- not a list</p><p>1. not a list</p><p>&gt; not a quote</p>",
        "<p>*stars* _under_ `tick` [brackets] \back &lt;p&gt; &amp;amp; a|b</p>",
        "<p>---</p><p>```</p><p>~~~</p><p>+ plus</p>",
        "<h2>Title #</h2><ol><li>a</li><li></li><li>c</li></ol><ul><li>x</li></ul>",
        "<ul><li>a</li></ul><ul><li>b</li></ul><blockquote><p>q</p></blockquote><hr>",
        '<p><span style="font-family:Courier New;color:#ff0000">c</span> x<br>y</p>',
        '<p><a href="https://x.org/a b(1).html">sp</a> <b><i>bi</i></b></p>',
        "<table><tr><td><p>a|b</p></td><td><p>c</p><p>d</p></td></tr></table>",
    ],
)
def test_markdown_text_never_turns_into_syntax(tmp_path, html):
    p = tmp_path / "a.md"
    save_document(SaveContent(html, {}), p, "md")
    first = p.read_text(encoding="utf-8")
    doc = open_document(p)
    assert doc.html == normalise_html(html)[0]  # exactly, from the first save on
    save_document(content_of(doc, p), p, "md")
    assert p.read_text(encoding="utf-8") == first


# ----- Important 5: warnings for HTML / Markdown, the page title, CSS emphasis ------------
from umc.doceditor.types import W_MDEXTRA, W_PAGESTYLE, W_RAWHTML, W_WEBPICS  # noqa: E402

WEB_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>T</title>
<style>p { color: red }</style></head>
<body><p class="intro">Hello <img src="https://example.com/x.png"></p>
<iframe src="https://example.com/"></iframe></body></html>
"""


def test_html_warnings_and_title_kept(tmp_path):
    p = tmp_path / "a.html"
    p.write_text(WEB_PAGE, encoding="utf-8")
    doc = open_document(p)
    assert doc.warnings == [W_PAGESTYLE, W_WEBPICS, W_RAWHTML]
    assert doc.title == "T" and doc.html == "<p>Hello</p>"
    for again in cycle(p, "html")[1:]:  # after the first save there is nothing to lose
        assert again.title == "T" and again.html == "<p>Hello</p>"
        assert again.warnings == []
    assert "<title>T</title>" in p.read_text(encoding="utf-8")


def test_html_title_falls_back_to_file_name(tmp_path):
    out = save_document(SaveContent("<p>x</p>", {}), tmp_path / "notes.html", "html")
    assert "<title>notes</title>" in out.read_text(encoding="utf-8")


def test_plain_html_has_no_warnings(tmp_path):
    p = tmp_path / "a.html"
    p.write_text("<html><head><title>x</title></head><body><p>a</p></body></html>")
    assert open_document(p).warnings == []


def test_markdown_extras_warn(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("Text[^1]\n\n[^1]: note\n\n<div>raw</div>\n", encoding="utf-8")
    assert W_MDEXTRA in open_document(p).warnings
    p.write_text("```python\n<div>in code</div>\n```\n\nPlain <u>inline</u>\n", encoding="utf-8")
    assert open_document(p).warnings == []


@pytest.mark.parametrize(
    ("css", "out"),
    [
        ("font-weight:700;font-style:italic", "<b><i>x</i></b>"),
        ("font-weight:bold", "<b>x</b>"),
        ("font-weight:500", "x"),
        ("font-style:oblique;text-decoration:underline line-through", "<i><u><s>x</s></u></i>"),
        (
            "text-decoration-line:line-through;color:red",
            '<span style="color:#ff0000"><s>x</s></span>',
        ),
    ],
)
def test_css_emphasis_becomes_tags(css, out):
    assert normalise_html(f'<p><span style="{css}">x</span></p>')[0] == f"<p>{out}</p>"


# ----- Important 3: a Word file saved over keeps its look ----------------------------------
from docx.enum.section import WD_ORIENT  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Mm, Pt  # noqa: E402

from umc.doceditor.docx_writer import write  # noqa: E402
from umc.doceditor.types import W_LISTSTYLE  # noqa: E402


def _borders(table) -> dict[str, str]:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        return {}
    return {el.tag.split("}")[1]: el.get(qn("w:val")) for el in borders}


def make_styled_docx(path: Path) -> Path:
    d = docx.Document()
    sec = d.sections[0]
    sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(297), Mm(210)
    sec.left_margin = Mm(15)
    normal = d.styles["Normal"]
    normal.font.name, normal.font.size = "Arial", Pt(12)
    normal.paragraph_format.first_line_indent = Pt(18)
    normal.paragraph_format.space_after = Pt(9)
    par = d.add_paragraph("Plain then ")
    par.add_run("strong words").style = d.styles["Strong"]
    plain = d.add_table(1, 2)  # no style, no borders
    plain.cell(0, 0).text, plain.cell(0, 1).text = "x", "y"
    grid = d.add_table(2, 2)
    grid.style = d.styles["Table Grid"]
    for r in range(2):
        for c in range(2):
            grid.cell(r, c).text = f"{r}{c}"
    d.add_paragraph("End")
    d.save(path)
    return path


def test_word_look_is_kept_when_saving_over(tmp_path):
    p = make_styled_docx(tmp_path / "styled.docx")
    before = docx.Document(p).sections[0]
    size = (before.page_width, before.page_height, before.left_margin)
    first = open_document(p)
    assert "<b>strong words</b>" in first.html
    assert '<table style="border:none"><tr><td><p>x</p></td>' in first.html
    assert "<table><tr><td><p>00</p></td>" in first.html
    opened = cycle(p, "docx")
    assert {o.html for o in opened} == {first.html}
    d = docx.Document(p)
    sec = d.sections[0]
    assert sec.orientation == WD_ORIENT.LANDSCAPE
    assert (sec.page_width, sec.page_height, sec.left_margin) == size
    assert sec.page_width > sec.page_height
    normal = d.styles["Normal"]
    assert (normal.font.name, normal.font.size) == ("Arial", Pt(12))
    assert normal.paragraph_format.first_line_indent == Pt(18)
    assert normal.paragraph_format.space_after == Pt(9)
    plain, grid = d.tables
    assert set(_borders(plain).values()) == {"none"}
    assert plain.style is None or plain.style.name != "Table Grid"
    assert set(_borders(grid).values()) == {"single"}
    # the run is bold again in Word, and the body holds only what the editor wrote
    strong = [r for r in d.paragraphs[0].runs if "strong" in r.text]
    assert strong and all(r.bold for r in strong)
    assert d.paragraphs[-1].text == "End"


def test_word_template_without_the_editors_styles(tmp_path):
    p = tmp_path / "bare.docx"
    d = docx.Document()
    styles = d.styles.element
    for name in ("Heading 1", "Quote", "List Bullet", "Table Grid", "Hyperlink"):
        for s in list(styles):
            el = s.find(qn("w:name"))
            if el is not None and el.get(qn("w:val")).lower() == name.lower():
                styles.remove(s)
    d.add_paragraph("seed")
    d.save(p)
    html = (
        "<h1>T</h1><blockquote><p>q</p></blockquote><ul><li>a</li></ul>"
        '<table><tr><td><p>c</p></td></tr></table><p><a href="https://x.org/">x</a></p>'
    )
    save_document(SaveContent(html, {}, template=p), p, "docx")
    assert open_document(p).html == html
    for _ in cycle(p, "docx"):
        pass
    assert open_document(p).html == html


def test_lettered_and_roman_lists_warn(tmp_path):
    p = write("<ol><li>a</li><li>b</li></ol>", {}, tmp_path / "l.docx")
    d = docx.Document(p)
    numbering = d.part.numbering_part.element
    for fmt in numbering.iter(qn("w:numFmt")):
        fmt.set(qn("w:val"), "lowerLetter")
    d.save(p)
    doc = open_document(p)
    assert W_LISTSTYLE in doc.warnings and "<ol><li>a</li><li>b</li></ol>" in doc.html
    assert W_LISTSTYLE not in open_document(write("<ol><li>a</li></ol>", {}, p)).warnings


def test_character_style_chain_and_direct_formatting(tmp_path):
    p = tmp_path / "c.docx"
    d = docx.Document()
    base = d.styles.add_style("Loud", 2)
    base.font.italic = True
    loud = d.styles.add_style("Louder", 2)
    loud.base_style = base
    loud.font.bold = True
    loud.font.color.rgb = docx.shared.RGBColor(0xFF, 0, 0)
    par = d.add_paragraph()
    par.add_run("a").style = loud
    run = par.add_run("b")
    run.style = loud
    run.bold = False  # direct formatting wins over the style
    d.save(p)
    assert open_document(p).html == (
        '<p><b><i><span style="color:#ff0000">a</span></i></b>'
        '<i><span style="color:#ff0000">b</span></i></p>'
    )


# ----- smaller fixes: list item order, empty list items -----------------------------------


def test_text_after_a_nested_list_stays_after_it():
    html, _ = normalise_html("<ul><li>a<ul><li>b</li></ul>c</li><li>d</li></ul>")
    assert html == "<ul><li>a<ul><li>b</li></ul></li><li>c</li><li>d</li></ul>"
    assert normalise_html(html)[0] == html
    html, _ = normalise_html("<ul><li>a<ul><li>b</li></ul>\n<ul><li>e</li></ul></li></ul>")
    assert html == "<ul><li>a<ul><li>b</li></ul><ul><li>e</li></ul></li></ul>"


@pytest.mark.parametrize("fmt", ["docx", "md", "html"])
def test_empty_list_items_survive(tmp_path, fmt):
    html = "<ul><li>a</li><li></li><li>c</li></ul>"
    p = tmp_path / f"a.{fmt}"
    save_document(SaveContent(html, {}), p, fmt)
    for doc in cycle(p, fmt):
        assert doc.html == html


# ===== Fix round 2 ========================================================================
from docx.oxml import OxmlElement  # noqa: E402

from umc.doceditor.types import W_MDFORMAT  # noqa: E402


def test_cell_borders_count_as_a_bordered_table(tmp_path):
    p = tmp_path / "lo.docx"
    d = docx.Document()
    t = d.add_table(1, 2)  # no table style, no tblBorders: like LibreOffice
    for cell in t._cells:
        cell.text = "c"
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        for edge in ("top", "left", "bottom", "right"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), "4")
            borders.append(el)
        tc_pr.append(borders)
    d.save(p)
    opened = cycle(p, "docx")
    assert all("border:none" not in o.html and "<table>" in o.html for o in opened)
    assert set(_borders(docx.Document(p).tables[0]).values()) == {"single"}


def test_markdown_starting_with_a_rule_is_not_front_matter(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("---\n\n# Title\n\nBody text\n\n---\n\nMore\n", encoding="utf-8")
    doc = open_document(p)
    assert doc.front_matter == ""
    assert doc.html == "<hr><h1>Title</h1><p>Body text</p><hr><p>More</p>"
    p.write_text("---\ntitle: x\n---\n\nText\n", encoding="utf-8")
    assert open_document(p).front_matter == "---\ntitle: x\n---\n"
    p.write_text("---\nnot yaml\n---\n\nText\n", encoding="utf-8")
    assert open_document(p).front_matter == ""


def test_markdown_tab_inside_a_line_stays_a_tab(tmp_path):
    p = tmp_path / "a.md"
    save_document(SaveContent("<p>Name:\tJohn</p>", {}), p, "md")
    assert "&#9;" in p.read_text(encoding="utf-8")
    for doc in cycle(p, "md"):
        assert doc.html == "<p>Name:\tJohn</p>"


def test_escaped_footnote_bracket_is_not_an_extra(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("Plain \\[^1] text\n", encoding="utf-8")
    assert open_document(p).warnings == []


def test_markdown_picture_size_survives(tmp_path):
    p = tmp_path / "a.md"
    html = '<p>x<img src="img/1.png" width="20" height="10"></p>'
    save_document(SaveContent(html, {"img/1.png": PNG_1PX}), p, "md")
    text = p.read_text(encoding="utf-8")
    assert '<img src="a_files/1.png" width="20" height="10">' in text
    for doc in cycle(p, "md"):
        assert doc.html == html and doc.images == {"img/1.png": PNG_1PX}


@pytest.mark.parametrize(
    "source",
    ["Some `code` here\n", "#### Small\n", "![a picture](pic.png)\n"],
)
def test_markdown_formats_the_editor_drops_warn(tmp_path, source):
    (tmp_path / "pic.png").write_bytes(PNG_1PX)
    p = tmp_path / "a.md"
    p.write_text(source, encoding="utf-8")
    assert W_MDFORMAT in open_document(p).warnings


def test_markdown_without_dropped_formats_does_not_warn(tmp_path):
    p = tmp_path / "a.md"
    (tmp_path / "x.png").write_bytes(PNG_1PX)  # a missing picture would warn (W_PICTURES)
    p.write_text("```\n`not a span`\n#### in code\n```\n\n### Ok \\`tick\\` ![](x.png)\n")
    assert open_document(p).warnings == []


# ----- Task 5d fix round: canonical inline tags, spaces around empty links ----------------


@pytest.mark.parametrize(
    ("html", "out"),
    [
        ("<p><b>x</b><b>y</b></p>", "<p><b>xy</b></p>"),  # the same tag twice in a row is one
        ("<p><b><i>x</i></b><b>y</b></p>", "<p><b><i>x</i>y</b></p>"),
        ('<p><a href="u">x</a><a href="u">y</a></p>', '<p><a href="u">xy</a></p>'),
        ("<pre><b>a</b><b>b</b></pre>", "<pre><b>ab</b></pre>"),
        # nested ones merge all the way in one pass
        ("<p><b><i>x</i></b><b><i>y</i></b></p>", "<p><b><i>xy</i></b></p>"),
        (
            '<p><a href="u"><b>x</b></a><a href="u"><b>y</b></a></p>',
            '<p><a href="u"><b>xy</b></a></p>',
        ),
        ("<pre><b><i>a</i></b><b><i>b</i></b></pre>", "<pre><b><i>ab</i></b></pre>"),
        # ... but an empty link stays its own link (a badge row)
        ('<p><a href="u"></a><a href="u">t</a></p>', '<p><a href="u"></a><a href="u">t</a></p>'),
        ("<p><b>x</b> <b>y</b></p>", "<p><b>x</b> <b>y</b></p>"),
    ],
)
def test_adjacent_identical_inline_tags_merge(html, out):
    assert normalise_html(html)[0] == out
    assert normalise_html(out)[0] == out


@pytest.mark.parametrize(
    ("html", "out"),
    [
        ('<p>a <a href="u"></a> b</p>', '<p>a <a href="u"></a> b</p>'),
        (
            '<p><a href="a"></a> <a href="b"></a> <a href="c"></a> Hello</p>',
            '<p><a href="a"></a> <a href="b"></a> <a href="c"></a> Hello</p>',
        ),
        ('<p><a href="u"></a>   x</p>', '<p><a href="u"></a> x</p>'),
        ("<p>a <b>b</b> c</p>", "<p>a <b>b</b> c</p>"),
    ],
)
def test_spaces_around_empty_links_are_kept(html, out):
    assert normalise_html(html)[0] == out
    assert normalise_html(out)[0] == out
