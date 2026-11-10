"""Find a font file by family name (bundled DejaVu first, then the system's fonts)."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_index: dict[str, dict[str, Path]] | None = None  # family (lower) → style (lower) → file
_lock = threading.Lock()
REGULAR = ("regular", "book", "normal", "roman", "medium")


def font_dirs() -> list[Path]:
    from umc.converters.subtitles import fonts_dir

    dirs: list[Path] = []
    bundled = fonts_dir()
    if bundled:
        dirs.append(bundled)
    home = Path.home()
    if sys.platform == "win32":
        dirs.append(Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    elif sys.platform == "darwin":
        dirs += [Path("/Library/Fonts"), Path("/System/Library/Fonts"), home / "Library/Fonts"]
    else:
        dirs += [Path("/usr/share/fonts"), home / ".local/share/fonts", home / ".fonts"]
    return [d for d in dirs if d.is_dir()]


def _build_index() -> dict[str, dict[str, Path]]:
    from PIL import ImageFont

    index: dict[str, dict[str, Path]] = {}
    for d in font_dirs():
        for p in sorted(d.rglob("*")):
            if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                family, style = ImageFont.truetype(str(p), 12).getname()
            except Exception:
                continue
            if family:
                index.setdefault(family.lower(), {}).setdefault((style or "").lower(), p)
    return index


def _get_index() -> dict[str, dict[str, Path]]:
    global _index
    with _lock:
        if _index is None:
            _index = _build_index()
            log.info("font index: %d families", len(_index))
        return _index


def find_font(family: str, bold: bool, italic: bool) -> Path | None:
    styles = _get_index().get(family.strip().lower())
    if not styles:
        return None
    wanted = " ".join(w for w, on in (("bold", bold), ("italic", italic)) if on)
    if wanted:
        for key in (wanted, wanted.replace("italic", "oblique")):
            if key in styles:
                return styles[key]
    for key in REGULAR:
        if key in styles:
            return styles[key]
    return next(iter(styles.values()))
