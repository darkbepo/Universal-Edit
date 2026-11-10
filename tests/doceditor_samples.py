"""Shared sample data for the document editor tests."""

from __future__ import annotations

import base64
import io

from PIL import Image


def _png_1px() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


PNG_1PX: bytes = _png_1px()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


# HTML fragments that must survive a DOCX round trip (also reused by the GUI tests).
CASES = [
    "<h1>T</h1><h2>U</h2><h3>V</h3>",
    '<p style="text-align:justify">a <b>b</b> <i>c</i> <u>d</u> <s>e</s></p>',
    '<p><span style="font-family:Arial;font-size:14pt;color:#ff0000;'
    'background-color:#ffcc00">f</span></p>',
    "<blockquote><p>q</p></blockquote><pre>code()</pre>",
    '<p style="margin-left:72pt">i</p><p style="page-break-before:always">n</p><hr>',
    '<p><a href="https://x.org/">x</a></p>',
    '<table><tr><td colspan="2"><p>a</p></td></tr>'
    "<tr><td><p>b</p></td><td><p>c</p></td></tr></table>",
    '<table style="border:none"><tr><td><p>x</p></td><td><p>y</p></td></tr></table>',
    "<ol><li>a</li><li></li><li>c</li></ol>",
]
