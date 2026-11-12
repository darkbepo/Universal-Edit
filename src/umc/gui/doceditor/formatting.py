"""Formatting operations of the document editor: pure functions on a ``QTextCursor``.

Every function wraps its change in one edit block, so each is exactly one undo step. They
produce documents ``html_export`` understands (see the HTML subset).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QTextBlock,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextLength,
    QTextListFormat,
    QTextTable,
    QTextTableFormat,
)

from umc.gui.doceditor.text_view import (
    CODE_GROUP,
    QUOTE_MARGIN,
    QUOTE_PROPERTY,
    apply_code_format,
    apply_heading_format,
    new_code_group,
)
from umc.gui.doceditor.text_view import (
    _format_fragments as format_fragments,
)

__all__ = [
    "block_style",
    "change_indent",
    "clear_formatting",
    "insert_hr",
    "insert_link",
    "insert_page_break",
    "insert_table",
    "is_on",
    "set_alignment",
    "set_block_style",
    "set_color",
    "set_font_family",
    "set_font_size",
    "set_highlight",
    "table_op",
    "toggle",
    "toggle_list",
]

MAX_INDENT = 8
_P = QTextFormat.Property
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3}
_ALIGNS = {
    "left": Qt.AlignmentFlag.AlignLeft,
    "center": Qt.AlignmentFlag.AlignHCenter,
    "right": Qt.AlignmentFlag.AlignRight,
    "justify": Qt.AlignmentFlag.AlignJustify,
}
_BULLETS = {
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
}


@contextmanager
def _edit(c: QTextCursor) -> Iterator[None]:
    c.beginEditBlock()
    try:
        yield
    finally:
        c.endEditBlock()


def _blocks(c: QTextCursor) -> list[QTextBlock]:
    doc = c.document()
    start, end = c.selectionStart(), c.selectionEnd()
    block = doc.findBlock(start)
    last = doc.findBlock(end)
    if end > start and end == last.position():
        last = last.previous()  # the selection stops at the start of that block
    out = []
    while block.isValid():
        out.append(block)
        if block == last:
            break
        block = block.next()
    return out


def _set_block_format(block: QTextBlock, fmt: QTextBlockFormat) -> None:
    QTextCursor(block).setBlockFormat(fmt)


# ----- paragraph style ------------------------------------------------------
def block_style(c: QTextCursor) -> str:
    fmt = c.blockFormat()
    if fmt.boolProperty(QUOTE_PROPERTY):
        return "quote"
    if fmt.headingLevel() > 0:
        return f"h{min(fmt.headingLevel(), 3)}"
    if fmt.nonBreakableLines():
        return "code"
    return "normal"


def _code_group_for(block: QTextBlock) -> int:
    """A line made code joins the code block directly above it (the user extends it), else it
    starts a block of its own; a code line below stays a separate block."""
    above = block.previous()
    if above.isValid():
        fmt = above.blockFormat()
        if (
            fmt.nonBreakableLines()
            and fmt.hasProperty(CODE_GROUP)
            and above.textList() is None
            and not fmt.boolProperty(QUOTE_PROPERTY)
        ):
            return fmt.intProperty(CODE_GROUP)
    return new_code_group()


def set_block_style(c: QTextCursor, style: str) -> None:
    with _edit(c):
        for block in _blocks(c):
            fmt = block.blockFormat()
            was_quote = fmt.boolProperty(QUOTE_PROPERTY)
            was_code = fmt.nonBreakableLines()
            was_heading = fmt.headingLevel() > 0

            def reset(cf: QTextCharFormat, was_code=was_code) -> None:
                cf.clearProperty(_P.FontPointSize)
                cf.clearProperty(_P.FontWeight)
                if was_code:
                    cf.clearProperty(_P.FontFamilies)

            if (was_heading and not was_quote) or was_code:
                format_fragments(block, reset)
            fmt.setHeadingLevel(0)
            fmt.setNonBreakableLines(False)
            fmt.clearProperty(QUOTE_PROPERTY)
            fmt.clearProperty(CODE_GROUP)
            if was_quote:
                fmt.setLeftMargin(0)
                fmt.setRightMargin(0)
            if style in _HEADINGS:
                fmt.setHeadingLevel(_HEADINGS[style])
            elif style == "code":
                fmt.setNonBreakableLines(True)
                fmt.setProperty(CODE_GROUP, _code_group_for(block))
            elif style == "quote":
                fmt.setProperty(QUOTE_PROPERTY, True)
                fmt.setLeftMargin(QUOTE_MARGIN)
                fmt.setRightMargin(QUOTE_MARGIN)
            _set_block_format(block, fmt)
            if style in _HEADINGS:
                apply_heading_format(block, _HEADINGS[style])
            elif style == "code":
                apply_code_format(block)


# ----- character formatting -------------------------------------------------
def is_on(c: QTextCursor, prop: str) -> bool:
    fmt = c.charFormat()
    if prop == "bold":
        return fmt.fontWeight() >= 600
    if prop == "italic":
        return fmt.fontItalic()
    if prop == "underline":
        return fmt.fontUnderline()
    if prop == "strike":
        return fmt.fontStrikeOut()
    raise ValueError(prop)


def toggle(c: QTextCursor, prop: str) -> None:
    on = not is_on(c, prop)
    fmt = QTextCharFormat()
    if prop == "bold":
        fmt.setFontWeight(700 if on else 400)
    elif prop == "italic":
        fmt.setFontItalic(on)
    elif prop == "underline":
        fmt.setFontUnderline(on)
    elif prop == "strike":
        fmt.setFontStrikeOut(on)
    else:
        raise ValueError(prop)
    _merge(c, fmt)


def _merge(c: QTextCursor, fmt: QTextCharFormat) -> None:
    with _edit(c):
        c.mergeCharFormat(fmt)


def set_font_family(c: QTextCursor, family: str) -> None:
    fmt = QTextCharFormat()
    fmt.setFontFamilies([family])
    _merge(c, fmt)


def set_font_size(c: QTextCursor, pt: float) -> None:
    fmt = QTextCharFormat()
    fmt.setFontPointSize(pt)
    _merge(c, fmt)


def set_color(c: QTextCursor, color: QColor) -> None:
    fmt = QTextCharFormat()
    fmt.setForeground(QBrush(color))
    _merge(c, fmt)


def set_highlight(c: QTextCursor, color: QColor | None) -> None:
    fmt = QTextCharFormat()
    fmt.setBackground(QBrush(color) if color is not None else QBrush())  # NoBrush = none
    _merge(c, fmt)


def set_alignment(c: QTextCursor, align: str) -> None:
    fmt = QTextBlockFormat()
    fmt.setAlignment(_ALIGNS[align])
    with _edit(c):
        c.mergeBlockFormat(fmt)


# ----- lists and indentation ---------------------------------------------------
def _list_level(block: QTextBlock) -> int:
    lst = block.textList()
    return max(1, lst.format().indent()) if lst is not None else 0


def _list_format(style: QTextListFormat.Style, level: int) -> QTextListFormat:
    fmt = QTextListFormat()
    fmt.setStyle(style)
    fmt.setIndent(level)
    return fmt


def _move_to_level(blocks: list[QTextBlock], level: int) -> None:
    """Put consecutive list items on one list at ``level`` (joining the item above if fitting)."""
    style = blocks[0].textList().format().style()
    target = None
    above = blocks[0].previous()
    if above.isValid() and above.textList() is not None:
        lst = above.textList()
        if max(1, lst.format().indent()) == level and (lst.format().style() in _BULLETS) == (
            style in _BULLETS
        ):
            target = lst
    for block in blocks:
        if target is None:
            target = QTextCursor(block).createList(_list_format(style, level))
        else:
            target.add(block)


def _runs(blocks: list[QTextBlock]) -> list[list[QTextBlock]]:
    """Consecutive blocks that are on the same list."""
    runs: list[list[QTextBlock]] = []
    for block in blocks:
        if runs and runs[-1][-1].textList() == block.textList():
            runs[-1].append(block)
        else:
            runs.append([block])
    return runs


def _fix_nesting(first: QTextBlock) -> None:
    """No list item may sit more than one level below the item above it."""
    block = first.previous()
    prev = _list_level(block) if block.isValid() else 0
    block = first
    while block.isValid() and block.textList() is not None:
        level = _list_level(block)
        if level > prev + 1:
            _move_to_level([block], prev + 1)
            level = prev + 1
        prev = level
        block = block.next()


def toggle_list(c: QTextCursor, kind: str) -> None:
    bullet = kind == "bullet"
    style = QTextListFormat.Style.ListDisc if bullet else QTextListFormat.Style.ListDecimal
    with _edit(c):
        blocks = _blocks(c)
        lists = [b.textList() for b in blocks]
        same_kind = all(
            lst is not None and (lst.format().style() in _BULLETS) == bullet for lst in lists
        )
        if same_kind:  # switch the list off
            for block in blocks:
                block.textList().remove(block)
                fmt = block.blockFormat()
                fmt.setIndent(0)
                _set_block_format(block, fmt)
            return
        for run in _runs(blocks):
            if run[0].textList() is not None:  # the other kind: change the list's style
                for lst in {id(b.textList()): b.textList() for b in run}.values():
                    fmt = lst.format()
                    fmt.setStyle(style)
                    lst.setFormat(fmt)
                continue
            level = 1
            above = run[0].previous()
            if above.isValid() and above.textList() is not None:
                level = _list_level(above)
            cur = QTextCursor(run[0])
            cur.setPosition(run[-1].position(), QTextCursor.MoveMode.KeepAnchor)
            cur.createList(_list_format(style, level))


def change_indent(c: QTextCursor, delta: int) -> None:
    with _edit(c):
        blocks = _blocks(c)
        if all(b.textList() is not None for b in blocks):
            for run in _runs(blocks):
                above = run[0].previous()
                cap = _list_level(above) + 1 if above.isValid() else 1
                level = min(MAX_INDENT, cap, max(1, _list_level(run[0]) + delta))
                if level != _list_level(run[0]):
                    _move_to_level(run, level)
            _fix_nesting(blocks[0])
            return
        for block in blocks:
            fmt = block.blockFormat()
            fmt.setIndent(min(MAX_INDENT, max(0, fmt.indent() + delta)))
            _set_block_format(block, fmt)


# ----- inserts ---------------------------------------------------------------------
def insert_link(c: QTextCursor, url: str, text: str | None) -> None:
    fmt = QTextCharFormat()
    fmt.setAnchor(True)
    fmt.setAnchorHref(url)
    fmt.setFontUnderline(True)  # Qt shows links this way itself; the export ignores both
    fmt.setForeground(QGuiApplication.palette().link())
    with _edit(c):
        if text is None and c.hasSelection():
            c.mergeCharFormat(fmt)
        else:
            c.insertText(text or url, fmt)


def insert_table(c: QTextCursor, rows: int, cols: int) -> None:
    fmt = QTextTableFormat()
    fmt.setBorder(1)
    fmt.setBorderCollapse(True)
    fmt.setCellPadding(4)
    fmt.setCellSpacing(0)
    fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
    with _edit(c):
        c.insertTable(max(1, rows), max(1, cols), fmt)


def _delete_table(c: QTextCursor, table: QTextTable) -> None:
    cur = QTextCursor(c.document())
    cur.setPosition(table.firstPosition() - 1)
    cur.setPosition(table.lastPosition() + 1, QTextCursor.MoveMode.KeepAnchor)
    cur.removeSelectedText()


def table_op(c: QTextCursor, op: str) -> None:
    table = c.currentTable()
    if table is None:
        return
    cell = table.cellAt(c)
    with _edit(c):
        if op == "row_above":
            table.insertRows(cell.row(), 1)
        elif op == "row_below":
            table.insertRows(cell.row() + cell.rowSpan(), 1)
        elif op == "col_left":
            table.insertColumns(cell.column(), 1)
        elif op == "col_right":
            table.insertColumns(cell.column() + cell.columnSpan(), 1)
        elif op == "del_row":
            if table.rows() <= cell.rowSpan():
                _delete_table(c, table)
            else:
                table.removeRows(cell.row(), cell.rowSpan())
        elif op == "del_col":
            if table.columns() <= cell.columnSpan():
                _delete_table(c, table)
            else:
                table.removeColumns(cell.column(), cell.columnSpan())
        elif op == "del_table":
            _delete_table(c, table)
        elif op == "merge":
            if c.hasSelection():
                table.mergeCells(c)
        elif op == "split":
            if cell.rowSpan() > 1 or cell.columnSpan() > 1:
                table.splitCell(cell.row(), cell.column(), 1, 1)
        else:
            raise ValueError(op)


def insert_page_break(c: QTextCursor) -> None:
    fmt = QTextBlockFormat()
    fmt.setPageBreakPolicy(QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore)
    with _edit(c):
        c.insertBlock()
        c.mergeBlockFormat(fmt)


def insert_hr(c: QTextCursor) -> None:
    rule = QTextBlockFormat()
    rule.setProperty(
        _P.BlockTrailingHorizontalRulerWidth, QTextLength(QTextLength.Type.PercentageLength, 100)
    )
    with _edit(c):
        if c.block().text():
            c.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            c.insertBlock(rule, QTextCharFormat())
        else:
            c.mergeBlockFormat(rule)
        if not c.block().next().isValid():
            c.insertBlock(QTextBlockFormat(), QTextCharFormat())


def clear_formatting(c: QTextCursor) -> None:
    with _edit(c):
        blocks = _blocks(c)
        ranges: list[tuple[int, int]] = []
        for block in blocks:
            lo, hi = block.position(), block.position() + block.length() - 1
            if c.hasSelection():
                lo, hi = max(lo, c.selectionStart()), min(hi, c.selectionEnd())
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if not frag.charFormat().isImageFormat():
                    a = max(lo, frag.position())
                    b = min(hi, frag.position() + frag.length())
                    if a < b:
                        ranges.append((a, b))
                it += 1
        cur = QTextCursor(c.document())
        for a, b in ranges:
            cur.setPosition(a)
            cur.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
            cur.setCharFormat(QTextCharFormat())
        set_block_style(c, "normal")
