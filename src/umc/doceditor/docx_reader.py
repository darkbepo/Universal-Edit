"""Read a DOCX file into the editor's HTML subset, its pictures and "can't keep" warnings (no Qt).

The mirror image of ``docx_writer``: Heading 1-3 -> h1-h3, Quote -> blockquote, Code -> pre,
list paragraphs (``w:numPr``) -> ul / ol, tables -> table, ``w:hyperlink`` -> a, direct run
properties -> b / i / u / s / span. The result is passed through ``normalise_html``.
"""

from __future__ import annotations

import html as _html
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

import docx
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.text.run import Run
from lxml import etree
from PIL import Image

from umc.core.errors import ConversionError
from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import (
    W_COLUMNS,
    W_COMMENTS,
    W_FIELDS,
    W_HEADERS,
    W_NOTES,
    W_OBJECTS,
    W_PICTURES,
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
    )
}
# Wrappers around block content that are read through (w:del / w:moveFrom are dropped).
_BLOCK_WRAPPERS = {_W + t for t in ("sdtContent", "customXml", "ins", "moveTo", "smartTag")}


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
    warnings: list[str] = field(default_factory=list)

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


def _val(el: Any, name: str) -> str | None:
    child = el.find(qn(name)) if el is not None else None
    return child.get(qn("w:val")) if child is not None else None


# ----- styles and lists ------------------------------------------------------------------


def _style_chain(ctx: _Ctx, p: etree._Element) -> list[Any]:
    chain: list[Any] = []
    style = ctx.doc.styles.get_by_id(p.style, WD_STYLE_TYPE.PARAGRAPH)
    while style is not None and len(chain) < 20:
        chain.append(style)
        style = style.base_style
    return chain


def _num_pr(el: Any) -> Any:
    ppr = el.find(qn("w:pPr")) if el is not None else None
    return ppr.find(qn("w:numPr")) if ppr is not None else None


def _list_info(ctx: _Ctx, p: etree._Element, chain: list[Any]) -> tuple[int, str, int] | None:
    """(numId, "ul" | "ol", ilvl) for a list paragraph, else None."""
    sources = [_num_pr(p)] + [_num_pr(s.element) for s in chain]
    num_id = next((v for v in (_val(s, "w:numId") for s in sources) if v is not None), None)
    ilvl = next((v for v in (_val(s, "w:ilvl") for s in sources) if v is not None), "0")
    if not num_id or num_id == "0":
        return None
    try:
        kind = _num_kind(ctx, num_id, ilvl)
        return None if kind == "none" else (int(num_id), kind, max(int(ilvl), 0))
    except ValueError:
        return None


def _num_kind(ctx: _Ctx, num_id: str, ilvl: str) -> str:
    numbering = ctx.doc.part.numbering_part.element
    num = next((n for n in numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == num_id), None)
    abstract_id = _val(num, "w:abstractNumId")
    abstract = next(
        (
            a
            for a in numbering.findall(qn("w:abstractNum"))
            if a.get(qn("w:abstractNumId")) == abstract_id
        ),
        None,
    )
    if abstract is None:
        return "ul"
    levels = abstract.findall(qn("w:lvl"))
    lvl = next((x for x in levels if x.get(qn("w:ilvl")) == ilvl), levels[0] if levels else None)
    fmt = _val(lvl, "w:numFmt")
    return "none" if fmt == "none" else "ul" if fmt == "bullet" else "ol"


# ----- inline content --------------------------------------------------------------------


def _run_fmt(r: etree._Element, in_heading: bool) -> _Fmt:
    run = Run(r, None)  # type: ignore[arg-type]
    font = run.font
    color = None
    if font.color is not None and font.color.rgb is not None:
        color = f"#{font.color.rgb}".lower()
    rpr = r.find(qn("w:rPr"))
    shd = rpr.find(qn("w:shd")) if rpr is not None else None
    fill = shd.get(qn("w:fill")) if shd is not None else None
    background = _HIGHLIGHT.get(_val(rpr, "w:highlight") or "")
    if fill and fill.lower() not in ("auto", "ffffff"):
        background = f"#{fill.lower()}"
    return _Fmt(
        bold=bool(run.bold) and not in_heading,
        italic=bool(run.italic),
        underline=bool(run.underline),
        strike=bool(font.strike),
        family=font.name,
        size=None if in_heading or font.size is None else round(font.size.pt, 2),
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
    name = next_image_name(ctx.images, ext)
    ctx.images[name] = data
    attrs = f' src="{name}"'
    extent = drawing.find(f".//{_WP}extent")
    if extent is not None:
        for key, attr in (("cx", "width"), ("cy", "height")):
            px = round(int(extent.get(key) or 0) / _EMU_PER_PX)
            if px > 0:
                attrs += f' {attr}="{px}"'
    return f"<img{attrs}>"


def _run_items(
    ctx: _Ctx, r: etree._Element, fmt: _Fmt, link: str | None, segs: list[_Segment]
) -> None:
    def add(kind: str, value: str) -> None:
        segs[-1].items.append((kind, value, fmt, link))

    for child in r:
        tag = child.tag
        if tag == _W + "t":
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
            _run_items(ctx, node, _run_fmt(node, heading), link, segs)
        elif tag == _W + "hyperlink":
            href = None
            rid = node.get(_R_ID)
            if rid and rid in ctx.doc.part.rels and ctx.doc.part.rels[rid].is_external:
                href = ctx.doc.part.rels[rid].target_ref
            _inline_nodes(ctx, node, heading, href or link, segs)
        elif tag in _TRANSPARENT:
            inner = node.find(qn("w:sdtContent")) if tag == _W + "sdt" else node
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
    align = _ALIGN.get(_val(ppr, "w:jc") or "")
    if align:
        parts.append(f"text-align:{align}")
    ind = ppr.find(qn("w:ind")) if ppr is not None else None
    left = ind.get(qn("w:left")) if ind is not None else None
    if with_indent and left:
        try:
            level = round(int(left) / 20 / 36)
        except ValueError:
            level = 0
        if level >= 1:
            parts.append(f"margin-left:{36 * level}pt")
    pbb = ppr.find(qn("w:pageBreakBefore")) if ppr is not None else None
    if page_break or (pbb is not None and pbb.get(qn("w:val")) not in ("0", "false", "off")):
        parts.append("page-break-before:always")
    return f' style="{";".join(parts)}"' if parts else ""


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
        chain = _style_chain(self.ctx, p)
        kind = next((_BLOCK_STYLES[s.name] for s in chain if s.name in _BLOCK_STYLES), "p")
        info = _list_info(self.ctx, p, chain) if kind == "p" else None
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
        for tr in tbl.findall(qn("w:tr")):
            cells: list[dict[str, Any]] = []
            col = 0
            for tc in tr.findall(qn("w:tc")):
                tcpr = tc.find(qn("w:tcPr"))
                span = int(_val(tcpr, "w:gridSpan") or 1)
                vm = tcpr.find(qn("w:vMerge")) if tcpr is not None else None
                if vm is not None and vm.get(qn("w:val")) != "restart" and col in started:
                    started[col]["rowspan"] += 1
                else:
                    cell = {"tc": tc, "colspan": span, "rowspan": 1}
                    cells.append(cell)
                    started[col] = cell
                col += span
            rows.append(cells)
        out = ["<table>"]
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
            if part.is_linked_to_previous:
                continue
            el = part._element
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
        elif tag == _W + "cols" and int(el.get(qn("w:num")) or 1) > 1:
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
    for text in (W_TEXTBOX, W_COLUMNS, W_TRACKED, W_COMMENTS, W_NOTES, W_FIELDS, W_OBJECTS):
        if text in found:
            ctx.warn(text)


def read(path: Path) -> OpenedDocument:
    """Read ``path`` (a DOCX) as the editor's HTML subset, its pictures and warnings."""
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        log.warning("Could not open %s as DOCX: %s", path, exc, exc_info=True)
        raise ConversionError("This Word document could not be opened.") from exc
    ctx = _Ctx(document)
    html = _Blocks(ctx).run(document.element.body)
    _collect_warnings(ctx)
    html, _ = normalise_html(html)
    return OpenedDocument(html, ctx.images, "docx", ctx.warnings)
