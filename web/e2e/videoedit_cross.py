"""Desktop side of the cross-app video project tests (run by the e2e suite with a found Python).

``save SPEC OUT``: writes a desktop project (``save_project``) built from the JSON spec, whose
files sit in OUT's folder, and prints it like ``load``. ``load PATH``: prints what the desktop's
``load_project`` makes of a project file as JSON (items, timeline spans, texts, missing sources).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from umc.videoedit import edits  # noqa: E402
from umc.videoedit.project import Project, ProjectSettings, Source, layout  # noqa: E402
from umc.videoedit.projectfile import load_project, save_project  # noqa: E402


def describe(project: Project, missing: list[str]) -> dict[str, Any]:
    spans = layout(project)
    span = {k: [s.start, s.end] for k, s in spans.items()}
    return {
        "sources": [
            {"id": s.id, "name": Path(s.path).name, "kind": s.kind, "size": s.size}
            for s in project.sources
        ],
        "main": [{"id": m.id, "source": m.source, "span": span[m.id]} for m in project.main],
        "music": [
            {"id": a.id, "source": a.source, "span": span[a.id]}
            for row in project.music_rows
            for a in row
        ],
        "texts": [
            {"id": t.id, "text": t.text, "span": span[t.id]}
            for row in project.text_rows
            for t in row
        ],
        "missing": missing,
    }


def build(spec: dict[str, Any], folder: Path) -> Project:
    """``spec``: sources (id, name, kind, duration, width, height, fps, has_audio), main / music /
    texts entries ({source} / {source, start} / {start}), each with optional ``changes``."""
    sources = []
    for s in spec["sources"]:
        path = folder / s["name"]
        sources.append(
            Source(
                s["id"],
                path,
                s["kind"],
                s["duration"],
                s["width"],
                s["height"],
                s["fps"],
                s["has_audio"],
                path.stat().st_size,
            )
        )
    p = edits.add_sources(Project(ProjectSettings(), ()), sources)
    for i, m in enumerate(spec.get("main", [])):
        p, item = edits.insert_main(p, i, m["source"])
        p = edits.update(p, item, **m.get("changes", {}))
    for a in spec.get("music", []):
        p, item = edits.add_at(p, a["source"], a.get("start", 0.0), 0)
        p = edits.update(p, item, **a.get("changes", {}))
    for t in spec.get("texts", []):
        p, item = edits.add_text(p, t["start"])
        p = edits.update(p, item, **t.get("changes", {}))
    return p


def main(argv: list[str]) -> None:
    if argv[0] == "save":
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        out = Path(argv[2]).absolute()
        save_project(build(spec, out.parent), out)
        argv = ["load", str(out)]
    project, missing = load_project(Path(argv[1]))
    text = json.dumps(describe(project, [m.source_id for m in missing]), ensure_ascii=False)
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main(sys.argv[1:])
