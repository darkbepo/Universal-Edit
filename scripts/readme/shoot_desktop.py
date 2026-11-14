"""Raw screenshot of the desktop video editor for the README (the website has none yet).

    python scripts/readme/shoot_desktop.py SAMPLES

Writes docs/images/raw/video.png (1440 × 860, dark theme); `shoot.mjs` frames it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

REPO = Path(__file__).resolve().parents[2]


def main() -> None:
    samples = Path(sys.argv[1])
    app = QApplication([])
    app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    from umc.gui import theme
    from umc.gui.videoedit.editor_page import VideoEditorPage

    app.setStyle("Fusion")
    app.setStyleSheet(theme.stylesheet())
    page = VideoEditorPage()
    page.resize(1440, 860)
    page.show()
    files = ["mountain-flight.mp4", "lake-sunrise.jpg", "desert-dusk.jpg", "summer-tune.wav"]
    assert page.open_paths([samples / f for f in files])
    page.add_text()
    box = page.inspector.panel.field_widget("text")
    if box is not None:
        box.setPlainText("Spring at the lakes")
    size = page.inspector.panel.field_widget("size")
    if size is not None:
        size.setValue(10.0)

    def shoot() -> None:
        page.seek(2.0)
        QTimer.singleShot(3000, finish)

    def finish() -> None:
        out = REPO / "docs" / "images" / "raw"
        out.mkdir(parents=True, exist_ok=True)
        page.grab().save(str(out / "video.png"))
        page._ask_save_changes = lambda: "discard"
        page.shutdown()
        app.quit()

    QTimer.singleShot(4000, shoot)
    app.exec()


if __name__ == "__main__":
    main()
