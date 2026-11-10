"""Write a QTextDocument back as the editor's HTML subset.

The document is walked block by block (never through Qt's own ``toHtml()``), and only the
properties a format actually *has* are written, so what was loaded comes back unchanged.
"""

from __future__ import annotations

import html as _html

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QGuiApplication,
    QTextBlock,
    QTextCharFormat,
    QTextDocument,
    QTextFormat,
    QTextFrame,
    QTextListFormat,
    QTextTable,
)

from umc.doceditor.html_subset import normalise_html

# Custom block property: the paragraph is a quote (shared with the text view).
QUOTE_PROPERTY = int(QTextFormat.Property.UserProperty) + 1

_BULLETS = {
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
}
_ALIGN = {
    Qt.AlignmentFlag.AlignHCenter: "center",
    Qt.AlignmentFlag.AlignRight: "right",
    Qt.AlignmentFlag.AlignJustify: "justify",
}
_ALIGN_BITS = (
    Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignJustify
)
_P = QTextFormat.Property
NBSP = "\xa0"
LINE_SEPARATOR = "\u2028"  # Shift+Enter inside a block
OBJECT = "\ufffc"  # a picture


def _rgb(brush) -> str | None:
    if brush.style() == Qt.BrushStyle.NoBrush:
        return None
    return brush.color().name()


def _inline(fmt: QTextCharFormat, text: str, *, heading: bool, code: bool) -> str:
    """One fragment of text with its character format."""
    body = _html.escape(text, quote=False).replace(" ", "<br>")
    anchor = fmt.anchorHref() if fmt.isAnchor() else ""
    styles = []
    if not code and fmt.hasProperty(_P.FontFamilies) and fmt.fontFamilies():
        styles.append(f"font-family:{fmt.fontFamilies()[0]}")
    if not (heading or code) and fmt.hasProperty(_P.FontPointSize):
        styles.append(f"font-size:{fmt.fontPointSize():g}pt")
    if fmt.hasProperty(_P.ForegroundBrush):
        colour = _rgb(fmt.foreground())
        link = QGuiApplication.palette().link().color().name()
        if colour and not (anchor and colour == link):  # Qt colours links itself
            styles.append(f"color:{colour}")
    if fmt.hasProperty(_P.BackgroundBrush) and (colour := _rgb(fmt.background())):
        styles.append(f"background-color:{colour}")
    wraps: list[str] = []
    if not heading and fmt.hasProperty(_P.FontWeight) and fmt.fontWeight() >= 600:
        wraps.append("b")
    if fmt.hasProperty(_P.FontItalic) and fmt.fontItalic():
        wraps.append("i")
    if not anchor and fmt.fontUnderline():  # Qt underlines links itself
        wraps.append("u")
    if fmt.hasProperty(_P.FontStrikeOut) and fmt.fontStrikeOut():
        wraps.append("s")
    for tag in reversed(wraps):
        body = f"<{tag}>{body}</{tag}>"
    if styles:
        body = f'<span style="{_html.escape(";".join(styles))}">{body}</span>'
    if anchor:
        body = f'<a href="{_html.escape(anchor)}">{body}</a>'
    return body


def _image(fmt: QTextCharFormat) -> str:
    img = fmt.toImageFormat()
    attrs = f'src="{_html.escape(img.name())}"'
    if img.hasProperty(_P.ImageWidth) and img.width() > 0:
        attrs += f' width="{round(img.width())}"'
    if img.hasProperty(_P.ImageHeight) and img.height() > 0:
        attrs += f' height="{round(img.height())}"'
    return f"<img {attrs}>"


def _block_style(block: QTextBlock) -> str:
    fmt = block.blockFormat()
    parts = []
    if fmt.hasProperty(_P.BlockAlignment):  # left is the default and never written
        align = _ALIGN.get(fmt.alignment() & _ALIGN_BITS)
        if align:
            parts.append(f"text-align:{align}")
    if fmt.indent() > 0:
        parts.append(f"margin-left:{36 * fmt.indent()}pt")
    if fmt.pageBreakPolicy() & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore:
        parts.append("page-break-before:always")
    return f' style="{";".join(parts)}"' if parts else ""


def _keep_spaces(text: str, before: str) -> tuple[str, str]:
    """The TXT reader's rule for typed text: a space at the start of a line or after another
    space becomes a no-break space, so HTML doesn't collapse it. Returns (text, last char)."""
    out = []
    for c in text:
        if c == " " and before in ("", " ", NBSP, LINE_SEPARATOR):
            c = NBSP
        out.append(c)
        before = c
    return "".join(out), before


def _block_text(block: QTextBlock, *, heading: bool = False, code: bool = False) -> str:
    out = []
    before = ""  # the start of the block
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        fmt = frag.charFormat()
        if fmt.isImageFormat():
            out.append(_image(fmt) * len(frag.text()))
            before = OBJECT
        else:
            text = frag.text()
            if not code:  # <pre> keeps every space anyway
                text, before = _keep_spaces(text, before)
            out.append(_inline(fmt, text, heading=heading, code=code))
        it += 1
    return "".join(out)


class _Writer:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.lists: list[tuple[int, object, str]] = []  # (indent, QTextList, tag)
        self.quote = False

    def close_lists(self, to_indent: int = 0) -> None:
        while self.lists and self.lists[-1][0] > to_indent:
            self.out.append(f"</li></{self.lists.pop()[2]}>")

    def close_quote(self) -> None:
        if self.quote:
            self.out.append("</blockquote>")
            self.quote = False

    def list_item(self, block: QTextBlock) -> None:
        lst = block.textList()
        lfmt = lst.format()
        indent = max(1, lfmt.indent())
        tag = "ul" if lfmt.style() in _BULLETS else "ol"
        self.close_lists(indent)
        if self.lists and self.lists[-1][0] == indent and self.lists[-1][1] != lst:
            self.close_lists(indent - 1)
        if self.lists and self.lists[-1][0] == indent:
            self.out.append("</li>")
        else:
            self.out.append(f"<{tag}>")
            self.lists.append((indent, lst, tag))
        self.out.append(f"<li{_block_style(block)}>{_block_text(block)}")

    def block(self, block: QTextBlock) -> None:
        fmt = block.blockFormat()
        if block.textList() is not None:
            self.close_quote()
            self.list_item(block)
            return
        self.close_lists()
        quote = bool(fmt.boolProperty(QUOTE_PROPERTY))
        if quote != self.quote:
            self.close_quote()
            if quote:
                self.out.append("<blockquote>")
                self.quote = True
        style = _block_style(block)
        level = fmt.headingLevel()
        if fmt.hasProperty(_P.BlockTrailingHorizontalRulerWidth):
            self.out.append("<hr>")
        elif level > 0 and not quote:
            tag = f"h{min(level, 3)}"
            self.out.append(f"<{tag}{style}>{_block_text(block, heading=True)}</{tag}>")
        elif fmt.nonBreakableLines() and not quote:
            self.out.append(f"<pre>{_block_text(block, code=True)}</pre>")
        else:
            self.out.append(f"<p{style}>{_block_text(block)}</p>")

    def finish(self) -> None:
        self.close_lists()
        self.close_quote()


def _children(it: QTextFrame.iterator) -> list[QTextBlock | QTextFrame]:
    items: list[QTextBlock | QTextFrame] = []
    while not it.atEnd():
        frame = it.currentFrame()
        items.append(frame if frame is not None else it.currentBlock())
        it += 1
    return items


def _container(it: QTextFrame.iterator) -> str:
    """The blocks and tables of one frame or table cell."""
    items = _children(it)
    w = _Writer()
    for i, item in enumerate(items):
        if isinstance(item, QTextFrame):
            w.finish()
            if isinstance(item, QTextTable):
                w.out.append(_table(item))
            continue
        near_frame = (i > 0 and isinstance(items[i - 1], QTextFrame)) or (
            i + 1 < len(items) and isinstance(items[i + 1], QTextFrame)
        )
        fmt = item.blockFormat()
        if (
            near_frame
            and not item.text()
            and item.textList() is None
            and not fmt.hasProperty(_P.BlockTrailingHorizontalRulerWidth)
        ):
            continue  # Qt's own empty paragraph around a table
        w.block(item)
    w.finish()
    return "".join(w.out)


def _table(table: QTextTable) -> str:
    rows = []
    for r in range(table.rows()):
        cells = []
        for c in range(table.columns()):
            cell = table.cellAt(r, c)
            if cell.row() != r or cell.column() != c:
                continue  # covered by a spanning cell
            span = ""
            if cell.columnSpan() > 1:
                span += f' colspan="{cell.columnSpan()}"'
            if cell.rowSpan() > 1:
                span += f' rowspan="{cell.rowSpan()}"'
            cells.append(f"<td{span}>{_container(cell.begin())}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    tag = "<table>" if table.format().border() > 0 else '<table style="border:none">'
    return f"{tag}{''.join(rows)}</table>"


def document_to_html(doc: QTextDocument) -> str:
    """The document as the editor's (normalised) HTML subset."""
    if doc.isEmpty():
        return ""
    return normalise_html(_container(doc.rootFrame().begin()))[0]
