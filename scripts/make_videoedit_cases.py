"""Writes tests/fixtures/videoedit_cases.json: small video projects + layout / edit calls → what the
desktop's videoedit model produces (layout spans, output size / fps, the project after an edit or
the error message) and one saved .umcvideo file. The website's port (web/src/videoedit/) must give
the same results (web/tests/videoedit*.test.ts); tests/test_videoedit_cases.py checks the file is
still what the desktop code produces. Run on purpose only, after changing project.py / edits.py /
projectfile.py:  python scripts/make_videoedit_cases.py

Ids of new items are "n1", "n2", … (the counter restarts for every case); the website does the same.
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from umc.videoedit import edits  # noqa: E402
from umc.videoedit.project import (  # noqa: E402
    AudioClip,
    PictureClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    VideoClip,
    layout,
    length,
    output_fps,
    output_size,
)
from umc.videoedit.projectfile import _build, _item_from_dict, _to_dict, save_project  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "videoedit_cases.json"
HERE = Path(".")

SOURCES = (
    Source("v1", Path("v1.mp4"), "video", 10.0, 1920, 1080, 30.0, True, 5_000_000),
    Source("v2", Path("v2.mp4"), "video", 4.0, 1280, 720, 25.0, False, 1_200_000),
    Source("p1", Path("p1.png"), "image", 0.0, 800, 600, 0.0, False, 90_000),
    Source("m1", Path("m1.mp3"), "audio", 60.0, 0, 0, 0.0, True, 960_000),
)


# ---- replaying (also used by tests/test_videoedit_cases.py) ----------------------------------


def project_from_dict(data: dict[str, Any]) -> Project:
    """A project from a ``_to_dict`` dict, built the way ``load_project`` builds it."""
    return Project(
        settings=_build(ProjectSettings, data["settings"]),
        sources=tuple(_build(Source, {**s, "path": Path(s["path"])}) for s in data["sources"]),
        main=tuple(_item_from_dict(m) for m in data["main"]),
        text_rows=tuple(tuple(_item_from_dict(t) for t in r) for r in data["text_rows"]),
        music_rows=tuple(tuple(_item_from_dict(a) for a in r) for r in data["music_rows"]),
    )


def to_dict(project: Project) -> dict[str, Any]:
    return _to_dict(project, HERE)


class _Counter:
    """Stands in for ``edits.new_id``: n1, n2, …"""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"n{self.n}"


_OPS: dict[str, Callable[..., Any]] = {
    "insert_main": lambda p, index, source_id: edits.insert_main(p, index, source_id),
    "add_at": lambda p, source_id, t, row=0: edits.add_at(p, source_id, t, row),
    "add_text": lambda p, t, row=0: edits.add_text(p, t, row),
    "remove": lambda p, item_id: edits.remove(p, item_id),
    "move_main": lambda p, item_id, index: edits.move_main(p, item_id, index),
    "move_item": lambda p, item_id, start, row: edits.move_item(p, item_id, start, row),
    "trim": lambda p, item_id, edge, delta: edits.trim(p, item_id, edge, delta),
    "split": lambda p, item_id, t: edits.split(p, item_id, t),
    "update": lambda p, item_id, changes: edits.update(p, item_id, **changes),
    "set_settings": lambda p, settings: edits.set_settings(p, ProjectSettings(**settings)),
    "add_row": lambda p, kind: edits.add_row(p, kind),
    "relink": lambda p, source_id, probed: edits.relink(
        p, source_id, Path(probed["path"]), _build(Source, {**probed, "path": Path(probed["path"])})
    ),
    "remove_source": lambda p, source_id: edits.remove_source(p, source_id),
    "apply_changes": lambda p, selection_id, changes: edits.apply_changes(p, selection_id, changes),
}


def run_edit(case: dict[str, Any]) -> dict[str, Any]:
    """What the desktop code gives for one edit case: result / id / error."""
    saved = edits.new_id
    edits.new_id = _Counter()  # type: ignore[assignment]
    try:
        out = _OPS[case["op"]](project_from_dict(case["project"]), *case["args"])
    except edits.EditError as exc:
        return {"result": None, "id": None, "error": str(exc)}
    finally:
        edits.new_id = saved
    new_id = None
    if isinstance(out, tuple):
        out, new_id = out
    return {"result": to_dict(out), "id": new_id, "error": None}


def run_layout(project: Project) -> dict[str, Any]:
    spans = layout(project)
    return {
        "spans": {k: [s.start, s.end, s.row_kind, s.row, s.index] for k, s in spans.items()},
        "length": length(project),
        "output_size": list(output_size(project)),
        "output_fps": output_fps(project),
    }


# ---- building cases --------------------------------------------------------------------------


def proj(main=(), text=((),), music=((),), settings=None, sources=SOURCES) -> Project:
    return Project(settings or ProjectSettings(), tuple(sources), tuple(main), text, music)


def V(id_: str, in_: float, out: float, source: str = "v1", **kw: Any) -> VideoClip:
    return VideoClip(id_, source, in_, out, **kw)


def P(id_: str, source: str = "p1", **kw: Any) -> PictureClip:
    return PictureClip(id_, source, **kw)


def T(id_: str, start: float, end: float, **kw: Any) -> TextItem:
    return TextItem(id_, start=start, end=end, **kw)


def A(id_: str, start: float, in_: float, out: float, **kw: Any) -> AudioClip:
    return AudioClip(id_, "m1", start, in_, out, **kw)


def layout_cases() -> list[dict[str, Any]]:
    fade = Transition("fade", 1.0)
    cases: list[tuple[str, Project]] = [
        ("back_to_back", proj([V("a", 0, 2), V("b", 0, 2, "v2")])),
        ("crossfade_overlap", proj([V("a", 0, 10), V("b", 0, 4, "v2", transition_in=fade)])),
        ("speed_2x", proj([V("a", 1, 5, speed=2.0), P("b")])),
        ("picture_3s", proj([P("a")])),
        (
            "text_and_music_extend_length",
            proj(
                [V("a", 0, 2)],
                text=((T("t", 0, 6),), (T("u", 1, 9),)),
                music=((A("m", 1, 0, 12),),),
            ),
        ),
        ("looped_music", proj([V("a", 0, 2)], music=((A("m", 1, 0, 5, loop_until=20.0),),))),
        (
            "rows_and_order",
            proj(
                [P("a"), V("b", 2, 4), P("c", duration=1.5)], text=((T("t1", 0, 1), T("t2", 2, 3)),)
            ),
        ),
    ]
    for res in ("720p", "1080p", "4k", "vertical", "square"):
        cases.append(
            (f"settings_{res}", proj([V("a", 0, 2)], settings=ProjectSettings(resolution=res)))
        )
    custom = ProjectSettings(resolution="custom", width=1281, height=721)
    cases.append(("settings_custom_even", proj([V("a", 0, 2)], settings=custom)))
    cases.append(
        (
            "settings_custom_tiny",
            proj([], settings=ProjectSettings(resolution="custom", width=2, height=3)),
        )
    )
    cases.append(("size_first_video", proj([V("a", 0, 2, "v2")])))
    cases.append(("size_first_video_after_picture", proj([P("a"), V("b", 0, 2, "v2")])))
    cases.append(("size_pictures_only", proj([P("a")])))
    cases.append(("fps_first", proj([V("a", 0, 2, "v2")])))
    cases.append(("fps_first_skips_picture", proj([P("a"), V("b", 0, 2, "v2")])))
    cases.append(("fps_fixed_24", proj([V("a", 0, 2)], settings=ProjectSettings(fps="24"))))
    cases.append(("fps_first_without_video", proj([P("a")])))
    cases.append(("empty_project", proj([])))
    out = []
    for name, project in cases:
        out.append({"name": name, "project": to_dict(project), **run_layout(project)})
    return out


def edit_cases() -> list[dict[str, Any]]:
    fade_half = Transition("fade", 0.5)
    c: list[tuple[str, Project, str, list[Any]]] = []

    def add(name: str, project: Project, op: str, *args: Any) -> None:
        c.append((name, project, op, list(args)))

    two = proj([V("a", 0, 2), V("b", 0, 2)])
    # split
    add("split_video", proj([V("a", 0, 4)]), "split", "a", 1.5)
    add("split_video_with_speed", proj([V("a", 0, 4, speed=2.0)]), "split", "a", 0.5)
    add(
        "split_keeps_fades_and_transition",
        proj([V("x", 0, 2), V("a", 0, 4, fade_in=1, fade_out=2, transition_in=fade_half)]),
        "split",
        "a",
        3.0,
    )
    for t in (0.0, 4.0, 5.0):
        add(f"split_outside_raises_{t:g}", proj([V("a", 0, 4)]), "split", "a", t)
    add("split_picture", proj([P("a", fade_in=1, fade_out=1)]), "split", "a", 1.0)
    add("split_unknown_item_raises", proj([V("a", 0, 4)]), "split", "gone", 1.0)
    add("split_text", proj(text=((T("t", 0, 4),),)), "split", "t", 1.0)
    add("split_music", proj(music=((A("m", 1.0, 0.0, 4.0),),)), "split", "m", 2.0)
    add(
        "split_music_looped",
        proj(music=((A("m", 1.0, 0.0, 4.0, loop_until=9.0),),)),
        "split",
        "m",
        3.0,
    )
    # trim
    add("trim_start_clamps_to_source", proj([V("a", 2, 4)]), "trim", "a", "start", -5)
    add("trim_end_clamps_to_source", proj([V("a", 2, 4)]), "trim", "a", "end", 100)
    add("trim_video_inside", proj([V("a", 2, 4, speed=2.0)]), "trim", "a", "end", 0.5)
    add("trim_picture_end", proj([P("a")]), "trim", "a", "end", 2)
    add("trim_picture_start", proj([P("a")]), "trim", "a", "start", 1)
    add("trim_text_start", proj(text=((T("t", 1, 4),),)), "trim", "t", "start", 0.5)
    add("trim_text_start_past_end", proj(text=((T("t", 1, 4),),)), "trim", "t", "start", 10)
    add("trim_text_end", proj(text=((T("t", 1, 4),),)), "trim", "t", "end", -1)
    for speed in (1.0, 2.0):
        add(
            f"trim_start_past_end_speed{speed:g}",
            proj([V("a", 0, 4, speed=speed)]),
            "trim",
            "a",
            "start",
            5,
        )
        add(
            f"trim_end_past_start_speed{speed:g}",
            proj([V("a", 2, 6, speed=speed)]),
            "trim",
            "a",
            "end",
            -50,
        )
    add(
        "trim_music_start_past_end",
        proj(music=((A("m", 1.0, 0.0, 4.0),),)),
        "trim",
        "m",
        "start",
        50,
    )
    add("trim_music_start", proj(music=((A("m", 1.0, 1.0, 4.0),),)), "trim", "m", "start", 0.5)
    add(
        "trim_music_start_before_zero",
        proj(music=((A("m", 1.0, 1.0, 4.0),),)),
        "trim",
        "m",
        "start",
        -5,
    )
    add("trim_music_end", proj(music=((A("m", 1.0, 0.0, 4.0),),)), "trim", "m", "end", 2)
    add(
        "trim_music_looped_end",
        proj(music=((A("m", 1.0, 0.0, 4.0, loop_until=9.0),),)),
        "trim",
        "m",
        "end",
        3,
    )
    # transitions (normalize)
    add(
        "transition_capped_to_half_shorter",
        proj([V("a", 0, 1), V("b", 0, 4, transition_in=Transition("fade", 3.0))]),
        "set_settings",
        {
            "resolution": "first",
            "width": 1920,
            "height": 1080,
            "fps": "first",
            "background": "#000000",
        },
    )
    add(
        "transition_dropped_when_neighbour_too_short",
        proj([V("a", 0, 0.1), V("b", 0, 4, transition_in=fade_half)]),
        "set_settings",
        {
            "resolution": "first",
            "width": 1920,
            "height": 1080,
            "fps": "first",
            "background": "#000000",
        },
    )
    add(
        "first_item_has_no_transition",
        proj([V("a", 0, 2), V("b", 0, 2, transition_in=fade_half)]),
        "move_main",
        "b",
        0,
    )
    add("move_main_to_end", two, "move_main", "a", 5)
    add(
        "move_main_wrong_row_raises",
        proj([V("a", 0, 2)], text=((T("t", 0, 1),),)),
        "move_main",
        "t",
        0,
    )
    # move_item
    two_text = proj(text=((T("t1", 0, 2), T("t2", 3, 5)),))
    add("move_item_overlap_raises", two_text, "move_item", "t2", 1.0, 0)
    add("move_item_touching_is_fine", two_text, "move_item", "t2", 2.0, 0)
    add("move_item_bad_row_raises", two_text, "move_item", "t2", 1.0, 3)
    add("move_item_to_other_row", proj(text=((T("t1", 0, 2),), ())), "move_item", "t1", 1.0, 1)
    add("move_item_main_raises", proj([V("a", 0, 2)]), "move_item", "a", 1.0, 0)
    add("move_item_negative_start", two_text, "move_item", "t2", -4.0, 0)
    add(
        "move_item_music",
        proj(music=((A("m", 1.0, 0.0, 4.0, loop_until=6.0),),)),
        "move_item",
        "m",
        3.0,
        0,
    )
    # add_at / insert_main / add_text
    add("add_at_audio_empty", proj(), "add_at", "m1", 1.0)
    add("add_at_audio_free_spot", proj(music=((A("a", 1.0, 0.0, 60.0),),)), "add_at", "m1", 2.0)
    add("add_at_audio_second_row", proj(), "add_at", "m1", 0.5, 2)
    add("add_at_video_nearest_boundary", two, "add_at", "v1", 1.9)
    add("add_at_picture_empty", proj(), "add_at", "p1", 5.0)
    add("insert_main_image_three_seconds", proj(), "insert_main", 0, "p1")
    add("insert_main_video", two, "insert_main", 1, "v2")
    add("insert_main_index_clamped", two, "insert_main", 99, "v1")
    add("insert_main_audio_raises", proj(), "insert_main", 0, "m1")
    add("insert_main_unknown_source_raises", proj(), "insert_main", 0, "zzz")
    add("add_text", proj(), "add_text", 2.0)
    add("add_text_new_row", proj(), "add_text", 1.0, 2)
    add("add_text_negative_start", proj(), "add_text", -3.0)
    # remove
    add("remove_clip", two, "remove", "a")
    add("remove_text", proj(text=((T("t", 0, 1),),)), "remove", "t")
    add("remove_unknown_raises", two, "remove", "gone")
    add(
        "remove_source_removes_items",
        proj([V("a", 0, 2), P("b")], music=((A("m", 0, 0, 5),),)),
        "remove_source",
        "v1",
    )
    add(
        "remove_source_music",
        proj([V("a", 0, 2)], music=((A("m", 0, 0, 5),),)),
        "remove_source",
        "m1",
    )
    # limits
    add(
        "too_many_items_raises",
        proj([P(f"i{i}", duration=0.1) for i in range(500)]),
        "insert_main",
        0,
        "p1",
    )
    add("too_long_raises", proj([P("a", duration=60.0)]), "add_text", 4 * 3600 + 1)
    # update
    one = proj([V("a", 0, 2)])
    add("update_speed_clamped", one, "update", "a", {"speed": 9.0})
    add("update_volume_clamped", one, "update", "a", {"volume": 500})
    add(
        "update_values",
        one,
        "update",
        "a",
        {"fit": "crop", "rotate": 90, "flip_h": True, "brightness": 20, "fade_in": 9.0},
    )
    add("update_unknown_field_raises", one, "update", "a", {"nonsense": 1})
    add("update_source_field_raises", one, "update", "a", {"source": "v2"})
    add("update_unknown_item_raises", one, "update", "gone", {"speed": 2.0})
    add("update_picture_duration", proj([P("a")]), "update", "a", {"duration": 100.0})
    add("update_text_start_overlap_raises", two_text, "update", "t2", {"start": 1.0})
    add("update_text_end_overlap_raises", two_text, "update", "t1", {"end": 4.0})
    add("update_text_end_ok", two_text, "update", "t1", {"end": 3.0})
    add(
        "update_text_content",
        two_text,
        "update",
        "t1",
        {"text": "Héllo\nwörld", "bold": True, "box_opacity": 150},
    )
    mrows = proj(music=((A("a", 0.0, 0.0, 2.0), A("b", 2.0, 0.0, 2.0)),))
    add("update_music_out_overlap_raises", mrows, "update", "a", {"out": 3.0})
    add("update_music_volume", mrows, "update", "a", {"volume": 50, "fade_in": 1.0})
    touching = proj(text=((T("t1", 0, 0.1 + 0.2), T("t2", 5, 6)),))
    add("touching_items_move", touching, "move_item", "t2", 0.1 + 0.2 - 1e-9, 0)
    add(
        "touching_items_update",
        touching,
        "update",
        "t2",
        {"start": 0.30000000000000004, "end": 1.0},
    )
    # settings, rows, relink
    add("add_row_text", one, "add_row", "text")
    add("add_row_music", one, "add_row", "music")
    add(
        "set_settings_720p",
        one,
        "set_settings",
        {"resolution": "720p", "width": 1920, "height": 1080, "fps": "30", "background": "#112233"},
    )
    new_src = {
        "id": "zzz",
        "path": "new.mp4",
        "kind": "video",
        "duration": 1.0,
        "width": 10,
        "height": 10,
        "fps": 30.0,
        "has_audio": True,
        "size": 777,
    }
    add("relink_shortens_clip", one, "relink", "v1", new_src)
    # apply_changes
    add("apply_changes_settings", two, "apply_changes", None, {"fps": "25"})
    add("apply_changes_item", two, "apply_changes", "a", {"speed": 2.0})
    add("apply_changes_transition_kind", two, "apply_changes", "tr:b", {"kind": "fade"})
    add(
        "apply_changes_transition_full",
        two,
        "apply_changes",
        "tr:b",
        {"kind": "wipeleft", "duration": 1.0},
    )
    add("apply_changes_unknown_item_raises", one, "apply_changes", "gone", {"speed": 2.0})
    add(
        "apply_changes_unknown_transition_clip_raises",
        one,
        "apply_changes",
        "tr:gone",
        {"kind": "fade"},
    )
    add("apply_changes_unknown_setting_raises", one, "apply_changes", None, {"nope": 1})
    add("apply_changes_unknown_transition_key_raises", two, "apply_changes", "tr:b", {"nope": 1})
    add(
        "apply_changes_transition_on_text_raises",
        proj(text=((T("t", 0, 1),),)),
        "apply_changes",
        "tr:t",
        {"kind": "fade"},
    )

    out = []
    for name, project, op, args in c:
        case = {"name": name, "project": to_dict(project), "op": op, "args": args}
        out.append({**case, **run_edit(case)})
    return out


def file_case() -> dict[str, Any]:
    """A project with every item kind; the text is what save_project writes beside its sources."""
    settings = ProjectSettings(
        resolution="custom", width=1280, height=720, fps="25", background="#112233"
    )
    main = (
        V(
            "a",
            1.5,
            8.0,
            speed=1.5,
            volume=80,
            mute=True,
            fade_in=0.5,
            fade_out=1.0,
            fit="crop",
            rotate=90,
            flip_h=True,
            flip_v=True,
            crop_left=5,
            crop_top=6,
            crop_right=7,
            crop_bottom=8,
            brightness=10,
            contrast=-20,
            saturation=30,
        ),
        P(
            "b",
            duration=4.5,
            fit="stretch",
            rotate=180,
            fade_in=0.5,
            brightness=-5,
            transition_in=Transition("slideleft", 0.8),
        ),
        V("c", 0, 4, "v2", transition_in=Transition("fade", 1.0)),
    )
    text = (
        (
            T(
                "t1",
                0.5,
                4.0,
                text="Héllo\nwörld ✓",
                anchor="top-left",
                x_offset=2.5,
                y_offset=-3.0,
                font="DejaVu Serif",
                size=12.5,
                bold=True,
                italic=True,
                colour="#ff0000",
                outline_colour="#00ff00",
                outline=3.5,
                box=True,
                box_colour="#0000ff",
                box_opacity=70,
                fade_in=0.5,
                fade_out=0.5,
            ),
        ),
        (T("t2", 6.0, 9.0),),
    )
    music = ((A("m", 1.0, 2.0, 30.0, volume=150, fade_in=1.0, fade_out=2.0, loop_until=40.0),),)
    with tempfile.TemporaryDirectory(prefix="umc-cases-") as tmp:
        folder = Path(tmp).resolve()
        real = []
        for s in SOURCES:
            (folder / s.path).write_bytes(b"")
            real.append(
                Source(
                    s.id,
                    folder / s.path,
                    s.kind,
                    s.duration,
                    s.width,
                    s.height,
                    s.fps,
                    s.has_audio,
                    s.size,
                )
            )
        project = Project(settings, tuple(real), main, text, music)
        save_project(project, folder / "p.umcvideo")
        text_out = (folder / "p.umcvideo").read_text(encoding="utf-8")
        for s in real:
            text_out = text_out.replace(json.dumps(str(s.path))[1:-1], s.path.name)
    bare = Project(settings, SOURCES, main, text, music)
    data = to_dict(bare)
    assert text_out == json.dumps(data, ensure_ascii=False, indent=1)
    return {"text": text_out, "project": data}


def main() -> None:
    cases = {"layout": layout_cases(), "edits": edit_cases(), "file": file_case()}
    OUT.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    errors = sum(1 for e in cases["edits"] if e["error"])
    print(
        f"wrote {OUT}: {len(cases['layout'])} layout, "
        f"{len(cases['edits'])} edit cases ({errors} errors)"
    )


if __name__ == "__main__":
    main()
