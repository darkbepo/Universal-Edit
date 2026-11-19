"""Song details (title, artist, album, …, cover) kept when the audio editor saves.

Read from the opened files; several joined files keep only what they all share (a cover only
when every file has the same one). Written into MP3 / M4A / FLAC / OGG / Opus saves through
``properties.music``. ``carried`` is ported line by line to ``web/src/audioedit/tags.ts``; both
suites check ``tests/fixtures/audioedit_tags_cases.json``."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from umc.properties import music

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CarriedTags:
    values: dict[str, str] = field(default_factory=dict)  # only fields with text
    cover: bytes | None = None

    def is_empty(self) -> bool:
        return not self.values and self.cover is None

    def edit_for(self, fmt: str) -> music.MusicEdit | None:
        """What to write into a file saved as ``fmt``; None when nothing (or it can't hold tags)."""
        if not music.can_change(fmt):
            return None
        cover = self.cover
        if cover is not None:
            try:
                music.cover_mime(cover)
            except music.MusicError:  # not a JPG / PNG: keep the text details anyway
                cover = None
        values: dict[str, str] = {}
        for name, text in self.values.items():
            try:  # one odd field (a vinyl track "A1") mustn't cost the others
                values.update(music.clean_values({name: text}))
            except music.MusicError:
                log.info("not keeping %s %r: not a value the tags can hold", name, text)
        if not values and cover is None:
            return None
        return music.MusicEdit(values=values, cover=cover)


def carried(
    details: Sequence[music.MusicDetails | None], covers: Sequence[bytes | None]
) -> CarriedTags:
    """The details every file shares. ``None`` = a file without readable tags."""
    if not details:
        return CarriedTags()
    shared = music.common([d or music.MusicDetails() for d in details])
    values = {name: text for name, text in shared.items() if text}
    cover = covers[0] if covers and all(c is not None and c == covers[0] for c in covers) else None
    return CarriedTags(values, cover)


def read_tags(paths: Sequence[Path]) -> CarriedTags:
    """``carried`` for files on disk; files mutagen can't read count as untagged."""
    details: list[music.MusicDetails | None] = []
    covers: list[bytes | None] = []
    for p in paths:
        d = c = None
        if music.can_change(Path(p).suffix.lstrip(".")):
            try:
                d, c = music.read_details(Path(p)), music.read_cover(Path(p))
            except music.MusicError:
                log.info("no readable tags in %s", p)
        details.append(d)
        covers.append(c)
    return carried(details, covers)
