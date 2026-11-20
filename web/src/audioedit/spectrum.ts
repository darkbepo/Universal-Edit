// The spectrogram view: how loud each frequency is, per screen column. Line-by-line port of
// src/umc/audioedit/spectrum.py; both check tests/fixtures/audioedit_spectrum_cases.json.
// Column i is one Hann-windowed block of noise.SIZE frames centred on the column's middle
// (zeros past either end of the sound). Its bands are grouped into `rows` rows on a log
// frequency scale from F_MIN to half the sample rate (the loudest band counts) and turned into a
// level 0…1: FLOOR_DB dB (or quieter) … 0 dB, where 0 dB is a full-scale sine. Row 0 is the
// lowest frequency (the bottom of the lane).
import { BANDS, SIZE, fft, window } from "./noise";

export const F_MIN = 30;
export const FLOOR_DB = -100;
export const MAX_ROWS = 512;
export const MAX_COLUMNS = 800; // wider views compute one column per columnStep pixels and stretch it
export const LABELS = [100, 1000, 10000]; // Hz marks drawn at the lane's edge
// colour ramp: quiet → loud (dark blue, purple, red, orange, pale yellow)
export const RAMP: readonly (readonly [number, number, number])[] = [[12, 7, 48], [88, 24, 128], [190, 45, 92], [246, 128, 32], [252, 238, 160]];

const clampRows = (rows: number) => Math.max(1, Math.min(MAX_ROWS, Math.floor(rows)));

/** First and end band of every row: row r reads bands [first[r], end[r]). Low rows can share
 *  one band (there are few bands down there), so the frequency scale stays right. */
export function rowBands(rate: number, rowsIn: number): { first: Int32Array; end: Int32Array } {
  const rows = clampRows(rowsIn);
  const top = rate / 2;
  const lo = Math.min(F_MIN, top / 2);
  const edges = new Int32Array(rows + 1);
  for (let r = 0; r <= rows; r++) edges[r] = Math.min(Math.floor((lo * (top / lo) ** (r / rows) * SIZE) / rate), BANDS - 1);
  const first = edges.slice(0, rows);
  const end = new Int32Array(rows);
  for (let r = 0; r < rows; r++) end[r] = Math.max(edges[r + 1], first[r] + 1);
  end[rows - 1] = BANDS;
  return { first, end };
}

/** Screen pixels per computed column, so at most MAX_COLUMNS are computed. */
export function columnStep(width: number): number {
  return Math.max(1, Math.ceil(width / MAX_COLUMNS));
}

/** Where `hz` sits on the row scale (0 = bottom edge, rows = top edge). */
export function rowOf(hz: number, rate: number, rows: number): number {
  const top = rate / 2;
  const lo = Math.min(F_MIN, top / 2);
  return (rows * Math.log(Math.max(hz, lo) / lo)) / Math.log(top / lo);
}

/** Levels 0…1 per channel, column after column (`rows` values each; index col * rows + row).
 *  k ≤ width columns: those past the end are left out. */
export function columns(samples: ArrayLike<number>[], rate: number, start: number, framesPerPx: number, width: number, rowsIn: number): { k: number; rows: number; levels: Float32Array[] } {
  const rows = clampRows(rowsIn);
  const n = samples[0]?.length ?? 0;
  if (width <= 0 || n === 0 || start >= n) return { k: 0, rows, levels: samples.map(() => new Float32Array(0)) };
  let k = 0;
  while (k < width && Math.floor(start + k * framesPerPx) < n) k++;
  const win = window();
  let sum = 0;
  for (let i = 0; i < SIZE; i++) sum += win[i];
  const scale = 2 / sum; // a full-scale sine → magnitude 1
  const { first, end } = rowBands(rate, rows);
  const re = new Float64Array(SIZE);
  const im = new Float64Array(SIZE);
  const mags = [new Float64Array(BANDS), new Float64Array(BANDS)];
  const levels = samples.map(() => new Float32Array(k * rows));
  const toLevel = (out: Float32Array, at: number, mag: Float64Array) => {
    for (let r = 0; r < rows; r++) {
      let peak = 0;
      for (let b = first[r]; b < end[r]; b++) if (mag[b] > peak) peak = mag[b];
      const db = 20 * Math.log10(Math.max(peak, 1e-12));
      out[at + r] = Math.max(0, Math.min(1, (db - FLOOR_DB) / -FLOOR_DB));
    }
  };
  // two channels per FFT: one as the real part, the other as the imaginary part
  for (let c = 0; c < samples.length; c += 2) {
    const a = samples[c], b = samples[c + 1];
    for (let col = 0; col < k; col++) {
      const at = Math.floor(start + (col + 0.5) * framesPerPx) - SIZE / 2;
      for (let i = 0; i < SIZE; i++) {
        const j = at + i;
        const inside = j >= 0 && j < n;
        re[i] = inside ? a[j] * win[i] : 0;
        im[i] = inside && b ? b[j] * win[i] : 0;
      }
      fft(re, im, false);
      for (let q = 0; q < BANDS; q++) {
        const p = (SIZE - q) % SIZE;
        const ar = (re[q] + re[p]) / 2, ai = (im[q] - im[p]) / 2; // X_a = (Z[q] + conj Z[-q]) / 2
        mags[0][q] = Math.sqrt(ar * ar + ai * ai) * scale;
        if (b) {
          const br = (im[q] + im[p]) / 2, bi = (re[p] - re[q]) / 2; // X_b = (Z[q] − conj Z[-q]) / 2i
          mags[1][q] = Math.sqrt(br * br + bi * bi) * scale;
        }
      }
      toLevel(levels[c], col * rows, mags[0]);
      if (b) toLevel(levels[c + 1], col * rows, mags[1]);
    }
  }
  return { k, rows, levels };
}

/** 256 colours along RAMP (index = round(level × 255)), for drawing quickly. */
export function colourTable(): Uint8ClampedArray {
  const t = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) t.set(colour(i / 255), i * 3);
  return t;
}

/** RGB for a level 0…1, along RAMP. */
export function colour(level: number): [number, number, number] {
  const t = Math.max(0, Math.min(1, level)) * (RAMP.length - 1);
  const i = Math.min(Math.floor(t), RAMP.length - 2);
  const f = t - i;
  const a = RAMP[i], b = RAMP[i + 1];
  return [Math.round(a[0] * (1 - f) + b[0] * f), Math.round(a[1] * (1 - f) + b[1] * f), Math.round(a[2] * (1 - f) + b[2] * f)];
}
