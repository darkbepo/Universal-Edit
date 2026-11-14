"""Saving and loading ``.umcvideo`` project files (JSON). No Qt."""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from umc.core.errors import ConversionError
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

FORMAT = "umcvideo"
VERSION = 1

_INVALID = "This isn't a valid video project file."
_ITEM_TYPES: dict[str, type] = {
    "video": VideoClip,
    "picture": PictureClip,
    "audio": AudioClip,
    "text": TextItem,
}
_TYPE_OF = {cls: name for name, cls in _ITEM_TYPES.items()}


@dataclass(frozen=True)
class MissingSource:
    source_id: str
    path: Path


def _item_to_dict(item: Any) -> dict[str, Any]:
    data = dataclasses.asdict(item)  # a nested Transition becomes a dict too
    return {"type": _TYPE_OF[type(item)], **data}


def _relpath(path: Path, folder: Path) -> str | None:
    """``path`` relative to the project's folder, always with ``/`` (same file on every OS)."""
    try:
        return Path(os.path.relpath(path, folder)).as_posix()
    except ValueError:  # another drive
        return None


def _to_dict(project: Project, folder: Path) -> dict[str, Any]:
    sources = []
    for s in project.sources:
        d = dataclasses.asdict(s)
        d["path"] = str(s.path)
        d["relpath"] = _relpath(s.path, folder)
        sources.append(d)
    return {
        "format": FORMAT,
        "version": VERSION,
        "settings": dataclasses.asdict(project.settings),
        "sources": sources,
        "main": [_item_to_dict(m) for m in project.main],
        "text_rows": [[_item_to_dict(t) for t in row] for row in project.text_rows],
        "music_rows": [[_item_to_dict(a) for a in row] for row in project.music_rows],
    }


def save_project(project: Project, path: Path) -> None:
    path = Path(path).absolute()
    text = json.dumps(_to_dict(project, path.parent), ensure_ascii=False, indent=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _build(cls: type, data: dict[str, Any]) -> Any:
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _item_from_dict(data: dict[str, Any]) -> Any:
    cls = _ITEM_TYPES[data["type"]]
    data = dict(data)
    if isinstance(data.get("transition_in"), dict):
        data["transition_in"] = _build(Transition, data["transition_in"])
    return _build(cls, data)


def _find_source(entry: dict[str, Any], folder: Path) -> Path | None:
    path = Path(entry["path"])
    if path.exists():
        return path
    rel = entry.get("relpath")
    if rel:
        # "/" since the first release; "\\" from a Windows copy still reads anywhere
        candidate = (folder / rel.replace("\\", "/")).resolve()
        if candidate.exists():
            return candidate
    return None


def load_project(path: Path) -> tuple[Project, list[MissingSource]]:
    path = Path(path).absolute()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise ValueError("wrong format")
        version = data["version"]
        if not isinstance(version, int):
            raise ValueError("bad version")
    except (OSError, ValueError, KeyError) as exc:
        raise ConversionError(_INVALID) from exc
    if version > VERSION:
        raise ConversionError("This project was made by a newer version of the app.")
    try:
        sources: list[Source] = []
        missing: list[MissingSource] = []
        for entry in data["sources"]:
            found = _find_source(entry, path.parent)
            if found is None:
                found = Path(entry["path"])
                missing.append(MissingSource(entry["id"], found))
            sources.append(_build(Source, {**entry, "path": found}))
        project = Project(
            settings=_build(ProjectSettings, data["settings"]),
            sources=tuple(sources),
            main=tuple(_item_from_dict(m) for m in data["main"]),
            text_rows=tuple(tuple(_item_from_dict(t) for t in r) for r in data["text_rows"]),
            music_rows=tuple(tuple(_item_from_dict(a) for a in r) for r in data["music_rows"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ConversionError(_INVALID) from exc
    return project, missing
