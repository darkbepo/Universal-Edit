"""Writes tests/fixtures/audioedit_spectrum_cases.json (run on purpose only: both apps check it)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from umc.audioedit import spectrum

RATE = 8000
FRAMES = 6000
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OUT = OUT / "audioedit_spectrum_cases.json"
# start, fpp, width, rows, channels (1 = the first channel only)
CASES = ((0, 100, 70, 24, 2), (1234.5, 0.25, 40, 7, 2), (5900, 10, 50, 33, 2), (300, 7, 30, 12, 1))


def signal() -> np.ndarray:
    """Two channels: a 1 kHz / 2.5 kHz tone + quiet hiss (MINSTD, exact in JS doubles too)."""
    state, out = 3, np.empty((2, FRAMES), np.float64)
    for i in range(FRAMES):
        for c, hz in enumerate((1000, 2500)):
            state = state * 48271 % 2147483647
            hiss = (state / 2147483647 - 0.5) * 0.01
            out[c, i] = 0.5 * math.sin(2 * math.pi * hz * i / RATE) + hiss
    return out


def main() -> None:
    x = signal()
    cases = []
    for start, fpp, width, rows, channels in CASES:
        lv = spectrum.columns(x[:channels], RATE, start, fpp, width, rows)
        cases.append(
            {
                "start": start,
                "fpp": fpp,
                "width": width,
                "rows": rows,
                "channels": channels,
                "k": int(lv.shape[1]),
                "levels": [[round(float(v), 7) for v in ch.reshape(-1)] for ch in lv],
            }
        )
    bands = []
    for rate, rows in ((44100, 200), (8000, 32), (22050, 5)):
        first, end = spectrum.row_bands(rate, rows)
        bands.append({"rate": rate, "rows": rows, "first": first.tolist(), "end": end.tolist()})
    levels = [0, 0.1, 0.3, 0.55, 0.8, 0.97, 1]
    data = {
        "_about": "Audio editor spectrogram: audioedit/spectrum.py ↔ web/src/audioedit/spectrum.ts "
        "(python scripts/make_spectrum_cases.py; regenerate on purpose only). The signal is "
        "made by signal() in the script and both tests; levels are column after column, "
        "within 1e-5.",
        "rate": RATE,
        "frames": FRAMES,
        "cases": cases,
        "row_bands": bands,
        "colours": {"levels": levels, "rgb": spectrum.colours(np.array(levels)).tolist()},
    }
    OUT.write_text(json.dumps(data, separators=(",", ":")) + "\n", "utf-8")


if __name__ == "__main__":
    main()
