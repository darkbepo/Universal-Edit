"""Syntax colours for the code editor, coloured in chunks so big files open at once.

``highlightBlock`` only colours blocks the chunk timer has reached (``_ready_until``); a timer
tick moves that mark on by ``CHUNK_LINES`` and re-highlights from the first new block — Qt then
carries on block by block while the stored states change, which also makes an edit like typing
``*/`` re-colour the lines after it. Block state = the ``LineLexer`` state int."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextDocument

from umc.codeedit.lexing import KINDS, LineLexer
from umc.gui import theme


class ChunkedHighlighter(QSyntaxHighlighter):
    CHUNK_LINES = 1500

    def __init__(self, document: QTextDocument, language_id: str) -> None:
        super().__init__(document)
        self._lexer = LineLexer(language_id)
        self._enabled = True
        self._ready_until = -1  # last block number that may be coloured
        self._coloured = 0  # blocks lexed in this run (an edit's cascade is limited by it)
        self._formats: dict[str, QTextCharFormat] = {}
        self._build_formats()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._step)
        theme.notifier().changed.connect(self._theme_changed)
        self._timer.start()

    # ------------------------------------------------------------------ public
    def set_language(self, language_id: str) -> None:
        self._lexer = LineLexer(language_id)
        self._restart()

    def set_enabled(self, on: bool) -> None:
        self._enabled = on
        self._restart()

    def done(self) -> bool:
        doc = self.document()
        return not self._enabled or doc is None or self._ready_until >= doc.blockCount() - 1

    # ------------------------------------------------------------------ inner
    def _build_formats(self) -> None:
        pal = theme.palette()
        self._formats = {}
        for kind in KINDS:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(pal[f"code_{kind}"]))
            if kind == "comment":
                fmt.setFontItalic(True)
            if kind == "keyword":
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self._formats[kind] = fmt

    def _theme_changed(self) -> None:
        self._build_formats()
        self._restart()

    def _restart(self) -> None:
        self._timer.stop()
        self._ready_until = -1
        self.rehighlight()  # clears the colours (no block is ready yet)
        if self._enabled:
            self._timer.start()

    def _reset_budget(self) -> None:
        self._coloured = 0

    def _step(self) -> None:
        doc = self.document()
        if doc is None or not self._enabled:
            return
        self._coloured = 0
        first = self._ready_until + 1
        self._ready_until = min(first + self.CHUNK_LINES - 1, doc.blockCount() - 1)
        block = doc.findBlockByNumber(first)
        if block.isValid():
            self.rehighlightBlock(block)  # continues while the stored states change
        if not self.done():
            self._timer.start()

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt override)
        number = self.currentBlock().blockNumber()
        if not self._enabled or number > self._ready_until:
            self.setCurrentBlockState(-1)
            return
        if not self._coloured:  # a new run (an edit or a timer tick): budget again next turn
            QTimer.singleShot(0, self._reset_budget)
        self._coloured += 1
        if self._coloured > self.CHUNK_LINES:
            # One edit changed the state of very many lines (a string or comment opened or
            # closed): leave the rest to the timer instead of freezing here.
            self._ready_until = number - 1
            self.setCurrentBlockState(-1)
            self._timer.start()
            return
        state = max(self.previousBlockState(), 0)
        spans, after = self._lexer.lex(text, state)
        for span in spans:
            self.setFormat(span.start, span.length, self._formats[span.kind])
        self.setCurrentBlockState(after)
