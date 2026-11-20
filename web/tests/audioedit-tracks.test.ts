// Several tracks and their mix (port of tests/test_audioedit_tracks.py; same shared cases).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/audio_mix_cases.json";
import { AudioDoc } from "../src/audioedit/document";
import * as tracks from "../src/audioedit/tracks";

const make = (samples: number[][], over: Partial<tracks.Track> = {}): tracks.Track =>
  ({ ...tracks.track(new AudioDoc(8000, samples.map((ch) => Float32Array.from(ch))), "t"), ...over });

describe("mix: shared cases", () => {
  for (const c of cases.cases) {
    it(c.name, () => {
      const ts = c.tracks.map((t) => make(t.samples, { gainDb: t.gain_db, pan: t.pan, mute: t.mute, solo: t.solo }));
      const out = tracks.mix(ts, c.start, c.end);
      expect(out.length).toBe(c.mix.length);
      out.forEach((ch, i) => expect(Array.from(ch)).toEqual(c.mix[i]));
    });
  }
});

describe("tracks", () => {
  it("solo wins and mute silences", () => {
    const a = make([[0]]), b = make([[0]], { mute: true }), c = make([[0]], { solo: true });
    expect(tracks.audible([a, b])).toEqual([a]);
    expect(tracks.audible([a, b, c])).toEqual([c]);
    c.mute = true;
    expect(tracks.audible([a, b, c])).toEqual([]);
  });

  it("pan keeps the centre at full", () => {
    expect(tracks.panGains(0)).toEqual([1, 1]);
    expect(tracks.panGains(-1)).toEqual([1, 0]);
    expect(tracks.panGains(0.5)).toEqual([0.5, 1]);
    expect(tracks.panGains(3)).toEqual([0, 1]);
  });

  it("mix length, channels and peak", () => {
    const mono = make([Array(10).fill(0.5)]), stereo = make([Array(4).fill(0.25), Array(4).fill(0.75)]);
    const out = tracks.mix([mono, stereo]);
    expect(out.length).toBe(2);
    expect([out[0][0], out[1][0]]).toEqual([0.75, 1.25]);
    expect([out[0][9], out[1][9]]).toEqual([0.5, 0.5]);
    expect(tracks.peakDb(out)).toBeCloseTo(20 * Math.log10(1.25), 9);
    expect(tracks.peakDb([new Float32Array(3)])).toBe(-Infinity);
    stereo.mute = true;
    expect(tracks.length([mono, stereo])).toBe(10);
    expect(tracks.mix([mono, stereo], 8)[0].length).toBe(2);
    expect(tracks.mix([], 0).map((ch) => ch.length)).toEqual([0]);
  });

  it("next name and totals", () => {
    const ts = [make([[0, 0, 0]]), make([[0, 0], [0, 0]])];
    ts[0].name = "Track 1";
    ts[1].name = "Track 3";
    expect(tracks.nextName(ts)).toBe("Track 4");
    expect(tracks.nextName([])).toBe("Track 1");
    expect(tracks.totalSamples(ts)).toBe(7);
  });
});

describe("changed", () => {
  it("is true only away from the defaults", () => {
    const t = make([[0.5]]);
    expect(tracks.changed(t)).toBe(false);
    t.mute = true;
    expect(tracks.changed(t)).toBe(true);
  });
});
