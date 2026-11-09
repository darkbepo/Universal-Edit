"""Documents → HTML → PDF without LibreOffice or Pandoc.

DOCX is read with mammoth (BSD-2), Markdown with Python-Markdown (BSD-3), and HTML is laid out
into PDF pages by PyMuPDF's Story engine — the same idea as the website (MuPDF lays out HTML).
The layout is simpler than LibreOffice's (no headers/footers, page breaks or exact fonts), so
the document route planner only picks these steps when LibreOffice isn't installed.
"""

from __future__ import annotations

import base64
import html
import re
import tempfile
from pathlib import Path

PAGE_CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.4; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
table { border-collapse: collapse; margin: 6pt 0; }
td, th { border: 1px solid #999; padding: 3pt 6pt; vertical-align: top; }
th { background: #eee; }
img { max-width: 100%; }
pre, code { font-family: monospace; font-size: 9.5pt; }
pre { white-space: pre-wrap; }
blockquote { margin-left: 18pt; color: #444; }
"""

A4 = (595, 842)  # points
MARGIN = 56  # ~20 mm


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _page(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title></head><body>\n{body}\n</body></html>\n"
    )


def docx_to_html(src: Path, dest: Path) -> Path:
    import mammoth

    with src.open("rb") as f:
        result = mammoth.convert_to_html(f)  # images become data: URIs
    dest.write_text(_page(src.stem, result.value), encoding="utf-8")
    return dest


def markdown_to_html(src: Path, dest: Path) -> Path:
    import markdown

    body = markdown.markdown(
        read_text(src), extensions=["tables", "fenced_code", "sane_lists", "toc"]
    )
    dest.write_text(_page(src.stem, body), encoding="utf-8")
    return dest


def text_to_html(src: Path, dest: Path) -> Path:
    dest.write_text(_page(src.stem, f"<pre>{html.escape(read_text(src))}</pre>"), encoding="utf-8")
    return dest


_DATA_URI = re.compile(r"""src=(["'])data:image/(\w+);base64,([^"']+)\1""")


def html_to_pdf(src: Path, dest: Path) -> Path:
    """Lay out an HTML file onto A4 pages with ~20 mm margins (PyMuPDF, in tools/pdf.py)."""
    from umc.tools import pdf

    text = read_text(src)
    with tempfile.TemporaryDirectory(prefix="umc-htmlpdf-") as tmp:
        # PyMuPDF's Story can't read data: URIs: write them out and point the <img> tags at them.
        n = 0

        def extract(m: re.Match) -> str:
            nonlocal n
            n += 1
            name = f"img{n}.{'jpg' if m.group(2) == 'jpeg' else m.group(2)}"
            (Path(tmp) / name).write_bytes(base64.b64decode(m.group(3)))
            return f'src="{name}"'

        text = _DATA_URI.sub(extract, text)
        pdf.layout_html(text, PAGE_CSS, [src.parent, Path(tmp)], dest, A4, MARGIN, src.name)
    return dest
