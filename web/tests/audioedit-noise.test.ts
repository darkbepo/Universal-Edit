import { describe, expect, it } from "vitest";
import data from "../../tests/fixtures/audioedit_noise_cases.json";
import * as noise from "../src/audioedit/noise";

describe("noise reduction (shared cases with the desktop app)", () => {
  const profile = noise.learn(data.noise, data.rate);

  it("learns the same profile", () => {
    data.profile_bands.forEach((want, i) => expect(profile[i * 64]).toBeCloseTo(want, 10));
  });

  for (const c of data.cases) {
    it(`reduces by ${c.reduce_db} dB, sensitivity ${c.sensitivity_db}, smoothing ${c.smoothing}`, () => {
      const out = noise.reduce(data.signal, data.rate, profile, { reduceDb: c.reduce_db, sensitivityDb: c.sensitivity_db, smoothing: c.smoothing });
      c.picks.forEach((want, ch) => want.forEach((v, j) => expect(Math.abs(out[ch][j * 37] - v)).toBeLessThan(1e-5)));
      c.rms.forEach((want, ch) => {
        const rms = Math.sqrt(out[ch].reduce((s, v) => s + v * v, 0) / out[ch].length);
        expect(rms).toBeCloseTo(want, 5);
      });
    });
  }

  it("matches the long case over several chunks (held gains carry over)", () => {
    const c = data.long;
    const x = new Float64Array(c.frames);
    let state = c.seed;
    for (let i = 0; i < c.frames; i++) {
      state = (state * 48271) % 2147483647;
      let v = (state / 2147483647 - 0.5) * 0.04;
      if (i >= Math.floor(c.frames / 2)) v += 0.3 * Math.sin((2 * Math.PI * 440 * i) / data.rate);
      x[i] = v;
    }
    const steps: number[] = [];
    const [out] = noise.reduce([x], data.rate, profile, { reduceDb: c.reduce_db, sensitivityDb: c.sensitivity_db, smoothing: c.smoothing, progress: (f) => steps.push(f) });
    c.picks.forEach((v, j) => expect(Math.abs(out[j * c.every] - v)).toBeLessThan(1e-5));
    expect(Math.sqrt(out.reduce((s, v) => s + v * v, 0) / out.length)).toBeCloseTo(c.rms, 5);
    expect(steps.length).toBe(c.progress_steps);
    expect(steps.at(-1)).toBeCloseTo(1, 10);
  });

  it("falls back to the defaults for values that aren't numbers", () => {
    const a = noise.reduce(data.signal, data.rate, profile, { reduceDb: Number.NaN, sensitivityDb: Number.NaN, smoothing: Number.NaN });
    const b = noise.reduce(data.signal, data.rate, profile);
    expect(Array.from(a[0])).toEqual(Array.from(b[0]));
  });

  it("names the real minimum when the noise part is too short", () => {
    expect(noise.noiseTooShort(44100)).toContain("0.1 seconds");
    expect(noise.noiseTooShort(8000)).toContain("0.6 seconds");
  });

  it("refuses too short a noise sample, and can be cancelled", () => {
    expect(() => noise.learn([new Float32Array(100)], 8000)).toThrow(noise.noiseTooShort(8000));
    expect(() => noise.reduce(data.signal, data.rate, profile, { cancelled: () => true })).toThrow(noise.Cancelled);
  });

  it("keeps a sound with no noise in it", () => {
    const quiet = new Float64Array(noise.BANDS); // profile of silence: everything is louder
    const x = [Float32Array.from({ length: 20000 }, (_, i) => 0.5 * Math.sin(i / 10))];
    const y = noise.reduce(x, 8000, quiet);
    let worst = 0;
    for (let i = 0; i < x[0].length; i++) worst = Math.max(worst, Math.abs(y[0][i] - x[0][i]));
    expect(worst).toBeLessThan(1e-6);
  });
});
