"""Writes tests/fixtures/audio_mix_cases.json (run on purpose only: both apps check it)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from umc.audioedit import tracks
from umc.audioedit.document import AudioDoc

RATE = 8000
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "audio_mix_cases.json"


def signal(seed: int, channels: int, frames: int) -> list[list[float]]:
    """MINSTD noise in −0.5 … 0.5, rounded to float32 (exact in JS doubles too)."""
    state, out = seed, []
    for _ in range(channels):
        ch = []
        for _ in range(frames):
            state = state * 48271 % 2147483647
            ch.append(float(np.float32(state / 2147483647 - 0.5)))
        out.append(ch)
    return out


# each track: seed, channels, frames, gain_db, pan, mute, solo[, offset]
CASES = {
    "one centred": [(1, 2, 40, 0.0, 0.0, False, False)],
    "mono + stereo, panned": [
        (2, 1, 50, -6.0, -0.5, False, False),
        (3, 2, 30, 3.0, 1.0, False, False),
    ],
    "mute": [(4, 2, 20, 0.0, 0.0, True, False), (5, 2, 25, -3.0, 0.25, False, False)],
    "solo wins": [(6, 1, 30, 0.0, 0.0, False, True), (7, 1, 60, 0.0, 0.0, False, False)],
    "mono out": [(8, 1, 20, -12.0, 0.0, False, False), (9, 1, 35, 6.0, 0.0, False, False)],
    "mono panned": [(8, 1, 20, -12.0, 0.7, False, False), (9, 1, 35, 6.0, 0.0, False, False)],
    "part": [(10, 2, 40, 0.0, -1.0, False, False), (11, 1, 15, -1.5, 0.3, False, False)],
    "offsets": [(12, 2, 30, 0.0, 0.0, False, False, 10), (13, 1, 20, -2.0, 0.4, False, False)],
    "offset part": [
        (14, 1, 25, 0.0, 0.0, False, False, 5),
        (15, 2, 10, 1.0, -0.6, False, False, 30),
    ],
    "gap": [(16, 1, 10, 0.0, 0.0, False, False), (17, 1, 10, 0.0, 0.0, False, False, 25)],
}
PARTS = {"part": (10, 30), "offset part": (8, 33)}


def main() -> None:
    cases = []
    for name, spec in CASES.items():
        ts = []
        for seed, channels, frames, db, pan, mute, solo, *rest in spec:
            x = signal(seed, channels, frames)
            t = tracks.Track(
                AudioDoc(RATE, np.array(x, np.float32)), f"t{seed}", db, pan, mute, solo, *rest
            )
            ts.append((t, x))
        start, end = PARTS.get(name, (0, None))
        out = tracks.mix([t for t, _ in ts], start, end)
        cases.append(
            {
                "name": name,
                "tracks": [
                    {
                        "samples": x,
                        "gain_db": t.gain_db,
                        "pan": t.pan,
                        "mute": t.mute,
                        "solo": t.solo,
                        "offset": t.offset,
                    }
                    for t, x in ts
                ],
                "start": start,
                "end": end,
                "mix": [[float(v) for v in ch] for ch in out],
            }
        )
    OUT.write_text(json.dumps({"rate": RATE, "cases": cases}, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
