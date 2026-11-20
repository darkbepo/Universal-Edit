// The sound of a time window of a video project (desktop: FFmpeg's audio graph in render.py).
import { describe, expect, it } from "vitest";
import { Limiter, SAMPLE_RATE, mixWindow, type AudioLookup } from "../src/videoedit/mix";
import { audioClip, pictureClip, videoClip, type AudioClip, type MainItem, type Project, type Source } from "../src/videoedit/project";

const SR = SAMPLE_RATE;
const src = (id: string, kind: Source["kind"], has_audio = true, duration = 60): Source => ({
  id, path: `${id}.x`, kind, duration, width: 64, height: 36, fps: 25, has_audio, size: 1,
});
const SOURCES = [src("v", "video"), src("w", "video"), src("q", "video", false), src("p", "image", false), src("m", "audio")];

function project(main: MainItem[], music: AudioClip[] = []): Project {
  return {
    settings: { resolution: "custom", width: 64, height: 36, fps: "25", background: "#000000" },
    sources: SOURCES,
    main,
    text_rows: [[]],
    music_rows: [music],
  };
}

/** Every source plays a constant `level` (per source id). */
const constant = (levels: Record<string, number>): AudioLookup => (id, _from, frames) => {
  const v = levels[id] ?? 0;
  return [new Float32Array(frames).fill(v), new Float32Array(frames).fill(v)];
};
/** Every sample says which source second it is: value = source time (for checking positions). */
const clock: AudioLookup = (_id, from, frames) => {
  const a = Float32Array.from({ length: frames }, (_, i) => from + i / SR);
  return [a, a.slice()];
};
const sineLookup = (freq: number, amp: number): AudioLookup => (_id, from, frames) => {
  const a = Float32Array.from({ length: frames }, (_, i) => amp * Math.sin(2 * Math.PI * freq * (from + i / SR)));
  return [a, a.slice()];
};
const at = (chs: Float32Array[], t: number, start = 0) => chs[0][Math.round((t - start) * SR)];

describe("mixWindow", () => {
  it("volume and mute", () => {
    const v = { ...videoClip("a", "v", 0, 2), volume: 50 };
    const out = mixWindow(project([v]), 0, SR, constant({ v: 0.8 }));
    expect(out.length).toBe(2);
    expect(out[0].length).toBe(SR);
    expect(at(out, 0.5)).toBeCloseTo(0.4, 6);
    expect(out[1][100]).toBeCloseTo(0.4, 6);
    const muted = mixWindow(project([{ ...v, mute: true }]), 0, SR, constant({ v: 0.8 }));
    expect(muted[0].every((x) => x === 0)).toBe(true);
    const silentSource = mixWindow(project([videoClip("a", "q", 0, 2)]), 0, SR, constant({ q: 0.8 }));
    expect(silentSource[0].every((x) => x === 0)).toBe(true);
  });

  it("fades multiply", () => {
    const v = { ...videoClip("a", "v", 0, 2), fade_in: 1, fade_out: 1.5 };
    const out = mixWindow(project([v]), 0, 2 * SR, constant({ v: 1 }));
    expect(at(out, 0)).toBeCloseTo(0, 6);
    expect(at(out, 0.25)).toBeCloseTo(0.25, 4); // fade out not reached yet
    // At 0.75 s: fade in 0.75, fade out (2 − 0.75) / 1.5 = 0.8333.
    expect(at(out, 0.75)).toBeCloseTo(0.75 * (1.25 / 1.5), 4);
    expect(at(out, 1.5)).toBeCloseTo(0.5 / 1.5, 4);
  });

  it("crossfade", () => {
    const a = videoClip("a", "v", 0, 2);
    const b = { ...videoClip("b", "w", 0, 2), transition_in: { kind: "fade", duration: 1 } };
    const p = project([a, b]); // a 0–2, b 1–3
    const only = (id: string) => mixWindow(p, 0, 3 * SR, constant({ [id]: 1 }));
    const va = only("v");
    const vb = only("w");
    expect(at(va, 0.5)).toBeCloseTo(1, 6);
    expect(at(va, 1.5)).toBeCloseTo(0.5, 4);
    expect(at(vb, 1.5)).toBeCloseTo(0.5, 4);
    expect(at(va, 1.25)).toBeCloseTo(0.75, 4);
    expect(at(vb, 1.25)).toBeCloseTo(0.25, 4);
    expect(at(vb, 2.5)).toBeCloseTo(1, 6);
    expect(at(va, 2.5)).toBe(0);
    const both = mixWindow(p, 0, 3 * SR, constant({ v: 1, w: 1 }));
    expect(at(both, 1.3)).toBeCloseTo(1, 4);
    // Without a transition the second clip simply follows.
    const cut = mixWindow(project([a, { ...b, transition_in: { kind: "none", duration: 1 } }]), 0, 4 * SR, constant({ w: 1 }));
    expect(at(cut, 1.5)).toBe(0);
    expect(at(cut, 2.5)).toBeCloseTo(1, 6);
  });

  it("source positions follow in_ and the item's place", () => {
    const a = videoClip("a", "v", 0, 1);
    const b = videoClip("b", "w", 5, 7);
    const out = mixWindow(project([a, b]), 0.5, 2 * SR, clock);
    expect(at(out, 0.75, 0.5)).toBeCloseTo(0.75, 5);
    expect(at(out, 1.5, 0.5)).toBeCloseTo(5.5, 5);
  });

  it("pictures are silent", () => {
    const p = project([pictureClip("a", "p", 2), videoClip("b", "v", 0, 2)]);
    const out = mixWindow(p, 0, 4 * SR, constant({ p: 1, v: 1 }));
    expect(at(out, 1)).toBe(0);
    expect(at(out, 3)).toBeCloseTo(1, 6);
  });

  it("music start, loop and end", () => {
    // start −1 s begins 1 s into the song.
    const early = { ...audioClip("m1", "m", -1, 0, 10) };
    const out = mixWindow(project([], [early]), 0, SR, clock);
    expect(at(out, 0)).toBeCloseTo(1, 5);
    expect(at(out, 0.5)).toBeCloseTo(1.5, 5);
    // Later start: silence before it.
    const late = audioClip("m2", "m", 0.5, 2, 10);
    const l = mixWindow(project([], [late]), 0, SR, clock);
    expect(at(l, 0.25)).toBe(0);
    expect(at(l, 0.75)).toBeCloseTo(2.25, 5);
    // Loop wraps at out − in_ (1.5 s) until loop_until.
    const loop = { ...audioClip("m3", "m", 0, 2, 3.5), loop_until: 5 };
    const lp = mixWindow(project([], [loop]), 0, 6 * SR, clock);
    expect(at(lp, 1)).toBeCloseTo(3, 5);
    expect(at(lp, 2)).toBeCloseTo(2.5, 5);
    expect(at(lp, 3.25)).toBeCloseTo(2.25, 5);
    expect(at(lp, 4.9)).toBeCloseTo(2.4, 4);
    // Zero after length (5 s).
    expect(at(lp, 5.2)).toBe(0);
    expect(lp[0].subarray(5 * SR).every((x) => x === 0)).toBe(true);
    // Without a loop the music ends at out − in_.
    const once = mixWindow(project([], [audioClip("m4", "m", 0, 0, 1)]), 0, 2 * SR, constant({ m: 1 }));
    expect(at(once, 0.9)).toBeCloseTo(1, 6);
    expect(at(once, 1.1)).toBe(0);
  });

  it("music volume and fades over the clip's duration", () => {
    const m = { ...audioClip("m1", "m", 1, 0, 10), volume: 50, fade_in: 2, fade_out: 1, loop_until: 0 };
    const out = mixWindow(project([], [m]), 0, 12 * SR, constant({ m: 1 }));
    expect(at(out, 2)).toBeCloseTo(0.25, 4);
    expect(at(out, 5)).toBeCloseTo(0.5, 6);
    expect(at(out, 10.5)).toBeCloseTo(0.25, 4);
  });

  it("main row and music are summed, unlimited", () => {
    const p = project([videoClip("a", "v", 0, 2)], [audioClip("m1", "m", 0, 0, 2)]);
    const out = mixWindow(p, 0, SR, constant({ v: 0.7, m: 0.6 }));
    expect(at(out, 0.5)).toBeCloseTo(1.3, 5);
  });

  it("speed uses stretch", () => {
    const asked: [number, number][] = [];
    const lookup: AudioLookup = (id, from, frames) => {
      asked.push([from, frames]);
      return constant({ v: 0.5 })(id, from, frames);
    };
    const v = { ...videoClip("a", "v", 1, 5), speed: 2 }; // 2 s on the timeline
    const out = mixWindow(project([v]), 0, SR / 2, lookup);
    expect(asked).toEqual([[1, SR]]);
    expect(out[0].length).toBe(SR / 2);
    expect(at(out, 0.25)).toBeCloseTo(0.5, 4);
    asked.length = 0;
    mixWindow(project([v]), 0.5, SR / 2, lookup);
    expect(asked.length).toBe(1);
    expect(asked[0][0]).toBeCloseTo(2, 9);
    expect(asked[0][1]).toBe(SR);
  });

  it("missing audio is silence", () => {
    const p = project([videoClip("a", "v", 0, 2)], [audioClip("m1", "m", 0, 0, 2)]);
    const lookup: AudioLookup = (id, from, frames) => (id === "v" ? null : constant({ m: 0.3 })(id, from, frames));
    const out = mixWindow(p, 0, SR, lookup);
    expect(at(out, 0.5)).toBeCloseTo(0.3, 6);
    const none = mixWindow(p, 0, SR, () => null);
    expect(none[0].every((x) => x === 0)).toBe(true);
  });

  it("nothing after the project's length, and an empty project is silent", () => {
    const out = mixWindow(project([videoClip("a", "v", 0, 1)]), 0.5, SR, constant({ v: 1 }));
    expect(at(out, 0.9, 0.5)).toBeCloseTo(1, 6);
    expect(out[0].subarray(SR / 2).every((x) => x === 0)).toBe(true);
    const empty = mixWindow(project([]), 0, 1000, constant({}));
    expect(empty[0].length).toBe(1000);
    expect(empty[1].every((x) => x === 0)).toBe(true);
  });

  it("two windows give the same samples as one", () => {
    const a = { ...videoClip("a", "v", 0.3, 2.3), fade_in: 0.4, volume: 80 };
    const b = { ...videoClip("b", "w", 1, 3), transition_in: { kind: "fade", duration: 0.7 }, fade_out: 0.5 };
    const m = { ...audioClip("m1", "m", -0.2, 1, 1.9), loop_until: 3.5, fade_in: 0.3, fade_out: 0.6 };
    const p = project([a, b], [m]);
    const look = sineLookup(330, 0.4);
    const start = 0.137;
    const n = 1.5 * SR;
    const whole = mixWindow(p, start, 2 * n, look);
    const first = mixWindow(p, start, n, look);
    const second = mixWindow(p, start + n / SR, n, look);
    for (let c = 0; c < 2; c++) {
      let worst = 0;
      for (let i = 0; i < n; i++) {
        worst = Math.max(worst, Math.abs(whole[c][i] - first[c][i]), Math.abs(whole[c][n + i] - second[c][i]));
      }
      expect(worst).toBeLessThan(1e-5);
    }
  });
});

describe("Limiter", () => {
  const sum = (lookup: AudioLookup, seconds: number) => {
    const p = project(
      [videoClip("a", "v", 0, seconds)],
      [audioClip("m1", "m", 0, 0, seconds), audioClip("m2", "w", 0, 0, seconds)],
    );
    return mixWindow(p, 0, seconds * SR, lookup);
  };

  it("three 0.6 sums never exceed 0.95", () => {
    const chs = sum(constant({ v: 0.6, m: 0.6, w: 0.6 }), 1);
    expect(at(chs, 0.5)).toBeCloseTo(1.8, 5);
    new Limiter().process(chs);
    for (const ch of chs) for (const x of ch) expect(Math.abs(x)).toBeLessThanOrEqual(0.95 + 1e-7);
    expect(at(chs, 0.5)).toBeCloseTo(0.95, 5);
    const sines = sum(sineLookup(220, 0.6), 1);
    new Limiter().process(sines);
    for (const ch of sines) for (const x of ch) expect(Math.abs(x)).toBeLessThanOrEqual(0.95 + 1e-7);
  });

  it("a 0.3 signal is bit-identical", () => {
    const chs = sineLookup(440, 0.3)("v", 0, SR) as Float32Array[];
    const copy = chs.map((c) => c.slice());
    new Limiter().process(chs);
    expect(chs).toEqual(copy);
  });

  it("gain recovers within 100 ms", () => {
    const loud = new Float32Array(SR).fill(1.9, 0, SR / 2).fill(0.5, SR / 2);
    const chs = [loud, loud.slice()];
    new Limiter().process(chs);
    expect(chs[0][100]).toBeCloseTo(0.95, 5);
    const after = SR / 2 + Math.round(0.1 * SR);
    expect(chs[0][after]).toBe(0.5);
    expect(chs[0].subarray(after).every((x) => x === 0.5)).toBe(true);
    // …but not at once: no step back up to full gain.
    expect(chs[0][SR / 2 + 1]).toBeLessThan(0.5);
  });

  it("splitting a signal into two windows gives the same output as one", () => {
    const chs = sum(sineLookup(150, 0.5), 1);
    const whole = chs.map((c) => c.slice());
    new Limiter().process(whole);
    const lim = new Limiter();
    const cut = 20011;
    const first = chs.map((c) => c.slice(0, cut));
    const second = chs.map((c) => c.slice(cut));
    lim.process(first);
    lim.process(second);
    for (let c = 0; c < 2; c++) {
      expect(first[c]).toEqual(whole[c].subarray(0, cut));
      expect(second[c]).toEqual(whole[c].subarray(cut));
    }
  });

  it("reset forgets the gain", () => {
    const lim = new Limiter();
    lim.process([new Float32Array(10).fill(2), new Float32Array(10).fill(2)]);
    lim.reset();
    const quiet = [new Float32Array(10).fill(0.5), new Float32Array(10).fill(0.5)];
    lim.process(quiet);
    expect(quiet[0][0]).toBe(0.5);
  });
});
