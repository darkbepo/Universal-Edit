import { describe, expect, it } from "vitest";
import data from "../../tests/fixtures/audioedit_spectrum_cases.json";
import * as spectrum from "../src/audioedit/spectrum";

/** Same as signal() in scripts/make_spectrum_cases.py and the desktop test. */
function signal(): Float64Array[] {
  const out = [new Float64Array(data.frames), new Float64Array(data.frames)];
  let state = 3;
  for (let i = 0; i < data.frames; i++) {
    [1000, 2500].forEach((hz, c) => {
      state = (state * 48271) % 2147483647;
      out[c][i] = 0.5 * Math.sin((2 * Math.PI * hz * i) / data.rate) + (state / 2147483647 - 0.5) * 0.01;
    });
  }
  return out;
}

describe("spectrogram (shared cases with the desktop app)", () => {
  const x = signal();
  for (const c of data.cases) {
    it(`columns from ${c.start}, ${c.fpp} frames per pixel, ${c.channels} channel(s)`, () => {
      const r = spectrum.columns(x.slice(0, c.channels), data.rate, c.start, c.fpp, c.width, c.rows);
      expect(r.k).toBe(c.k);
      expect(r.rows).toBe(c.rows);
      c.levels.forEach((want, ch) => want.forEach((v, i) => expect(Math.abs(r.levels[ch][i] - v)).toBeLessThan(1e-5)));
    });
  }

  it("groups bands into the same rows and colours", () => {
    for (const b of data.row_bands) {
      const { first, end } = spectrum.rowBands(b.rate, b.rows);
      expect(Array.from(first)).toEqual(b.first);
      expect(Array.from(end)).toEqual(b.end);
    }
    data.colours.levels.forEach((l, i) => expect(spectrum.colour(l)).toEqual(data.colours.rgb[i]));
  });

  it("puts a tone on its row at full level", () => {
    const rate = 44100, rows = 200;
    const tone = Float32Array.from({ length: rate }, (_, i) => Math.sin((2 * Math.PI * 1000 * i) / rate));
    const r = spectrum.columns([tone], rate, 0, rate / 100, 100, rows);
    const col = r.levels[0].subarray(50 * rows, 51 * rows);
    let best = 0;
    for (let i = 1; i < rows; i++) if (col[i] > col[best]) best = i;
    expect(best).toBe(Math.floor(spectrum.rowOf(1000, rate, rows)));
    expect(col[best]).toBeCloseTo(1, 1);
  });

  it("caps the computed columns", () => {
    expect([800, 801, 1920].map(spectrum.columnStep)).toEqual([1, 2, 3]);
  });

  it("stops at the end of the sound", () => {
    const quiet = [new Float32Array(1000)];
    expect(spectrum.columns(quiet, 8000, 2000, 1, 50, 10).k).toBe(0);
    expect(spectrum.columns(quiet, 8000, 900, 10, 50, 10).k).toBe(10);
    expect(Math.max(...spectrum.columns(quiet, 8000, 0, 1, 50, 10).levels[0])).toBe(0);
  });
});
