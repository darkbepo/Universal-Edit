"""The document editor's text view: the HTML subset on a white page column."""

from __future__ import annotations

import base64
import html as _html
import logging
import re
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QPointF, QRectF, Qt, QUrl
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
    QTextFrame,
    QTextImageFormat,
    QTextTable,
)
from PySide6.QtWidgets import QFrame, QTextEdit, QToolTip

from umc.core.errors import ConversionError
from umc.doceditor.html_subset import normalise_html
from umc.doceditor.types import (
    MAX_FILE_BYTES,
    DocImages,
    OpenedDocument,
    SaveContent,
    next_image_name,
)
from umc.gui import theme
from umc.gui.doceditor.html_export import (
    CODE_GROUP,
    CODE_JOINED,
    CODE_WHOLE,
    LINK_PLACEHOLDER,
    PADDED_CELL,
    QUOTE_GROUP,
    QUOTE_PROPERTY,
    WRAP_PREFIX,
    code_group_of,
    document_to_html,
    new_code_group,
    set_code_group,
)

log = logging.getLogger(__name__)

__all__ = [
    "CODE_FONT",
    "CODE_PT",
    "HEADING_PT",
    "PAGE_MARGIN",
    "PAGE_WIDTH",
    "CODE_GROUP",
    "QUOTE_PROPERTY",
    "DocTextView",
    "apply_code_format",
    "apply_heading_format",
    "new_code_group",
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
_FILE_IMG = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*(["']))(file:[^"']*)\2""", re.I)
CELL_PLACEHOLDER = "\u200b"  # stands in for an empty first paragraph of a table cell
CODE_PLACEHOLDER = "\u2060"  # stands in for an empty first line of a code block
_PLACEHOLDERS = CELL_PLACEHOLDER + CODE_PLACEHOLDER  # removed right after loading
_PRE = re.compile(r"<pre>(.*?)</pre>", re.S)
_EMPTY_LINK = re.compile(r'(<a href="[^"]*">)</a>')
# An inline tag (1 = "/", 2 = name), a picture or line break (3), another tag, or text.
_INLINE_TOKEN = re.compile(r"<(/?)(b|i|u|s|a|span)\b[^>]*>|(<(?:img|br)\b[^>]*>)|<[^>]*>|[^<]+")
_TABLE_TOKEN = re.compile(r"<(/?)(table|tr|td)\b([^>]*)>")
_SPAN_ATTR = re.compile(r'\b(colspan|rowspan)="(\d+)"')
_TAB = re.compile(r"\t(?![^<>]*>)")  # a tab in text, not inside a tag
_NOT_A_PICTURE = "This file isn't a picture the editor can add (PNG, JPG, GIF, BMP or WebP)."


def _local_data_uri(src: str) -> str:
    """A pasted ``file:`` picture on this computer as a data: URI ("" otherwise)."""
    url = QUrl(_html.unescape(src))
    path = url.toLocalFile()
    if (
        url.scheme() != "file"
        or url.host() not in ("", "localhost")
        or path.startswith(("//", "\\\\"))
    ):
        return ""  # a network share would be contacted
    p = Path(path)
    ext = _IMAGE_EXTS.get(p.suffix.lower().lstrip("."))
    try:
        if ext is None or not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
            return ""
        data = p.read_bytes()
    except OSError:
        return ""
    if QImage.fromData(data).isNull():
        return ""
    mime = "jpeg" if ext == "jpg" else ext
    return f"data:image/{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _code_blocks(html: str) -> str:
    """Number the code blocks for CODE_GROUP (Qt keeps ``-qt-user-state`` on a <pre>'s first
    line; load() turns it into the property). Consecutive one-line <pre>s (Word's code lines,
    the editor's own) are one block, up to one that ends with a line break (a whole Markdown
    code block), as the Markdown writer joins them. The state is ``group * 4 + form``, the
    block's CODE_FORM: a block whose first <pre> has several lines or a final line break is
    written back as one <pre>. Qt drops an empty <pre> and the first line break of one, so
    those get CODE_PLACEHOLDER first, which load() removes again."""
    out: list[str] = []
    pos, group, form, joins = 0, 0, 0, False
    for m in _PRE.finditer(html):
        body = m.group(1)
        if not (joins and m.start() == pos):
            group = new_code_group()
            form = CODE_WHOLE if body.endswith("\n") else CODE_JOINED if "\n" in body else 0
        joins = not (body.endswith("\n") or body.endswith("<br>"))
        if not body or body.startswith("\n"):
            body = CODE_PLACEHOLDER + body
        out.append(html[pos : m.start()])
        out.append(f'<pre style="-qt-user-state:{group * 4 + form}">{body}</pre>')
        pos = m.end()
    out.append(html[pos:])
    return "".join(out)


def _mark_wraps(html: str) -> str:
    """Record how inline tags nest (Qt keeps a character's formats but not their order, so
    ``<b><a>x</a></b>`` would come back as ``<a><b>x</b></a>``): every text, picture and line
    break inside tags gets a tool tip naming them, outermost first (html_export.WRAP_PREFIX).
    An empty link gets LINK_PLACEHOLDER, because Qt drops empty anchors."""
    html = _EMPTY_LINK.sub(lambda m: m.group(1) + LINK_PLACEHOLDER + "</a>", html)
    out: list[str] = []
    stack: list[str] = []
    for m in _INLINE_TOKEN.finditer(html):
        tok = m.group(0)
        if m.group(2):  # an inline tag
            if m.group(1):
                if stack and stack[-1] == m.group(2):
                    stack.pop()
            else:
                stack.append(m.group(2))
            out.append(tok)
        elif stack and (m.group(3) or not tok.startswith("<")):  # text, <img>, <br>
            out.append(f'<span title="{WRAP_PREFIX}{" ".join(stack)}">{tok}</span>')
        else:
            out.append(tok)
    return "".join(out)


def _table_shapes(html: str) -> list[list[set[int]]]:
    """For every table in document order (as Qt lists its frames), the columns each row
    really has (its cells and those spanning into it from above); Qt pads short rows."""
    tables: list[list[set[int]]] = []
    open_: list[tuple[list[set[int]], dict[int, int]]] = []  # rows, rowspans still running
    col = 0
    for m in _TABLE_TOKEN.finditer(html):
        closing, tag = m.group(1), m.group(2)
        if tag == "table":
            if closing:
                if open_:
                    open_.pop()
                continue
            rows: list[set[int]] = []
            tables.append(rows)
            open_.append((rows, {}))
        elif closing or not open_:
            continue
        elif tag == "tr":
            rows, spans = open_[-1]
            rows.append({c for c, left in spans.items() if left > 0})
            for c in list(spans):
                spans[c] -= 1
            col = 0
        elif open_[-1][0]:  # a cell
            rows, spans = open_[-1]
            row = rows[-1]
            while col in row:
                col += 1
            attrs = dict(_SPAN_ATTR.findall(m.group(3)))
            colspan = int(attrs.get("colspan", 1))
            rowspan = int(attrs.get("rowspan", 1))
            for c in range(col, col + colspan):
                row.add(c)
                if rowspan > 1:
                    spans[c] = rowspan - 1
            col += colspan
    return tables


def _qt_html(html: str) -> str:
    """Rewrite the subset into what Qt's HTML reader understands."""

    def quote(match: re.Match[str], depth: list[int]) -> str:
        tag = match.group(0)
        if tag == "<blockquote>":
            depth[0] += 1
            depth[1] = new_code_group()  # one number per quote, see DocTextView._fix_block
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
        return f'<p style="{style + ";" if style else ""}{margins};-qt-user-state:{depth[1]}">'

    depth = [0, 0]
    html = _mark_wraps(html)
    # Qt's HTML reader turns a tab into a space unless white-space is preserved around it.
    html = _TAB.sub('<span style="white-space:pre-wrap">\t</span>', html)
    # Tables: bordered like inserted ones, unless the subset says border:none.
    html = html.replace(
        "<table>",
        '<table border="1" cellspacing="0" cellpadding="4" style="border-collapse:collapse">',
    ).replace('<table style="border:none">', '<table border="0" cellspacing="0" cellpadding="4">')
    # A cell already starts with an empty block: an empty first paragraph would add a
    # second one (one more per save). It gets a placeholder that load() removes again.
    html = re.sub(
        r'(<td[^>]*>)<(p|h[1-3])((?: style="[^"]*")?)></\2>',
        lambda m: f"{m.group(1)}<{m.group(2)}{m.group(3)}>{CELL_PLACEHOLDER}</{m.group(2)}>",
        html,
    )
    # An empty list item likewise: marked empty, Qt adds an empty paragraph before a list
    # that starts the document or a cell, and drops an item that holds only a nested list.
    html = re.sub(r'(<li(?: style="[^"]*")?>)(?=</li>|<[uo]l>)', rf"\g<1>{CELL_PLACEHOLDER}", html)
    # Qt drops empty paragraphs unless they are marked the way its own HTML marks them.
    html = re.sub(
        r'<(p|h[1-3])(?: style="([^"]*)")?>(?=</\1>|<[uo]l>)',
        lambda m: (
            f'<{m.group(1)} style="{m.group(2) + ";" if m.group(2) else ""}'
            '-qt-paragraph-type:empty"><br>'
        ),
        html,
    )
    html = _code_blocks(html)
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


def _frames(frame: QTextFrame) -> list[QTextFrame]:
    """The frames inside ``frame``, in document order (a table before the tables in it)."""
    out = []
    for child in frame.childFrames():
        out.append(child)
        out.extend(_frames(child))
    return out


def _mark_padding(doc: QTextDocument, shapes: list[list[set[int]]]) -> None:
    """Mark the cells Qt added to fill short rows (PADDED_CELL), so they aren't written."""
    tables = [f for f in _frames(doc.rootFrame()) if isinstance(f, QTextTable)]
    if len(tables) != len(shapes):
        return  # not the tables the HTML has: mark nothing rather than the wrong cells
    for table, rows in zip(tables, shapes, strict=True):
        for r, columns in enumerate(rows[: table.rows()]):
            for c in range(table.columns()):
                cell = table.cellAt(r, c)
                if cell.row() == r and cell.column() == c and c not in columns:
                    fmt = cell.format()
                    fmt.setProperty(PADDED_CELL, True)
                    cell.setFormat(fmt)


def _drop_placeholder(block: QTextBlock) -> None:
    """Remove what _qt_html put in an empty cell paragraph, list item or code line."""
    text = block.text()
    cursor = QTextCursor(block)
    if text == CELL_PLACEHOLDER and (block.textList() is not None or cursor.currentTable()):
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
    elif text.startswith(CODE_PLACEHOLDER) and block.blockFormat().nonBreakableLines():
        cursor.deleteChar()


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
        self.front_matter = ""  # carried from the opened document into content()
        self.title = ""
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptRichText(True)
        self.viewport().setObjectName("DocPage")
        self.document().setDocumentMargin(PAGE_MARGIN)
        self._update_margins()

    # ----- loading --------------------------------------------------------
    def load(self, opened: OpenedDocument) -> None:
        self.images = dict(opened.images)
        self.front_matter, self.title = opened.front_matter, opened.title
        doc = self.document()
        doc.setUndoRedoEnabled(False)
        html = normalise_html(opened.html)[0]
        self.setHtml(_qt_html(html))
        _mark_padding(doc, _table_shapes(html))
        for name, data in self.images.items():
            self._add_resource(name, data)
        doc.setDocumentMargin(PAGE_MARGIN)
        block = doc.begin()
        group: tuple[int, int] | None = None  # the previous line's code block and its form
        while block.isValid():
            _drop_placeholder(block)
            self._fix_block(block)
            group = self._fix_code(block, group)
            block = block.next()
        doc.setUndoRedoEnabled(True)
        doc.clearUndoRedoStacks()
        doc.setModified(False)

    def _fix_block(self, block: QTextBlock) -> None:
        fmt = block.blockFormat()
        changed = False
        if fmt.leftMargin() == QUOTE_MARGIN and fmt.rightMargin() == QUOTE_MARGIN:
            fmt.setProperty(QUOTE_PROPERTY, True)
            if block.userState() >= 0:  # which quote (see _qt_html)
                fmt.setProperty(QUOTE_GROUP, block.userState())
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

    @staticmethod
    def _fix_code(block: QTextBlock, group: tuple[int, int] | None) -> tuple[int, int] | None:
        """Give a loaded code line its CODE_GROUP and CODE_FORM: a <pre>'s first line brings
        them (as Qt's user state, see _code_blocks), the lines after it share them. Returns
        (group, form), or None when the block isn't code."""
        fmt = block.blockFormat()
        state = block.userState()
        block.setUserState(-1)
        if not fmt.nonBreakableLines() or fmt.headingLevel() > 0 or block.textList() is not None:
            return None
        if state >= 0:
            group = divmod(state, 4)
        elif group is None:
            group = (new_code_group(), 0)
        set_code_group(fmt, *group)
        QTextCursor(block).setBlockFormat(fmt)
        return group

    @staticmethod
    def _join_pasted(block: QTextBlock, last: int) -> None:
        """A pasted code line joins the code block it lands in: the one above it, else the one
        right below the pasted lines; otherwise the pasted lines are a block of their own. A
        pasted quote line likewise joins the quote above it."""
        fmt = block.blockFormat()
        pasted_quote = block.userState() >= 0  # a quote the pasted HTML has (see _qt_html)
        block.setUserState(-1)
        if block.textList() is not None or fmt.headingLevel() > 0:
            return
        above = block.previous()
        if fmt.boolProperty(QUOTE_PROPERTY):
            if not pasted_quote and fmt.hasProperty(QUOTE_GROUP):
                return  # a line that was already there
            afmt = above.blockFormat() if above.isValid() else None
            if afmt is not None and afmt.boolProperty(QUOTE_PROPERTY) and above.textList() is None:
                if afmt.hasProperty(QUOTE_GROUP):
                    fmt.setProperty(QUOTE_GROUP, afmt.intProperty(QUOTE_GROUP))
                else:
                    fmt.clearProperty(QUOTE_GROUP)
                QTextCursor(block).setBlockFormat(fmt)
            return
        if not fmt.nonBreakableLines() or fmt.hasProperty(CODE_GROUP):
            return  # not code, or a line that was already there
        group = None
        for near in (above, block.document().findBlockByNumber(last + 1)):
            nfmt = near.blockFormat() if near.isValid() else None
            if (
                nfmt is not None
                and nfmt.nonBreakableLines()
                and near.textList() is None
                and not nfmt.boolProperty(QUOTE_PROPERTY)
                and (group := code_group_of(nfmt)) is not None
            ):
                break
            group = None
        set_code_group(fmt, *(group or (new_code_group(), 0)))
        QTextCursor(block).setBlockFormat(fmt)

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
    def _free_name(self, ext: str) -> str:
        name = next_image_name(self.images, ext)
        n = len(self.images) + 1
        while name in self.images:
            n += 1
            name = f"img/{n}.{ext}"
        return name

    def _store(self, data: bytes, ext: str) -> tuple[str, QImage]:
        name = self._free_name(ext)
        self.images[name] = data
        return name, self._add_resource(name, data)

    def insert_image(self, path: Path) -> None:
        ext = _IMAGE_EXTS.get(path.suffix.lower().lstrip("."))
        if ext is None or path.stat().st_size > MAX_FILE_BYTES:
            raise ConversionError(_NOT_A_PICTURE)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ConversionError("The picture couldn't be read.", details=str(exc)) from exc
        if QImage.fromData(data).isNull():
            raise ConversionError(_NOT_A_PICTURE)
        self._insert_picture(data, ext)

    def _insert_picture(self, data: bytes, ext: str) -> None:
        name, image = self._store(data, ext)
        fmt = QTextImageFormat()
        fmt.setName(name)
        room = PAGE_WIDTH - 2 * PAGE_MARGIN
        if image.width() > room:  # shrink wide pictures to the page
            fmt.setWidth(room)
            fmt.setHeight(round(image.height() * room / image.width()))
        self.textCursor().insertImage(fmt)

    # ----- pasting --------------------------------------------------------
    def insertFromMimeData(self, source) -> None:  # noqa: N802 (Qt override)
        """Pasted HTML is normalised and its pictures kept; a picture or plain text as such."""
        if source.hasHtml():
            try:
                self._paste_html(source.html())
            except ConversionError:  # HTML lxml can't read: paste the text instead
                log.warning("Pasted HTML couldn't be read; pasting its text", exc_info=True)
                if source.hasText():
                    self.textCursor().insertText(source.text())
        elif source.hasImage():
            image = source.imageData()
            if isinstance(image, QImage) and not image.isNull():
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                image.save(buf, "PNG")
                self._insert_picture(bytes(buf.data()), "png")
        elif source.hasText():
            self.textCursor().insertText(source.text())

    def _paste_html(self, html: str) -> None:
        html = _FILE_IMG.sub(lambda m: m.group(1) + _local_data_uri(m.group(3)) + m.group(2), html)
        fragment, pictures = normalise_html(html)  # data: pictures decoded, the web dropped
        renames = {}
        for old, data in pictures.items():
            name, _image = self._store(data, old.rsplit(".", 1)[-1])
            renames[old] = name
        fragment = _IMG_SRC.sub(
            lambda m: f'<img src="{renames.get(m.group(1), m.group(1))}"', fragment
        )
        # The placeholders _qt_html adds are told apart from the pasted text by removing any
        # the text has (invisible characters).
        fragment = fragment.translate(dict.fromkeys(map(ord, _PLACEHOLDERS + LINK_PLACEHOLDER)))
        if not fragment:
            return
        cursor = self.textCursor()
        cursor.beginEditBlock()
        first = cursor.block().blockNumber()
        cursor.insertHtml(_qt_html(fragment))
        block = self.document().findBlockByNumber(first)
        last = cursor.block().blockNumber()
        while block.isValid() and block.blockNumber() <= last:
            # pasted into a line, an empty item or code line keeps its placeholder mid-line
            text = block.text()
            for i in reversed(range(len(text))):
                if text[i] in _PLACEHOLDERS:
                    remove = QTextCursor(block)
                    remove.setPosition(block.position() + i)
                    remove.deleteChar()
            self._fix_block(block)
            self._join_pasted(block, last)
            block = block.next()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    # ----- saving ---------------------------------------------------------
    def content(self) -> SaveContent:
        doc = self.document()
        html = document_to_html(doc)
        used = set(_IMG_SRC.findall(html))
        text = doc.toPlainText().replace("￼", "").replace(LINK_PLACEHOLDER, "")
        return SaveContent(
            html,
            {name: data for name, data in self.images.items() if name in used},
            text + "\n" if text else "",
            front_matter=self.front_matter,
            title=self.title,
        )

    def viewportEvent(self, event) -> bool:  # noqa: N802 (Qt override)
        """The tool tips that record tag nesting (WRAP_PREFIX) are never shown."""
        if event.type() == QEvent.Type.ToolTip:
            tip = self.cursorForPosition(event.pos()).charFormat().toolTip()
            if tip.startswith(WRAP_PREFIX):
                QToolTip.hideText()
                event.accept()  # handled here, not passed on to be shown
                return True
        return super().viewportEvent(event)

    def createMimeDataFromSelection(self):  # noqa: N802 (Qt override)
        """Copied text without the invisible LINK_PLACEHOLDER."""
        mime = super().createMimeDataFromSelection()
        if mime.hasText():
            mime.setText(mime.text().replace(LINK_PLACEHOLDER, ""))
        return mime

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
