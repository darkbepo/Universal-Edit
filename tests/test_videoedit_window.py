from __future__ import annotations

from pathlib import Path

import pytest

from umc.videoedit.project import (
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    VideoClip,
)
from umc.videoedit.window import window

VID = Source("v", Path("v.mp4"), "video", 10.0, 640, 360, 30.0, True)
PIC = Source("p", Path("p.png"), "image", 0.0, 640, 360, 0.0, False)
AUD = Source("a", Path("a.mp3"), "audio", 10.0, 0, 0, 0.0, True)


def _proj(main=(), text=((),), music=((),)):
    return Project(ProjectSettings(), (VID, PIC, AUD), tuple(main), text, music)


def _clip(cid, **kw):
    return VideoClip(cid, "v", 0.0, 2.0, **kw)


def test_window_on_cut():
    w = window(_proj([_clip("a"), _clip("b")]), 2.0, 3.0)
    assert [c.id for c in w.project.main] == ["b"]
    assert w.lead == 0
    assert w.duration == 1.0


def test_window_inside_clip():
    w = window(_proj([_clip("a"), _clip("b")]), 1.0, 3.0)
    assert w.project.main[0].id == "a"
    assert w.project.main[0].in_ == 1.0
    assert w.lead == 0
    assert [c.id for c in w.project.main] == ["a", "b"]


def test_window_inside_transition():
    tr = Transition("fade", 0.5)
    w = window(_proj([_clip("a"), _clip("b", transition_in=tr)]), 1.6, 2.5)
    a, b = w.project.main
    assert (a.id, b.id) == ("a", "b")
    assert a.in_ == pytest.approx(1.5)
    assert a.out - a.in_ == pytest.approx(0.5)
    assert b.transition_in == tr
    assert w.lead == pytest.approx(0.1)


def test_window_inside_fade_in_not_cut():
    w = window(_proj([_clip("a", fade_in=1.0)]), 0.4, 1.5)
    assert w.project.main[0].in_ == 0
    assert w.lead == pytest.approx(0.4)


def test_window_picture_cut():
    w = window(_proj([PictureClip("x", "p", duration=3.0)]), 1.0, 2.0)
    assert w.project.main[0].duration == 2.0


def test_window_past_main_end():
    t = TextItem("t", start=0.0, end=6.0)
    w = window(_proj([_clip("a"), _clip("b")], text=((t,),)), 5.0, 5.5)
    assert w.project.main == ()
    (tt,) = w.project.text_rows[0]
    assert tt.start == -5
    assert tt.end == 1
    assert w.duration == 0.5
    assert w.lead == 0


def test_window_shifts_music_negative():
    m = AudioClip("m", "a", 0.0, 0.0, 4.0)
    w = window(_proj([_clip("a")], music=((m,),)), 1.0, 2.0)
    assert w.project.music_rows[0][0].start == -1


def test_window_drops_items_after_end():
    w = window(_proj([_clip("a"), _clip("b"), _clip("c")]), 0.0, 2.0)
    assert [c.id for c in w.project.main] == ["a"]


def test_window_keeps_item_whose_transition_starts_before_end():
    tr = Transition("fade", 0.5)
    w = window(_proj([_clip("a"), _clip("b", transition_in=tr)]), 0.0, 1.8)
    assert [c.id for c in w.project.main] == ["a", "b"]


def test_window_cut_converts_to_source_time_with_speed():
    clip = VideoClip("a", "v", 0.0, 8.0, speed=2.0)  # 4 s on the timeline
    w = window(_proj([clip]), 1.0, 3.0)
    assert w.project.main[0].in_ == 2.0
    assert w.lead == 0


def test_window_cut_past_fade_in_clears_it():
    w = window(_proj([_clip("a", fade_in=0.5)]), 1.0, 2.0)
    assert w.project.main[0].in_ == 1.0
    assert w.project.main[0].fade_in == 0.0


def test_window_cut_exactly_at_fade_in_end_clears_it():
    w = window(_proj([_clip("a", fade_in=1.0)]), 1.0, 2.0)
    assert w.project.main[0].in_ == 1.0
    assert w.project.main[0].fade_in == 0.0


def test_window_uncut_fade_in_is_kept():
    w = window(_proj([_clip("a", fade_in=1.0)]), 0.4, 1.5)
    assert w.project.main[0].fade_in == 1.0


def test_window_transition_branch_clears_fade_in_of_previous():
    tr = Transition("fade", 0.5)
    w = window(_proj([_clip("a", fade_in=0.5), _clip("b", transition_in=tr)]), 1.6, 2.5)
    a = w.project.main[0]
    assert a.in_ == pytest.approx(1.5)
    assert a.fade_in == 0.0


def test_window_transition_branch_fade_in_guard_on_previous():
    tr = Transition("fade", 0.5)
    w = window(_proj([_clip("a", fade_in=1.8), _clip("b", transition_in=tr)]), 1.6, 2.5)
    a, b = w.project.main
    assert a.in_ == 0
    assert a.fade_in == 1.8
    assert w.lead == pytest.approx(1.6)
    assert b.transition_in == tr


def test_window_none_transition_does_not_keep_previous():
    w = window(_proj([_clip("a"), _clip("b", transition_in=Transition("none", 0.5))]), 2.0, 3.0)
    assert [c.id for c in w.project.main] == ["b"]
    assert w.lead == 0


def test_window_music_loop_until_shifts():
    m = AudioClip("m", "a", 0.0, 0.0, 2.0, loop_until=6.0)
    w = window(_proj([_clip("a")], music=((m,),)), 1.0, 2.0)
    got = w.project.music_rows[0][0]
    assert got.start == -1
    assert got.loop_until == 5.0
