"""Probing media files into project sources, and building a starting project. No Qt."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from umc.converters.slideshow import oriented_size
from umc.core.detect import MediaFamily, detect
from umc.core.errors import ConversionError
from umc.tools import ffmpeg
from umc.videoedit import edits
from umc.videoedit.project import Project, ProjectSettings, Source

log = logging.getLogger(__name__)

PROJECT_SUFFIX = ".umcvideo"


def _cannot_open(path: Path) -> ConversionError:
    return ConversionError(f"Couldn't open “{path.name}”.")


def probe_source(path: Path, source_id: str) -> Source:
    """Read a video, picture or audio file; raises ``ConversionError`` if it can't be used."""
    path = Path(path).absolute()
    found = detect(path)
    family = found.family if found else None
    if family == MediaFamily.IMAGE:
        try:
            width, height = oriented_size(path)
        except Exception as exc:
            log.warning("Couldn't read picture %s: %s", path, exc)
            raise _cannot_open(path) from exc
        return Source(source_id, path, "image", 0.0, width, height, 0.0, False)
    if family not in (MediaFamily.VIDEO, MediaFamily.AUDIO):
        raise _cannot_open(path)
    try:
        info = ffmpeg.probe(path)
    except ConversionError as exc:
        log.warning("Couldn't probe %s: %s", path, exc)
        raise _cannot_open(path) from exc
    if family == MediaFamily.AUDIO or not info.has_video:
        if not info.has_audio:
            raise _cannot_open(path)
        return Source(source_id, path, "audio", info.duration, 0, 0, 0.0, True)
    width, height = info.width, info.height
    if info.rotation in (90, 270):
        width, height = height, width
    return Source(source_id, path, "video", info.duration, width, height, info.fps, info.has_audio)


def new_project(paths: Sequence[Path]) -> tuple[Project, list[str]]:
    """A project from dropped files; returns it with messages for files that couldn't be used."""
    paths = [Path(p) for p in paths]
    if any(p.suffix.lower() == PROJECT_SUFFIX for p in paths):
        if len(paths) != 1:
            raise ConversionError("Open a video project on its own, without other files.")
        from umc.videoedit.projectfile import load_project

        project, missing = load_project(paths[0])
        return project, [f"Couldn't find “{m.path.name}”." for m in missing]

    project = Project(settings=ProjectSettings(), sources=())
    messages: list[str] = []
    sources: list[Source] = []
    for path in paths:
        try:
            sources.append(probe_source(path, f"s{len(sources) + 1}"))
        except ConversionError:
            messages.append(f"Couldn't open “{path.name}”.")
    project = edits.add_sources(project, sources)
    music_end = 0.0
    for src in sources:
        if src.kind == "audio":
            project, _ = edits.add_at(project, src.id, music_end, 0)
            music_end += src.duration
        else:
            project, _ = edits.insert_main(project, len(project.main), src.id)
    return project, messages
