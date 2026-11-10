"""Writes tests/fixtures/doceditor-vectors.json: test vectors made by the desktop document editor.

Both apps assert them (tests/test_doceditor_vectors.py, web/tests/doceditor-vectors.test.ts), so
the website's HTML normaliser, Markdown/TXT readers and Markdown writer stay identical to the
desktop's.  Run:  .venv/Scripts/python scripts/make_doceditor_vectors.py
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from tests.doceditor_samples import CASES  # noqa: E402
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
    return {"normalise": normalise, "markdown": markdown, "txt": txt, "cases": list(CASES)}


def render() -> str:
    return json.dumps(_vectors(), ensure_ascii=False, indent=1) + "\n"


def main() -> None:
    OUT.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
