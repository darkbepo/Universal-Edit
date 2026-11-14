"""Previews for the code editor: Markdown in a side pane, HTML in the default browser.

The HTML preview is a copy in a ``umc-codepreview-*`` temp folder (never the user's folder) with
a ``<base href>`` pointing back at the original folder, so its pictures and CSS still load."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QTextBrowser, QWidget

_HEAD = re.compile(r"<head(?:\s[^>]*)?>", re.I)
_last_dir: Path | None = None


def markdown_html(text: str) -> str:
    from markdown_it import MarkdownIt

    return MarkdownIt("commonmark").enable("table").enable("strikethrough").render(text)


def html_preview_file(text: str, source: Path | None) -> Path:
    """Write ``text`` to a fresh temp folder (the previous preview's is removed) and return it."""
    global _last_dir
    if _last_dir is not None:
        shutil.rmtree(_last_dir, ignore_errors=True)
    _last_dir = Path(tempfile.mkdtemp(prefix="umc-codepreview-"))
    if source is not None:
        base = f'<base href="{source.parent.resolve().as_uri()}/">'
        m = _HEAD.search(text)
        text = text[: m.end()] + base + text[m.end() :] if m else base + text
    out = _last_dir / (source.name if source else "preview.html")
    out.write_text(text, encoding="utf-8")
    return out


def open_in_browser(path: Path) -> bool:
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class MarkdownPane(QTextBrowser):
    """The Markdown preview beside the code (links open in the default browser)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CodePreview")
        self.setOpenExternalLinks(True)
        self.source_html = ""

    def set_markdown(self, text: str) -> None:
        self.source_html = markdown_html(text)
        bar = self.verticalScrollBar()
        keep = bar.value()
        self.setHtml(self.source_html)
        bar.setValue(keep)
