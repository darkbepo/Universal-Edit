"""Paragraph styles and lists of a DOCX document, as the reader needs them (no Qt).

The website's `docx-styles.ts` is the mirror image.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from lxml import etree

from umc.doceditor.types import W_LISTSTYLE

if TYPE_CHECKING:
    from umc.doceditor.docx_reader import _Ctx


def to_int(text: str | None, default: int | None = None) -> int | None:
    """``int(text)``, or ``default`` when it isn't a whole number (Word files can be odd)."""
    try:
        return int(text)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def w_val(el: Any, name: str) -> str | None:
    child = el.find(qn(name)) if el is not None else None
    return child.get(qn("w:val")) if child is not None else None


def style_chain(ctx: _Ctx, p: etree._Element) -> list[Any]:
    chain: list[Any] = []
    style = ctx.doc.styles.get_by_id(p.style, WD_STYLE_TYPE.PARAGRAPH)
    while style is not None and len(chain) < 20:
        chain.append(style)
        style = style.base_style
    return chain


def undefined_style(ctx: _Ctx, p: etree._Element) -> str | None:
    """The paragraph's style id when the file doesn't define it as a paragraph style."""
    style_id = p.style
    if not style_id:
        return None
    style = ctx.doc.styles.element.get_by_id(style_id)
    return style_id if style is None or style.get(qn("w:type")) != "paragraph" else None


def num_pr(el: Any) -> Any:
    ppr = el.find(qn("w:pPr")) if el is not None else None
    return ppr.find(qn("w:numPr")) if ppr is not None else None


def list_info(ctx: _Ctx, p: etree._Element, chain: list[Any]) -> tuple[int, str, int] | None:
    """(numId, "ul" | "ol", ilvl) for a list paragraph, else None."""
    sources = [num_pr(p)] + [num_pr(s.element) for s in chain]
    num_id = next((v for v in (w_val(s, "w:numId") for s in sources) if v is not None), None)
    ilvl = next((v for v in (w_val(s, "w:ilvl") for s in sources) if v is not None), "0")
    if not num_id or num_id == "0":
        return None
    kind = num_kind(ctx, num_id, ilvl)
    if kind == "none":
        return None
    num, level = to_int(num_id), to_int(ilvl)
    return None if num is None or level is None else (num, kind, max(level, 0))


def num_kind(ctx: _Ctx, num_id: str, ilvl: str) -> str:
    numbering = ctx.numbering()
    if numbering is None:  # no numbering part: a list we can't classify is a bullet list
        return "ul"
    num = next((n for n in numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == num_id), None)
    abstract_id = w_val(num, "w:abstractNumId")
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
    fmt = w_val(lvl, "w:numFmt")
    if fmt not in (None, "none", "bullet", "decimal"):
        ctx.warn(W_LISTSTYLE)  # still opened as a numbered list
    return "none" if fmt == "none" else "ul" if fmt == "bullet" else "ol"
