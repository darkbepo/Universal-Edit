"""“LibreOffice/Pandoc/FFmpeg is needed” dialog with Download + Check again buttons."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from umc.tools import ffmpeg, libreoffice, pandoc

_CHECKS = {
    "LibreOffice": libreoffice.is_available,
    "Pandoc": pandoc.is_available,
    "FFmpeg": ffmpeg.is_available,
    "FFmpeg (ffprobe)": ffmpeg.is_available,
}

_WHY = {
    "LibreOffice": "LibreOffice is a free office suite. Universal Media Converter uses it in the "
    "background to convert Word, OpenDocument, RTF, spreadsheet and presentation files.",
    "Pandoc": "Pandoc is a free document converter used for Markdown, HTML, EPUB and similar "
    "formats.",
    "FFmpeg": "FFmpeg is a free video toolkit used for every video and audio conversion.",
}


class ToolMissingDialog(QDialog):
    """``exec()`` returns Accepted once the tool is found (so the caller can retry)."""

    def __init__(self, tool: str, url: str, message: str, parent=None) -> None:
        super().__init__(parent)
        self.tool = tool
        self.setWindowTitle(f"{tool} needed")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        title = QLabel(f"{tool} is required")
        title.setObjectName("Title")
        body = QLabel(
            f"{message}\n\n{_WHY.get(tool.split(' ')[0], '')}\n\n"
            "Install it, then press “I've installed it” — no restart needed."
        )
        body.setWordWrap(True)
        self.status = QLabel("")
        self.status.setObjectName("Error")
        lay.addWidget(title)
        lay.addWidget(body)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        cancel = QPushButton("Cancel")
        check = QPushButton("I've installed it — check again")
        download = QPushButton(f"Download {tool.split(' ')[0]}")
        download.setObjectName("Primary")
        buttons.addWidget(cancel)
        buttons.addStretch(1)
        buttons.addWidget(check)
        buttons.addWidget(download)
        lay.addLayout(buttons)

        cancel.clicked.connect(self.reject)
        download.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
        check.clicked.connect(self._check)

    def _check(self) -> None:
        check = _CHECKS.get(self.tool)
        if check is not None and check():
            self.accept()
        else:
            self.status.setText(
                f"{self.tool} still wasn't found. If you just installed it, make sure the "
                "installation finished."
            )
