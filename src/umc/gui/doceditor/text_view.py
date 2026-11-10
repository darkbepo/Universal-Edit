"""The document editor's text view: the HTML subset on a white page column."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
    QTextBlock,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextImageFormat,
)
from PySide6.QtWidgets import QFrame, QTextEdit

from umc.core.errors import ConversionError
from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import DocImages, OpenedDocument, SaveContent, next_image_name
from umc.gui import theme
from umc.gui.doceditor.html_export import QUOTE_PROPERTY, document_to_html

__all__ = [
    "CODE_FONT",
    "CODE_PT",
    "HEADING_PT",
    "PAGE_MARGIN",
    "PAGE_WIDTH",
    "QUOTE_PROPERTY",
    "DocTextView",
    "apply_code_format",
    "apply_heading_format",
]

PAGE_WIDTH = 794  # px, A4 at 96 dpi
PAGE_MARGIN = 76
HEADING_PT = {1: 20, 2: 16, 3: 13}
CODE_FONT = "Consolas"
CODE_PT = 10
QUOTE_MARGIN = 40  # px on both sides
INDENT_PX = 48  # one indent level (36 pt)

_IMAGE_EXTS = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "gif": "gif",
    "bmp": "bmp",
    "webp": "webp",
}
_QUOTE_TOKENS = re.compile(r'<blockquote>|</blockquote>|<p(?: style="([^"]*)")?>')
_MARGIN_PT = re.compile(r"(<(?:p|h[1-3]|li) [^>]*?)margin-left:(\d+(?:\.\d+)?)pt")
_IMG_SRC = re.compile(r'<img src="([^"]+)"')
_TAB = re.compile(r"\t(?![^<>]*>)")  # a tab in text, not inside a tag
_NOT_A_PICTURE = "This file isn't a picture the editor can add (PNG, JPG, GIF, BMP or WebP)."


def _qt_html(html: str) -> str:
    """Rewrite the subset into what Qt's HTML reader understands."""

    def quote(match: re.Match[str], depth: list[int]) -> str:
        tag = match.group(0)
        if tag == "<blockquote>":
            depth[0] += 1
            return ""
        if tag == "</blockquote>":
            depth[0] -= 1
            return ""
        if depth[0] <= 0:
            return tag
        style = ";".join(
            s for s in (match.group(1) or "").split(";") if s and not s.startswith("margin-")
        )
        margins = f"margin-left:{QUOTE_MARGIN}px;margin-right:{QUOTE_MARGIN}px"
        return f'<p style="{style + ";" if style else ""}{margins}">'

    depth = [0]
    # Qt's HTML reader turns a tab into a space unless white-space is preserved around it.
    html = _TAB.sub('<span style="white-space:pre-wrap">\t</span>', html)
    # Qt drops empty paragraphs unless they are marked the way its own HTML marks them.
    html = re.sub(
        r'<(p|h[1-3]|li)(?: style="([^"]*)")?>(?=</\1>|<[uo]l>)',
        lambda m: (
            f'<{m.group(1)} style="{m.group(2) + ";" if m.group(2) else ""}'
            '-qt-paragraph-type:empty"><br>'
        ),
        html,
    )
    html = _QUOTE_TOKENS.sub(lambda m: quote(m, depth), html)
    html = _MARGIN_PT.sub(
        lambda m: f"{m.group(1)}margin-left:{float(m.group(2)) * 4 / 3:g}px", html
    )
    # Qt's CSS reader ignores text-align:justify; the align attribute works for every value.
    return re.sub(
        r'<(p|h[1-3]|li) style="([^"]*?)text-align:(\w+);?([^"]*)"',
        lambda m: f'<{m.group(1)} align="{m.group(3)}" style="{m.group(2)}{m.group(4)}"',
        html,
    )


def _format_fragments(block: QTextBlock, change) -> None:
    cursor = QTextCursor(block)
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        fmt = frag.charFormat()
        if not fmt.isImageFormat():
            change(fmt)
            cursor.setPosition(frag.position())
            cursor.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(fmt)
        it += 1
    fmt = block.charFormat()
    change(fmt)
    cursor.setPosition(block.position())
    cursor.setBlockCharFormat(fmt)


def apply_heading_format(block: QTextBlock, level: int) -> None:
    """Show a heading block at its size, in bold (never exported)."""

    def change(fmt: QTextCharFormat) -> None:
        fmt.clearProperty(QTextFormat.Property.FontSizeAdjustment)
        fmt.setFontPointSize(HEADING_PT[min(level, 3)])
        fmt.setFontWeight(700)

    _format_fragments(block, change)


def apply_code_format(block: QTextBlock) -> None:
    """Show a code block in the code font (never exported)."""

    def change(fmt: QTextCharFormat) -> None:
        fmt.setFontFamilies([CODE_FONT])
        fmt.setFontPointSize(CODE_PT)

    _format_fragments(block, change)


class DocTextView(QTextEdit):
    """Rich text editing of one document in the editor's HTML subset."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.images: DocImages = {}
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptRichText(True)
        self.viewport().setObjectName("DocPage")
        self.document().setDocumentMargin(PAGE_MARGIN)
        self._update_margins()

    # ----- loading --------------------------------------------------------
    def load(self, opened: OpenedDocument) -> None:
        self.images = dict(opened.images)
        doc = self.document()
        doc.setUndoRedoEnabled(False)
        self.setHtml(_qt_html(normalise_html(opened.html)[0]))
        for name, data in self.images.items():
            self._add_resource(name, data)
        doc.setDocumentMargin(PAGE_MARGIN)
        block = doc.begin()
        while block.isValid():
            self._fix_block(block)
            block = block.next()
        doc.setUndoRedoEnabled(True)
        doc.clearUndoRedoStacks()
        doc.setModified(False)

    def _fix_block(self, block: QTextBlock) -> None:
        fmt = block.blockFormat()
        changed = False
        if fmt.leftMargin() == QUOTE_MARGIN and fmt.rightMargin() == QUOTE_MARGIN:
            fmt.setProperty(QUOTE_PROPERTY, True)
            changed = True
        elif fmt.leftMargin() > 0:
            fmt.setIndent(fmt.indent() + round(fmt.leftMargin() / INDENT_PX))
            fmt.setLeftMargin(0)
            changed = True
        if changed:
            QTextCursor(block).setBlockFormat(fmt)
        if fmt.headingLevel() > 0 and not fmt.boolProperty(QUOTE_PROPERTY):
            apply_heading_format(block, fmt.headingLevel())
        elif fmt.nonBreakableLines():
            apply_code_format(block)

    def _add_resource(self, name: str, data: bytes) -> QImage:
        image = QImage.fromData(data)
        self.document().addResource(QTextDocument.ResourceType.ImageResource, QUrl(name), image)
        return image

    def loadResource(self, type_: int, url: QUrl):  # noqa: N802 (Qt override)
        data = self.images.get(url.toString())
        if data is not None and type_ == QTextDocument.ResourceType.ImageResource:
            return QImage.fromData(data)
        return super().loadResource(type_, url)

    # ----- pictures -------------------------------------------------------
    def insert_image(self, path: Path) -> None:
        ext = _IMAGE_EXTS.get(path.suffix.lower().lstrip("."))
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConversionError("The picture couldn't be read.", details=str(exc)) from exc
        if ext is None or QImage.fromData(data).isNull():
            raise ConversionError(_NOT_A_PICTURE)
        name = next_image_name(self.images, ext)
        n = len(self.images) + 1
        while name in self.images:
            n += 1
            name = f"img/{n}.{ext}"
        self.images[name] = data
        image = self._add_resource(name, data)
        fmt = QTextImageFormat()
        fmt.setName(name)
        room = PAGE_WIDTH - 2 * PAGE_MARGIN
        if image.width() > room:  # shrink wide pictures to the page
            fmt.setWidth(room)
            fmt.setHeight(round(image.height() * room / image.width()))
        self.textCursor().insertImage(fmt)

    # ----- saving ---------------------------------------------------------
    def content(self) -> SaveContent:
        doc = self.document()
        html = document_to_html(doc)
        used = set(_IMG_SRC.findall(html))
        text = doc.toPlainText().replace("￼", "")
        return SaveContent(
            html,
            {name: data for name, data in self.images.items() if name in used},
            doc.toMarkdown(),
            text + "\n" if text else "",
        )

    # ----- page column ----------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._update_margins()
        super().resizeEvent(event)

    def _update_margins(self) -> None:
        side = max(16, (self.width() - 2 * self.frameWidth() - PAGE_WIDTH) // 2)
        self.setViewportMargins(side, 16, side, 16)

    def page_break_lines(self) -> list[float]:
        """Viewport y of every page-break marker."""
        layout = self.document().documentLayout()
        offset = self.verticalScrollBar().value()
        ys = []
        block = self.document().begin()
        while block.isValid():
            policy = block.blockFormat().pageBreakPolicy()
            if policy & QTextFormat.PageBreakFlag.PageBreak_AlwaysBefore:
                ys.append(layout.blockBoundingRect(block).top() - offset)
            block = block.next()
        return ys

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        ys = self.page_break_lines()
        if not ys:
            return
        painter = QPainter(self.viewport())
        pen = QPen(QColor(theme.palette()["muted"]))
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        font = painter.font()
        font.setPointSizeF(max(6.0, font.pointSizeF() * 0.8))
        painter.setFont(font)
        width = self.viewport().width()
        for y in ys:
            painter.drawLine(QPointF(8, y), QPointF(width - 8, y))
            painter.drawText(
                QRectF(0, y - 18, width - 12, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
                "Page break",
            )
        painter.end()
