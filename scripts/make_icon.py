"""Render the Universal Edit logo (src/umc/gui/logo.svg) into every icon file that's committed.

Outputs: packaging/icon.ico (Windows .exe), packaging/icon.png, and the website's
web/public/icon.png + icon.svg (favicon and top-bar logo). Run again after editing logo.svg;
tests/test_branding.py checks the copies still match.
"""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "src" / "umc" / "gui" / "logo.svg"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int):
    """The logo as a ``size`` × ``size`` Pillow image (each size drawn from the SVG, not scaled)."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(SVG))
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def main() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    _app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (QtSvg needs one)
    pack = ROOT / "packaging"
    render(1024).save(pack / "icon.png")
    images = [render(s) for s in ICO_SIZES]
    images[-1].save(pack / "icon.ico", sizes=[(s, s) for s in ICO_SIZES], append_images=images[:-1])
    render(256).save(ROOT / "web" / "public" / "icon.png")
    shutil.copyfile(SVG, ROOT / "web" / "public" / "icon.svg")


if __name__ == "__main__":
    main()
