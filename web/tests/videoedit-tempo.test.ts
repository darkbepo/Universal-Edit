// Speed changes that keep the pitch (desktop: FFmpeg atempo; website: our own WSOLA in tempo.ts).
import { describe, expect, it } from "vitest";
import { stretch } from "../src/videoedit/tempo";

const RATE = 48000;
const sine = (freq: number, seconds: number, amp = 0.5) =>
  Float32Array.from({ length: Math.round(seconds * RATE) }, (_, i) => amp * Math.sin((2 * Math.PI * freq * i) / RATE));

const rms = (a: Float32Array, from = 0, to = a.length) => {
  let s = 0;
  for (let i = from; i < to; i++) s += a[i] * a[i];
  return Math.sqrt(s / Math.max(1, to - from));
};

/** Frequency from the zero crossings over the middle 80 %. */
function frequency(a: Float32Array): number {
  const from = Math.floor(a.length * 0.1);
  const to = Math.floor(a.length * 0.9);
  let crossings = 0;
  for (let i = from + 1; i < to; i++) if ((a[i - 1] < 0) !== (a[i] < 0)) crossings++;
  return crossings / 2 / ((to - from) / RATE);
}

describe("stretch", () => {
  for (const speed of [0.25, 0.5, 2, 4]) {
    it(`keeps a 440 Hz tone at ${speed}×`, () => {
      const input = sine(440, 2);
      const [out] = stretch([input], speed);
      expect(out.length).toBe(Math.round(input.length / speed));
      expect(out.every((v) => Number.isFinite(v))).toBe(true);
      expect(Math.abs(frequency(out) - 440) / 440).toBeLessThan(0.02);
      const ratio = rms(out, Math.floor(out.length * 0.1), Math.floor(out.length * 0.9)) / rms(input);
      expect(ratio).toBeGreaterThan(0.8);
      expect(ratio).toBeLessThan(1.2);
    });
  }

  it("gives exactly the asked number of frames", () => {
    const input = sine(440, 1);
    expect(stretch([input], 2, 24000)[0].length).toBe(24000);
    expect(stretch([input], 2, 30000)[0].length).toBe(30000);
    expect(stretch([input], 0.5, 1000)[0].length).toBe(1000);
  });

  it("starts at full strength when cut from the middle of a longer sound", () => {
    const whole = sine(440, 3);
    const window = whole.subarray(RATE, RATE + 48000);
    const [out] = stretch([window], 2, 24000);
    // the first 5 ms already carry the tone, not a fade from silence
    expect(rms(out, 0, 240) / rms(window)).toBeGreaterThan(0.8);
    expect(Math.abs(out[0] - window[0])).toBeLessThan(1e-6);
  });

  it("speed 1 is a copy", () => {
    const input = sine(300, 0.5);
    const [out] = stretch([input], 1);
    expect(out).not.toBe(input);
    expect(Array.from(out)).toEqual(Array.from(input));
    expect(stretch([input], 1, 10)[0].length).toBe(10);
    const padded = stretch([input], 1, input.length + 5)[0];
    expect(padded.length).toBe(input.length + 5);
    expect(padded[input.length + 2]).toBe(0);
  });

  it("tiny input gives the right length without throwing", () => {
    const input = sine(440, 100 / RATE);
    expect(input.length).toBe(100);
    for (const speed of [0.25, 0.5, 2, 4]) {
      const [out] = stretch([input], speed);
      expect(out.length).toBe(Math.round(100 / speed));
      expect(out.every((v) => Number.isFinite(v))).toBe(true);
    }
    expect(stretch([new Float32Array(0)], 2, 7)[0].length).toBe(7);
    expect(stretch([input], 2, 0)[0].length).toBe(0);
  });

  it("keeps two channels independent", () => {
    const left = sine(440, 1);
    const right = new Float32Array(left.length);
    for (const speed of [0.5, 2]) {
      const [l, r] = stretch([left, right], speed);
      expect(r.length).toBe(l.length);
      expect(r.every((v) => v === 0)).toBe(true);
      expect(rms(l)).toBeGreaterThan(0.3);
    }
  });

  it("is fast enough for playback (1 s of stereo at 2×)", () => {
    const left = sine(440, 1);
    const right = sine(660, 1);
    const started = performance.now();
    stretch([left, right], 2);
    stretch([left, right], 0.5);
    expect(performance.now() - started).toBeLessThan(500);
  });
});
