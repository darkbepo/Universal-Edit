"""Video editor project model and timeline maths. Pure Python, no Qt."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from umc.core.options import opt

TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("none", "None"),
    ("fade", "Crossfade"),
    ("fadeblack", "Fade through black"),
    ("wipeleft", "Wipe left"),
    ("wiperight", "Wipe right"),
    ("slideleft", "Slide left"),
    ("slideright", "Slide right"),
)
ANCHORS: tuple[tuple[str, str], ...] = (
    ("top-left", "Top left"),
    ("top", "Top"),
    ("top-right", "Top right"),
    ("left", "Left"),
    ("center", "Centre"),
    ("right", "Right"),
    ("bottom-left", "Bottom left"),
    ("bottom", "Bottom"),
    ("bottom-right", "Bottom right"),
)
FITS: tuple[tuple[str, str], ...] = (
    ("letterbox", "Letterbox"),
    ("crop", "Crop to fill"),
    ("stretch", "Stretch"),
)
ROTATIONS: tuple[tuple[int, str], ...] = ((0, "None"), (90, "90°"), (180, "180°"), (270, "270°"))

_PRESET_SIZES = {
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
    "vertical": (1080, 1920),
    "square": (1080, 1080),
}


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def _fade(label: str) -> float:
    return opt(0.0, label, kind="float", min=0.0, max=5.0, step=0.1, decimals=1, unit="s")


def _grade(label: str) -> int:
    return opt(0, label, kind="slider", min=-100, max=100)


def _volume() -> float:
    return opt(1.0, "Volume", kind="slider", min=0.0, max=2.0, step=0.01, unit="%")


@dataclass(frozen=True)
class ProjectSettings:
    resolution: str = opt(
        "first",
        "Resolution",
        choices=(
            ("first", "Match first video"),
            ("720p", "720p (1280×720)"),
            ("1080p", "1080p (1920×1080)"),
            ("4k", "4K (3840×2160)"),
            ("vertical", "Vertical (1080×1920)"),
            ("square", "Square (1080×1080)"),
            ("custom", "Custom"),
        ),
    )
    width: int = opt(
        1920,
        "Width",
        kind="int",
        min=2,
        max=7680,
        unit="px",
        show_if=lambda v: v.get("resolution") == "custom",
    )
    height: int = opt(
        1080,
        "Height",
        kind="int",
        min=2,
        max=7680,
        unit="px",
        show_if=lambda v: v.get("resolution") == "custom",
    )
    fps: str = opt(
        "first",
        "Frame rate",
        choices=(("first", "Match first video"), "24", "25", "30", "50", "60"),
    )
    background: str = opt("#000000", "Background", kind="color")


@dataclass(frozen=True)
class Source:
    id: str
    path: Path
    kind: Literal["video", "image", "audio"]
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


@dataclass(frozen=True)
class Transition:
    kind: str = opt("none", "Transition", choices=TRANSITIONS)
    duration: float = opt(
        0.5, "Duration", kind="float", min=0.1, max=3.0, step=0.1, decimals=1, unit="s"
    )


@dataclass(frozen=True)
class VideoClip:
    id: str
    source: str
    in_: float
    out: float
    speed: float = opt(
        1.0, "Speed", kind="float", min=0.25, max=4.0, step=0.25, decimals=2, unit="×"
    )
    volume: float = _volume()
    mute: bool = opt(False, "Mute")
    fade_in: float = _fade("Fade in")
    fade_out: float = _fade("Fade out")
    fit: str = opt("letterbox", "Fit", choices=FITS)
    rotate: int = opt(0, "Rotate", choices=ROTATIONS)
    flip_h: bool = opt(False, "Flip horizontally")
    flip_v: bool = opt(False, "Flip vertically")
    crop_left: int = opt(0, "Crop left", kind="slider", min=0, max=100, unit="%")
    crop_top: int = opt(0, "Crop top", kind="slider", min=0, max=100, unit="%")
    crop_right: int = opt(0, "Crop right", kind="slider", min=0, max=100, unit="%")
    crop_bottom: int = opt(0, "Crop bottom", kind="slider", min=0, max=100, unit="%")
    brightness: int = _grade("Brightness")
    contrast: int = _grade("Contrast")
    saturation: int = _grade("Saturation")
    transition_in: Transition = field(default_factory=Transition)


@dataclass(frozen=True)
class PictureClip:
    id: str
    source: str
    duration: float = opt(
        3.0, "Duration", kind="float", min=0.1, max=60.0, step=0.1, decimals=1, unit="s"
    )
    fit: str = opt("letterbox", "Fit", choices=FITS)
    rotate: int = opt(0, "Rotate", choices=ROTATIONS)
    flip_h: bool = opt(False, "Flip horizontally")
    flip_v: bool = opt(False, "Flip vertically")
    fade_in: float = _fade("Fade in")
    fade_out: float = _fade("Fade out")
    brightness: int = _grade("Brightness")
    contrast: int = _grade("Contrast")
    saturation: int = _grade("Saturation")
    transition_in: Transition = field(default_factory=Transition)


@dataclass(frozen=True)
class AudioClip:
    id: str
    source: str
    start: float
    in_: float
    out: float
    volume: float = _volume()
    fade_in: float = _fade("Fade in")
    fade_out: float = _fade("Fade out")
    loop_until: float = 0.0


@dataclass(frozen=True)
class TextItem:
    id: str
    text: str = opt("Text", "Text", kind="text")
    start: float = 0.0
    end: float = 3.0
    anchor: str = opt("bottom", "Position", choices=ANCHORS)
    x_offset: float = opt(
        0.0, "Horizontal offset", kind="float", min=-100, max=100, step=0.5, decimals=1, unit="%"
    )
    y_offset: float = opt(
        0.0, "Vertical offset", kind="float", min=-100, max=100, step=0.5, decimals=1, unit="%"
    )
    font: str = opt("DejaVu Sans", "Font", kind="text")
    size: float = opt(8.0, "Size", kind="float", min=1, max=50, step=0.5, decimals=1, unit="%")
    bold: bool = opt(False, "Bold")
    italic: bool = opt(False, "Italic")
    colour: str = opt("#ffffff", "Colour", kind="color")
    outline_colour: str = opt("#000000", "Outline colour", kind="color")
    outline: float = opt(
        2.0, "Outline", kind="float", min=0, max=10, step=0.5, decimals=1, unit="px"
    )
    box: bool = opt(False, "Background box")
    box_colour: str = opt("#000000", "Box colour", kind="color")
    box_opacity: float = opt(
        0.5, "Box opacity", kind="slider", min=0.0, max=1.0, step=0.01, unit="%"
    )
    fade_in: float = _fade("Fade in")
    fade_out: float = _fade("Fade out")


MainItem = VideoClip | PictureClip


@dataclass(frozen=True)
class Project:
    settings: ProjectSettings
    sources: tuple[Source, ...]
    main: tuple[MainItem, ...] = ()
    text_rows: tuple[tuple[TextItem, ...], ...] = ((),)
    music_rows: tuple[tuple[AudioClip, ...], ...] = ((),)

    def source(self, source_id: str) -> Source:
        for s in self.sources:
            if s.id == source_id:
                return s
        raise KeyError(source_id)


@dataclass(frozen=True)
class Span:
    start: float
    end: float
    row_kind: Literal["main", "text", "music"]
    row: int
    index: int


def clip_duration(item: MainItem | AudioClip) -> float:
    if isinstance(item, VideoClip):
        return (item.out - item.in_) / item.speed
    if isinstance(item, PictureClip):
        return item.duration
    if item.loop_until > 0:
        return item.loop_until - item.start
    return item.out - item.in_


def layout(project: Project) -> dict[str, Span]:
    """Where every item sits on the timeline, keyed by item id."""
    spans: dict[str, Span] = {}
    prev_end = 0.0
    for i, item in enumerate(project.main):
        tr = item.transition_in
        overlap = tr.duration if tr.kind != "none" else 0.0
        start = 0.0 if i == 0 else prev_end - overlap
        end = start + clip_duration(item)
        spans[item.id] = Span(start, end, "main", 0, i)
        prev_end = end
    for r, row in enumerate(project.text_rows):
        for i, t in enumerate(row):
            spans[t.id] = Span(t.start, t.end, "text", r, i)
    for r, mrow in enumerate(project.music_rows):
        for i, a in enumerate(mrow):
            spans[a.id] = Span(a.start, a.start + clip_duration(a), "music", r, i)
    return spans


def length(project: Project) -> float:
    return max((s.end for s in layout(project).values()), default=0.0)


def find(project: Project, item_id: str) -> tuple[str, int, int, object]:
    for i, item in enumerate(project.main):
        if item.id == item_id:
            return "main", 0, i, item
    for r, row in enumerate(project.text_rows):
        for i, t in enumerate(row):
            if t.id == item_id:
                return "text", r, i, t
    for r, mrow in enumerate(project.music_rows):
        for i, a in enumerate(mrow):
            if a.id == item_id:
                return "music", r, i, a
    raise KeyError(item_id)


def _even(n: int) -> int:
    return max(2, n - (n % 2))


def _first_video(project: Project) -> Source | None:
    for item in project.main:
        src = project.source(item.source)
        if src.kind == "video":
            return src
    return None


def output_size(project: Project) -> tuple[int, int]:
    res = project.settings.resolution
    if res in _PRESET_SIZES:
        return _PRESET_SIZES[res]
    if res == "custom":
        return _even(project.settings.width), _even(project.settings.height)
    first = _first_video(project)
    if first is None:
        return 1920, 1080
    return _even(first.width), _even(first.height)


def output_fps(project: Project) -> float:
    fps = project.settings.fps
    if fps != "first":
        return float(fps)
    first = _first_video(project)
    return round(first.fps, 3) if first else 30.0
