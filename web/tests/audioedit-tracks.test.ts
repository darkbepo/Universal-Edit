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
      const ts = c.tracks.map((t) => make(t.samples, { gainDb: t.gain_db, pan: t.pan, mute: t.mute, solo: t.solo, offset: t.offset }));
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

  it("start times: length, extent, changed", () => {
    const a = make([Array(10).fill(0.5)]), b = make([Array(4).fill(0.25)], { offset: 20 });
    expect(tracks.trackEnd(b)).toBe(24);
    expect(tracks.length([a, b])).toBe(24);
    b.mute = true;
    expect(tracks.length([a, b])).toBe(10);
    expect(tracks.extent([a, b])).toBe(24);
    expect(tracks.changed(b)).toBe(true);
    expect(tracks.changed(a)).toBe(false);
    expect(tracks.trackEnd(make([[]], { offset: 50 }))).toBe(0);
    const mixed = tracks.mix([a, { ...b, mute: false }], 8, 22)[0];
    expect(Array.from(mixed)).toEqual([0.5, 0.5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.25, 0.25]);
  });

  it("duplicate copies the sound and settings, not the history", () => {
    const a = make([[0.1, 0.2]], { gainDb: -3, offset: 7, name: "Voice" });
    a.doc.amplify(6);
    const b = tracks.duplicate(a, tracks.copyName(a.name, [a]));
    expect(b.name).toBe("Voice copy");
    expect([b.gainDb, b.offset]).toEqual([-3, 7]);
    expect(Array.from(b.doc.samples[0])).toEqual(Array.from(a.doc.samples[0]));
    expect(b.doc.samples[0]).not.toBe(a.doc.samples[0]);
    expect(b.doc.canUndo()).toBe(false);
    expect(tracks.copyName("Voice", [a, b])).toBe("Voice copy 2");
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
