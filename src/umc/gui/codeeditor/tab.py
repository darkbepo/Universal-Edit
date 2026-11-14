"""One open file of the code editor."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtGui import QTextDocument

from umc.codeedit.textfile import Encoding, Newline, TextFile
from umc.gui.codeeditor.highlight import ChunkedHighlighter


@dataclass(eq=False)
class CodeTab:
    path: Path | None  # None = Untitled
    file: TextFile | None  # as read / last saved; None for Untitled (first save: UTF-8, "\n")
    document: QTextDocument
    highlighter: ChunkedHighlighter
    language_id: str
    encoding: Encoding = "utf-8"
    newline: Newline = "\n"
    bom: bool = False
    indent: str = "    "
    disk_stamp: tuple[int, int] | None = None  # (mtime_ns, size) at open / last save
    colours: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def dirty(self) -> bool:
        if self.document.isModified():
            return True
        if self.file is None:
            return False
        return (self.encoding, self.newline, self.bom) != (
            self.file.encoding,
            self.file.newline,
            self.file.bom,
        )

    @property
    def title(self) -> str:
        return self.path.name if self.path else "Untitled"
