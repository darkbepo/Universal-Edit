from __future__ import annotations

import json
import shutil

import pytest

from umc.core.detect import MediaFamily, detect
from umc.core.errors import ConversionError
from umc.core.registry import targets_for
from umc.videoedit import projectfile
from umc.videoedit.media import new_project, probe_source
from umc.videoedit.project import AudioClip, PictureClip, VideoClip


def test_new_project_puts_files_on_rows(vmedia):
    project, messages = new_project([vmedia / "red.mp4", vmedia / "pic.png", vmedia / "music.wav"])
    assert messages == []
    assert [s.id for s in project.sources] == ["s1", "s2", "s3"]
    assert [type(m) for m in project.main] == [VideoClip, PictureClip]
    assert [type(a) for a in project.music_rows[0]] == [AudioClip]
    assert project.music_rows[0][0].start == 0.0


def test_new_project_skips_bad_file(vmedia, tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_text("not a video")
    project, messages = new_project([bad, vmedia / "red.mp4"])
    assert len(messages) == 1 and "bad.mp4" in messages[0]
    assert len(project.main) == 1


def test_probe_source_basics(vmedia):
    red = probe_source(vmedia / "red.mp4", "s1")
    assert (red.kind, red.width, red.height, red.has_audio) == ("video", 320, 240, True)
    assert 1.9 < red.duration < 2.2
    blue = probe_source(vmedia / "blue.mp4", "s2")
    assert blue.has_audio is False
    pic = probe_source(vmedia / "pic.png", "s3")
    assert (pic.kind, pic.duration, pic.width, pic.height) == ("image", 0.0, 200, 100)
    assert probe_source(vmedia / "music.wav", "s4").kind == "audio"


def test_probe_source_uses_display_rotation(vmedia):
    src = probe_source(vmedia / "rot.mp4", "s1")
    assert (src.width, src.height) == (240, 320)


def test_project_round_trip(vmedia, tmp_path):
    project, _ = new_project([vmedia / "red.mp4", vmedia / "pic.png", vmedia / "music.wav"])
    path = tmp_path / "p.umcvideo"
    projectfile.save_project(project, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == "umcvideo" and data["version"] == 1
    loaded, missing = projectfile.load_project(path)
    assert missing == []
    assert loaded == project


def test_new_project_loads_umcvideo(vmedia, tmp_path):
    project, _ = new_project([vmedia / "red.mp4"])
    path = tmp_path / "p.umcvideo"
    projectfile.save_project(project, path)
    loaded, messages = new_project([path])
    assert loaded == project and messages == []


def test_load_finds_moved_folder_by_relpath(vmedia, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    for name in ("red.mp4", "pic.png"):
        shutil.copy(vmedia / name, a / name)
    project, _ = new_project([a / "red.mp4", a / "pic.png"])
    projectfile.save_project(project, a / "p.umcvideo")
    b = tmp_path / "b"
    a.rename(b)
    loaded, missing = projectfile.load_project(b / "p.umcvideo")
    assert missing == []
    assert all(s.path.parent == b for s in loaded.sources)


@pytest.mark.parametrize("sep", ["/", "\\"])
def test_relpath_is_stored_with_forward_slashes_and_read_either_way(vmedia, tmp_path, sep):
    a = tmp_path / "a"
    (a / "media").mkdir(parents=True)
    shutil.copy(vmedia / "red.mp4", a / "media" / "red.mp4")
    project, _ = new_project([a / "media" / "red.mp4"])
    projectfile.save_project(project, a / "p.umcvideo")
    data = json.loads((a / "p.umcvideo").read_text(encoding="utf-8"))
    assert data["sources"][0]["relpath"] == "media/red.mp4"  # the same file on every system
    data["sources"][0]["relpath"] = sep.join(["media", "red.mp4"])
    (a / "p.umcvideo").write_text(json.dumps(data), encoding="utf-8")
    b = tmp_path / "b"
    a.rename(b)
    loaded, missing = projectfile.load_project(b / "p.umcvideo")
    assert missing == []
    assert loaded.sources[0].path == (b / "media" / "red.mp4").resolve()


def test_load_reports_missing(vmedia, tmp_path):
    shutil.copy(vmedia / "red.mp4", tmp_path / "red.mp4")
    project, _ = new_project([tmp_path / "red.mp4"])
    projectfile.save_project(project, tmp_path / "p.umcvideo")
    (tmp_path / "red.mp4").unlink()
    loaded, missing = projectfile.load_project(tmp_path / "p.umcvideo")
    assert [m.source_id for m in missing] == ["s1"]
    assert missing[0].path.name == "red.mp4"
    assert loaded.sources[0].id == "s1"


def test_load_newer_version_refused(vmedia, tmp_path):
    project, _ = new_project([vmedia / "red.mp4"])
    path = tmp_path / "p.umcvideo"
    projectfile.save_project(project, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConversionError, match="newer version"):
        projectfile.load_project(path)


def test_load_garbage_refused(tmp_path):
    path = tmp_path / "p.umcvideo"
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(ConversionError, match="isn't a valid video project"):
        projectfile.load_project(path)
    path.write_text('{"format": "other"}', encoding="utf-8")
    with pytest.raises(ConversionError, match="isn't a valid video project"):
        projectfile.load_project(path)


def test_detect_umcvideo_family(tmp_path):
    p = tmp_path / "x.umcvideo"
    p.write_text("{}")
    d = detect(p)
    assert d is not None and d.family == MediaFamily.PROJECT
    ids = {t.id for t in targets_for([d])}
    assert not any(i.split(":")[0] in {"mp4", "png", "frames", "slideshow", "video"} for i in ids)
    assert "mp4" not in ids and "png" not in ids


def test_size_round_trips(tmp_path):
    from pathlib import Path

    from umc.videoedit.project import Project, ProjectSettings, Source

    (tmp_path / "v.mp4").write_bytes(b"")
    src = Source("s1", Path(tmp_path / "v.mp4"), "video", 2.0, 320, 240, 25.0, True, size=1234)
    project = Project(ProjectSettings(), (src,))
    projectfile.save_project(project, tmp_path / "p.umcvideo")
    loaded, missing = projectfile.load_project(tmp_path / "p.umcvideo")
    assert missing == []
    assert loaded.sources[0].size == 1234


def test_old_file_without_size_loads(vmedia, tmp_path):
    project, _ = new_project([vmedia / "red.mp4"])
    path = tmp_path / "p.umcvideo"
    projectfile.save_project(project, path)
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data["sources"]:
        del entry["size"]
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded, missing = projectfile.load_project(path)
    assert missing == [] and loaded.sources[0].size == 0


def test_loads_web_style_file(tmp_path):
    from umc.videoedit.project import layout

    (tmp_path / "v1.mp4").write_bytes(b"")
    (tmp_path / "p1.png").write_bytes(b"")
    data = {
        "format": "umcvideo",
        "version": 1,
        "settings": {"resolution": "first", "width": 1920, "height": 1080, "fps": "first"},
        "sources": [
            {
                "id": "v1",
                "path": "v1.mp4",
                "kind": "video",
                "duration": 10,
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "has_audio": True,
                "size": 5,
                "web_only": True,
            },
            {
                "id": "p1",
                "path": "p1.png",
                "kind": "image",
                "duration": 0,
                "width": 800,
                "height": 600,
                "fps": 0,
                "has_audio": False,
                "size": 5,
            },
        ],
        "main": [
            {"type": "video", "id": "a", "source": "v1", "in_": 0, "out": 4, "web_only": True},
            {"type": "picture", "id": "b", "source": "p1"},
        ],
        "text_rows": [[]],
        "music_rows": [[]],
    }
    path = tmp_path / "p.umcvideo"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded, missing = projectfile.load_project(path)
    assert missing == []
    spans = layout(loaded)
    assert spans["a"].end - spans["a"].start == 4.0
    assert loaded.sources[0].size == 5


def test_probe_source_fills_size(vmedia):
    src = probe_source(vmedia / "red.mp4", "s1")
    assert src.size == (vmedia / "red.mp4").stat().st_size > 0


def _save_valid(vmedia, tmp_path):
    shutil.copy(vmedia / "red.mp4", tmp_path / "red.mp4")
    project, _ = new_project([tmp_path / "red.mp4", vmedia / "music.wav"])
    path = tmp_path / "p.umcvideo"
    projectfile.save_project(project, path)
    return path, json.loads(path.read_text(encoding="utf-8"))


def _refused(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConversionError, match="isn't a valid video project file"):
        projectfile.load_project(path)


def test_load_refuses_unknown_duplicate_and_misplaced(vmedia, tmp_path):
    path, good = _save_valid(vmedia, tmp_path)
    import copy

    d = copy.deepcopy(good)
    d["main"][0]["source"] = "nope"
    _refused(path, d)
    d = copy.deepcopy(good)
    d["music_rows"][0][0]["source"] = "nope"
    _refused(path, d)
    d = copy.deepcopy(good)
    d["sources"].append(dict(d["sources"][0]))
    _refused(path, d)
    d = copy.deepcopy(good)
    d["music_rows"][0][0]["id"] = d["main"][0]["id"]
    _refused(path, d)
    d = copy.deepcopy(good)
    d["main"][0].pop("in_")
    _refused(path, d)
    d = copy.deepcopy(good)
    d["main"].append({"type": "text", "id": "tt", "start": 0, "end": 1})
    _refused(path, d)
    d = copy.deepcopy(good)
    d["music_rows"][0][0]["type"] = "video"
    _refused(path, d)


def test_missing_relative_source_stays_in_project_folder(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    data = {
        "format": "umcvideo",
        "version": 1,
        "settings": {},
        "sources": [
            {
                "id": "s1",
                "path": "clip.mp4",
                "kind": "video",
                "duration": 1.0,
                "width": 4,
                "height": 4,
                "fps": 30.0,
                "has_audio": False,
            }
        ],
        "main": [],
        "text_rows": [[]],
        "music_rows": [[]],
    }
    path = folder / "p.umcvideo"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded, missing = projectfile.load_project(path)
    assert loaded.sources[0].path == folder / "clip.mp4"
    assert missing[0].path == folder / "clip.mp4"


def test_find_source_ignores_folders_and_empty_paths(tmp_path):
    (tmp_path / "adir").mkdir()
    assert projectfile._find_source({"path": str(tmp_path / "adir")}, tmp_path) is None
    assert projectfile._find_source({"path": ""}, tmp_path) is None
