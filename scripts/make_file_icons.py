"""Draw the file-type icons (umc.core.fileicons) into packaging/fileicons/<kind>.ico.

    python scripts/make_file_icons.py

Each .ico holds every size in ``fileicons.SIZES``, each drawn at that size (not scaled down
from the big one), so small icons stay sharp. Run it after changing a colour or symbol; the
files are checked in, like packaging/icon.ico."""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

from umc.core import fileicons  # noqa: E402

OUT = ROOT / "packaging" / "fileicons"


def render(svg: str, size: int) -> Image.Image:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p)
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def write_ico(kind: str, folder: Path = OUT) -> Path:
    images = [render(fileicons.svg(kind, s), s) for s in fileicons.SIZES]
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{kind}.ico"
    big = images[-1]
    big.save(out, format="ICO", sizes=[im.size for im in images], append_images=images[:-1])
    return out


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (QPainter needs it)
    for icon in fileicons.ICONS:
        print(write_ico(icon.kind))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
