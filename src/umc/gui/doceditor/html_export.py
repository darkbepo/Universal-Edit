"""Write a QTextDocument back as the editor's HTML subset.

The document is walked block by block (never through Qt's own ``toHtml()``), and only the
properties a format actually *has* are written, so what was loaded comes back unchanged.
"""

from __future__ import annotations

import html as _html
import itertools
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QGuiApplication,
    QTextBlock,
    QTextBlockFormat,
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
# Custom block property: which code block a code line belongs to (an int). Lines typed in a
# block copy it (Qt copies the block format on Enter); where it changes between two code
# lines, one block ends and the next begins.
CODE_GROUP = int(QTextFormat.Property.UserProperty) + 2
# Custom block property: how a code block is written. Without it, one <pre> per line (Word's
# code lines, the editor's own); CODE_WHOLE: the block is one <pre> ending with a line break
# (a Markdown code block); CODE_JOINED: one <pre> without it (a web page's <pre>).
CODE_FORM = int(QTextFormat.Property.UserProperty) + 3
CODE_WHOLE = 1
CODE_JOINED = 2
# Custom block property: which quote a quoted paragraph belongs to (an int; two quotes in a
# row stay two). Typed quote lines have none and join the quote above.
QUOTE_GROUP = int(QTextFormat.Property.UserProperty) + 4
# Custom table cell property: Qt added the cell to fill a short row; while it stays empty it
# isn't written.
PADDED_CELL = int(QTextFormat.Property.UserProperty) + 5
_groups = itertools.count(1)


def new_code_group() -> int:
    """A number no code block has yet."""
    return next(_groups)


def set_code_group(fmt: QTextBlockFormat, group: int, form: int = 0) -> None:
    """Make ``fmt`` a line of code block ``group``, written in ``form`` (CODE_FORM)."""
    fmt.setProperty(CODE_GROUP, group)
    if form:
        fmt.setProperty(CODE_FORM, form)
    else:
        fmt.clearProperty(CODE_FORM)


def code_group_of(fmt: QTextBlockFormat) -> tuple[int, int] | None:
    """(group, form) of a code line's format, None without a group."""
    if not fmt.hasProperty(CODE_GROUP):
        return None
    return fmt.intProperty(CODE_GROUP), (
        fmt.intProperty(CODE_FORM) if fmt.hasProperty(CODE_FORM) else 0
    )


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
# An empty link (a badge whose web picture was dropped) holds this invisible character in the
# view, because Qt drops empty anchors; it is never exported.
LINK_PLACEHOLDER = "\u2063"
# A loaded fragment's tool tip "umc-wrap:b a" records how its tags were nested, outermost
# first (Qt keeps only the formats, not their order); it is never exported.
WRAP_PREFIX = "umc-wrap:"


def _rgb(brush) -> str | None:
    if brush.style() == Qt.BrushStyle.NoBrush:
        return None
    return brush.color().name()


def _wraps(fmt: QTextCharFormat, *, heading: bool, code: bool) -> list[tuple[str, str, str]]:
    """The tags a fragment is wrapped in, as (name, opening tag, closing tag), outermost
    first in the default order: link, span, b, i, u, s."""
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
    out = []
    if anchor:
        out.append(("a", f'<a href="{_html.escape(anchor)}">', "</a>"))
    if styles:
        out.append(("span", f'<span style="{_html.escape(";".join(styles))}">', "</span>"))
    if not heading and fmt.hasProperty(_P.FontWeight) and fmt.fontWeight() >= 600:
        out.append(("b", "<b>", "</b>"))
    if fmt.hasProperty(_P.FontItalic) and fmt.fontItalic():
        out.append(("i", "<i>", "</i>"))
    if not anchor and fmt.fontUnderline():  # Qt underlines links itself
        out.append(("u", "<u>", "</u>"))
    if fmt.hasProperty(_P.FontStrikeOut) and fmt.fontStrikeOut():
        out.append(("s", "<s>", "</s>"))
    return out


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
    space becomes a no-break space, so HTML doesn't collapse it (a space after a no-break
    space is kept: it doesn't collapse). Returns (text, last char)."""
    out = []
    for c in text:
        out.append(NBSP if c == " " and before in ("", " ", LINE_SEPARATOR) else c)
        before = c
    return "".join(out), before


def _ordered(wraps: list[tuple[str, str, str]], order: list[str], stack: list) -> list:
    """The fragment's tags outermost first: as the loaded document nested them (``order``,
    see text_view.WRAP_ORDER), else the tags already open first, then the default order."""
    rest = list(wraps)
    first = []
    if order:
        for name in order:
            for w in rest:
                if w[0] == name:
                    first.append(w)
                    rest.remove(w)
                    break
    else:
        for w in stack:
            if w not in rest:
                break
            first.append(w)
            rest.remove(w)
    return first + rest


def _block_text(block: QTextBlock, *, heading: bool = False, code: bool = False) -> str:
    out: list[str] = []
    stack: list[tuple[str, str, str]] = []  # the tags open now, outermost first
    before = ""  # the start of the block
    empty_link = False  # the last thing written was an empty link (LINK_PLACEHOLDER)

    def write(wraps: list, body: str, *, fresh_link: bool = False) -> None:
        keep = 0
        while keep < min(len(stack), len(wraps)) and stack[keep] == wraps[keep]:
            if fresh_link and wraps[keep][0] == "a":
                break  # two empty links in a row stay two links
            keep += 1
        while len(stack) > keep:
            out.append(stack.pop()[2])
        for w in wraps[keep:]:
            out.append(w[1])
            stack.append(w)
        out.append(body)

    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        fmt = frag.charFormat()
        order = fmt.toolTip().removeprefix(WRAP_PREFIX).split() if fmt.toolTip() else []
        wraps = _ordered(_wraps(fmt, heading=heading, code=code), order, stack)
        if fmt.isImageFormat():
            for _ in frag.text():
                write(wraps, _image(fmt))
            before, empty_link = OBJECT, False
            it += 1
            continue
        for part in re.split(f"({LINK_PLACEHOLDER})", frag.text()):
            if part == LINK_PLACEHOLDER:
                if fmt.isAnchor():
                    write(wraps, "", fresh_link=empty_link)
                    empty_link = True
                before = OBJECT
                continue
            if not part:
                continue
            if not code:  # <pre> keeps every space anyway
                part, before = _keep_spaces(part, before)
            write(wraps, _html.escape(part, quote=False).replace(LINE_SEPARATOR, "<br>"))
            empty_link = False
        it += 1
    while stack:
        out.append(stack.pop()[2])
    return "".join(out)


class _Writer:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.lists: list[tuple[int, object, str]] = []  # (indent, QTextList, tag)
        self.quote = False
        self.quote_group: int | None = None

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

    def block(self, block: QTextBlock, ends_code: bool = False) -> None:
        """``ends_code``: the last line of a code block that another one follows (written
        with a final line break, the way Markdown reads a whole code block)."""
        fmt = block.blockFormat()
        if block.textList() is not None:
            self.close_quote()
            self.list_item(block)
            return
        self.close_lists()
        quote = bool(fmt.boolProperty(QUOTE_PROPERTY))
        group = fmt.intProperty(QUOTE_GROUP) if fmt.hasProperty(QUOTE_GROUP) else None
        if quote != self.quote or (quote and group != self.quote_group):
            self.close_quote()
            if quote:
                self.out.append("<blockquote>")
                self.quote, self.quote_group = True, group
        style = _block_style(block)
        level = fmt.headingLevel()
        if fmt.hasProperty(_P.BlockTrailingHorizontalRulerWidth):
            self.out.append("<hr>")
        elif level > 0 and not quote:
            tag = f"h{min(level, 3)}"
            self.out.append(f"<{tag}{style}>{_block_text(block, heading=True)}</{tag}>")
        elif fmt.nonBreakableLines() and not quote:
            end = "\n" if ends_code else ""
            self.out.append(f"<pre>{_block_text(block, code=True)}{end}</pre>")
        else:
            self.out.append(f"<p{style}>{_block_text(block)}</p>")

    def code(self, blocks: list[QTextBlock], form: int) -> None:
        """A code block written as one <pre> (``form``: CODE_WHOLE or CODE_JOINED)."""
        self.close_lists()
        self.close_quote()
        body = "\n".join(_block_text(b, code=True) for b in blocks)
        end = "\n" if form == CODE_WHOLE else ""
        self.out.append(f"<pre>{body}{end}</pre>")

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
    skip = 0
    for i, item in enumerate(items):
        if skip:
            skip -= 1
            continue
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
        form = _code_form(item) if _is_code(item) else 0
        if form:
            group = _code_group(item)
            lines = [item]
            for later in items[i + 1 :]:
                if not (isinstance(later, QTextBlock) and _is_code(later)):
                    break
                if _code_group(later) != group:
                    break
                lines.append(later)
            w.code(lines, form)
            skip = len(lines) - 1
            continue
        nxt = items[i + 1] if i + 1 < len(items) else None
        ends_code = (
            _is_code(item)
            and isinstance(nxt, QTextBlock)
            and _is_code(nxt)
            and _code_group(nxt) != _code_group(item)
        )
        w.block(item, ends_code)
    w.finish()
    return "".join(w.out)


def _is_code(block: QTextBlock) -> bool:
    fmt = block.blockFormat()
    return (
        fmt.nonBreakableLines()
        and block.textList() is None
        and fmt.headingLevel() == 0
        and not fmt.boolProperty(QUOTE_PROPERTY)
        and not fmt.hasProperty(_P.BlockTrailingHorizontalRulerWidth)
    )


def _code_group(block: QTextBlock) -> int | None:
    fmt = block.blockFormat()
    return fmt.intProperty(CODE_GROUP) if fmt.hasProperty(CODE_GROUP) else None


def _code_form(block: QTextBlock) -> int:
    fmt = block.blockFormat()
    form = fmt.intProperty(CODE_FORM) if fmt.hasProperty(CODE_FORM) else 0
    return form if form in (CODE_WHOLE, CODE_JOINED) else 0


def _padding(cell) -> bool:
    """A cell Qt added to a short row, still empty."""
    return cell.format().boolProperty(PADDED_CELL) and cell.firstPosition() == cell.lastPosition()


def _table(table: QTextTable) -> str:
    rows = []
    for r in range(table.rows()):
        cells = []
        real = [
            table.cellAt(r, c)
            for c in range(table.columns())
            if table.cellAt(r, c).row() == r and table.cellAt(r, c).column() == c
        ]  # without those covered by a spanning cell
        while real and _padding(real[-1]):
            real.pop()
        for cell in real:
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
    first = doc.begin()
    if (
        doc.isEmpty()
        and not _is_code(first)
        and first.textList() is None
        and not first.blockFormat().boolProperty(QUOTE_PROPERTY)
    ):  # nothing but an empty paragraph (an empty code line, list item or quote is kept)
        return ""
    return normalise_html(_container(doc.rootFrame().begin()))[0]
