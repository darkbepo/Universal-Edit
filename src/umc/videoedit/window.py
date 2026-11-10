"""Render windows: the slice of the timeline the preview renders. Pure Python, no Qt."""

from __future__ import annotations

from dataclasses import dataclass, replace

from umc.videoedit.project import (
    AudioClip,
    MainItem,
    Project,
    TextItem,
    Transition,
    VideoClip,
    clip_duration,
    layout,
)


@dataclass(frozen=True)
class Window:
    """Render ``project``, drop ``lead`` seconds, keep ``duration`` seconds."""

    project: Project
    lead: float
    duration: float


def _cut(item: MainItem, cut: float) -> MainItem:
    """The item without its first ``cut`` timeline seconds (its transition is dropped)."""
    if cut > 0:
        if isinstance(item, VideoClip):
            item = replace(item, in_=item.in_ + cut * item.speed)
        else:
            item = replace(item, duration=item.duration - cut)
    return replace(item, transition_in=Transition())


def _first_cut(item: MainItem, cut: float) -> tuple[MainItem, float]:
    """Cut ``item``, but never inside its fade-in. Returns (item, seconds really cut)."""
    if 0 < cut < item.fade_in:
        cut = 0.0
    return _cut(item, cut), cut


def _main_window(project: Project, start: float, end: float) -> tuple[tuple[MainItem, ...], float]:
    main = project.main
    spans = layout(project)
    if not main or start >= spans[main[-1].id].end:
        return (), start
    f = 0
    for i, item in enumerate(main):
        if spans[item.id].start <= start:
            f = i
    span_f = spans[main[f].id]
    tr = main[f].transition_in
    kept: list[MainItem] = []
    if f > 0 and tr.kind != "none" and start < span_f.start + tr.duration:
        prev = main[f - 1]
        span_p = spans[prev.id]
        first, cut = _first_cut(prev, span_f.start - span_p.start)
        kept.append(first)
        window_start = span_p.start + cut
        kept.append(main[f])
    else:
        first, cut = _first_cut(main[f], start - span_f.start)
        kept.append(first)
        window_start = span_f.start + cut
    for item in main[f + 1 :]:
        if spans[item.id].start >= end:
            break
        kept.append(item)
    return tuple(kept), window_start


def _shift_text(t: TextItem, by: float) -> TextItem:
    return replace(t, start=t.start - by, end=t.end - by)


def _shift_music(a: AudioClip, by: float) -> AudioClip:
    loop = a.loop_until - by if a.loop_until > 0 else a.loop_until
    return replace(a, start=a.start - by, loop_until=loop)


def window(project: Project, start: float, end: float) -> Window:
    main, window_start = _main_window(project, start, end)
    text_rows = tuple(
        tuple(_shift_text(t, window_start) for t in row if t.end > window_start and t.start < end)
        for row in project.text_rows
    )
    music_rows = tuple(
        tuple(
            _shift_music(a, window_start)
            for a in row
            if a.start + clip_duration(a) > window_start and a.start < end
        )
        for row in project.music_rows
    )
    sub = replace(project, main=main, text_rows=text_rows, music_rows=music_rows)
    return Window(sub, start - window_start, end - start)
