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
    expected = html.replace("<td><p>c</p><p>d</p></td>", "<td><p>c<br>d</p></td>")
    assert doc.html == normalise_html(expected)[0]
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
