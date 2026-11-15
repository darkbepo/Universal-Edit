"""Properties step 4: changing video details (umc.properties.video + the Properties card)."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

from umc.core.errors import ConversionCancelled
from umc.properties import video
from umc.properties.video import VideoEdit

CASES = json.loads((Path(__file__).parent / "fixtures" / "video_cases.json").read_text("utf-8"))
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
PROBES = {c["name"]: c["probe"] for c in CASES["probe"] if "expect" in c}


# ---------------------------------------------------------------- shared cases
@pytest.mark.parametrize("case", CASES["clean"], ids=lambda c: json.dumps(c["values"]))
def test_clean_values_shared_cases(case):
    def offset(_parts):
        return case["offset"]

    if "error" in case:
        with pytest.raises(video.VideoError, match=case["error"]):
            video.clean_values(case["values"], offset=offset)
    else:
        assert video.clean_values(case["values"], offset=offset) == case["expect"]


def test_dates_shared_cases():
    for c in CASES["local"]:
        assert video.local_text(c["iso"], c["offset"]) == c["expect"], c
    for c in CASES["stored"]:
        assert video.stored_date(c["stamp"]) == c["expect"], c


def test_common_shared_cases():
    for c in CASES["common"]:
        details = [video.VideoDetails(**d) for d in c["details"]]
        assert video.common(details) == c["expect"]


@pytest.mark.parametrize("case", CASES["probe"], ids=lambda c: c["name"])
def test_details_from_probe_shared_cases(case):
    if "error" in case:
        with pytest.raises(video.VideoError, match=case["error"]):
            video.details_from_probe(case["probe"], case["ext"])
    else:
        assert asdict(video.details_from_probe(case["probe"], case["ext"])) == case["expect"]


@pytest.mark.parametrize("case", CASES["args"], ids=lambda c: f"{c['file']} {c['edit']}")
def test_ffmpeg_args_shared_cases(case):
    ext = case["file"].rsplit(".", 1)[1]
    details = video.details_from_probe(PROBES[case["file"]], ext)
    e = case["edit"]
    edit = VideoEdit(e["values"], {int(k): v for k, v in e["languages"].items()}, e["defaults"])

    def run():
        video.check_edit(details, edit)
        clean = video.clean_values(edit.values, offset=lambda _p: case["offset"])
        src, dst = "/in/" + case["file"], "/out/" + case["file"]
        return video.ffmpeg_args(ext, src, dst, details, edit, clean, case["major"])

    if "error" in case:
        with pytest.raises(video.VideoError, match=case["error"]):
            run()
    else:
        assert run() == case["expect"]


def test_languages_shared():
    assert [list(x) for x in video.LANGUAGES] == CASES["languages"]


def test_only_these_formats_can_change():
    assert {e for e in ("mp4", "MOV", "m4v", "mkv", "webm") if video.can_change(e)} == {
        "mp4", "MOV", "m4v", "mkv", "webm"
    }  # fmt: skip
    assert not any(video.can_change(e) for e in ("avi", "wmv", "flv", "mp3"))


# ---------------------------------------------------------------- real files
def _clip(path: Path, *extra: str) -> Path:
    """A short video with two sound tracks (English, Danish = default) and German subtitles."""
    srt = path.with_suffix(".srt")
    srt.write_text("1\n00:00:00,000 --> 00:00:00,400\nHallo\n", "utf-8")
    sub = {".mkv": "srt", ".webm": "webvtt"}.get(path.suffix, "mov_text")
    vcodec = ["-c:v", "libvpx"] if path.suffix == ".webm" else ["-c:v", "libx264"]
    acodec = ["-c:a", "libvorbis"] if path.suffix == ".webm" else ["-c:a", "aac"]
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=d=0.5:s=64x48:r=10",
           "-f", "lavfi", "-i", "sine=d=0.5", "-f", "lavfi", "-i", "sine=f=600:d=0.5",
           "-i", str(srt), "-map", "0", "-map", "1", "-map", "2", "-map", "3",
           *vcodec, *acodec, "-c:s", sub,
           "-metadata", "title=Old title", "-metadata", "creation_time=2021-05-06T07:08:09Z",
           "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=dan",
           "-metadata:s:s:0", "language=ger",
           "-disposition:a:0", "0", "-disposition:a:1", "default", *extra, str(path)]  # fmt: skip
    subprocess.run(cmd, check=True)
    srt.unlink()
    return path


@needs_ffmpeg
@pytest.mark.parametrize("ext", ["mp4", "mov", "m4v", "mkv", "webm"])
def test_round_trip(tmp_path, ext):
    src = _clip(tmp_path / f"clip.{ext}")
    d = video.read_details(src)
    assert (d.title, d.date) == ("Old title", "2021-05-06T07:08:09Z")
    audio = [t for t in d.tracks if t.kind == "audio"]
    assert [(t.language, t.default) for t in audio] == [("eng", False), ("dan", True)]
    values = {"title": "Ny titel ✓", "description": "Two\nlines", "date": "2024-02-03 10:20:30"}
    if ext in video.ROTATING_EXTS:
        values["rotation"] = "90"
    edit = VideoEdit(values, {audio[0].index: "fre"}, {"audio": audio[0].index})
    mtime = src.stat().st_mtime_ns
    seen: list[float] = []
    assert video.save_file(src, edit, progress=seen.append) == src.resolve()
    assert src.stat().st_mtime_ns == mtime  # the modified date is kept
    after = video.read_details(src)
    assert (after.title, after.description) == ("Ny titel ✓", "Two\nlines")
    expected = video.clean_values({"date": "2024-02-03 10:20:30"})["date"]
    assert after.date == expected
    assert after.rotation == (90 if ext in video.ROTATING_EXTS else 0)
    audio = [t for t in after.tracks if t.kind == "audio"]
    assert [(t.language, t.default) for t in audio] == [("fre", True), ("dan", False)]
    assert len(after.tracks) == len(d.tracks)  # the subtitles are still there
    assert seen and seen[-1] == 1.0
    assert not list(tmp_path.glob(".umc-saving-*"))


@needs_ffmpeg
def test_clear_date_and_copy(tmp_path):
    src = _clip(tmp_path / "clip.mp4")
    before = src.read_bytes()
    copy = video.save_file(src, VideoEdit({"date": "", "title": ""}), as_copy=True)
    assert copy.name == "clip (1).mp4" and src.read_bytes() == before
    d = video.read_details(copy)
    assert (d.date, d.title) == ("", "")
    # turning it back to "not turned"
    turned = video.save_file(copy, VideoEdit({"rotation": "270"}))
    assert video.read_details(turned).rotation == 270
    video.save_file(turned, VideoEdit({"rotation": "0"}))
    assert video.read_details(turned).rotation == 0


@needs_ffmpeg
def test_mkv_no_default_subtitles(tmp_path):
    src = _clip(tmp_path / "clip.mkv", "-disposition:s:0", "default")
    d = video.read_details(src)
    sub = next(t for t in d.tracks if t.kind == "subtitle")
    assert sub.default
    video.save_file(src, VideoEdit(defaults={"subtitle": None}))
    sub = next(t for t in video.read_details(src).tracks if t.kind == "subtitle")
    assert not sub.default


@needs_ffmpeg
def test_bad_values_change_nothing(tmp_path):
    src = _clip(tmp_path / "clip.mkv")
    before = src.read_bytes()
    with pytest.raises(video.VideoError, match="Write the date"):
        video.save_file(src, VideoEdit({"date": "yesterday"}))
    with pytest.raises(video.VideoError, match="can't store a rotation"):
        video.save_file(src, VideoEdit({"rotation": "90"}))
    with pytest.raises(video.VideoError, match="3-letter code"):
        video.save_file(src, VideoEdit(languages={1: "danish"}))
    assert src.read_bytes() == before


@needs_ffmpeg
def test_cancel_and_locked_original(tmp_path, monkeypatch):
    import os

    src = _clip(tmp_path / "clip.mp4")
    before = src.read_bytes()
    stop = threading.Event()
    stop.set()
    with pytest.raises(ConversionCancelled):
        video.save_file(src, VideoEdit({"title": "x"}), cancel=stop)
    assert src.read_bytes() == before
    assert not list(tmp_path.glob(".umc-saving-*"))

    def locked(a, b):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(video.VideoError, match="open in another program"):
        video.save_file(src, VideoEdit({"title": "x"}))
    assert src.read_bytes() == before
    assert not list(tmp_path.glob(".umc-saving-*"))


@needs_ffmpeg
def test_not_enough_space(tmp_path, monkeypatch):
    src = _clip(tmp_path / "clip.mp4")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: shutil._ntuple_diskusage(1, 1, 1))
    with pytest.raises(video.VideoError, match="enough free space"):
        video.save_file(src, VideoEdit({"title": "x"}))


# ---------------------------------------------------------------- the Properties card
@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.setOrganizationName("UMC-tests")
    monkeypatch.setenv("UMC_REDUCE_MOTION", "1")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def _form(qtbot, w):
    panel = w.properties_panel
    qtbot.waitUntil(
        lambda: not panel.video_form.isHidden() and "File" in panel.rows(), timeout=10000
    )
    return panel.video_form


def _wait_saved(qtbot, w):
    panel = w.properties_panel

    def done() -> bool:
        note = panel.save_message()
        form = panel.active_form()
        return not form.save_button.isEnabled() and "Sav" in note and "Saving" not in note

    qtbot.waitUntil(done, timeout=20000)


def _settle(qtbot, w) -> None:
    qtbot.waitUntil(lambda: not w.file_list.thumbnails_pending(), timeout=10000)
    w.properties_panel.tasks.wait(5000)


@needs_ffmpeg
def test_card_changes_the_original(qtbot, window, tmp_path, monkeypatch):
    src = _clip(tmp_path / "clip.mp4")
    asked = []
    monkeypatch.setattr(
        type(window.properties_panel), "confirm_original", lambda self, n: asked.append(n) or True
    )
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert window.properties_panel.form.isHidden() and window.properties_panel.music_form.isHidden()
    assert form.title.text() == "Old title"
    assert form.date.text() == video.shown_date("2021-05-06T07:08:09Z")
    assert not form.rotation.isHidden() and form.rotation.currentData() == 0
    assert not form.save_button.isEnabled()
    assert set(form.defaults) == {"audio", "subtitle"}
    # MP4 always has a default subtitle track, so "None" isn't offered once one is marked
    assert form.defaults["audio"].findData(None) == -1
    form.title.setText("New title")
    form.title.textEdited.emit("New title")
    form.rotation.setCurrentIndex(form.rotation.findData(180))
    first_audio = next(i for i, b in form.languages.items() if "Sound 1" in b.accessibleName())
    form.languages[first_audio].setCurrentIndex(form.languages[first_audio].findData("spa"))
    form.defaults["audio"].setCurrentIndex(form.defaults["audio"].findData(first_audio))
    assert form.save_button.isEnabled()
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert asked == [1]
    assert window.properties_panel.save_message() == "Saved."
    d = video.read_details(src)
    assert (d.title, d.rotation) == ("New title", 180)
    audio = [t for t in d.tracks if t.kind == "audio"]
    assert [(t.language, t.default) for t in audio] == [("spa", True), ("dan", False)]
    qtbot.waitUntil(lambda: form.title.text() == "New title", timeout=10000)
    _settle(qtbot, window)


@needs_ffmpeg
def test_card_applies_to_every_video(qtbot, window, tmp_path, monkeypatch):
    a = _clip(tmp_path / "a.mp4")
    b = _clip(tmp_path / "b.mkv")
    video.save_file(b, VideoEdit({"title": "Other"}))
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: True)
    window.add_files([str(a), str(b)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert not form.apply_all.isHidden() and form.apply_all.text() == "Apply to all 2 videos"
    form.apply_all.setChecked(True)
    assert form.title.text() == "" and form.title.placeholderText() == "Several values"
    assert form.rotation.isHidden()  # the MKV can't store one
    assert form.tracks_box.isHidden()  # tracks only for one video
    form.description.setPlainText("Same for both")
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert window.properties_panel.save_message() == "Saved 2 videos."
    da, db = video.read_details(a), video.read_details(b)
    assert (da.title, db.title) == ("Old title", "Other")  # "Several values" left alone
    assert da.description == db.description == "Same for both"
    _settle(qtbot, window)


@needs_ffmpeg
def test_card_refuses_a_bad_date_and_names_lost_parts(qtbot, window, tmp_path, monkeypatch):
    src = _clip(tmp_path / "clip.mov")
    before = src.read_bytes()
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: True)
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    form.date.setText("next week")
    form.date.textEdited.emit("next week")
    assert form.date.property("invalid") is True
    form.save_button.click()
    assert "Write the date" in window.properties_panel.save_message()
    assert src.read_bytes() == before
    form.set_details(
        video.details_from_probe(PROBES["gopro.mp4"], "mp4"),
        [video.details_from_probe(PROBES["gopro.mp4"], "mp4")],
    )
    assert "GPS" in form.lost.text() and not form.lost.isHidden()
    _settle(qtbot, window)
