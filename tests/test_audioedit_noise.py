"""Audio editor noise reduction: the shared cases with the website and a real clean-up."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import numpy as np
import pytest

from umc.audioedit import noise
from umc.core.errors import ConversionCancelled

DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "audioedit_noise_cases.json").read_text("utf-8")
)


def _profile() -> np.ndarray:
    return noise.learn(np.array(DATA["noise"], np.float64), DATA["rate"])


def test_shared_profile():
    profile = _profile()
    for i, want in enumerate(DATA["profile_bands"]):
        assert profile[i * 64] == pytest.approx(want, rel=1e-9, abs=1e-15)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda c: f"{c['reduce_db']}dB")
def test_shared_reduce_cases(case):
    out = noise.reduce(
        np.array(DATA["signal"], np.float64),
        DATA["rate"],
        _profile(),
        case["reduce_db"],
        case["sensitivity_db"],
        case["smoothing"],
    )
    for ch, picks in enumerate(case["picks"]):
        assert np.abs(out[ch][::37] - np.array(picks)).max() < 1e-5
    for ch, want in enumerate(case["rms"]):
        assert np.sqrt(np.mean(out[ch].astype(np.float64) ** 2)) == pytest.approx(want, abs=1e-5)


def _long_signal(seed: int, frames: int, rate: int) -> np.ndarray:
    """Same as long_signal in scripts/make_noise_cases.py and the web test."""
    state, out = seed, np.empty(frames, np.float64)
    for i in range(frames):
        state = state * 48271 % 2147483647
        v = (state / 2147483647 - 0.5) * 0.04
        if i >= frames // 2:
            v += 0.3 * np.sin(2 * np.pi * 440 * i / rate)
        out[i] = v
    return out[None, :]


def test_shared_long_case_crosses_chunks():
    c = DATA["long"]
    steps: list[float] = []
    x = _long_signal(c["seed"], c["frames"], DATA["rate"])
    out = noise.reduce(
        x, DATA["rate"], _profile(), c["reduce_db"], c["sensitivity_db"], c["smoothing"],
        progress=steps.append,
    )  # fmt: skip
    assert np.abs(out[0][:: c["every"]] - np.array(c["picks"])).max() < 1e-5
    assert np.sqrt(np.mean(out[0].astype(np.float64) ** 2)) == pytest.approx(c["rms"], abs=1e-5)
    assert len(steps) == c["progress_steps"] > 1


def test_too_short_names_the_real_minimum():
    assert "0.1 seconds" in noise.noise_too_short(44100)
    assert "0.6 seconds" in noise.noise_too_short(8000)  # 4096 frames = 0.512 s


def _db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2))))


def test_turns_hiss_down_and_keeps_the_tone():
    rate = 44100
    rng = np.random.default_rng(7)
    t = np.arange(rate * 3) / rate
    tone = 0.3 * np.sin(2 * np.pi * 440 * t)
    tone[:rate] = 0  # the first second is only hiss
    x = (tone + 0.02 * rng.standard_normal(t.size))[None, :].astype(np.float32)
    y = noise.reduce(x, rate, noise.learn(x[:, :rate], rate), 24, 6, 3)
    assert y.shape == x.shape and y.dtype == np.float32
    assert _db(y[0, :rate]) < _db(x[0, :rate]) - 12  # the hiss is much quieter
    assert _db(y[0, 2 * rate :] - tone[2 * rate :]) < _db(tone[2 * rate :]) - 25  # tone kept


def test_clean_sound_comes_back_unchanged():
    x = (0.5 * np.sin(np.arange(20000) / 10))[None, :].astype(np.float32)
    y = noise.reduce(x, 8000, np.zeros(noise.BANDS))
    assert np.abs(y - x).max() < 1e-6


def test_refusals_cancel_and_progress():
    with pytest.raises(ValueError, match="longer part"):
        noise.learn(np.zeros((1, 100), np.float32), 8000)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionCancelled):
        noise.reduce(
            np.zeros((1, 8000), np.float32), 8000, np.ones(noise.BANDS), cancel_event=cancel
        )
    seen = []
    noise.reduce(
        np.zeros((2, 200_000), np.float32), 8000, np.ones(noise.BANDS), progress=seen.append
    )
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


def test_learn_uses_at_most_thirty_seconds():
    rate = 1000
    x = np.zeros((1, 40 * rate), np.float32)
    x[0, 30 * rate :] = 1.0  # loud after 30 s: must not count
    assert noise.learn(x, rate).max() == 0
