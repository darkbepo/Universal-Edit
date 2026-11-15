"""Properties step 3: changing music tags (umc.properties.music + the Properties panel's card)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from dataclasses import fields
from pathlib import Path

import pytest
from PIL import Image

from umc.properties import music

CASES = json.loads((Path(__file__).parent / "fixtures" / "music_cases.json").read_text("utf-8"))
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

ENCODERS = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "flac": ["-c:a", "flac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
}


def _song(path: Path) -> Path:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=0.5"]
    subprocess.run([*cmd, *ENCODERS[path.suffix[1:]], str(path)], check=True)
    return path


def _jpeg(color: str = "red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buf, "JPEG")
    return buf.getvalue()


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(buf, "PNG")
    return buf.getvalue()


def _ffprobe_tags(path: Path) -> dict[str, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json",
         "-show_entries", "format_tags:stream=codec_type:stream_tags", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    data = json.loads(out)
    tags = {k.lower(): v for k, v in data.get("format", {}).get("tags", {}).items()}
    for s in data.get("streams", []):
        if s.get("codec_type") == "audio":  # not the cover picture's own label
            tags.update({k.lower(): v for k, v in s.get("tags", {}).items()})
    return tags


# ---------------------------------------------------------------- shared cases (web too)
@pytest.mark.parametrize("case", CASES["clean"], ids=lambda c: json.dumps(c["values"]))
def test_clean_values_shared_cases(case):
    if "error" in case:
        with pytest.raises(music.MusicError) as err:
            music.clean_values(case["values"])
        assert str(err.value) == case["error"]
    else:
        assert music.clean_values(case["values"]) == case["expect"]


def test_common_shared_cases():
    for case in CASES["common"]:
        details = [music.MusicDetails(**d) for d in case["details"]]
        assert music.common(details) == case["expect"]


def test_cover_shared_cases():
    for case in CASES["cover"]:
        data = bytes.fromhex(case["hex"])
        if "error" in case:
            with pytest.raises(music.MusicError, match=case["error"]):
                music.cover_mime(data)
        else:
            assert music.cover_mime(data) == case["expect"]


def test_fields_cover_the_details():
    names = [f.name for f in fields(music.MusicDetails)]
    assert names == [*music.FIELDS, "has_cover"]


def test_only_these_formats_can_change():
    assert all(music.can_change(e) for e in ("mp3", "MP3", "m4a", "flac", "ogg", "opus"))
    assert not any(music.can_change(e) for e in ("wav", "aac", "wma", "mp4", "jpg"))


def test_not_a_song_is_refused():
    with pytest.raises(music.MusicError, match="couldn't be read"):
        music.read_details(b"not music at all" * 10)


# ---------------------------------------------------------------- every format, for real
@needs_ffmpeg
@pytest.mark.parametrize("ext", sorted(ENCODERS))
def test_round_trip(tmp_path, ext):
    src = _song(tmp_path / f"song.{ext}")
    edit = music.MusicEdit(
        {"title": "Blue in Green", "artist": "Miles Davis", "album": "Kind of Blue",
         "album_artist": "Miles Davis Sextet", "track": "3/5", "disc": "1/1", "year": "1959",
         "genre": "Jazz"},
        _jpeg(),
    )  # fmt: skip
    saved = music.save_file(src, edit)
    assert saved == src.resolve()
    d = music.read_details(src)
    assert (d.title, d.artist, d.album, d.album_artist) == (
        "Blue in Green", "Miles Davis", "Kind of Blue", "Miles Davis Sextet"
    )  # fmt: skip
    assert (d.track, d.disc, d.year, d.genre, d.has_cover) == ("3/5", "1/1", "1959", "Jazz", True)
    tags = _ffprobe_tags(src)  # another program sees them too
    assert tags.get("title") == "Blue in Green"
    assert tags.get("artist") == "Miles Davis"
    # change one field, keep the rest; then clear it and take the cover out
    music.save_file(src, music.MusicEdit({"title": "So What"}))
    d = music.read_details(src)
    assert (d.title, d.album, d.track, d.has_cover) == ("So What", "Kind of Blue", "3/5", True)
    music.save_file(src, music.MusicEdit({"album": "", "track": "4"}, remove_cover=True))
    d = music.read_details(src)
    assert (d.album, d.track, d.has_cover) == ("", "4", False)
    assert subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-f", "null", "-"],
                          capture_output=True).returncode == 0  # fmt: skip


@needs_ffmpeg
def test_png_cover_and_unicode(tmp_path):
    src = _song(tmp_path / "song.flac")
    music.save_file(src, music.MusicEdit({"title": "Ärger 日本"}, _png()))
    d = music.read_details(src)
    assert (d.title, d.has_cover) == ("Ärger 日本", True)


@needs_ffmpeg
def test_mp3_uses_id3_v23_with_a_front_cover(tmp_path):
    from mutagen.id3 import ID3

    src = _song(tmp_path / "song.mp3")
    music.save_file(src, music.MusicEdit({"title": "x"}, _jpeg()))
    tags = ID3(src)
    assert tags.version[:2] == (2, 3)
    pics = tags.getall("APIC")
    assert len(pics) == 1 and pics[0].type == 3 and pics[0].mime == "image/jpeg"
    # a new cover replaces the old one, it isn't added next to it
    music.save_file(src, music.MusicEdit({}, _jpeg("green")))
    assert len(ID3(src).getall("APIC")) == 1


@needs_ffmpeg
def test_bad_values_change_nothing(tmp_path):
    src = _song(tmp_path / "song.mp3")
    before = src.read_bytes()
    with pytest.raises(music.MusicError, match="track number"):
        music.save_file(src, music.MusicEdit({"track": "13/12"}))
    with pytest.raises(music.MusicError, match="JPG or PNG"):
        music.save_file(src, music.MusicEdit({}, b"GIF89a" + b"0" * 20))
    assert src.read_bytes() == before


@needs_ffmpeg
def test_copy_and_locked_original(tmp_path, monkeypatch):
    import os

    src = _song(tmp_path / "song.ogg")
    before = src.read_bytes()
    copy = music.save_file(src, music.MusicEdit({"title": "Copy"}), as_copy=True)
    assert copy.name == "song (1).ogg" and src.read_bytes() == before
    assert music.read_details(copy).title == "Copy"

    def locked(a, b):
        raise PermissionError("in use")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(music.MusicError, match="open in another program"):
        music.save_file(src, music.MusicEdit({"title": "x"}))
    assert src.read_bytes() == before
    assert not list(tmp_path.glob(".umc-saving-*"))


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
        lambda: not panel.music_form.isHidden() and "File" in panel.rows(), timeout=10000
    )
    return panel.music_form


def _type(form, name: str, text: str) -> None:
    form.edits[name].setText(text)
    form.edits[name].textEdited.emit(text)


def _wait_saved(qtbot, w):
    panel = w.properties_panel

    def done() -> bool:
        note = panel.save_message()
        form = panel.active_form()
        return not form.save_button.isEnabled() and "Sav" in note and "Saving" not in note

    qtbot.waitUntil(done, timeout=10000)


def _settle(qtbot, w) -> None:
    qtbot.waitUntil(lambda: not w.file_list.thumbnails_pending(), timeout=10000)
    w.properties_panel.tasks.wait(5000)


@needs_ffmpeg
def test_card_saves_a_copy(qtbot, window, tmp_path):
    src = _song(tmp_path / "song.mp3")
    music.save_file(src, music.MusicEdit({"title": "Old", "artist": "Band"}))
    before = src.read_bytes()
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert window.properties_panel.form.isHidden()  # the photo card isn't shown
    assert form.edits["title"].text() == "Old"
    assert form.cover_status.text() == "No cover picture"
    assert not form.save_button.isEnabled()
    _type(form, "title", "New")
    assert form.save_button.isEnabled()
    form.copy_button.click()
    _wait_saved(qtbot, window)
    assert src.read_bytes() == before
    d = music.read_details(tmp_path / "song (1).mp3")
    assert (d.title, d.artist) == ("New", "Band")
    assert "song (1).mp3" in window.properties_panel.save_message()
    _settle(qtbot, window)


@needs_ffmpeg
def test_card_changes_the_original_with_a_cover(qtbot, window, tmp_path, monkeypatch):
    src = _song(tmp_path / "song.m4a")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(_jpeg())
    asked = []
    monkeypatch.setattr(
        type(window.properties_panel), "confirm_original", lambda self, n: asked.append(n) or True
    )
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    _type(form, "track", "2/9")
    form.set_cover_file(cover)
    assert form.cover_status.text() == "New cover: cover.jpg"
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert asked == [1]
    d = music.read_details(src)
    assert (d.track, d.has_cover) == ("2/9", True)
    assert window.properties_panel.save_message() == "Saved."
    qtbot.waitUntil(lambda: form.cover_status.text() == "Has a cover picture", timeout=10000)
    _settle(qtbot, window)


@needs_ffmpeg
def test_card_applies_to_every_song(qtbot, window, tmp_path, monkeypatch):
    a = _song(tmp_path / "a.mp3")
    b = _song(tmp_path / "b.flac")
    music.save_file(a, music.MusicEdit({"title": "One", "album": "LP"}))
    music.save_file(b, music.MusicEdit({"title": "Two", "album": "LP"}))
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: True)
    window.add_files([str(a), str(b)])
    window.enter_properties()
    form = _form(qtbot, window)
    assert not form.apply_all.isHidden() and form.apply_all.text() == "Apply to all 2 songs"
    form.apply_all.setChecked(True)
    assert form.edits["title"].text() == ""
    assert form.edits["title"].placeholderText() == "Several values"
    assert form.edits["album"].text() == "LP"
    _type(form, "artist", "Same band")
    form.save_button.click()
    _wait_saved(qtbot, window)
    assert window.properties_panel.save_message() == "Saved 2 songs."
    da, db = music.read_details(a), music.read_details(b)
    assert (da.title, db.title) == ("One", "Two")  # "Several values" left alone
    assert da.artist == db.artist == "Same band"
    _settle(qtbot, window)


@needs_ffmpeg
def test_card_refuses_a_bad_track(qtbot, window, tmp_path, monkeypatch):
    src = _song(tmp_path / "song.opus")
    before = src.read_bytes()
    monkeypatch.setattr(type(window.properties_panel), "confirm_original", lambda self, n: True)
    window.add_files([str(src)])
    window.enter_properties()
    form = _form(qtbot, window)
    _type(form, "track", "three")
    assert form.edits["track"].property("invalid") is True
    form.save_button.click()
    assert "track number" in window.properties_panel.save_message()
    assert src.read_bytes() == before
    bad = tmp_path / "cover.gif"
    Image.new("RGB", (4, 4)).save(bad, "GIF")
    form.set_cover_file(bad)
    assert "JPG or PNG" in form.note.text()
    _settle(qtbot, window)


def test_card_not_for_wav(qtbot, window, media):
    wavs = list(media.glob("*.wav"))
    if not wavs:
        pytest.skip("no wav sample")
    window.add_files([str(wavs[0])])
    window.enter_properties()
    qtbot.waitUntil(lambda: "File" in window.properties_panel.rows(), timeout=10000)
    assert window.properties_panel.music_form.isHidden()
    assert window.properties_panel.form.isHidden()
    _settle(qtbot, window)
