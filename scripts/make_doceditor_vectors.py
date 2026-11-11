"""Writes tests/fixtures/doceditor-vectors.json: test vectors made by the desktop document editor.

Both apps assert them (tests/test_doceditor_vectors.py, web/tests/doceditor-vectors.test.ts), so
the website's HTML normaliser, Markdown/TXT/DOCX readers and Markdown writer stay identical to
the desktop's.  Run:  .venv/Scripts/python scripts/make_doceditor_vectors.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.doceditor_samples import CASES, DOCX_CASES, docx_package  # noqa: E402
from umc.doceditor.docx_reader import read as read_docx  # noqa: E402
from umc.doceditor.html_subset import normalise_html_report  # noqa: E402
from umc.doceditor.io import open_document  # noqa: E402
from umc.doceditor.md_writer import to_markdown  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "doceditor-vectors.json"
NBSP = "\u00a0"
# A fixed 1x1 PNG (not encoded at run time, so the vectors never depend on the Pillow/zlib build).
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
)
PNG_1PX = base64.b64decode(PNG_B64)

NORMALISE = [
    # sanitising: scripts, classes, inline styles, fonts, headings
    '<html><body><h1 class="x">T</h1><script>bad()</script>'
    '<p style="text-align:center;color:red;margin-top:3px">a <strong>b</strong> '
    '<span style="color:RGB(255,0,0);font-size:12pt;position:absolute">c</span>'
    '<font face="Arial">d</font></p><div>e</div><h4>f</h4></body></html>',
    "<p>Blåbærgrød — Привет — 你好 — 😀</p>",
    '<p><img src="https://example.com/x.png"><a href="http://a">l</a></p>',
    # whitespace: tabs, NBSP, source formatting
    f"<p>a\tb {NBSP}{NBSP}c</p><p>{NBSP}{NBSP}x</p>",
    "<p>\n\t\tHello\n\t\tworld\n</p>",
    "<p>a   b</p>\n<p>  c  </p>",
    # CSS emphasis
    '<p><span style="font-weight:700;font-style:italic">x</span></p>',
    '<p><span style="font-weight:500">x</span></p>',
    '<p><span style="font-style:oblique;text-decoration:underline line-through">x</span></p>',
    '<p><span style="text-decoration-line:line-through;color:red">x</span></p>',
    # blocks inside inline tags
    '<a href="http://a"><div>one</div><div>two</div></a>',
    "<span><div>one</div><div>two</div></span>",
    # nested blocks, empty li, text after a nested list
    "<div><div><p>deep</p></div><div>x</div></div>",
    "<ul><li>a</li><li></li><li>c</li></ul>",
    "<ul><li>a<ul><li>b</li></ul>c</li><li>d</li></ul>",
    "<ul><li>a<ul><li>b</li></ul>\n<ul><li>e</li></ul></li></ul>",
    "<ol><li>a<ol><li>b<ol><li>c</li></ol></li></ol></li></ol>",
    # loose lists (markdown-it puts each item's text in a paragraph)
    "<ul>\n<li>\n<p>a</p>\n</li>\n<li>\n<p>b</p>\n</li>\n</ul>",
    "<ol>\n<li>\n<p>a</p>\n<p>para two</p>\n<ul>\n<li>x</li>\n</ul>\n</li>\n"
    "<li>\n<p>b</p>\n</li>\n</ol>",
    # tables
    '<table style="border:none"><tr><td><p>x</p></td><td><p>y</p></td></tr></table>',
    '<table border="1"><tr><th>H</th><th>I</th></tr><tr><td>1</td><td></td></tr></table>',
    '<table><tr><td colspan="2">a</td></tr><tr><td>b</td><td>c</td></tr></table>',
    # pages with style / class / remote picture (warnings)
    "<html><head><title>My page</title><style>p{color:red}</style></head><body>"
    '<p class="lead">Hello <img src="http://example.com/a.png"></p></body></html>',
    "<html><body><script>alert(1)</script><p>hi</p></body></html>",
    "  \n",
    f'<p>a<img src="data:image/png;base64,{base64.b64encode(PNG_1PX).decode()}" width="10"></p>',
    "<pre>line 1\n  line 2</pre><blockquote><p>q</p></blockquote><hr>",
    '<p style="text-align:justify;margin-left:36pt">m</p><p style="page-break-before:always">n</p>',
    # odd input that must not fail: a URL urllib can't split, a page without a body
    '<p><a href="http://[::1/x">t</a><img src="http://[::1/x.png"></p>',
    "<html><head><title>T</title></head></html>",
    "<html><p>x</p></html>",
    # indents are capped at 20 levels (720 pt); exponents aren't lengths
    '<p style="margin-left:1000000000000000000000000000000pt">x</p>',
    '<p style="margin-left:1e30pt">x</p><p style="margin-left:738pt">y</p>',
    f'<ul><li style="margin-left:{"9" * 400}px">x</li></ul>',
    # white space where a list item's paragraph starts or ends
    "<ul><li><span> </span><p>x</p></li></ul>",
    "<ul><li>a <p>b</p></li></ul>",
    "<ul><li><p>a </p>\nb</li><li><b>a </b><p>b</p></li></ul>",
    # numeric references as HTML5 reads them (Windows-1252 for 128-159)
    "<p>it&#146;s &#150; &#128;5</p>",
    "<p>&#x92;&#X96;&#0146;&#146x &#00000000000000000000150; &#129;&#160;&#127;</p>",
    "<p>&#0;a&#xD800;b&#x110000;c&#99999999999999999999999;d&#x10FFFF;</p>"
    '<p><a href="http://x/&#146;">l</a></p>',
    # references to characters XML can't hold become U+FFFD
    "<p>&#1;&#xFFFE;&#xD800;&#x110000;&#0;&#127;&#xFDD0;&#xFDEF;&#x1FFFF;&#x10FFFE;&#11;&#31;</p>"
    "<p>a&#9;b&#xFDCF;&#xFDF0;&#x10FFFD;</p>",
    # links whose host urllib refuses keep a safe scheme; pictures are dropped
    '<p><a href="http://[abc]/x">t</a><a href="javascript://[abc]/%0aalert(1)">j</a>'
    '<a href="//[abc]/y">r</a><a href="http://[::1/x">u</a><img src="http://[abc]/p.png"></p>',
    # raw text and RCDATA elements read as HTML5 does
    "<xmp>&#146; <b>x</b> &amp;</xmp>",
    "<noembed><i>a</i></noembed><noframes><i>a</i></noframes><p>b</p>",
    "<plaintext>a<p>b</p>",
    '<iframe><img src="http://x/a.png"></iframe><p>x</p>',
    '<script>"<xmp>"</script><p>hi</p><!-- <xmp> --><p>&#146;</p>',
    "<title>a<b>b</b>&#146;</title><textarea>&#1;<b>t</b></textarea><p>&#1;</p>",
    # an inline wrapper left empty before a break in a list item
    "<ul><li><b> </b><p>x</p></li></ul>",
    '<ul><li>a<span style="color:red"><i> </i></span><p>b</p></li></ul>',
    # a carriage return written as a reference
    "<p>a&#13;b</p><pre>a&#13;b</pre><p>c&#13;&#10;d</p>",
    # a stray </p> starts no paragraph (libxml2 ignores it; HTML5 would add an empty one)
    "<p><xmp>x <b>y</b></xmp></p><p>t<xmp>x</xmp>u</p>",
    "<p>a</p></p><div>b</p>c</div>x</p>y<p><pre>z</pre></p><p></p>",
    # a tag name inside a quoted attribute value starts nothing
    '<p><a title="<xmp>" href="http://a">l</a>&#1;</p><p>b</p>',
    "<p><span title='<xmp>'>s</span>&#146;<b>b</b></p>",
    '<xmp title=">">a&#1;</xmp><p>&#1;b</p>',
    "<p title='<pre>'>x</p><pre>\n\na</pre>",
    '<p data-x="<p>">a</p></p><p x=y>b</p><p =">">c</p>',
    # the "/" of a self-closing start tag is ignored, as HTML5 does
    "<p/>x</p><div/>y<p title='a/'/>t</p><p><a href=x/>l</a>a<br/>z</p>",
]

FENCE_MD = (
    "# Project\n\nIntro with **bold**, *italic*, <u>under</u> and <s>strike</s>.\n\n"
    "```python\ndef f():\n\n    return 1\n```\n\n> A quote\n> over two lines\n\n"
)
MARKDOWN = [
    {"md": FENCE_MD},
    {"md": "- one\n  - two\n    - three\n- four\n\n1. a\n2. b\n"},
    {"md": "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n"},
    {"md": "---\ntitle: Hello\ntags: [a, b]\n---\n\n# Body\n\ntext\n"},
    {"md": "---\n\n# After a rule\n\ntext\n\n---\n\nmore\n"},
    {"md": 'Sized: <img src="img/1.png" width="20">\n'},
    {"md": "A [link](https://example.com/) and an ![alt](https://example.com/x.png)\n"},
    {"md": "Text with `code` and a footnote[^1].\n\n[^1]: note\n"},
    {"md": "#### Small\n\nline one  \nline two\n"},
    {"md": "- a\n  - b\n    - c\n\n1. x\n   - y\n"},
    {"md": "- a\n\n- b\n"},
    {"md": "1. a\n\n   para two\n\n2. b\n"},
    {"md": "- [ ] todo\n- [x] done\n"},
    {"md": "<!-- note -->\n\n| a | b |\n|:--|--:|\n| 1 | 2 |\n"},
    {"md": "##\n\ntext\n"},
    {"md": "***bi*** and **_x_** and [a b](<u v>)\n"},
    {"md": "Café — *Blåbær* 你好\n\n* star\n* list\n"},
]
TXT = [
    "def f():\n    return 1\n\tTabbed\nA    B\n",
    "first\n\nsecond with <tags> & more\n",
    "Name     Qty   Price\nApple    3     1.50\n",
    "  leading and trailing  \n",
    "one line",
    "",
    "Blåbær — 你好\r\nWindows line ends\r\n",
]


def _vectors() -> dict:
    normalise = []
    for src in NORMALISE:
        out, images, warnings = normalise_html_report(src)
        entry = {"in": src, "out": out, "warnings": warnings}
        if images:
            entry["images"] = {k: base64.b64encode(v).decode("ascii") for k, v in images.items()}
        normalise.append(entry)

    markdown = []
    with tempfile.TemporaryDirectory(prefix="umc-vectors-") as tmp:
        base = Path(tmp)
        for n, item in enumerate(MARKDOWN):
            folder = base / f"m{n}"
            folder.mkdir()
            path = folder / "a.md"
            path.write_text(item["md"], encoding="utf-8", newline="")
            doc = open_document(path)
            entry = {
                "md": item["md"],
                "html": doc.html,
                "front_matter": doc.front_matter,
                "warnings": doc.warnings,
                "md_out": to_markdown(doc.html),
            }
            markdown.append(entry)

        txt = []
        for n, text in enumerate(TXT):
            path = base / f"t{n}.txt"
            path.write_bytes(text.encode("utf-8"))
            txt.append({"txt": text, "html": open_document(path).html})
        docx = []
        for n, case in enumerate(DOCX_CASES):
            path = base / f"d{n}.docx"
            path.write_bytes(docx_package(case))
            doc = read_docx(path)
            entry = {k: v for k, v in case.items() if k not in ("html", "warnings")}
            docx.append({**entry, "html": doc.html, "warnings": doc.warnings})
    return {
        "normalise": normalise,
        "markdown": markdown,
        "txt": txt,
        "docx": docx,
        "cases": list(CASES),
    }


def render() -> str:
    return json.dumps(_vectors(), ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
