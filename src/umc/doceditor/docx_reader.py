"""Read a DOCX file into the editor's HTML subset, its pictures and "can't keep" warnings (no Qt).

The mirror image of ``docx_writer``: Heading 1-3 -> h1-h3, Quote -> blockquote, Code -> pre,
list paragraphs (``w:numPr``) -> ul / ol, tables -> table, ``w:hyperlink`` -> a, direct run
properties -> b / i / u / s / span. The result is passed through ``normalise_html``.
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from io import BytesIO
from pathlib import Path
from typing import Any

from docx.oxml.ns import qn
from docx.text.run import Run
from lxml import etree
from PIL import Image

from umc.core.errors import ConversionError
from umc.doceditor.docx_package import open_package
from umc.doceditor.docx_styles import (
    list_info,
    num_pr,
    style_chain,
    to_int,
    undefined_style,
    w_val,
)
from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import (
    W_COLUMNS,
    W_COMMENTS,
    W_EQUATIONS,
    W_FIELDS,
    W_HEADERS,
    W_NOTES,
    W_OBJECTS,
    W_PICTURES,
    W_RTL_RUBY,
    W_TEXTBOX,
    W_TRACKED,
    DocImages,
    OpenedDocument,
    next_image_name,
)

log = logging.getLogger(__name__)

_EMU_PER_PX = 9525
NBSP = "\xa0"
_HEADINGS = {f"Heading {n}": f"h{min(n, 3)}" for n in range(1, 10)} | {"Title": "h1"}
_BLOCK_STYLES = {
    **_HEADINGS,
    "Quote": "quote",
    "Intense Quote": "quote",
    "Code": "pre",
    "HTML Preformatted": "pre",
    "Source Code": "pre",
}
# Word's built-in style ids, used when the file doesn't define the paragraph's style (no
# styles part, or an id that isn't in it).
_BUILTIN_IDS = {f"Heading{n}": f"h{min(n, 3)}" for n in range(1, 10)} | {
    "Title": "h1",
    "Quote": "quote",
    "IntenseQuote": "quote",
    "Code": "pre",
    "HTMLPreformatted": "pre",
    "SourceCode": "pre",
}
_BUILTIN_LIST = re.compile(r"List(Bullet|Number)([2-9]?)")
_BUILTIN_LIST_IDS = {"Bullet": -1, "Number": -2}  # numIds of their own for these lists
_HEX = re.compile(r"[0-9A-Fa-f]{1,6}")
_ALIGN = {"left": "left", "center": "center", "right": "right", "both": "justify"}
_HIGHLIGHT = {
    "yellow": "#ffff00",
    "green": "#00ff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "blue": "#0000ff",
    "red": "#ff0000",
    "darkBlue": "#000080",
    "darkCyan": "#008080",
    "darkGreen": "#008000",
    "darkMagenta": "#800080",
    "darkRed": "#800000",
    "darkYellow": "#808000",
    "darkGray": "#808080",
    "lightGray": "#c0c0c0",
    "black": "#000000",
    "white": "#ffffff",
}
_IMAGE_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/x-ms-bmp": "bmp",
    "image/webp": "webp",
}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
_V = "{urn:schemas-microsoft-com:vml}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_TRACKED = {_W + t for t in ("ins", "del", "moveFrom", "moveTo")}
_TRANSPARENT = {
    _W + t
    for t in (
        "sdt",
        "sdtContent",
        "smartTag",
        "customXml",
        "ins",
        "moveTo",
        "fldSimple",
        "dir",
        "bdo",
    )
}
# Wrappers around block content that are read through (w:del / w:moveFrom are dropped).
_BLOCK_WRAPPERS = {_W + t for t in ("sdtContent", "customXml", "ins", "moveTo", "smartTag")}
_EQUATIONS = {_M + "oMath", _M + "oMathPara", _W + "altChunk"}
# A ruby annotation (w:rt) and a right-to-left direction are dropped (their text is kept).
_RTL_RUBY = {_W + "rt", _W + "dir", _W + "bdo"}


@dataclass(frozen=True)
class _Fmt:
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    family: str | None = None
    size: float | None = None
    color: str | None = None
    background: str | None = None


# (kind, value, format, link): kind is "t" (text), "br" or "img" (value = the <img> html).
_Item = tuple[str, str, _Fmt, str | None]


@dataclass
class _Segment:
    """Inline content of a paragraph; a page break inside it starts a new segment."""

    items: list[_Item] = field(default_factory=list)
    page_break: bool = False


@dataclass
class _Ctx:
    doc: Any
    images: DocImages = field(default_factory=dict)
    picture_names: dict[tuple[bytes, str], str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    _numbering: Any = field(default=False, repr=False)

    def numbering(self) -> Any:
        """The numbering part's XML, or None when the file has none (or it can't be read)."""
        if self._numbering is False:
            try:
                self._numbering = self.doc.part.numbering_part.element
            except Exception:  # python-docx can't create a missing one (NotImplementedError)
                self._numbering = None
        return self._numbering

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


def _alternative(node: etree._Element) -> etree._Element | None:
    """The branch of an ``mc:AlternateContent`` that is read: its Fallback, else its Choice."""
    fallback = node.find(_MC + "Fallback")
    return fallback if fallback is not None else node.find(_MC + "Choice")


def _wrapped(parent: etree._Element, tag: str) -> Iterator[etree._Element]:
    """The ``tag`` children of ``parent`` (rows of a table, cells of a row), also those inside
    content controls (``w:sdt``) and custom XML."""
    for node in parent:
        if node.tag == tag:
            yield node
        elif node.tag == _W + "sdt":
            inner = node.find(qn("w:sdtContent"))
            if inner is not None:
                yield from _wrapped(inner, tag)
        elif node.tag == _W + "customXml":
            yield from _wrapped(node, tag)


def _symbol(code: str | None) -> str:
    """The character of a ``w:sym`` (its hex code; a Private Use one is kept as it is), or ""
    for a code that isn't a character XML can hold."""
    if not code or not _HEX.fullmatch(code):
        return ""
    n = int(code, 16)
    bad = (
        n < 0x20
        or 0xD800 <= n <= 0xDFFF
        or 0xFDD0 <= n <= 0xFDEF
        or (n & 0xFFFE) == 0xFFFE
        or n > 0x10FFFF
    )
    return "" if bad else chr(n)


# ----- inline content --------------------------------------------------------------------


def _flag(rpr: Any, name: str) -> bool | None:
    """An on/off run property (None when it isn't set)."""
    el = rpr.find(qn(name)) if rpr is not None else None
    if el is None:
        return None
    val = (el.get(qn("w:val")) or "").lower()
    if name == "w:u":
        return val != "none"
    return val not in ("0", "false", "off")


def _rgb(rpr: Any) -> str | None:
    val = w_val(rpr, "w:color")
    return f"#{val.lower()}" if val and len(val) == 6 and val.lower() != "auto" else None


def _char_style_rprs(ctx: _Ctx, rpr: Any) -> list[Any]:
    """The rPr of the run's character style and the styles it is based on, nearest first."""
    out: list[Any] = []
    style_id = w_val(rpr, "w:rStyle")
    styles = ctx.doc.styles.element
    while style_id and len(out) < 20:
        style = styles.get_by_id(style_id)
        if style is None or style.get(qn("w:type")) != "character":
            break
        out.append(style.find(qn("w:rPr")))
        style_id = w_val(style, "w:basedOn")
    return out


def _font_name(run: Run) -> str | None:
    try:
        return run.font.name
    except Exception:
        return None


def _font_size(run: Run) -> float | None:
    try:
        size = run.font.size
    except Exception:  # w:sz isn't a number
        return None
    return None if size is None else round(size.pt, 2)


def _run_fmt(ctx: _Ctx, r: etree._Element, in_heading: bool, in_link: bool) -> _Fmt:
    """Direct run formatting, falling back to the run's character style chain (never the
    paragraph style or document defaults: a saved-over file keeps those itself)."""
    run = Run(r, None)  # type: ignore[arg-type]
    rpr = r.find(qn("w:rPr"))
    chain = _char_style_rprs(ctx, rpr)

    def resolved(name: str, from_styles: bool = True) -> bool:
        value = _flag(rpr, name)
        for style_rpr in chain if from_styles else ():
            if value is not None:
                break
            value = _flag(style_rpr, name)
        return bool(value)

    color = _rgb(rpr)
    if color is None and not in_link:  # a link's style colour is how links look anyway
        color = next((c for c in map(_rgb, chain) if c), None)
    shd = rpr.find(qn("w:shd")) if rpr is not None else None
    fill = shd.get(qn("w:fill")) if shd is not None else None
    background = _HIGHLIGHT.get(w_val(rpr, "w:highlight") or "")
    if fill and fill.lower() not in ("auto", "ffffff"):
        background = f"#{fill.lower()}"
    return _Fmt(
        bold=resolved("w:b") and not in_heading,
        italic=resolved("w:i"),
        underline=resolved("w:u", from_styles=not in_link),
        strike=resolved("w:strike"),
        family=_font_name(run),
        size=None if in_heading else _font_size(run),
        color=color,
        background=background,
    )


def _picture(ctx: _Ctx, drawing: etree._Element) -> str | None:
    blip = drawing.find(f".//{_A_BLIP}")
    if blip is None or blip.get(_R_EMBED) is None:
        return None
    try:
        part = ctx.doc.part.related_parts[blip.get(_R_EMBED)]
        data, ctype = part.blob, part.content_type
    except KeyError:
        ctx.warn(W_PICTURES)
        return None
    ext = _IMAGE_EXT.get(ctype)
    if ext is None:  # EMF, TIFF, ...: convert to PNG when Pillow can
        try:
            with Image.open(BytesIO(data)) as im:
                im.load()
                out = BytesIO()
                im.convert("RGBA").save(out, "PNG")
            data, ext = out.getvalue(), "png"
        except Exception:
            log.warning("Picture of type %s can't be read; left out", ctype)
            ctx.warn(W_PICTURES)
            return None
    name = ctx.picture_names.get((data, ext))  # one picture used twice keeps one name
    if name is None:
        name = next_image_name(ctx.images, ext)
        ctx.images[name] = data
        ctx.picture_names[(data, ext)] = name
    attrs = f' src="{name}"'
    extent = drawing.find(f".//{_WP}extent")
    if extent is not None:
        for key, attr in (("cx", "width"), ("cy", "height")):
            px = round(to_int(extent.get(key) or "0", 0) / _EMU_PER_PX)
            if px > 0:
                attrs += f' {attr}="{px}"'
    return f"<img{attrs}>"


def _run_items(
    ctx: _Ctx,
    r: etree._Element,
    fmt: _Fmt,
    link: str | None,
    segs: list[_Segment],
    heading: bool,
) -> None:
    def add(kind: str, value: str) -> None:
        segs[-1].items.append((kind, value, fmt, link))

    for child in r:
        tag = child.tag
        if tag == _MC + "AlternateContent":
            branch = _alternative(child)
            if branch is not None:
                _run_items(ctx, branch, fmt, link, segs, heading)
        elif tag == _W + "ruby":  # the base text; the annotation above it is left out
            base = child.find(qn("w:rubyBase"))
            if base is not None:
                _inline_nodes(ctx, base, heading, link, segs)
        elif tag == _W + "sym":
            if char := _symbol(child.get(qn("w:char"))):
                font = child.get(qn("w:font"))
                if font and "\ue000" <= char <= "\uf8ff":  # a symbol font's own character
                    segs[-1].items.append(("t", char, replace(fmt, family=font), link))
                else:
                    add("t", _keep_spaces(char, _last_char(segs[-1])))
        elif tag == _W + "t":
            add("t", _keep_spaces(child.text or "", _last_char(segs[-1])))
        elif tag == _W + "tab":
            add("t", "\t")
        elif tag == _W + "noBreakHyphen":
            add("t", "-")
        elif tag == _W + "cr":
            add("br", "")
        elif tag == _W + "br":
            if child.get(qn("w:type")) == "page":
                if segs[-1].items:
                    segs.append(_Segment())
                segs[-1].page_break = True
            else:
                add("br", "")
        elif tag == _W + "drawing" and (img := _picture(ctx, child)) is not None:
            add("img", img)


def _last_char(seg: _Segment) -> str:
    """The last character of the paragraph so far ("" at its start, "\n" after a break)."""
    for kind, value, _fmt, _link in reversed(seg.items):
        if kind == "br":
            return "\n"
        if kind == "img":
            return "\ufffc"
        if value:
            return value[-1]
    return ""


def _keep_spaces(text: str, before: str) -> str:
    """Spaces HTML would collapse become no-break spaces: one at the start of a line, or one
    after another space (the writer turns them back into ordinary spaces)."""
    out = []
    for c in text:
        if c == " " and before in ("", "\n", " ", NBSP):
            c = NBSP
        out.append(c)
        before = " " if c == NBSP else c
    return "".join(out)


def _inline_nodes(
    ctx: _Ctx, parent: etree._Element, heading: bool, link: str | None, segs: list[_Segment]
) -> None:
    for node in parent:
        tag = node.tag
        if tag == _W + "r":
            fmt = _run_fmt(ctx, node, heading, link is not None)
            _run_items(ctx, node, fmt, link, segs, heading)
        elif tag == _W + "hyperlink":
            href = None
            rid = node.get(_R_ID)
            if rid and rid in ctx.doc.part.rels and ctx.doc.part.rels[rid].is_external:
                href = ctx.doc.part.rels[rid].target_ref
            _inline_nodes(ctx, node, heading, href or link, segs)
        elif tag in _TRANSPARENT or tag == _MC + "AlternateContent":
            if tag == _W + "sdt":
                inner = node.find(qn("w:sdtContent"))
            else:
                inner = node if tag in _TRANSPARENT else _alternative(node)
            if inner is not None:
                _inline_nodes(ctx, inner, heading, link, segs)


def _render_items(items: list[_Item], pre: bool) -> str:
    out: list[str] = []
    i = 0
    while i < len(items):
        link = items[i][3]
        j = i
        while j < len(items) and items[j][3] == link:
            j += 1
        inner = _render_group(items[i:j], pre)
        out.append(f'<a href="{_html.escape(link, quote=True)}">{inner}</a>' if link else inner)
        i = j
    return "".join(out)


def _render_group(items: list[_Item], pre: bool) -> str:
    out: list[str] = []
    i = 0
    while i < len(items):
        kind, value, fmt, _ = items[i]
        i += 1
        if kind == "br":
            out.append("\n" if pre else "<br>")
        elif kind == "img":
            out.append(value)
        else:  # merge neighbouring text runs with identical formatting
            while i < len(items) and items[i][0] == "t" and items[i][2] == fmt:
                value += items[i][1]
                i += 1
            if pre:  # <pre> keeps every space as it is
                value = value.replace(NBSP, " ")
            out.append(_wrap(_html.escape(value, quote=False), fmt))
    return "".join(out)


def _wrap(text: str, fmt: _Fmt) -> str:
    style = []
    if fmt.family:
        style.append(f"font-family:{fmt.family}")
    if fmt.size:
        style.append(f"font-size:{fmt.size:g}pt")
    if fmt.color:
        style.append(f"color:{fmt.color}")
    if fmt.background:
        style.append(f"background-color:{fmt.background}")
    if style:
        text = f'<span style="{_html.escape(";".join(style), quote=True)}">{text}</span>'
    for flag, tag in ((fmt.strike, "s"), (fmt.underline, "u"), (fmt.italic, "i"), (fmt.bold, "b")):
        if flag:
            text = f"<{tag}>{text}</{tag}>"
    return text


# ----- blocks ----------------------------------------------------------------------------


def _is_hr(p: etree._Element) -> bool:
    ppr = p.find(qn("w:pPr"))
    border = ppr.find(qn("w:pBdr")) if ppr is not None else None
    bottom = border.find(qn("w:bottom")) if border is not None else None
    return bottom is not None and bottom.get(qn("w:val")) not in (None, "none", "nil")


def _is_blank(p: etree._Element) -> bool:
    """A paragraph with no text, picture or break."""
    return not (
        p.tag != _W + "p"
        or "".join(p.itertext()).strip()
        or p.find(f".//{_W}drawing") is not None
        or p.find(f".//{_W}br") is not None
        or _is_hr(p)
    )


def _para_style(p: etree._Element, with_indent: bool, page_break: bool) -> str:
    ppr = p.find(qn("w:pPr"))
    parts = []
    align = _ALIGN.get(w_val(ppr, "w:jc") or "")
    if align:
        parts.append(f"text-align:{align}")
    ind = ppr.find(qn("w:ind")) if ppr is not None else None
    left = ind.get(qn("w:left")) if ind is not None else None
    if with_indent and left:
        level = round(to_int(left, 0) / 20 / 36)
        if level >= 1:
            parts.append(f"margin-left:{36 * level}pt")
    pbb = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
    if page_break or (pbb is not None and pbb.get(qn("w:val")) not in ("0", "false", "off")):
        parts.append("page-break-before:always")
    return f' style="{";".join(parts)}"' if parts else ""


def _visible(borders: Any) -> bool:
    return any(
        (edge.get(qn("w:val")) or "none") not in ("none", "nil") for edge in borders.iterchildren()
    )


def _has_borders(ctx: _Ctx, tbl: etree._Element) -> bool:
    """Bordered when any cell has a visible border (LibreOffice writes only those), else by
    the table's own borders, else by its table style's (no style and no borders: none)."""
    for tc_borders in tbl.iter(qn("w:tcBorders")):
        if _visible(tc_borders):
            return True
    tbl_pr = tbl.find(qn("w:tblPr"))
    borders = tbl_pr.find(qn("w:tblBorders")) if tbl_pr is not None else None
    if borders is not None:
        return _visible(borders)
    style_id = w_val(tbl_pr, "w:tblStyle")
    styles = ctx.doc.styles.element
    for _ in range(20):
        style = styles.get_by_id(style_id) if style_id else None
        if style is None:
            return False
        style_pr = style.find(qn("w:tblPr"))
        borders = style_pr.find(qn("w:tblBorders")) if style_pr is not None else None
        if borders is not None:
            return _visible(borders)
        style_id = w_val(style, "w:basedOn")
    return False


class _Blocks:
    """Turns the paragraphs and tables of one container (body or table cell) into HTML."""

    def __init__(self, ctx: _Ctx) -> None:
        self.ctx = ctx
        self.out: list[str] = []
        self.lists: list[tuple[int, str]] = []  # open lists (numId, tag); the last <li> is open
        self.quote = False

    def run(self, container: etree._Element) -> str:
        nodes = list(self._children(container))
        if len(nodes) > 1 and nodes[-2].tag == _W + "tbl" and _is_blank(nodes[-1]):
            nodes.pop()  # Word needs a paragraph after a final table; it isn't content
        for node in nodes:
            if node.tag == _W + "tbl":
                self._close_all()
                self.out.append(self._table(node))
            else:
                self._paragraph(node)
        self._close_all()
        return "".join(self.out)

    def _children(self, container: etree._Element) -> Iterator[etree._Element]:
        for node in container:
            if node.tag == _W + "sdt":
                inner = node.find(qn("w:sdtContent"))
                if inner is not None:
                    yield from self._children(inner)
            elif node.tag in _BLOCK_WRAPPERS:
                yield from self._children(node)
            elif node.tag == _MC + "AlternateContent":
                branch = _alternative(node)
                if branch is not None:
                    yield from self._children(branch)
            elif node.tag in (_W + "p", _W + "tbl"):
                yield node

    def _close_lists(self, keep: int = 0) -> None:
        while len(self.lists) > keep:
            self.out.append(f"</li></{self.lists.pop()[1]}>")

    def _close_quote(self) -> None:
        if self.quote:
            self.out.append("</blockquote>")
            self.quote = False

    def _close_all(self) -> None:
        self._close_lists()
        self._close_quote()

    def _paragraph(self, p: etree._Element) -> None:
        chain = style_chain(self.ctx, p)
        kind = next((_BLOCK_STYLES[s.name] for s in chain if s.name in _BLOCK_STYLES), "p")
        builtin = undefined_style(self.ctx, p) if kind == "p" else None
        kind = _BUILTIN_IDS.get(builtin or "", kind)
        info = list_info(self.ctx, p, chain) if kind == "p" else None
        if info is None and builtin and num_pr(p) is None:
            m = _BUILTIN_LIST.fullmatch(builtin)
            if m:  # ListBullet, ListBullet2 (one level deeper), ..., ListNumber
                tag = "ul" if m.group(1) == "Bullet" else "ol"
                info = (_BUILTIN_LIST_IDS[m.group(1)], tag, int(m.group(2) or "1") - 1)
        segs = [_Segment()]
        _inline_nodes(self.ctx, p, kind in ("h1", "h2", "h3"), None, segs)
        if kind == "p" and info is None and len(segs) == 1 and not segs[0].items and _is_hr(p):
            self._close_all()
            self.out.append("<hr>")
            return
        if info is not None:
            self._list_item(info, p, [it for s in segs for it in s.items])
            return
        self._close_lists()
        if kind == "quote":
            if not self.quote:
                self.out.append("<blockquote>")
                self.quote = True
        else:
            self._close_quote()
        tag = "p" if kind == "quote" else kind
        for seg in segs:
            style = _para_style(p, True, seg.page_break)
            self.out.append(f"<{tag}{style}>{_render_items(seg.items, kind == 'pre')}</{tag}>")

    def _list_item(self, info: tuple[int, str, int], p: etree._Element, items: list[_Item]) -> None:
        num_id, tag, ilvl = info
        self._close_quote()
        ilvl = min(ilvl, len(self.lists))
        self._close_lists(ilvl + 1)
        if len(self.lists) == ilvl + 1:
            if self.lists[-1] == (num_id, tag):
                self.out.append("</li>")
            else:
                self._close_lists(ilvl)
        if len(self.lists) == ilvl:
            self.lists.append((num_id, tag))
            self.out.append(f"<{tag}>")
        self.out.append(f"<li{_para_style(p, False, False)}>{_render_items(items, False)}")

    def _table(self, tbl: etree._Element) -> str:
        rows: list[list[dict[str, Any]]] = []
        started: dict[int, dict[str, Any]] = {}  # grid column -> cell a bare vMerge continues
        for tr in _wrapped(tbl, _W + "tr"):
            cells: list[dict[str, Any]] = []
            col = 0
            for tc in _wrapped(tr, _W + "tc"):
                tcpr = tc.find(qn("w:tcPr"))
                span = to_int(w_val(tcpr, "w:gridSpan") or "1", 1)
                vm = tcpr.find(qn("w:vMerge")) if tcpr is not None else None
                if vm is not None and vm.get(qn("w:val")) != "restart" and col in started:
                    started[col]["rowspan"] += 1
                else:
                    cell = {"tc": tc, "colspan": span, "rowspan": 1}
                    cells.append(cell)
                    started[col] = cell
                col += span
            rows.append(cells)
        out = ["<table>" if _has_borders(self.ctx, tbl) else '<table style="border:none">']
        for cells in rows:
            out.append("<tr>")
            for cell in cells:
                attrs = "".join(f' {k}="{cell[k]}"' for k in ("colspan", "rowspan") if cell[k] > 1)
                inner = _Blocks(self.ctx).run(cell["tc"]) or "<p></p>"
                out.append(f"<td{attrs}>{inner}</td>")
            out.append("</tr>")
        out.append("</table>")
        return "".join(out)


# ----- warnings --------------------------------------------------------------------------


def _unsupported_field(instr: str | None) -> bool:
    """True for a field the editor can't keep (anything but page numbers)."""
    words = (instr or "").split()
    return bool(words) and words[0].upper() not in ("PAGE", "NUMPAGES")


def _collect_warnings(ctx: _Ctx) -> None:
    doc = ctx.doc
    for section in doc.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            try:
                if part.is_linked_to_previous:
                    continue
                el = part._element
            except Exception:  # the header's part is missing from the file
                continue
            if "".join(el.itertext()).strip() or el.find(f".//{_W}drawing") is not None:
                ctx.warn(W_HEADERS)
    found: set[str] = set()
    if any(rel.reltype.endswith("/comments") for rel in doc.part.rels.values()):
        found.add(W_COMMENTS)
    for el in doc.element.iter():
        tag = el.tag
        if not isinstance(tag, str):
            continue
        if tag in (_W + "txbxContent", _WPS + "wsp", _V + "shape"):
            found.add(W_TEXTBOX)
        elif tag == _W + "cols" and to_int(el.get(qn("w:num")) or "1", 1) > 1:
            found.add(W_COLUMNS)
        elif tag in _TRACKED:
            found.add(W_TRACKED)
        elif tag == _W + "commentReference":
            found.add(W_COMMENTS)
        elif tag in (_W + "footnoteReference", _W + "endnoteReference"):
            found.add(W_NOTES)
        elif tag in (_W + "fldSimple", _W + "instrText") and _unsupported_field(
            el.get(qn("w:instr")) or el.text
        ):
            found.add(W_FIELDS)
        elif tag == _W + "object":
            found.add(W_OBJECTS)
        elif tag in _EQUATIONS:
            found.add(W_EQUATIONS)
        elif tag in _RTL_RUBY:
            found.add(W_RTL_RUBY)
    for text in (
        W_TEXTBOX,
        W_COLUMNS,
        W_TRACKED,
        W_COMMENTS,
        W_NOTES,
        W_FIELDS,
        W_OBJECTS,
        W_EQUATIONS,
        W_RTL_RUBY,
    ):
        if text in found:
            ctx.warn(text)


def read(path: Path) -> OpenedDocument:
    """Read ``path`` (a DOCX) as the editor's HTML subset, its pictures and warnings."""
    try:
        document = open_package(path)
    except ConversionError:
        raise
    except Exception as exc:
        log.warning("Could not open %s as DOCX: %s", path, exc, exc_info=True)
        raise ConversionError("This Word document could not be opened.") from exc
    ctx = _Ctx(document)
    html = _Blocks(ctx).run(document.element.body)
    _collect_warnings(ctx)
    html, _ = normalise_html(html)
    return OpenedDocument(html, ctx.images, "docx", ctx.warnings)
