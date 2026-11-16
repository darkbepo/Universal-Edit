"""The viewer's folder browsing model (no Qt): groups, Up, history, the address line, waveform."""

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from umc.viewer import browse, waveform


def _make(folder: Path, names: list[str]) -> None:
    for name in names:
        p = folder / name
        if name.endswith("/"):
            p.mkdir(parents=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"x")


def test_listing_groups_files_by_kind_and_hides_empty_groups(tmp_path):
    _make(tmp_path, ["b.png", "a10.jpg", "a2.jpg", "song.mp3", "notes.txt", "x.unknownbin"])
    _make(tmp_path, ["Sub 10/", "Sub 2/", ".hidden/", ".secret.png"])
    (tmp_path / "x.unknownbin").write_bytes(b"\x00\x01\x02")
    listing = browse.list_location(tmp_path)
    assert [p.name for p in listing.folders] == ["Sub 2", "Sub 10"]
    assert [p.name for p in listing.groups["pictures"]] == ["a2.jpg", "a10.jpg", "b.png"]
    assert [c.id for c in listing.shown_categories()] == ["folders", "pictures", "music", "text"]
    assert [p.name for p in listing.files()] == [
        "a2.jpg",
        "a10.jpg",
        "b.png",
        "song.mp3",
        "notes.txt",
    ]


def test_missing_folder_lists_nothing(tmp_path):
    listing = browse.list_location(tmp_path / "gone")
    assert listing.files() == [] and listing.folders == [] and listing.shown_categories() == []


def test_up_goes_to_the_parent_until_the_top(tmp_path):
    assert browse.parent_of(tmp_path / "a") == tmp_path
    top = Path(tmp_path.anchor)
    if sys.platform == "win32":
        assert browse.parent_of(top) is browse.COMPUTER
    else:
        assert browse.parent_of(top) is None
    assert browse.parent_of(browse.COMPUTER) is None


def test_history_back_and_forward():
    h = browse.History()
    a, b, c = Path("/a"), Path("/b"), Path("/c")
    h.visit(a)
    assert not h.can_back() and not h.can_forward()
    h.visit(b)
    h.visit(b)  # the same place again isn't a step
    h.visit(c)
    assert h.back() == b and h.back() == a and h.back() is None
    assert h.can_forward()
    assert h.forward() == b
    h.visit(Path("/d"))  # going somewhere new drops Forward
    assert not h.can_forward() and h.current == Path("/d")
    assert h.back() == b


def test_address_line(tmp_path, monkeypatch):
    _make(tmp_path, ["photos/", "photos/cat.png"])
    assert browse.resolve_address(str(tmp_path / "photos"), tmp_path) == (tmp_path / "photos", None)
    assert browse.resolve_address(f'"{tmp_path / "photos"}"', tmp_path)[0] == tmp_path / "photos"
    assert browse.resolve_address("photos", tmp_path) == (tmp_path / "photos", None)  # relative
    file = tmp_path / "photos" / "cat.png"
    assert browse.resolve_address(str(file), tmp_path) == (file.parent, file)
    monkeypatch.setenv("UMC_TEST_DIR", str(tmp_path))
    var = "%UMC_TEST_DIR%" if sys.platform == "win32" else "$UMC_TEST_DIR"
    assert browse.resolve_address(var + "/photos", Path.cwd())[0] == tmp_path / "photos"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert browse.resolve_address("~/photos", Path.cwd())[0] == tmp_path / "photos"
    assert browse.resolve_address(str(tmp_path / "nope"), tmp_path) is None
    assert browse.resolve_address("   ", tmp_path) is None
    assert browse.resolve_address("photos", browse.COMPUTER) is None


def test_display():
    assert browse.display(browse.COMPUTER) == "This PC"
    assert browse.display(Path("/x/y")) == str(Path("/x/y"))


def test_step_skips_folded_files():
    a, b, c, d = (Path(n) for n in "abcd")
    order = [a, b, c, d]
    assert browse.step_index(order, a, 1, set()) == 1
    assert browse.step_index(order, a, 1, {b, c}) == 3
    assert browse.step_index(order, d, -1, {b, c}) == 0
    assert browse.step_index(order, d, 5, set()) == 3  # stays at the end
    assert browse.step_index(order, b, 1, {b}) == 2  # the shown file may be in a folded group
    assert browse.step_index(order, None, 1, {a}) == 1  # nothing shown: the first allowed
    assert browse.step_index(order, a, 1, {a, b, c, d} - {a}) == 0
    assert browse.step_index([], None, 1, set()) == -1


def test_waveform_levels_from_samples():
    quiet_then_loud = np.concatenate([np.full(100, 0.1), np.full(100, -0.8)])
    levels = waveform.levels_from_samples(quiet_then_loud, 4)
    assert len(levels) == 4
    assert levels[2] == levels[3] == 1.0  # the loudest part reaches the top
    assert 0 < levels[0] < levels[2]
    assert waveform.levels_from_samples(np.zeros(10), 4) == [0.0] * 4
    assert waveform.levels_from_samples(np.array([]), 4) == []
    assert len(waveform.levels_from_samples(np.ones(3), 10)) == 3  # never more bars than samples


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs FFmpeg")
def test_waveform_of_a_real_file(media):
    levels = waveform.levels(media / "tone.wav", columns=50)
    assert len(levels) == 50 and max(levels) == 1.0
