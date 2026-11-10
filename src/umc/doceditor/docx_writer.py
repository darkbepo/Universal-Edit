"""Write the editor's HTML subset (plus its pictures) as a DOCX file (no Qt).

The mapping is mirrored exactly by ``docx_reader``: h1-h3 -> Heading 1-3, blockquote > p ->
Quote, pre -> Code, lists -> List Bullet / List Number paragraphs with their own ``w:num``,
tables -> Table Grid, links -> ``w:hyperlink``, spans -> run formatting.
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
from typing import Any

import docx
import lxml.html
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt, RGBColor
from lxml import etree

from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import DocImages

log = logging.getLogger(__name__)

_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}
_HEADINGS = {"h1": "Heading 1", "h2": "Heading 2", "h3": "Heading 3"}
_INLINE_FLAGS = {"b": "bold", "i": "italic", "u": "underline", "s": "strike"}
_EMU_PER_PX = 9525
NBSP = "\xa0"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


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
    link: bool = False


@dataclass
class _Ctx:
    images: DocImages
    numbering: Any  # the <w:numbering> element


def _tag(node: etree._Element) -> str:
    return node.tag.lower() if isinstance(node.tag, str) else ""


def _style_dict(node: etree._Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for decl in (node.get("style") or "").split(";"):
        key, sep, val = decl.partition(":")
        if sep:
            result[key.strip().lower()] = val.strip()
    return result


def _pt(value: str | None) -> float | None:
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*pt", (value or "").strip().lower())
    return float(m.group(1)) if m else None


def _ensure_styles(doc: Any) -> None:
    styles = doc.styles
    names = {s.name for s in styles}
    if "Hyperlink" not in names:
        link = styles.add_style("Hyperlink", WD_STYLE_TYPE.CHARACTER)
        link.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
        link.font.underline = True
    if "Code" not in names:
        code = styles.add_style("Code", WD_STYLE_TYPE.PARAGRAPH)
        code.base_style = styles["Normal"]
        code.font.name = "Consolas"
        code.font.size = Pt(10)


def _own_num(ctx: _Ctx, ordered: bool) -> int:
    """A new numbering with its own definition of one kind (decimal / bullet) at all 9 levels.

    The template's abstract definitions only describe level 0, so nested items need this."""
    ids = [int(a.get(qn("w:abstractNumId"))) for a in ctx.numbering.findall(qn("w:abstractNum"))]
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(max(ids, default=-1) + 1))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "hybridMultilevel")
    abstract.append(multi)
    for level in range(9):
        lvl = OxmlElement("w:lvl")
        lvl.set(qn("w:ilvl"), str(level))
        for tag, val in (
            ("w:start", "1"),
            ("w:numFmt", "decimal" if ordered else "bullet"),
            ("w:lvlText", f"%{level + 1}." if ordered else "•"),
            ("w:lvlJc", "left"),
        ):
            el = OxmlElement(tag)
            el.set(qn("w:val"), val)
            lvl.append(el)
        ppr = OxmlElement("w:pPr")
        ind = OxmlElement("w:ind")
        ind.set(qn("w:left"), str(720 * (level + 1)))
        ind.set(qn("w:hanging"), "360")
        ppr.append(ind)
        lvl.append(ppr)
        abstract.append(lvl)
    first_num = ctx.numbering.find(qn("w:num"))
    if first_num is not None:
        first_num.addprevious(abstract)
    else:
        ctx.numbering.append(abstract)
    num = ctx.numbering.add_num(int(abstract.get(qn("w:abstractNumId"))))
    if ordered:
        override = OxmlElement("w:lvlOverride")
        override.set(qn("w:ilvl"), "0")
        start = OxmlElement("w:startOverride")
        start.set(qn("w:val"), "1")
        override.append(start)
        num.append(override)
    return int(num.get(qn("w:numId")))


def _paragraph_props(par: Any, node: etree._Element) -> None:
    style = _style_dict(node)
    if style.get("text-align") in _ALIGN:
        par.alignment = _ALIGN[style["text-align"]]
    indent = _pt(style.get("margin-left"))
    if indent:
        par.paragraph_format.left_indent = Pt(indent)
    if style.get("page-break-before") == "always":
        par.paragraph_format.page_break_before = True


def _style_run(run: Any, fmt: _Fmt) -> None:
    run.bold = fmt.bold or None
    run.italic = fmt.italic or None
    run.underline = fmt.underline or None
    if fmt.strike:
        run.font.strike = True
    if fmt.link:
        run.style = "Hyperlink"
    if fmt.family:
        run.font.name = fmt.family
        run._r.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), fmt.family)
    if fmt.size:
        run.font.size = Pt(fmt.size)
    if fmt.color:
        run.font.color.rgb = RGBColor.from_string(fmt.color.lstrip("#").upper())
    if fmt.background:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fmt.background.lstrip("#").upper())
        run._r.get_or_add_rPr().append(shd)


def _place(par: Any, run: Any, holder: Any) -> None:
    """Move a run into a hyperlink element (runs start out as children of the paragraph)."""
    if holder is not par._p:
        holder.append(run._r)


def _inline(ctx: _Ctx, par: Any, node: etree._Element, fmt: _Fmt, holder: Any) -> None:
    """Write a node's text and children as runs of ``par`` (inside ``holder``)."""

    def text(value: str | None) -> None:
        if value:
            run = par.add_run(value.replace(NBSP, " "))  # python-docx writes tabs as w:tab
            for t in run._r.findall(qn("w:t")):
                t.set(XML_SPACE, "preserve")
            _style_run(run, fmt)
            _place(par, run, holder)

    text(node.text)
    for child in node:
        tag = _tag(child)
        if tag in _INLINE_FLAGS:
            _inline(ctx, par, child, replace(fmt, **{_INLINE_FLAGS[tag]: True}), holder)
        elif tag == "span":
            st = _style_dict(child)
            sub = replace(
                fmt,
                family=st.get("font-family") or fmt.family,
                size=_pt(st.get("font-size")) or fmt.size,
                color=st.get("color") or fmt.color,
                background=st.get("background-color") or fmt.background,
            )
            _inline(ctx, par, child, sub, holder)
        elif tag == "a" and child.get("href"):
            link = OxmlElement("w:hyperlink")
            link.set(
                qn("r:id"), par.part.relate_to(child.get("href"), RT.HYPERLINK, is_external=True)
            )
            par._p.append(link)
            _inline(ctx, par, child, replace(fmt, link=True), link)
        elif tag == "br":
            run = par.add_run()
            run.add_break()
            _place(par, run, holder)
        elif tag == "img":
            _picture(ctx, par, child, holder)
        elif tag:
            _inline(ctx, par, child, fmt, holder)
        text(child.tail)


def _picture(ctx: _Ctx, par: Any, node: etree._Element, holder: Any) -> None:
    src = node.get("src") or ""
    data = ctx.images.get(src)
    if data is None:
        log.warning("Picture %s has no data; left out of the document", src)
        return
    size: dict[str, Any] = {}
    for key in ("width", "height"):
        with contextlib.suppress(ValueError):
            size[key] = Emu(round(float(node.get(key) or "") * _EMU_PER_PX))
    run = par.add_run()
    try:
        run.add_picture(BytesIO(data), **size)
    except Exception:  # python-docx raises UnrecognizedImageError and friends
        log.warning("Picture %s is not a readable image; left out of the document", src)
        run._r.getparent().remove(run._r)
        return
    _place(par, run, holder)


def _new_par(container: Any, reuse: list[Any], style: str | None = None) -> Any:
    par = reuse.pop() if reuse else container.add_paragraph()
    if style:
        par.style = style
    return par


def _write_text_block(
    ctx: _Ctx, container: Any, reuse: list[Any], node: etree._Element, style: str
) -> None:
    par = _new_par(container, reuse, style)
    _paragraph_props(par, node)
    _inline(ctx, par, node, _Fmt(), par._p)


def _li_parts(li: etree._Element) -> list[tuple[str, etree._Element]]:
    """A list item as ("text", inline holder) / ("p", paragraph) / ("list", nested list)."""
    parts: list[tuple[str, etree._Element]] = []
    cur: etree._Element | None = None

    def flush() -> None:
        nonlocal cur
        if cur is not None and (cur.text or len(cur)):
            parts.append(("text", cur))
        cur = None

    def inline_holder() -> etree._Element:
        nonlocal cur
        if cur is None:
            cur = etree.Element("span")
        return cur

    if li.text:
        inline_holder().text = li.text
    for child in li:
        tag = _tag(child)
        if tag in ("ul", "ol"):
            flush()
            parts.append(("list", child))
        elif tag in ("p", "div"):
            flush()
            parts.append(("p", child))
        else:
            inline_holder().append(child)  # moves the child with its tail
            continue
        if child.tail:
            inline_holder().text = child.tail
    flush()
    return parts


def _write_list(
    ctx: _Ctx,
    container: Any,
    reuse: list[Any],
    node: etree._Element,
    level: int,
    num_id: int | None,
    num_ordered: bool | None = None,
) -> None:
    ordered = _tag(node) == "ol"
    if num_id is None or num_ordered != ordered:  # every top-level list gets its own numbering
        num_id = _own_num(ctx, ordered)
    style = "List Number" if ordered else "List Bullet"
    for li in node:
        if _tag(li) != "li":
            continue
        for kind, item in _li_parts(li):
            if kind == "list":
                _write_list(ctx, container, reuse, item, min(level + 1, 9), num_id, ordered)
                continue
            par = _new_par(container, reuse, style)
            _paragraph_props(par, item)
            num_pr = par._p.get_or_add_pPr().get_or_add_numPr()
            num_pr.get_or_add_ilvl().val = level - 1
            num_pr.get_or_add_numId().val = num_id
            _inline(ctx, par, item, _Fmt(), par._p)


def _table_rows(table: etree._Element) -> list[etree._Element]:
    """The table's own rows (not those of tables nested in its cells)."""
    rows: list[etree._Element] = []
    for child in table:
        if _tag(child) == "tr":
            rows.append(child)
        elif _tag(child) in ("thead", "tbody", "tfoot"):
            rows.extend(tr for tr in child if _tag(tr) == "tr")
    return rows


def _write_table(ctx: _Ctx, container: Any, node: etree._Element) -> None:
    rows = _table_rows(node)
    placed: list[tuple[int, int, int, int, etree._Element]] = []  # row, col, rowspan, colspan, td
    taken: set[tuple[int, int]] = set()
    ncols = 0
    for r, tr in enumerate(rows):
        c = 0
        for td in tr:
            if _tag(td) not in ("td", "th"):
                continue
            while (r, c) in taken:
                c += 1
            cs = max(int(td.get("colspan") or 1), 1)
            rs = min(max(int(td.get("rowspan") or 1), 1), len(rows) - r)
            taken.update((rr, cc) for rr in range(r, r + rs) for cc in range(c, c + cs))
            placed.append((r, c, rs, cs, td))
            c += cs
            ncols = max(ncols, c)
    if not placed:
        return
    table = container.add_table(len(rows), ncols)
    table.style = "Table Grid"
    for r, c, rs, cs, _td in placed:  # merge first: merging moves a cell's content
        if rs > 1 or cs > 1:
            table.cell(r, c).merge(table.cell(r + rs - 1, c + cs - 1))
    for r, c, _rs, _cs, td in placed:
        cell = table.cell(r, c)
        _write_blocks(ctx, cell, [cell.paragraphs[0]], td)
        if cell._tc[-1].tag == qn("w:tbl"):  # a cell must end with a paragraph
            cell.add_paragraph()


def _write_hr(container: Any, reuse: list[Any]) -> None:
    par = _new_par(container, reuse)
    border = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for key, val in (("val", "single"), ("sz", "6"), ("space", "1"), ("color", "auto")):
        bottom.set(qn(f"w:{key}"), val)
    border.append(bottom)
    par._p.get_or_add_pPr().append(border)


def _write_blocks(ctx: _Ctx, container: Any, reuse: list[Any], root: etree._Element) -> None:
    """Write the block children of ``root``; ``reuse`` holds a paragraph to fill first."""
    stray: etree._Element | None = None  # loose inline content between blocks

    def flush() -> None:
        nonlocal stray
        if stray is not None and (stray.text or len(stray)):
            _write_text_block(ctx, container, reuse, stray, "Normal")
        stray = None

    def loose() -> etree._Element:
        nonlocal stray
        if stray is None:
            stray = etree.Element("p")
        return stray

    if root.text and root.text.strip():
        loose().text = root.text
    for child in list(root):
        tag = _tag(child)
        if tag in _HEADINGS:
            flush()
            _write_text_block(ctx, container, reuse, child, _HEADINGS[tag])
        elif tag in ("p", "div"):
            flush()
            _write_text_block(ctx, container, reuse, child, "Normal")
        elif tag == "pre":
            flush()
            _write_text_block(ctx, container, reuse, child, "Code")
        elif tag == "blockquote":
            flush()
            for p in child:
                if _tag(p) == "p":
                    _write_text_block(ctx, container, reuse, p, "Quote")
        elif tag in ("ul", "ol"):
            flush()
            _write_list(ctx, container, reuse, child, 1, None)
        elif tag == "table":
            flush()
            _write_table(ctx, container, child)
        elif tag == "hr":
            flush()
            _write_hr(container, reuse)
        else:
            loose().append(child)  # inline element at block level; keeps its tail
            continue
        if child.tail and child.tail.strip():
            loose().text = child.tail
    flush()


def write(html: str, images: DocImages, dest: Path) -> Path:
    """Write ``html`` (the editor's subset) and its ``images`` as a DOCX at ``dest``."""
    html, _ = normalise_html(html)
    document = docx.Document()
    _ensure_styles(document)
    ctx = _Ctx(
        images,
        document.part.numbering_part.element,
    )
    root = lxml.html.fragment_fromstring(html or "<p></p>", create_parent="div")
    _write_blocks(ctx, document, [], root)
    if not document.paragraphs:
        document.add_paragraph()
    dest.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(dest))
    return dest
