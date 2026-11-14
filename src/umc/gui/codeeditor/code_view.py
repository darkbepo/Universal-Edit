"""The code editor's text area: line numbers, current-line and bracket highlight, Tab / Shift+Tab
on selected lines, auto-indent on Enter, comment toggling, zoom and word wrap."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QKeyEvent,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from umc.codeedit import editing
from umc.gui import theme

BASE_POINT_SIZE = 10
MIN_POINT_SIZE, MAX_POINT_SIZE = 6, 40
BRACKET_WINDOW = 20_000


def raw_text(document) -> str:
    """The text exactly as typed. ``toPlainText()`` turns no-break spaces into spaces and line
    separators into newlines, which would change files on saving."""
    return document.toRawText().replace(" ", "\n")


def _monospace_family() -> str:
    """A good monospace font this computer has (Windows' Consolas / Cascadia Mono first)."""
    have = set(QFontDatabase.families())
    for family in ("Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo", "Liberation Mono"):
        if family in have:
            return family
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


class _Gutter(QWidget):
    def __init__(self, view: CodeView) -> None:
        super().__init__(view)
        self.view = view

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.view.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        self.view.paint_gutter(event)


class CodeView(QPlainTextEdit):
    cursor_moved = Signal(int, int)  # line, column (1-based)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CodeView")
        self._unit = editing.DEFAULT_INDENT
        self._language = "text"
        self._find: list[tuple[int, int]] = []
        self._problems: list = []  # codeedit.check.Problem
        self._point_size = BASE_POINT_SIZE
        self._family = _monospace_family()
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabChangesFocus(False)
        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_margins)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._on_cursor_moved)
        theme.notifier().changed.connect(self._refresh_selections)
        self._apply_font()

    # ------------------------------------------------------------------ settings
    def set_indent_unit(self, unit: str) -> None:
        self._unit = unit or editing.DEFAULT_INDENT

    def indent_unit(self) -> str:
        return self._unit

    def set_language(self, language_id: str) -> None:
        self._language = language_id

    def set_wrap(self, on: bool) -> None:
        mode = QPlainTextEdit.LineWrapMode.WidgetWidth if on else QPlainTextEdit.LineWrapMode.NoWrap
        self.setLineWrapMode(mode)

    def zoom(self, steps: int) -> None:
        """Bigger / smaller text; 0 goes back to the normal size."""
        size = BASE_POINT_SIZE if steps == 0 else self._point_size + steps
        self._point_size = max(MIN_POINT_SIZE, min(MAX_POINT_SIZE, size))
        self._apply_font()

    def point_size(self) -> int:
        return self._point_size

    def _apply_font(self) -> None:
        # A rule of its own: the app's "* { font-size }" would otherwise win over setFont.
        family = self._family.replace('"', "")
        size = self._point_size
        self.setStyleSheet(
            f'QPlainTextEdit#CodeView {{ font-family: "{family}"; font-size: {size}pt; }}'
        )
        font = self.font()
        font.setFamily(family)
        font.setPointSize(self._point_size)
        self.setFont(font)
        self.document().setDefaultFont(font)
        self._gutter.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._update_margins()

    def setDocument(self, document) -> None:  # noqa: N802 (Qt override)
        super().setDocument(document)
        if document is not None and hasattr(self, "_gutter"):
            document.setDefaultFont(self.font())  # a new tab's document: the code font too
            self._update_margins()

    def set_problems(self, problems: list) -> None:
        """Wavy red underline under each problem (from its column, or the whole line)."""
        self._problems = problems
        self._refresh_selections()

    def set_find_matches(self, matches: list[tuple[int, int]]) -> None:
        self._find = matches
        self._refresh_selections()

    # ------------------------------------------------------------------ gutter
    def gutter_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_margins(self, _count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event) -> None:
        pal = theme.palette()
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor(pal["surface"]))
        painter.setPen(QColor(pal["code_gutter"]))
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        width = self._gutter.width() - 8
        height = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                if block.blockNumber() == current:
                    painter.setPen(QColor(pal["text"]))
                painter.drawText(
                    0, top, width, height, Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1)
                )
                painter.setPen(QColor(pal["code_gutter"]))
            block = block.next()
            top = bottom
        painter.end()

    # ------------------------------------------------------------------ highlights
    def _on_cursor_moved(self) -> None:
        self._refresh_selections()
        cursor = self.textCursor()
        self.cursor_moved.emit(cursor.blockNumber() + 1, cursor.positionInBlock() + 1)
        self._gutter.update()

    def _refresh_selections(self) -> None:
        pal = theme.palette()
        selections: list[QTextEdit.ExtraSelection] = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(pal["code_line"]))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections.append(line)
        for start, end in self._find:
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(pal["code_match"]))
            sel.cursor = QTextCursor(self.document())
            sel.cursor.setPosition(start)
            sel.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selections.append(sel)
        for p in self._bracket_pair():
            sel = QTextEdit.ExtraSelection()
            sel.format.setBackground(QColor(pal["code_bracket"]))
            sel.cursor = QTextCursor(self.document())
            sel.cursor.setPosition(p)
            sel.cursor.setPosition(p + 1, QTextCursor.MoveMode.KeepAnchor)
            selections.append(sel)
        doc = self.document()
        for p in self._problems:
            block = doc.findBlockByNumber(p.line - 1)
            if not block.isValid():
                block = doc.lastBlock()
            sel = QTextEdit.ExtraSelection()
            sel.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            sel.format.setUnderlineColor(QColor(pal["danger"]))
            sel.cursor = QTextCursor(block)
            col = max(0, min(p.column - 1, block.length() - 1)) if p.column else 0
            sel.cursor.setPosition(block.position() + col)
            end = min(block.position() + max(block.length() - 1, col + 1), doc.characterCount() - 1)
            sel.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selections.append(sel)
        self.setExtraSelections(selections)

    def _bracket_pair(self) -> tuple[int, ...]:
        """The bracket at the cursor and its partner, looked for within ``BRACKET_WINDOW``
        characters (a whole-text scan made every cursor move slow in big files)."""
        cursor = self.textCursor()
        if cursor.hasSelection():
            return ()
        doc = self.document()
        pos = cursor.position()
        here = next((p for p in (pos, pos - 1) if p >= 0 and doc.characterAt(p) in "()[]{}"), None)
        if here is None:
            return ()
        start = max(0, here - BRACKET_WINDOW)
        end = min(doc.characterCount() - 1, here + BRACKET_WINDOW)
        window = QTextCursor(doc)
        window.setPosition(start)
        window.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        other = editing.matching_bracket(window.selectedText(), here - start)
        return () if other is None else (here, start + other)

    # ------------------------------------------------------------------ editing
    def _selected_block_range(self) -> tuple[int, int]:
        cursor = self.textCursor()
        doc = self.document()
        start = doc.findBlock(cursor.selectionStart()).blockNumber()
        end_block = doc.findBlock(cursor.selectionEnd())
        end = end_block.blockNumber()
        if cursor.hasSelection() and end > start and cursor.selectionEnd() == end_block.position():
            end -= 1  # a selection ending at the start of a line doesn't include that line
        return start, end

    def _apply_lines(self, change: Callable[[list[str]], list[str]]) -> None:
        """Replace the selected lines (whole lines) with ``change(lines)`` as one undo step and
        keep them selected."""
        doc = self.document()
        first, last = self._selected_block_range()
        start = doc.findBlockByNumber(first).position()
        last_block = doc.findBlockByNumber(last)
        end = last_block.position() + last_block.length() - 1
        cursor = QTextCursor(doc)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        old = cursor.selectedText().replace(" ", "\n").split("\n")
        new = change(old)
        if new == old:
            return
        cursor.beginEditBlock()
        cursor.insertText("\n".join(new))
        cursor.endEditBlock()
        cursor.setPosition(start)
        cursor.setPosition(start + len("\n".join(new)), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)

    def toggle_comment(self) -> None:
        self._apply_lines(lambda lines: editing.toggle_comment(lines, self._language))

    def go_to_line(self, line: int) -> None:
        doc = self.document()
        block = doc.findBlockByNumber(max(0, min(line - 1, doc.blockCount() - 1)))
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key, mods = event.key(), event.modifiers()
        plain = not mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
        if key == Qt.Key.Key_Tab and plain and not mods & Qt.KeyboardModifier.ShiftModifier:
            cursor = self.textCursor()
            if cursor.hasSelection() and " " in cursor.selectedText():
                self._apply_lines(lambda lines: editing.indent_lines(lines, self._unit))
            else:
                cursor.insertText(self._unit)
            return
        if key == Qt.Key.Key_Backtab and plain:
            self._apply_lines(lambda lines: editing.outdent_lines(lines, self._unit))
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and plain:
            cursor = self.textCursor()
            before = cursor.block().text()[: cursor.positionInBlock()]
            cursor.beginEditBlock()
            cursor.insertText("\n" + editing.next_line_indent(before, self._unit))
            cursor.endEditBlock()
            self.ensureCursorVisible()
            return
        super().keyPressEvent(event)
