from __future__ import annotations

import copy
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


def test_percentage_sliders_use_whole_percent_values():
    from umc.videoedit.project import AudioClip

    for cls in (VideoClip, AudioClip):
        spec = next(s for s in option_specs(cls) if s.name == "volume")
        assert (spec.default, spec.max, spec.unit) == (100, 200, "%")
    spec = next(s for s in option_specs(TextItem) if s.name == "box_opacity")
    assert (spec.default, spec.max, spec.unit) == (50, 100, "%")


# ---- edits and history ------------------------------------------------------------------------

from umc.videoedit import edits  # noqa: E402
from umc.videoedit.history import History  # noqa: E402
from umc.videoedit.project import AudioClip  # noqa: E402


def test_split_video_clip():
    p = proj([VideoClip("a", "v", 0, 4)])
    q = edits.split(p, "a", 1.5)
    assert len(q.main) == 2
    first, second = q.main
    assert (first.in_, first.out) == (0, 1.5)
    assert (second.in_, second.out) == (1.5, 4)
    assert len({"a", first.id, second.id}) == 3
    assert length(q) == length(p)


def test_split_with_speed():
    p = proj([VideoClip("a", "v", 0, 4, speed=2.0)])
    q = edits.split(p, "a", 0.5)
    assert (q.main[0].out, q.main[1].in_) == (1.0, 1.0)


def test_split_keeps_fades_and_transition_on_the_right_halves():
    p = proj(
        [
            VideoClip("x", "v", 0, 2),
            VideoClip("a", "v", 0, 4, fade_in=1, fade_out=2, transition_in=Transition("fade", 0.5)),
        ]
    )
    q = edits.split(p, "a", 3.0)
    first, second = q.main[1], q.main[2]
    assert (first.fade_in, first.fade_out, first.transition_in.kind) == (1, 0, "fade")
    assert (second.fade_in, second.fade_out, second.transition_in.kind) == (0, 2, "none")


def test_split_outside_raises():
    p = proj([VideoClip("a", "v", 0, 4)])
    for t in (0.0, 4.0, 5.0):
        with pytest.raises(edits.EditError, match="inside the clip"):
            edits.split(p, "a", t)


def test_trim_clamps_to_source():
    p = proj([VideoClip("a", "v", 2, 4)])
    assert edits.trim(p, "a", "start", -5).main[0].in_ == 0
    assert edits.trim(p, "a", "end", 100).main[0].out == 10.0


def test_trim_picture_and_text():
    p = proj([PictureClip("a", "p")], text=((TextItem("t", start=1, end=4),),))
    assert edits.trim(p, "a", "end", 2).main[0].duration == 5
    assert edits.trim(p, "t", "start", 0.5).text_rows[0][0].start == 1.5


def test_transition_capped_to_half_shorter():
    p = proj(
        [
            VideoClip("a", "v", 0, 1),
            VideoClip("b", "v", 0, 4, transition_in=Transition("fade", 3.0)),
        ]
    )
    assert edits.normalize(p).main[1].transition_in.duration == 0.5


def test_first_item_has_no_transition():
    p = proj(
        [
            VideoClip("a", "v", 0, 2),
            VideoClip("b", "v", 0, 2, transition_in=Transition("fade", 0.5)),
        ]
    )
    q = edits.move_main(p, "b", 0)
    assert q.main[0].id == "b"
    assert q.main[0].transition_in.kind == "none"


def test_move_item_overlap_raises():
    p = proj([], text=((TextItem("t1", start=0, end=2), TextItem("t2", start=3, end=5)),))
    with pytest.raises(edits.EditError, match="That spot is taken."):
        edits.move_item(p, "t2", 1.0, 0)
    q = edits.move_item(p, "t2", 2.0, 0)
    assert q.text_rows[0][1].start == 2.0


def test_add_at_audio_finds_free_spot():
    p = proj([], sources=[src("v"), src("m", "audio")])
    p, a = edits.add_at(p, "m", 1.0)
    p, b = edits.add_at(p, "m", 2.0)
    spans = layout(p)
    assert spans[a].start == 1.0
    assert spans[b].start == spans[a].end


def test_add_at_video_uses_nearest_boundary():
    p = proj([VideoClip("a", "v", 0, 2), VideoClip("b", "v", 0, 2)])
    q, new = edits.add_at(p, "v", 1.9)
    assert [c.id for c in q.main] == ["a", new, "b"]


def test_insert_main_image_is_three_seconds():
    p, new = edits.insert_main(proj([]), 0, "p")
    assert p.main[0].id == new
    assert p.main[0].duration == 3.0


def test_add_text():
    p, tid = edits.add_text(proj([]), 2.0)
    t = p.text_rows[0][0]
    assert (t.id, t.start, t.end, t.text) == (tid, 2.0, 5.0, "Text")


def test_remove_source_removes_items():
    p = proj([VideoClip("a", "v", 0, 2), PictureClip("b", "p")])
    q = edits.remove_source(p, "v")
    assert [c.id for c in q.main] == ["b"]
    assert [s.id for s in q.sources] == ["p"]


def test_project_too_large():
    p = proj([])
    with pytest.raises(edits.ProjectTooLarge, match="too big to edit"):
        for _ in range(501):
            p, _id = edits.insert_main(p, len(p.main), "p")
    assert len(p.main) == 500


def test_project_too_long():
    p = proj([PictureClip("a", "p", duration=60.0)])
    with pytest.raises(edits.ProjectTooLarge):
        edits.add_text(p, 4 * 3600 + 1)


def test_update_and_unknown_field():
    p = proj([VideoClip("a", "v", 0, 2)])
    assert edits.update(p, "a", speed=9.0).main[0].speed == 4.0
    assert edits.update(p, "a", volume=500).main[0].volume == 200
    with pytest.raises(edits.EditError):
        edits.update(p, "a", nonsense=1)


def test_add_row_set_settings_relink():
    p = proj([VideoClip("a", "v", 0, 2)])
    assert len(edits.add_row(p, "text").text_rows) == 2
    assert len(edits.add_row(p, "music").music_rows) == 2
    assert edits.set_settings(p, ProjectSettings(resolution="720p")).settings.resolution == "720p"
    probed = Source("zzz", Path("new.mp4"), "video", 1.0, 10, 10, 30.0, True)
    q = edits.relink(p, "v", Path("new.mp4"), probed)
    assert q.source("v").path == Path("new.mp4")
    assert q.main[0].out == 1.0


def test_split_text_and_music():
    m = AudioClip("m", "v", 1.0, 0.0, 4.0)
    p = Project(ProjectSettings(), (src("v"),), (), ((TextItem("t", start=0, end=4),),), ((m,),))
    q = edits.split(p, "t", 1.0)
    assert [(t.start, t.end) for t in q.text_rows[0]] == [(0, 1.0), (1.0, 4)]
    q = edits.split(p, "m", 2.0)
    a, b = q.music_rows[0]
    assert (a.start, a.in_, a.out) == (1.0, 0.0, 1.0)
    assert (b.start, b.in_, b.out) == (2.0, 1.0, 4.0)


def test_edits_do_not_mutate_input():
    p = proj(
        [VideoClip("a", "v", 0, 4), PictureClip("b", "p")],
        sources=[src("v"), src("p", "image"), src("m", "audio")],
        text=((TextItem("t", start=0, end=2),),),
    )
    snapshot = copy.deepcopy(p)
    calls = [
        lambda: edits.add_sources(p, [src("n")]),
        lambda: edits.insert_main(p, 0, "p"),
        lambda: edits.add_at(p, "m", 1.0),
        lambda: edits.add_text(p, 5.0),
        lambda: edits.remove(p, "a"),
        lambda: edits.move_main(p, "b", 0),
        lambda: edits.move_item(p, "t", 3.0, 0),
        lambda: edits.trim(p, "a", "end", -1),
        lambda: edits.split(p, "a", 1.0),
        lambda: edits.update(p, "a", speed=2.0),
        lambda: edits.set_settings(p, ProjectSettings(resolution="4k")),
        lambda: edits.add_row(p, "text"),
        lambda: edits.remove_source(p, "v"),
    ]
    for call in calls:
        call()
        assert p == snapshot


def test_history_undo_redo_dirty():
    p0 = proj([])
    h = History(p0)
    assert not h.dirty and not h.can_undo and not h.can_redo
    p1, _ = edits.add_text(p0, 0)
    p2, _ = edits.add_text(p1, 5)
    v = h.version
    h.push(p1, "Add text")
    h.push(p2, "Add more text")
    assert h.version == v + 2 and h.dirty
    assert h.undo_label == "Add more text"
    h.undo()
    assert h.project is p1 and h.version == v + 3 and h.dirty
    assert h.redo_label == "Add more text"
    h.mark_saved()
    assert not h.dirty
    h.redo()
    assert h.project is p2 and h.dirty and h.version == v + 4
    h.push(p2, "noop")
    assert h.version == v + 4


def test_history_cap():
    h = History(proj([]))
    for i in range(305):
        h.push(proj([], text=((TextItem(f"t{i}"),),)), f"step {i}")
    n = 0
    while h.can_undo:
        h.undo()
        n += 1
    assert n == 300


@pytest.mark.parametrize("speed", [1.0, 2.0])
def test_trim_start_past_end_keeps_out(speed):
    p = proj([VideoClip("a", "v", 0, 4, speed=speed)])
    for delta in (5, 10):
        c = edits.trim(p, "a", "start", delta).main[0]
        assert c.out == 4
        assert c.in_ == pytest.approx(4 - 0.1 * speed)


@pytest.mark.parametrize("speed", [1.0, 2.0])
def test_trim_end_past_start_keeps_in(speed):
    p = proj([VideoClip("a", "v", 2, 6, speed=speed)])
    c = edits.trim(p, "a", "end", -50).main[0]
    assert c.in_ == 2
    assert c.out == pytest.approx(2 + 0.1 * speed)


def test_trim_music_start_keeps_out():
    m = AudioClip("m", "v", 1.0, 0.0, 4.0)
    p = Project(ProjectSettings(), (src("v"),), (), ((),), ((m,),))
    c = edits.trim(p, "m", "start", 50).music_rows[0][0]
    assert c.out == 4.0
    assert c.in_ == pytest.approx(3.9)


def test_transition_dropped_when_neighbour_too_short():
    p = proj(
        [
            VideoClip("a", "v", 0, 0.1),
            VideoClip("b", "v", 0, 4, transition_in=Transition("fade", 0.5)),
        ]
    )
    assert edits.normalize(p).main[1].transition_in == Transition()


def test_update_checks_overlap_for_text_and_music():
    p = proj([], text=((TextItem("t1", start=0, end=2), TextItem("t2", start=3, end=5)),))
    with pytest.raises(edits.EditError, match="That spot is taken."):
        edits.update(p, "t2", start=1.0)
    with pytest.raises(edits.EditError, match="That spot is taken."):
        edits.update(p, "t1", end=4.0)
    assert edits.update(p, "t1", end=3.0).text_rows[0][0].end == 3.0
    a, b = AudioClip("a", "v", 0.0, 0.0, 2.0), AudioClip("b", "v", 2.0, 0.0, 2.0)
    mp = Project(ProjectSettings(), (src("v"),), (), ((),), ((a, b),))
    with pytest.raises(edits.EditError, match="That spot is taken."):
        edits.update(mp, "a", out=3.0)


def test_touching_items_are_not_overlapping():
    t1 = TextItem("t1", start=0, end=0.1 + 0.2)
    p = proj([], text=((t1, TextItem("t2", start=5, end=6)),))
    assert edits.move_item(p, "t2", 0.1 + 0.2 - 1e-9, 0).text_rows[0][1].start > 0
    assert edits.update(p, "t2", start=0.30000000000000004, end=1.0)
