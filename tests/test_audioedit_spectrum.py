"""The spectrogram model (shared cases with web/tests/audioedit-spectrum.test.ts)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from umc.audioedit import spectrum

DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "audioedit_spectrum_cases.json").read_text("utf-8")
)


def _signal() -> np.ndarray:
    """Same as signal() in scripts/make_spectrum_cases.py and the web test."""
    rate, frames = DATA["rate"], DATA["frames"]
    state, out = 3, np.empty((2, frames), np.float64)
    for i in range(frames):
        for c, hz in enumerate((1000, 2500)):
            state = state * 48271 % 2147483647
            out[c, i] = (
                0.5 * math.sin(2 * math.pi * hz * i / rate) + (state / 2147483647 - 0.5) * 0.01
            )
    return out


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: f"start{c['start']}x{c['channels']}")
def test_shared_columns(case):
    x = _signal()[: case["channels"]]
    lv = spectrum.columns(x, DATA["rate"], case["start"], case["fpp"], case["width"], case["rows"])
    assert lv.shape == (case["channels"], case["k"], case["rows"])
    for ch, want in enumerate(case["levels"]):
        assert np.abs(lv[ch].reshape(-1) - np.array(want)).max() < 1e-5


def test_shared_row_bands_and_colours():
    for b in DATA["row_bands"]:
        first, end = spectrum.row_bands(b["rate"], b["rows"])
        assert first.tolist() == b["first"] and end.tolist() == b["end"]
    c = DATA["colours"]
    assert spectrum.colours(np.array(c["levels"])).tolist() == c["rgb"]


def test_a_tone_lands_on_its_row_at_full_level():
    rate, rows = 44100, 200
    t = np.arange(rate) / rate
    x = np.sin(2 * np.pi * 1000 * t)[None, :].astype(np.float32)
    lv = spectrum.columns(x, rate, 0, rate / 100, 100, rows)
    col = lv[0, 50]
    assert int(np.argmax(col)) == int(spectrum.row_of(1000, rate, rows))
    assert col.max() == pytest.approx(1.0, abs=0.02)  # full-scale sine ≈ 0 dB (−1 between bands)
    assert col[: rows // 4].max() < 0.5  # far below the tone it's quiet


def test_silence_past_the_end_and_bad_sizes():
    x = np.zeros((1, 1000), np.float32)
    assert spectrum.columns(x, 8000, 0, 1, 50, 10).max() == 0  # silence = floor
    assert spectrum.columns(x, 8000, 2000, 1, 50, 10).shape == (1, 0, 10)
    assert spectrum.columns(x, 8000, 900, 10, 50, 10).shape[1] == 10  # stops at the end
    assert spectrum.columns(x, 8000, 0, 1, 5, 10_000).shape[2] == spectrum.MAX_ROWS
    first, end = spectrum.row_bands(8000, 1)
    assert first.tolist() == [7] and end.tolist() == [1025]  # 30 Hz = band 7.7


def test_column_step_caps_the_work():
    assert spectrum.column_step(800) == 1
    assert spectrum.column_step(801) == 2
    assert spectrum.column_step(1920) == 3
