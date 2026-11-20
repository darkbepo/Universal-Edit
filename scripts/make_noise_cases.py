"""Writes tests/fixtures/audioedit_noise_cases.json (run on purpose only: both apps check it)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from umc.audioedit import noise

RATE = 8000
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audioedit_noise_cases.json"


def _signal(seed: int, frames: int, tone: bool) -> list[float]:
    state = seed
    out = []
    for i in range(frames):
        state = (1103515245 * state + 12345) % 2**31
        v = (state / 2**31 - 0.5) * 0.04
        if tone and i >= frames // 3:
            v += 0.3 * math.sin(2 * math.pi * 440 * i / RATE)
        out.append(round(v, 6))
    return out


LONG = {"seed": 7, "frames": 140_000, "every": 1999}  # > CHUNK blocks: crosses chunk borders


def long_signal(seed: int, frames: int) -> np.ndarray:
    """Hiss (MINSTD, exact in JS doubles too) + a 440 Hz tone in the second half; mono."""
    state, out = seed, np.empty(frames, np.float64)
    for i in range(frames):
        state = state * 48271 % 2147483647
        v = (state / 2147483647 - 0.5) * 0.04
        if i >= frames // 2:
            v += 0.3 * math.sin(2 * math.pi * 440 * i / RATE)
        out[i] = v
    return out[None, :]


def main() -> None:
    noise_part = [_signal(1, 4200, False), _signal(2, 4200, False)]
    signal = [_signal(3, 3000, True), _signal(4, 3000, True)]
    profile = noise.learn(np.array(noise_part, np.float64), RATE)
    picks = list(range(0, 3000, 37))
    cases = []
    for reduce_db, sens, smooth in ((12, 6, 3), (30, 12, 0), (6, 0, 12)):
        y = noise.reduce(np.array(signal, np.float64), RATE, profile, reduce_db, sens, smooth)
        cases.append(
            {
                "reduce_db": reduce_db,
                "sensitivity_db": sens,
                "smoothing": smooth,
                "picks": [[float(ch[i]) for i in picks] for ch in y],
                "rms": [float(np.sqrt(np.mean(ch.astype(np.float64) ** 2))) for ch in y],
            }
        )
    x = long_signal(LONG["seed"], LONG["frames"])
    steps: list[float] = []
    y = noise.reduce(x, RATE, profile, 18, 6, 3, progress=steps.append)
    long_case = {
        **LONG,
        "reduce_db": 18,
        "sensitivity_db": 6,
        "smoothing": 3,
        "picks": [float(y[0, i]) for i in range(0, LONG["frames"], LONG["every"])],
        "rms": float(np.sqrt(np.mean(y[0].astype(np.float64) ** 2))),
        "progress_steps": len(steps),
    }
    data = {
        "_about": "Audio editor noise reduction: audioedit/noise.py ↔ web/src/audioedit/noise.ts "
        "(python scripts/make_noise_cases.py; regenerate on purpose only). profile_bands = "
        "learn(noise)[every 64th band]; picks = reduce(signal)[ch][every 37th frame], within "
        "1e-5. long = a generated "
        "mono signal (long_signal in the script and both tests) over several chunks.",
        "rate": RATE,
        "noise": noise_part,
        "signal": signal,
        "profile_bands": [float(profile[k]) for k in range(0, noise.BANDS, 64)],
        "cases": cases,
        "long": long_case,
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n", "utf-8")


if __name__ == "__main__":
    main()
