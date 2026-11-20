// Noise reduction for the audio editor (a spectral gate, the way Audacity does it).
// Line-by-line port of src/umc/audioedit/noise.py; both check tests/fixtures/audioedit_noise_cases.json.
// 1. learn() a noise profile from a part with only background noise: the mean power of every
//    frequency band (all channels together).
// 2. reduce() another part: in each short block (SIZE frames, HOP apart, Hann window), bands
//    that aren't louder than the noise (+ sensitivity dB) are turned down by reduceDb. A
//    turned-down band comes back at once but fades down over RELEASE seconds, and each gain is
//    averaged with `smoothing` neighbouring bands. Everything is computed in float64.

export const SIZE = 2048;
export const HOP = SIZE / 4;
export const BANDS = SIZE / 2 + 1;
export const RELEASE = 0.1;
export const CHUNK = 256;
export const LEARN_MAX_SECONDS = 30;

export const REDUCE_MIN = 1, REDUCE_MAX = 48, REDUCE_DEFAULT = 12;
export const SENSITIVITY_MIN = 0, SENSITIVITY_MAX = 24, SENSITIVITY_DEFAULT = 6;
export const SMOOTHING_MIN = 0, SMOOTHING_MAX = 12, SMOOTHING_DEFAULT = 3;

export const MIN_NOISE_FRAMES = SIZE * 2;
export const NO_PROFILE = "Select a part with only background noise first and press “Learn noise”.";

/** The refusal for a too short noise part, naming the real minimum (rounded up to 0.1 s). */
export function noiseTooShort(rate: number): string {
  const tenths = Math.max(1, Math.ceil((MIN_NOISE_FRAMES * 10) / rate));
  return `Select a longer part of only noise (at least ${Math.floor(tenths / 10)}.${tenths % 10} seconds).`;
}

export class Cancelled extends Error {}

/** Periodic Hann window. */
export function window(): Float64Array {
  const w = new Float64Array(SIZE);
  for (let n = 0; n < SIZE; n++) w[n] = 0.5 - 0.5 * Math.cos((2 * Math.PI * n) / SIZE);
  return w;
}

// ---------------------------------------------------------------- FFT (radix 2, SIZE points)
const BITS = Math.log2(SIZE);
const REV = new Uint32Array(SIZE);
for (let i = 0; i < SIZE; i++) {
  let r = 0;
  for (let b = 0; b < BITS; b++) r |= ((i >> b) & 1) << (BITS - 1 - b);
  REV[i] = r;
}
const COS = new Float64Array(SIZE / 2);
const SIN = new Float64Array(SIZE / 2);
for (let i = 0; i < SIZE / 2; i++) {
  COS[i] = Math.cos((2 * Math.PI * i) / SIZE);
  SIN[i] = Math.sin((2 * Math.PI * i) / SIZE);
}

/** In-place complex FFT of (re, im); `inverse` without the 1/N. */
export function fft(re: Float64Array, im: Float64Array, inverse: boolean): void {
  for (let i = 0; i < SIZE; i++) {
    const j = REV[i];
    if (j > i) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  const sign = inverse ? 1 : -1;
  for (let len = 2; len <= SIZE; len <<= 1) {
    const half = len >> 1;
    const step = SIZE / len;
    for (let start = 0; start < SIZE; start += len) {
      for (let k = 0; k < half; k++) {
        const wr = COS[k * step];
        const wi = sign * SIN[k * step];
        const a = start + k;
        const b = a + half;
        const xr = re[b] * wr - im[b] * wi;
        const xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr; im[b] = im[a] - xi;
        re[a] += xr; im[a] += xi;
      }
    }
  }
}

/** The mean power per band of `samples` (channels of frames): the noise profile. */
export function learn(samples: ArrayLike<number>[], rate: number): Float64Array {
  const frames = Math.min(samples[0]?.length ?? 0, Math.floor(LEARN_MAX_SECONDS * rate));
  if (frames < MIN_NOISE_FRAMES) throw new Error(noiseTooShort(rate));
  const total = new Float64Array(BANDS);
  const win = window();
  const re = new Float64Array(SIZE);
  const im = new Float64Array(SIZE);
  let count = 0;
  for (const ch of samples) {
    const n = Math.floor((frames - SIZE) / HOP) + 1; // whole blocks inside the sample only
    for (let b = 0; b < n; b++) {
      for (let i = 0; i < SIZE; i++) { re[i] = ch[b * HOP + i] * win[i]; im[i] = 0; }
      fft(re, im, false);
      for (let k = 0; k < BANDS; k++) total[k] += re[k] * re[k] + im[k] * im[k];
    }
    count += n;
  }
  for (let k = 0; k < BANDS; k++) total[k] /= count;
  return total;
}

/** Each gain averaged with `width` bands on each side (fewer at the ends); in place via `out`. */
function smooth(gains: Float64Array, width: number, out: Float64Array): Float64Array {
  if (width <= 0) return gains;
  const c = new Float64Array(BANDS + 1);
  for (let k = 0; k < BANDS; k++) c[k + 1] = c[k] + gains[k];
  for (let k = 0; k < BANDS; k++) {
    const lo = Math.max(0, k - width);
    const hi = Math.min(BANDS, k + width + 1);
    out[k] = (c[hi] - c[lo]) / (hi - lo);
  }
  return out;
}

export interface ReduceOptions {
  reduceDb?: number;
  sensitivityDb?: number;
  smoothing?: number;
  /** Called between chunks; return true to stop (throws Cancelled). */
  cancelled?: () => boolean;
  progress?: (fraction: number) => void;
}

/** `samples` with the noise in `profile` turned down. */
export function reduce(samples: ArrayLike<number>[], rate: number, profile: ArrayLike<number>, o: ReduceOptions = {}): Float32Array[] {
  const pick = (v: number | undefined, fallback: number) => (v !== undefined && Number.isFinite(v) ? v : fallback);
  const reduceDb = Math.max(REDUCE_MIN, Math.min(REDUCE_MAX, pick(o.reduceDb, REDUCE_DEFAULT)));
  const sensitivityDb = Math.max(SENSITIVITY_MIN, Math.min(SENSITIVITY_MAX, pick(o.sensitivityDb, SENSITIVITY_DEFAULT)));
  const smoothing = Math.trunc(Math.max(SMOOTHING_MIN, Math.min(SMOOTHING_MAX, pick(o.smoothing, SMOOTHING_DEFAULT))));
  const floor = 10 ** (-reduceDb / 20);
  const scale = 10 ** (sensitivityDb / 10);
  const threshold = Float64Array.from(profile, (p) => p * scale);
  const fall = floor ** (HOP / (RELEASE * rate));
  const win = window();
  const frames = samples[0]?.length ?? 0;
  const count = Math.floor((frames + SIZE) / HOP) + 1;
  const total = (count - 1) * HOP + SIZE;
  const totalSteps = samples.length * Math.ceil(count / CHUNK);
  const re = new Float64Array(SIZE);
  const im = new Float64Array(SIZE);
  const gains = new Float64Array(BANDS);
  const smoothed = new Float64Array(BANDS);
  let steps = 0;
  return samples.map((ch) => {
    const x = new Float64Array(total);
    for (let i = 0; i < frames; i++) x[SIZE + i] = ch[i];
    const acc = new Float64Array(total);
    const held = new Float64Array(BANDS).fill(floor);
    for (let b = 0; b < count; b++) {
      if (b % CHUNK === 0) {
        if (o.cancelled?.()) throw new Cancelled("Cancelled.");
      }
      const at = b * HOP;
      for (let i = 0; i < SIZE; i++) { re[i] = x[at + i] * win[i]; im[i] = 0; }
      fft(re, im, false);
      for (let k = 0; k < BANDS; k++) {
        const raw = re[k] * re[k] + im[k] * im[k] > threshold[k] ? 1 : floor;
        held[k] = Math.max(raw, Math.max(held[k] * fall, floor));
        gains[k] = held[k];
      }
      const g = smooth(gains, smoothing, smoothed);
      for (let k = 0; k < BANDS; k++) { re[k] *= g[k]; im[k] *= g[k]; }
      for (let k = 1; k < SIZE / 2; k++) { re[SIZE - k] = re[k]; im[SIZE - k] = -im[k]; }
      im[0] = 0; im[SIZE / 2] = 0; // like numpy's irfft
      fft(re, im, true);
      for (let i = 0; i < SIZE; i++) acc[at + i] += (re[i] / SIZE) * win[i];
      if ((b + 1) % CHUNK === 0 || b === count - 1) o.progress?.(++steps / totalSteps);
    }
    const out = new Float32Array(frames);
    for (let i = 0; i < frames; i++) out[i] = acc[SIZE + i] / 1.5; // Hann² at 75 % overlap sums to 1.5
    return out;
  });
}
