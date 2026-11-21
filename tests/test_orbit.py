"""The badges orbiting the empty drop zone: shared maths (both apps) and the desktop widget."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from umc.core import orbit

ROOT = Path(__file__).parent.parent


def test_web_copy_matches():
    web = json.loads((ROOT / "web/src/ui/orbit.json").read_text(encoding="utf-8"))
    assert web == json.loads(json.dumps(orbit.export())), "run python -m umc.core.orbit"


def test_cases_match_the_shared_file():
    cases = json.loads((ROOT / "tests/fixtures/orbit_cases.json").read_text(encoding="utf-8"))
    for case in cases:
        got = orbit.layout(*case["args"])
        assert len(got) == orbit.SLOTS
        for s, want in zip(got, case["spots"], strict=True):
            for key, value in want.items():
                assert getattr(s, key) == pytest.approx(value, abs=1e-9), (case["args"], key)


def test_every_badge_has_an_icon_in_both_sets():
    from umc.gui.icon_data import ICONS

    web = (ROOT / "web/src/ui/icon_data.ts").read_text(encoding="utf-8")
    assert orbit.SLOTS == 48
    for icon, _label, _colour in orbit.BADGES:
        assert icon in ICONS and f'"{icon}":' in web


def test_every_file_type_has_a_badge():
    """A new extension in core/detect.py needs a badge in orbit.BADGES (then regenerate
    web/src/ui/orbit.json and the cases: see the module's docstring)."""
    from umc.core import detect

    labels = [label for _icon, label, _colour in orbit.BADGES]
    assert len(labels) == len(set(labels))
    families = [n for n in dir(detect) if n.endswith("_EXTS") and not n.startswith("_")]
    known = set()
    for name in families:
        if name not in ("TEXT_DOCUMENT_EXTS", "PRESENTATION_EXTS", "SPREADSHEET_EXTS"):
            known |= {e.upper() for e in getattr(detect, name)}
    known -= {e.upper() for e in getattr(detect, "PROJECT_EXTS", ())}  # our own project files
    assert known <= set(labels), sorted(known - set(labels))


def test_badges_come_and_go_and_all_take_turns():
    seen: set[int] = set()
    for step in range(0, 1200):
        spots = orbit.layout(step * 0.5, 1640, 570, 330, 110)
        shown = [s.badge for s in spots if s.alpha > 0]
        seen |= set(shown)
    assert seen == set(range(len(orbit.BADGES)))  # every type appears within ten minutes
    # one slot over time: fades in, stays, fades out, gap, then the next badge
    lives = [orbit.slot_badge(5, t / 10) for t in range(0, 600)]  # a minute
    assert {b for b, _ in lives} >= {5, 5 + orbit.SLOTS}
    assert any(life == 0 for _, life in lives) and any(life == 1 for _, life in lives)
    assert any(0 < life < 1 for _, life in lives)


def test_no_file_type_is_ever_on_screen_twice():
    for step in range(0, 7200):  # an hour
        shown = [s.badge for s in orbit.layout(step * 0.5, 1640, 570, 330, 110) if s.alpha > 0]
        assert len(shown) == len(set(shown)), step * 0.5


def test_reduced_motion_shows_whole_badges():
    spots = orbit.layout(0, 1640, 570, 330, 110, still=True)
    assert [s.badge for s in spots] == list(range(orbit.SLOTS))
    far = [s for s in spots if abs(s.x) > 600]  # away from the text: nothing fades them
    assert far and all(s.scale == pytest.approx(0.82 + 0.18 * s.z) for s in far)


def test_some_rings_move_up_and_down():
    upright = [r for r in orbit.RINGS if abs(r.roll) > 45]
    assert len(upright) == 2


def test_badges_stay_inside_and_clear_of_the_text():
    w, h, tw, th = 1200, 500, 320, 110
    for t in range(0, 400):
        for s in orbit.layout(t * 0.5, w, h, tw, th):
            assert abs(s.x) <= w / 2 - orbit.PAD + 1 and abs(s.y) <= h / 2
            if abs(s.x) < tw / 2 and abs(s.y) < th / 2:
                assert s.alpha == 0  # never over the text


def test_pull_draws_in_and_burst_pushes_out_and_fades():
    base = orbit.layout(2, 1200, 500, 300, 100)
    pulled = orbit.layout(2, 1200, 500, 300, 100, pull=1)
    burst = orbit.layout(2, 1200, 500, 300, 100, burst=0.5)
    done = orbit.layout(2, 1200, 500, 300, 100, burst=1)
    for b, p, x, d in zip(base, pulled, burst, done, strict=True):
        assert abs(p.x) <= abs(b.x) + 1e-9 and abs(x.x) >= abs(b.x) - 1e-9
        assert d.alpha == 0
    assert orbit.speed(1) > orbit.speed(0) == 1


# ----------------------------------------------------------------------------- GUI
@pytest.fixture
def window(qtbot, monkeypatch):
    # Windows test machines have "animation effects" off: say motion is fine for these tests
    monkeypatch.setenv("UMC_REDUCE_MOTION", "0")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.resize(1200, 800)
    w.show()
    return w


def test_badges_move_while_empty_and_stop_with_files(qtbot, window, media, monkeypatch):
    o = window.drop.orbit
    assert o.active
    qtbot.waitUntil(lambda: o.tau > 0.05, timeout=3000)
    window.add_files([str(media / "tall.jpg")])
    assert not o.active and not o.timer.isActive()
    window.clear_files()
    assert o.active and o.timer.isActive()
    qtbot.waitUntil(lambda: not window.file_list.thumbnails_pending(), timeout=10000)


def test_drop_bursts_then_adds(qtbot, window, media):
    o = window.drop.orbit
    o.set_pull(True)
    qtbot.waitUntil(lambda: o.pull > 0.5, timeout=3000)
    window.drop.drop_with_burst(lambda: window.handle_drop([str(media / "tall.jpg")]))
    assert not window.files  # not yet: the badges burst first
    assert o.pull_target == 0
    qtbot.waitUntil(lambda: bool(window.files), timeout=3000)
    assert window.drop.compact and not o.active
    qtbot.waitUntil(lambda: not window.file_list.thumbnails_pending(), timeout=10000)


def test_reduced_motion_keeps_them_still_and_drops_at_once(qtbot, monkeypatch, media):
    monkeypatch.setenv("UMC_REDUCE_MOTION", "1")
    from umc.gui.main_window import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    o = w.drop.orbit
    qtbot.wait(100)
    assert not o.timer.isActive() and o.tau == 0
    assert any(s.alpha > 0 for s in o.spots())  # still drawn, just not moving
    w.drop.drop_with_burst(lambda: w.handle_drop([str(media / "tall.jpg")]))
    assert w.files  # no waiting
    qtbot.waitUntil(lambda: not w.file_list.thumbnails_pending(), timeout=10000)


def test_a_drop_that_adds_nothing_brings_the_badges_back(qtbot, window, tmp_path):
    junk = tmp_path / "blob.xyz"
    junk.write_bytes(b"\x00\x01?\x00")  # binary: still refused (unknown text is accepted)
    o = window.drop.orbit
    window.drop.drop_with_burst(lambda: window.handle_drop([str(junk)]))
    assert o.burst_start is not None
    qtbot.waitUntil(lambda: o.burst_start is None, timeout=3000)
    assert not window.files and o.active and o.timer.isActive()
    assert any(s.alpha > 0 for s in o.spots())


def test_a_drop_of_links_only_lets_go_of_the_pull(window):
    from PySide6.QtCore import QMimeData, QUrl

    class FakeDrop:
        def __init__(self) -> None:
            self._m = QMimeData()
            self._m.setUrls([QUrl("https://example.com/a.png")])

        def mimeData(self):  # noqa: N802
            return self._m

    window.drop.orbit.set_pull(True)
    window.dropEvent(FakeDrop())
    assert window.drop.orbit.pull_target == 0
    assert not window.files


def test_no_animation_while_minimised(qtbot, window):
    o = window.drop.orbit
    assert o.timer.isActive()
    window.showMinimized()
    qtbot.waitUntil(lambda: not o.timer.isActive(), timeout=3000)
    window.showNormal()
    qtbot.waitUntil(lambda: o.timer.isActive(), timeout=3000)
