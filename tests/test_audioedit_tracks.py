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
        make(t["samples"], gain_db=t["gain_db"], pan=t["pan"], mute=t["mute"], solo=t["solo"])
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
