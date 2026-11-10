from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from umc.core.options import option_specs
from umc.videoedit.project import (
    PictureClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    VideoClip,
    layout,
    length,
    output_size,
)


def src(id_: str, kind: str = "video", w: int = 1920, h: int = 1080) -> Source:
    return Source(id_, Path(f"{id_}.mp4"), kind, 10.0, w, h, 30.0, True)  # type: ignore[arg-type]


def proj(main, sources=None, settings=None, text=((),)) -> Project:
    return Project(
        settings or ProjectSettings(),
        tuple(sources or [src("v"), src("p", "image")]),
        tuple(main),
        text,
    )


def test_layout_back_to_back():
    p = proj([VideoClip("a", "v", 0, 2), VideoClip("b", "v", 0, 2)])
    sp = layout(p)
    assert (sp["a"].start, sp["a"].end) == (0, 2)
    assert (sp["b"].start, sp["b"].end) == (2, 4)
    assert length(p) == 4


def test_layout_transition_overlaps():
    p = proj(
        [
            VideoClip("a", "v", 0, 2),
            VideoClip("b", "v", 0, 2, transition_in=Transition("fade", 0.5)),
        ]
    )
    assert layout(p)["b"].start == 1.5
    assert length(p) == 3.5


def test_layout_speed_and_picture():
    p = proj([VideoClip("a", "v", 1, 5, speed=2.0), PictureClip("b", "p")])
    sp = layout(p)
    assert (sp["a"].start, sp["a"].end) == (0, 2)
    assert (sp["b"].start, sp["b"].end) == (2, 5)


def test_length_includes_text_and_music():
    p = proj([VideoClip("a", "v", 0, 2)], text=((TextItem("t", start=0, end=6),),))
    assert length(p) == 6


def test_output_size_presets():
    assert output_size(proj([], settings=ProjectSettings(resolution="vertical"))) == (1080, 1920)
    custom = ProjectSettings(resolution="custom", width=1281, height=721)
    assert output_size(proj([], settings=custom)) == (1280, 720)
    p = proj([VideoClip("a", "s", 0, 1)], sources=[src("s", w=320, h=240)])
    assert output_size(p) == (320, 240)
    assert output_size(proj([])) == (1920, 1080)


def test_items_are_frozen():
    clip = VideoClip("a", "v", 0, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        clip.speed = 2.0  # type: ignore[misc]


def test_option_specs_cover_inspector_fields():
    names = {s.name for s in option_specs(VideoClip)}
    assert {"speed", "volume", "fit", "brightness"} <= names
    assert not names & {"id", "source"}
