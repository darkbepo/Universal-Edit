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


# ----- hand-built DOCX packages (shared vectors: both apps read them the same way) ----------

_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
_RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DOCX_NAMESPACES = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": _RT,
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
_W_EQUATIONS = "equations and embedded documents"


def _r(text: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'


def _p(inner: str, style: str = "") -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}{inner}</w:p>"


def _tc(text: str) -> str:
    return f"<w:tc>{_p(_r(text))}</w:tc>"


def _alt(choice: str, fallback: str | None) -> str:
    out = f'<mc:AlternateContent><mc:Choice Requires="w14">{choice}</mc:Choice>'
    if fallback is not None:
        out += f"<mc:Fallback>{fallback}</mc:Fallback>"
    return out + "</mc:AlternateContent>"


_BUILTIN_IDS = [
    "Heading1",
    "Heading2",
    "Heading3",
    "Heading4",
    "Heading9",
    "Title",
    "Quote",
    "IntenseQuote",
    "Code",
    "HTMLPreformatted",
    "SourceCode",
    "ListBullet",
    "ListBullet2",
    "ListNumber",
    "Fancy",
]
_STYLES_XML = (
    f'{_XML_HEAD}<w:styles xmlns:w="{DOCX_NAMESPACES["w"]}">'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="My Body"/></w:style>'
    '<w:style w:type="character" w:styleId="Heading2"><w:name w:val="Loud"/></w:style>'
    "</w:styles>"
)
_STYLES_TYPE = (
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
)

# name, body (w: elements; the final w:sectPr is added), and optionally rels / types (elements
# added to word/_rels/document.xml.rels and [Content_Types].xml) and parts (name -> text);
# html / warnings are what both readers must give.
DOCX_CASES: list[dict] = [
    {
        "name": "rows in content controls and custom XML",
        "body": "<w:tbl><w:tr>" + _tc("A") + "</w:tr>"
        "<w:sdt><w:sdtPr/><w:sdtContent><w:tr>" + _tc("B") + "</w:tr></w:sdtContent></w:sdt>"
        '<w:customXml w:element="x"><w:tr>' + _tc("C") + "</w:tr></w:customXml></w:tbl>",
        "html": '<table style="border:none"><tr><td><p>A</p></td></tr><tr><td><p>B</p></td></tr>'
        "<tr><td><p>C</p></td></tr></table>",
        "warnings": [],
    },
    {
        "name": "cells in content controls and custom XML",
        "body": "<w:tbl><w:tr>" + _tc("A") + "<w:sdt><w:sdtContent>" + _tc("B") + "</w:sdtContent>"
        '</w:sdt><w:customXml w:element="x">' + _tc("C") + "</w:customXml></w:tr></w:tbl>",
        "html": '<table style="border:none"><tr><td><p>A</p></td><td><p>B</p></td>'
        "<td><p>C</p></td></tr></table>",
        "warnings": [],
    },
    {
        "name": "right-to-left runs",
        "body": _p(
            '<w:dir w:val="rtl">' + _r("שלום ") + "</w:dir>"
            '<w:bdo w:val="rtl">' + _r("abc") + "</w:bdo>"
        ),
        "html": "<p>שלום abc</p>",
        "warnings": [],
    },
    {
        "name": "ruby keeps the base text",
        "body": _p(
            "<w:r><w:ruby><w:rubyPr/><w:rt>"
            + _r("kan")
            + "</w:rt><w:rubyBase>"
            + _r("漢")
            + "</w:rubyBase></w:ruby></w:r>"
            + _r("字")
        ),
        "html": "<p>漢字</p>",
        "warnings": [],
    },
    {
        "name": "alternate content uses the fallback, else the choice",
        "body": _alt(_p(_r("block choice")), _p(_r("block fallback")))
        + _p(_alt(_r("inline choice"), _r("inline fallback")))
        + _p("<w:r>" + _alt("<w:t>run choice</w:t>", "<w:t>run fallback</w:t>") + "</w:r>")
        + _p(_alt(_r("only choice"), None)),
        "html": "<p>block fallback</p><p>inline fallback</p><p>run fallback</p><p>only choice</p>",
        "warnings": [],
    },
    {
        "name": "symbols",
        "body": _p(
            '<w:r><w:t>a</w:t><w:sym w:font="Wingdings" w:char="F04A"/><w:t>b</w:t>'
            '<w:sym w:char="263a"/><w:sym w:char="0x41"/><w:sym w:char="D800"/>'
            '<w:sym w:char="9"/><w:sym w:char="110000"/><w:sym/></w:r>'
        ),
        "html": "<p>ab☺</p>",
        "warnings": [],
    },
    {
        "name": "an equation keeps the text around it",
        "body": _p(_r("E = ") + "<m:oMath><m:r><m:t>mc2</m:t></m:r></m:oMath>")
        + _p("<m:oMathPara><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></m:oMathPara>")
        + _p(_r("after")),
        "html": "<p>E =</p><p></p><p>after</p>",
        "warnings": [_W_EQUATIONS],
    },
    {
        "name": "an embedded HTML document",
        "body": _p(_r("before")) + '<w:altChunk r:id="rIdAlt"/>' + _p(_r("after")),
        "rels": f'<Relationship Id="rIdAlt" Type="{_RT}/aFChunk" Target="chunk.html"/>',
        "types": '<Default Extension="html" ContentType="text/html"/>',
        "parts": {"word/chunk.html": "<html><body><p>chunk</p></body></html>"},
        "html": "<p>before</p><p>after</p>",
        "warnings": [_W_EQUATIONS],
    },
    {
        "name": "Word's built-in style ids without a styles part",
        "body": "".join(_p(_r(f"s{n}"), style) for n, style in enumerate(_BUILTIN_IDS)),
        "html": "<h1>s0</h1><h2>s1</h2><h3>s2</h3><h3>s3</h3><h3>s4</h3><h1>s5</h1>"
        "<blockquote><p>s6</p><p>s7</p></blockquote><pre>s8</pre><pre>s9</pre><pre>s10</pre>"
        "<ul><li>s11<ul><li>s12</li></ul></li></ul><ol><li>s13</li></ol><p>s14</p>",
        "warnings": [],
    },
    {
        "name": "a style the file defines wins over the built-in id",
        "body": _p(_r("a"), "Heading1") + _p(_r("b"), "Heading2"),
        "rels": f'<Relationship Id="rIdS" Type="{_RT}/styles" Target="styles.xml"/>',
        "types": _STYLES_TYPE,
        "parts": {"word/styles.xml": _STYLES_XML},
        "html": "<p>a</p><h2>b</h2>",
        "warnings": [],
    },
]


def docx_package(case: dict) -> bytes:
    """The DOCX bytes of one of ``DOCX_CASES`` (web/tests/doceditor-docx.test.ts builds the same
    package in TypeScript)."""
    import zipfile

    ns = " ".join(f'xmlns:{k}="{v}"' for k, v in DOCX_NAMESPACES.items())
    parts = {
        "[Content_Types].xml": (
            f'{_XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="application/vnd.'
            'openxmlformats-package.relationships+xml"/><Default Extension="xml" '
            'ContentType="application/xml"/><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.'
            f'document.main+xml"/>{case.get("types", "")}</Types>'
        ),
        "_rels/.rels": (
            f'{_XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships"><Relationship Id="rId1" Type="{_RT}/officeDocument" '
            'Target="word/document.xml"/></Relationships>'
        ),
        "word/document.xml": (
            f"{_XML_HEAD}<w:document {ns}><w:body>{case['body']}<w:sectPr/></w:body></w:document>"
        ),
        "word/_rels/document.xml.rels": (
            f'{_XML_HEAD}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'relationships">{case.get("rels", "")}</Relationships>'
        ),
        **case.get("parts", {}),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts.items():
            z.writestr(name, text.encode("utf-8"))
    return out.getvalue()
