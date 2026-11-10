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
