"""File-type icons (no Qt): one per kind of file the viewer opens, so pictures, videos, songs,
documents … look different in Explorer / the file manager when Universal Edit opens them.

Each icon is a page with a folded corner in the kind's own colour and its line symbol (the
app's Tabler icons) in white, with no text. ``svg(kind)`` draws it; ``scripts/
make_file_icons.py`` turns them into ``packaging/fileicons/<kind>.ico`` (Windows, checked in
like ``packaging/icon.ico``); the .deb installs the SVGs as MIME-type icons."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from umc.core.detect import MediaFamily, family_of_ext


@dataclass(frozen=True)
class FileIcon:
    kind: str  # file name and ProgID part: "image" → UniversalEdit.View.image, image.ico
    title: str  # Windows' "Type" column: "Picture (Universal Edit)"
    symbol: str  # icon_data name
    colour: str  # the page's colour
    families: tuple[MediaFamily, ...]


ICONS: tuple[FileIcon, ...] = (
    FileIcon("image", "Picture", "photo", "#2f9e6b", (MediaFamily.IMAGE,)),
    FileIcon("video", "Video", "movie", "#d64545", (MediaFamily.VIDEO,)),
    FileIcon("audio", "Music", "music", "#8b5cf6", (MediaFamily.AUDIO,)),
    FileIcon("document", "Document", "document", "#3b6fd8", (MediaFamily.DOCUMENT,)),
    FileIcon("vector", "Vector drawing", "vector", "#d6439a", (MediaFamily.VECTOR,)),
    FileIcon("model", "3D model", "cube", "#e08a1e", (MediaFamily.MODEL,)),
    FileIcon("cad", "CAD drawing", "cad", "#16a0b5", (MediaFamily.CAD,)),
    FileIcon("subtitle", "Subtitles", "subtitle", "#b7861a", (MediaFamily.SUBTITLE,)),
    FileIcon("code", "Code or text", "code", "#5b6475", (MediaFamily.CODE,)),
    FileIcon("archive", "Archive", "archive", "#8a6d3b", (MediaFamily.ARCHIVE,)),
)
BY_KIND = {i.kind: i for i in ICONS}
_BY_FAMILY = {f: i for i in ICONS for f in i.families}
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)  # in each .ico


def icon_for_ext(ext: str) -> FileIcon | None:
    family = family_of_ext(ext)
    return _BY_FAMILY.get(family) if family is not None else None


def svg(kind: str, size: int = 256) -> str:
    """The icon as a 256 × 256 SVG; for small sizes (``size`` ≤ 32 px) the symbol's lines are
    bolder, so it still reads at 16 px."""
    from umc.gui.icon_data import ICONS as SYMBOLS  # plain strings; importing loads no Qt

    icon = BY_KIND[kind]
    page = (
        "M60 16h96l60 60v148a16 16 0 0 1 -16 16h-140a16 16 0 0 1 -16 -16v-192a16 16 0 0 1 16 -16z"
    )
    fold = "M156 16v44a16 16 0 0 0 16 16h44z"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{_mix(icon.colour, "#ffffff", 0.12)}"/>'
        f'<stop offset="1" stop-color="{_mix(icon.colour, "#000000", 0.12)}"/>'
        "</linearGradient></defs>"
        f'<path d="{page}" fill="url(#g)"/>'
        f'<path d="{fold}" fill="#ffffff" fill-opacity="0.45"/>'
        '<g transform="translate(70 90) scale(5)" fill="none" stroke="#ffffff" '
        f'stroke-width="{1.9 if size > 32 else 2.7}" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f"{SYMBOLS[icon.symbol]}</g></svg>"
    )


def _mix(colour: str, other: str, amount: float) -> str:
    a = [int(colour[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b, strict=True))


def ico_folder() -> Path:
    """Where the .ico files are: inside the built app, else packaging/fileicons."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "fileicons"
    return Path(__file__).resolve().parents[3] / "packaging" / "fileicons"


def ico_path(kind: str) -> Path:
    return ico_folder() / f"{kind}.ico"
