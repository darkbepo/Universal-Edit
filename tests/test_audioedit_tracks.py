"""Several tracks and their mix (umc.audioedit.tracks; web/tests/audioedit-tracks.test.ts
checks the same shared cases)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from umc.audioedit import tracks
from umc.audioedit.document import AudioDoc

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "audio_mix_cases.json").read_text(encoding="utf-8")
)


def make(samples, **kw) -> tracks.Track:
    return tracks.Track(AudioDoc(8000, np.array(samples, np.float32)), "t", **kw)


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda c: c["name"])
def test_shared_cases(case) -> None:
    ts = [
        make(
            t["samples"],
            gain_db=t["gain_db"],
            pan=t["pan"],
            mute=t["mute"],
            solo=t["solo"],
            offset=t["offset"],
        )
        for t in case["tracks"]
    ]
    out = tracks.mix(ts, case["start"], case["end"])
    want = np.array(case["mix"], np.float32)
    assert out.shape == want.shape
    assert np.array_equal(out, want)


def test_audible_solo_and_mute() -> None:
    a, b, c = make([[0.0]]), make([[0.0]], mute=True), make([[0.0]], solo=True)
    assert tracks.audible([a, b]) == [a]
    assert tracks.audible([a, b, c]) == [c]
    c.mute = True  # a muted solo is silent, and the others stay silent
    assert tracks.audible([a, b, c]) == []


def test_start_times_length_extent_changed() -> None:
    a, b = make([[0.5] * 10]), make([[0.25] * 4], offset=20)
    assert b.end == 24
    assert tracks.length([a, b]) == 24
    b.mute = True
    assert tracks.length([a, b]) == 10 and tracks.extent([a, b]) == 24
    assert tracks.changed(b) and not tracks.changed(a)
    assert make(np.zeros((1, 0)), offset=50).end == 0
    b.mute = False
    mixed = tracks.mix([a, b], 8, 22)[0].tolist()
    assert mixed == [0.5, 0.5] + [0.0] * 10 + [0.25, 0.25]


def test_duplicate_copies_sound_and_settings_not_history() -> None:
    a = make([[0.1, 0.2]], gain_db=-3, offset=7)
    a.name = "Voice"
    a.doc.amplify(6)
    b = tracks.duplicate(a, tracks.copy_name(a.name, [a]))
    assert b.name == "Voice copy" and (b.gain_db, b.offset) == (-3, 7)
    assert np.array_equal(b.doc.samples, a.doc.samples)
    assert b.doc.samples is not a.doc.samples and not b.doc.can_undo()
    assert tracks.copy_name("Voice", [a, b]) == "Voice copy 2"


def test_pan_gains_keep_the_centre_at_full() -> None:
    assert tracks.pan_gains(0) == (1.0, 1.0)
    assert tracks.pan_gains(-1) == (1.0, 0.0)
    assert tracks.pan_gains(0.5) == (0.5, 1.0)
    assert tracks.pan_gains(3) == (0.0, 1.0)


def test_mix_length_channels_and_peak() -> None:
    mono, stereo = make([[0.5] * 10]), make([[0.25] * 4, [0.75] * 4])
    out = tracks.mix([mono, stereo])
    assert out.shape == (2, 10)
    assert out[:, 0].tolist() == [0.75, 1.25] and out[:, 9].tolist() == [0.5, 0.5]
    assert tracks.peak_db(out) == pytest.approx(20 * math.log10(1.25))
    assert tracks.peak_db(np.zeros((1, 3), np.float32)) == -math.inf
    stereo.mute = True
    assert tracks.length([mono, stereo]) == 10 and tracks.mix([mono, stereo], 8).shape == (2, 2)
    assert tracks.mix([], 0).shape == (1, 0)


def test_next_name_and_totals() -> None:
    ts = [make([[0.0] * 3]), make([[0.0] * 2, [0.0] * 2])]
    ts[0].name, ts[1].name = "Track 1", "Track 3"
    assert tracks.next_name(ts) == "Track 4"
    assert tracks.next_name([]) == "Track 1"
    assert tracks.total_samples(ts) == 7


def test_changed_and_lazy_mix() -> None:
    t = make([[0.5] * 10, [0.25] * 10])
    assert not tracks.changed(t)
    t.gain_db = -6.0
    assert tracks.changed(t)
    other = make([[0.1] * 4])
    lazy = tracks.LazyMix([t, other], 10)
    assert lazy.shape == (2, 10)
    assert np.array_equal(lazy[:, 2:7], tracks.mix([t, other], 2, 7))
    assert lazy[:, 8:20].shape == (2, 2)  # clipped at the end, like an array
