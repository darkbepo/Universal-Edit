"""Editing operations on a video editor project. Pure Python, no Qt.

Every function returns a new ``Project`` that went through ``normalize`` and never changes its
input (the model is made of frozen dataclasses).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from umc.core.errors import ConversionError
from umc.videoedit.project import (
    AudioClip,
    MainItem,
    PictureClip,
    Project,
    ProjectSettings,
    Source,
    TextItem,
    Transition,
    VideoClip,
    clip_duration,
    find,
    layout,
    length,
    new_id,
)

MIN_DURATION = 0.1
MAX_LENGTH = 4 * 3600
MAX_ITEMS = 500
PICTURE_SECONDS = 3.0
TEXT_SECONDS = 3.0
_TOO_LARGE = "This project is too big to edit (max 4 hours and 500 items)."


class EditError(ConversionError):
    """An edit can't be done; the message is shown to the user."""


class ProjectTooLarge(EditError):
    def __init__(self) -> None:
        super().__init__(_TOO_LARGE)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _source_duration(project: Project, source_id: str) -> float:
    try:
        return project.source(source_id).duration
    except KeyError:
        return 0.0


def _clamp_range(in_: float, out: float, duration: float, min_len: float) -> tuple[float, float]:
    """Clamp a source range to [0, duration] keeping at least ``min_len`` between the ends."""
    in_ = max(0.0, in_)
    if duration > 0:
        in_ = min(in_, max(0.0, duration - min_len))
        out = _clamp(out, in_ + min_len, max(duration, in_ + min_len))
    else:
        out = max(out, in_ + min_len)
    return in_, out


def _normalize_main(project: Project, item: MainItem) -> MainItem:
    fade_in = _clamp(item.fade_in, 0.0, 5.0)
    fade_out = _clamp(item.fade_out, 0.0, 5.0)
    if isinstance(item, PictureClip):
        return dataclasses.replace(
            item, duration=_clamp(item.duration, 0.1, 60.0), fade_in=fade_in, fade_out=fade_out
        )
    speed = _clamp(item.speed, 0.25, 4.0)
    in_, out = _clamp_range(
        item.in_, item.out, _source_duration(project, item.source), MIN_DURATION * speed
    )
    return dataclasses.replace(
        item,
        in_=in_,
        out=out,
        speed=speed,
        volume=int(_clamp(item.volume, 0, 200)),
        fade_in=fade_in,
        fade_out=fade_out,
    )


def _normalize_audio(project: Project, a: AudioClip) -> AudioClip:
    in_, out = _clamp_range(a.in_, a.out, _source_duration(project, a.source), MIN_DURATION)
    start = max(0.0, a.start)
    loop = a.loop_until
    if loop > 0:
        loop = max(loop, start + MIN_DURATION)
    return dataclasses.replace(
        a,
        start=start,
        in_=in_,
        out=out,
        loop_until=loop,
        volume=int(_clamp(a.volume, 0, 200)),
        fade_in=_clamp(a.fade_in, 0.0, 5.0),
        fade_out=_clamp(a.fade_out, 0.0, 5.0),
    )


def _normalize_text(t: TextItem) -> TextItem:
    start = max(0.0, t.start)
    return dataclasses.replace(
        t,
        start=start,
        end=max(t.end, start + MIN_DURATION),
        box_opacity=int(_clamp(t.box_opacity, 0, 100)),
        fade_in=_clamp(t.fade_in, 0.0, 5.0),
        fade_out=_clamp(t.fade_out, 0.0, 5.0),
    )


def _normalize_transitions(main: tuple[MainItem, ...]) -> tuple[MainItem, ...]:
    out: list[MainItem] = []
    for i, item in enumerate(main):
        tr = item.transition_in
        if i == 0:
            tr = Transition()
        elif tr.kind != "none":
            shorter = min(clip_duration(main[i - 1]), clip_duration(item))
            cap = min(3.0, shorter / 2)
            if cap < 0.1:  # too short for any transition
                tr = Transition()
            else:
                tr = dataclasses.replace(tr, duration=round(_clamp(tr.duration, 0.1, cap), 6))
        if tr != item.transition_in:
            item = dataclasses.replace(item, transition_in=tr)
        out.append(item)
    return tuple(out)


def normalize(project: Project) -> Project:
    """Bring every value into its allowed range; refuse projects that are too big."""
    count = (
        len(project.main)
        + sum(len(r) for r in project.text_rows)
        + sum(len(r) for r in project.music_rows)
    )
    if count > MAX_ITEMS:
        raise ProjectTooLarge
    main = _normalize_transitions(tuple(_normalize_main(project, m) for m in project.main))
    result = dataclasses.replace(
        project,
        main=main,
        text_rows=tuple(tuple(_normalize_text(t) for t in row) for row in project.text_rows),
        music_rows=tuple(
            tuple(_normalize_audio(project, a) for a in row) for row in project.music_rows
        ),
    )
    if length(result) > MAX_LENGTH:
        raise ProjectTooLarge
    return result


# ---- helpers ----------------------------------------------------------------------------------


def _replace_item(project: Project, item_id: str, new: object | None) -> Project:
    """Swap an item for ``new`` (several items when a list, none when None)."""
    kind, row, idx, _ = find(project, item_id)
    repl = [] if new is None else list(new) if isinstance(new, list) else [new]
    if kind == "main":
        main = project.main[:idx] + tuple(repl) + project.main[idx + 1 :]
        return dataclasses.replace(project, main=main)  # type: ignore[arg-type]
    rows = project.text_rows if kind == "text" else project.music_rows
    new_row = rows[row][:idx] + tuple(repl) + rows[row][idx + 1 :]
    rows = rows[:row] + (new_row,) + rows[row + 1 :]
    key = "text_rows" if kind == "text" else "music_rows"
    return dataclasses.replace(project, **{key: rows})


def _source(project: Project, source_id: str) -> Source:
    try:
        return project.source(source_id)
    except KeyError:
        raise EditError("That file is not part of the project.") from None


def _find(project: Project, item_id: str) -> tuple[str, int, int, Any]:
    try:
        return find(project, item_id)
    except KeyError:
        raise EditError("That item no longer exists.") from None


def _new_main(source: Source) -> MainItem:
    if source.kind == "image":
        return PictureClip(new_id(), source.id, duration=PICTURE_SECONDS)
    return VideoClip(new_id(), source.id, 0.0, source.duration)


# ---- edits ------------------------------------------------------------------------------------


def add_sources(project: Project, sources: Sequence[Source]) -> Project:
    return normalize(dataclasses.replace(project, sources=project.sources + tuple(sources)))


def insert_main(project: Project, index: int, source_id: str) -> tuple[Project, str]:
    source = _source(project, source_id)
    if source.kind == "audio":
        raise EditError("Audio can only go on a music row.")
    item = _new_main(source)
    index = max(0, min(index, len(project.main)))
    main = project.main[:index] + (item,) + project.main[index:]
    return normalize(dataclasses.replace(project, main=main)), item.id


def _free_spot(row: tuple[Any, ...], spans: dict[str, Any], start: float, dur: float) -> float:
    spots = sorted((spans[x.id].start, spans[x.id].end) for x in row)
    moved = True
    while moved:
        moved = False
        for s, e in spots:
            if start < e and start + dur > s:
                start = e
                moved = True
    return start


def add_at(project: Project, source_id: str, t: float, row: int = 0) -> tuple[Project, str]:
    source = _source(project, source_id)
    if source.kind != "audio":
        spans = layout(project)
        bounds = [spans[m.id].start for m in project.main]
        bounds.append(spans[project.main[-1].id].end if project.main else 0.0)
        index = min(range(len(bounds)), key=lambda i: abs(bounds[i] - t))
        return insert_main(project, index, source_id)
    rows = project.music_rows
    row = max(0, row)
    while len(rows) <= row:
        rows += ((),)
    dur = source.duration
    start = _free_spot(rows[row], layout(project), max(0.0, t), dur)
    clip = AudioClip(new_id(), source_id, start, 0.0, dur)
    rows = rows[:row] + (rows[row] + (clip,),) + rows[row + 1 :]
    return normalize(dataclasses.replace(project, music_rows=rows)), clip.id


def add_text(project: Project, t: float, row: int = 0) -> tuple[Project, str]:
    rows = project.text_rows
    row = max(0, row)
    while len(rows) <= row:
        rows += ((),)
    start = max(0.0, t)
    item = TextItem(new_id(), start=start, end=start + TEXT_SECONDS)
    rows = rows[:row] + (rows[row] + (item,),) + rows[row + 1 :]
    return normalize(dataclasses.replace(project, text_rows=rows)), item.id


def remove(project: Project, item_id: str) -> Project:
    _find(project, item_id)
    return normalize(_replace_item(project, item_id, None))


def move_main(project: Project, item_id: str, new_index: int) -> Project:
    kind, _, idx, item = _find(project, item_id)
    if kind != "main":
        raise EditError("Only clips on the main track can be reordered.")
    rest = project.main[:idx] + project.main[idx + 1 :]
    new_index = max(0, min(new_index, len(rest)))
    return normalize(
        dataclasses.replace(project, main=rest[:new_index] + (item,) + rest[new_index:])
    )


_EPS = 1e-6


def _item_end(kind: str, item: Any) -> float:
    return item.start + clip_duration(item) if kind == "music" else item.end


def _ensure_free(row: tuple[Any, ...], item_id: str, kind: str, start: float, end: float) -> None:
    """Raise unless [start, end) is free in ``row`` (touching items are fine)."""
    for other in row:
        if other.id == item_id:
            continue
        if start < _item_end(kind, other) - _EPS and end > other.start + _EPS:
            raise EditError("That spot is taken.")


def move_item(project: Project, item_id: str, start: float, row: int) -> Project:
    kind, old_row, idx, item = _find(project, item_id)
    if kind == "main":
        raise EditError("Use move_main for clips on the main track.")
    rows = project.text_rows if kind == "text" else project.music_rows
    if not 0 <= row < len(rows):
        raise EditError("That row doesn't exist.")
    start = max(0.0, start)
    dur = _item_end(kind, item) - item.start
    _ensure_free(rows[row], item_id, kind, start, start + dur)
    if kind == "text":
        moved = dataclasses.replace(item, start=start, end=start + dur)
    else:
        moved = dataclasses.replace(item, start=start)
        if item.loop_until > 0:
            moved = dataclasses.replace(moved, loop_until=start + dur)
    removed = _replace_item(project, item_id, None)
    rows = removed.text_rows if kind == "text" else removed.music_rows
    rows = rows[:row] + (rows[row] + (moved,),) + rows[row + 1 :]
    key = "text_rows" if kind == "text" else "music_rows"
    return normalize(dataclasses.replace(removed, **{key: rows}))


def trim(project: Project, item_id: str, edge: Literal["start", "end"], delta: float) -> Project:
    kind, _, _, item = _find(project, item_id)
    start_edge = edge == "start"
    if isinstance(item, VideoClip):
        d = delta * item.speed
        min_len = MIN_DURATION * item.speed
        duration = _source_duration(project, item.source)
        if start_edge:
            new = dataclasses.replace(
                item, in_=_clamp(item.in_ + d, 0.0, max(0.0, item.out - min_len))
            )
        else:
            high = duration if duration > 0 else float("inf")
            new = dataclasses.replace(
                item, out=_clamp(item.out + d, item.in_ + min_len, max(high, item.in_ + min_len))
            )
    elif isinstance(item, PictureClip):
        new = dataclasses.replace(item, duration=item.duration + (-delta if start_edge else delta))
    elif isinstance(item, AudioClip):
        if start_edge:
            delta = max(delta, -item.start, -item.in_)
            delta = min(
                delta, clip_duration(item) - MIN_DURATION, item.out - item.in_ - MIN_DURATION
            )
            new = dataclasses.replace(item, start=item.start + delta, in_=item.in_ + delta)
        elif item.loop_until > 0:
            new = dataclasses.replace(item, loop_until=item.loop_until + delta)
        else:
            new = dataclasses.replace(item, out=item.out + delta)
    else:
        if start_edge:
            delta = min(delta, item.end - item.start - MIN_DURATION)
            new = dataclasses.replace(item, start=item.start + delta)
        else:
            new = dataclasses.replace(item, end=item.end + delta)
    return normalize(_replace_item(project, item_id, new))


def split(project: Project, item_id: str, t: float) -> Project:
    kind, _, _, item = _find(project, item_id)
    span = layout(project)[item_id]
    if not span.start + 1e-9 < t < span.end - 1e-9:
        raise EditError("Put the playhead inside the clip to split it.")
    rel = t - span.start
    if isinstance(item, VideoClip):
        cut = item.in_ + rel * item.speed
        a = dataclasses.replace(item, id=new_id(), out=cut, fade_out=0.0)
        b = dataclasses.replace(item, id=new_id(), in_=cut, fade_in=0.0, transition_in=Transition())
    elif isinstance(item, PictureClip):
        a = dataclasses.replace(item, id=new_id(), duration=rel, fade_out=0.0)
        b = dataclasses.replace(
            item,
            id=new_id(),
            duration=item.duration - rel,
            fade_in=0.0,
            transition_in=Transition(),
        )
    elif isinstance(item, AudioClip):
        a = dataclasses.replace(item, id=new_id(), fade_out=0.0)
        b = dataclasses.replace(item, id=new_id(), start=t, fade_in=0.0)
        if item.loop_until > 0:
            a = dataclasses.replace(a, loop_until=t)
        else:
            a = dataclasses.replace(a, out=item.in_ + rel)
            b = dataclasses.replace(b, in_=item.in_ + rel)
    else:
        a = dataclasses.replace(item, id=new_id(), end=t, fade_out=0.0)
        b = dataclasses.replace(item, id=new_id(), start=t, fade_in=0.0)
    return normalize(_replace_item(project, item_id, [a, b]))


def update(project: Project, item_id: str, **changes: Any) -> Project:
    _, _, _, item = _find(project, item_id)
    valid = {f.name for f in dataclasses.fields(item)} - {"id", "source"}
    unknown = set(changes) - valid
    if unknown:
        raise EditError(f"Unknown setting: {', '.join(sorted(unknown))}.")
    result = normalize(_replace_item(project, item_id, dataclasses.replace(item, **changes)))
    kind, row, _, new = find(result, item_id)
    if kind in ("text", "music") and changes.keys() & {"start", "end", "in_", "out", "loop_until"}:
        rows = result.text_rows if kind == "text" else result.music_rows
        _ensure_free(rows[row], item_id, kind, new.start, _item_end(kind, new))
    return result


def set_settings(project: Project, settings: ProjectSettings) -> Project:
    return normalize(dataclasses.replace(project, settings=settings))


def add_row(project: Project, kind: Literal["text", "music"]) -> Project:
    if kind == "text":
        return dataclasses.replace(project, text_rows=project.text_rows + ((),))
    return dataclasses.replace(project, music_rows=project.music_rows + ((),))


def relink(project: Project, source_id: str, path: Path, probed: Source) -> Project:
    new = dataclasses.replace(probed, id=source_id, path=path)
    sources = tuple(new if s.id == source_id else s for s in project.sources)
    return normalize(dataclasses.replace(project, sources=sources))


def remove_source(project: Project, source_id: str) -> Project:
    return normalize(
        dataclasses.replace(
            project,
            sources=tuple(s for s in project.sources if s.id != source_id),
            main=tuple(m for m in project.main if m.source != source_id),
            text_rows=project.text_rows,
            music_rows=tuple(
                tuple(a for a in row if a.source != source_id) for row in project.music_rows
            ),
        )
    )


def apply_changes(project: Project, selection_id: str | None, changes: dict[str, Any]) -> Project:
    """Settings changed in the inspector, for the project (None), a transition ("tr:<clip id>")
    or an item."""
    if selection_id is None:
        valid = {f.name for f in dataclasses.fields(ProjectSettings)}
        unknown = set(changes) - valid
        if unknown:
            raise EditError(f"Unknown setting: {', '.join(sorted(unknown))}.")
        return set_settings(project, dataclasses.replace(project.settings, **changes))
    if selection_id.startswith("tr:"):
        clip_id = selection_id[3:]
        _, _, _, clip = _find(project, clip_id)
        if not isinstance(clip, VideoClip | PictureClip):
            raise EditError("Only clips on the main track have transitions.")
        valid = {f.name for f in dataclasses.fields(Transition)}
        unknown = set(changes) - valid
        if unknown:
            raise EditError(f"Unknown setting: {', '.join(sorted(unknown))}.")
        return update(
            project, clip_id, transition_in=dataclasses.replace(clip.transition_in, **changes)
        )
    return update(project, selection_id, **changes)
