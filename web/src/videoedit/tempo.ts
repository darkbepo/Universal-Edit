// Speed changes that keep the pitch: a WSOLA time stretch on planar Float32 audio at 48 kHz.
// The desktop uses FFmpeg's `atempo` (src/umc/videoedit/render.py); the website's preview and
// export mix sound in our own code, one window at a time (mix.ts asks for `frames · speed` source
// frames and wants exactly `frames` back). Pure maths, no DOM.
//
// Frames of FRAME samples (40 ms) are laid down every HOP (50 % overlap) in the output, each read
// from about `HOP · speed` further on in the input, shifted by up to ±SEARCH so it lines up with
// how the previous frame would have carried on (cross-correlation, coarse on a decimated signal,
// then refined). The output is divided by the summed window, so the first and last half frame are
// the input itself — a window cut from the middle of a longer sound starts at full strength.

const FRAME = 1920; // 40 ms at 48 kHz
const HOP = FRAME / 2;
const SEARCH = 480; // ±10 ms
const COARSE_STEP = 8; // shift step of the coarse search…
const COARSE_DECIMATE = 4; // …comparing every 4th sample
const FINE_DECIMATE = 2;

/** Hann window shifted by half a sample: never 0 at the ends, and two at 50 % overlap sum to 1. */
const WINDOW = Float32Array.from({ length: FRAME }, (_, n) => 0.5 - 0.5 * Math.cos((2 * Math.PI * (n + 0.5)) / FRAME));

function fitted(a: Float32Array, frames: number): Float32Array {
  const out = new Float32Array(frames);
  out.set(a.length > frames ? a.subarray(0, frames) : a);
  return out;
}

/** Linear resampling, for inputs shorter than one frame (the pitch moves, but only for a few ms). */
function resample(a: Float32Array, frames: number): Float32Array {
  const out = new Float32Array(frames);
  if (a.length === 0 || frames === 0) return out;
  if (a.length === 1 || frames === 1) return out.fill(a[0]);
  const step = (a.length - 1) / (frames - 1);
  for (let i = 0; i < frames; i++) {
    const x = i * step;
    const j = Math.min(a.length - 2, Math.floor(x));
    const f = x - j;
    out[i] = a[j] + (a[j + 1] - a[j]) * f;
  }
  return out;
}

/** How well x[at…] matches x[target…] over the overlap, normalised by the candidate's energy. */
function score(x: Float32Array, at: number, target: number, step: number): number {
  let dot = 0;
  let energy = 0;
  for (let i = 0; i < HOP; i += step) {
    const v = x[at + i];
    dot += v * x[target + i];
    energy += v * v;
  }
  return energy > 0 ? dot / Math.sqrt(energy) : 0;
}

/** The input position in [lo, hi] that best continues from `target`. */
function bestShift(x: Float32Array, lo: number, hi: number, target: number): number {
  let best = lo;
  let bestScore = -Infinity;
  for (let at = lo; at <= hi; at += COARSE_STEP) {
    const s = score(x, at, target, COARSE_DECIMATE);
    if (s > bestScore) [best, bestScore] = [at, s];
  }
  const from = Math.max(lo, best - COARSE_STEP + 1);
  const to = Math.min(hi, best + COARSE_STEP - 1);
  bestScore = -Infinity;
  for (let at = from; at <= to; at++) {
    const s = score(x, at, target, FINE_DECIMATE);
    if (s > bestScore) [best, bestScore] = [at, s];
  }
  return best;
}

/**
 * Play `input` (one Float32Array per channel, 48 kHz) `speed` times as fast without changing its
 * pitch. The result has exactly `frames ?? round(length / speed)` samples per channel (cut or
 * padded with silence). Speed 1 returns copies.
 */
export function stretch(input: Float32Array[], speed: number, frames?: number): Float32Array[] {
  const length = input.length ? input[0].length : 0;
  const total = Math.max(0, Math.round(frames ?? length / speed));
  if (speed === 1) return input.map((a) => fitted(a, total));
  if (length < FRAME) return input.map((a) => resample(a, total));
  const out = input.map(() => new Float32Array(total));
  if (total === 0) return out;

  // The search compares the channels' sum (one array, made once).
  let mono = input[0];
  if (input.length > 1) {
    mono = new Float32Array(length);
    for (const a of input) for (let i = 0; i < length; i++) mono[i] += a[i];
  }
  const weights = new Float32Array(total);
  const last = length - FRAME;
  let prev = 0;
  for (let k = 0, start = 0; start < total; k++, start += HOP) {
    let at = 0;
    if (k > 0) {
      const nominal = Math.min(last, Math.max(0, Math.round(start * speed)));
      const target = Math.min(last, prev + HOP);
      at = bestShift(mono, Math.max(0, nominal - SEARCH), Math.min(last, nominal + SEARCH), target);
    }
    const n = Math.min(FRAME, total - start);
    for (let c = 0; c < input.length; c++) {
      const src = input[c];
      const dst = out[c];
      for (let i = 0; i < n; i++) dst[start + i] += src[at + i] * WINDOW[i];
    }
    for (let i = 0; i < n; i++) weights[start + i] += WINDOW[i];
    prev = at;
  }
  for (const dst of out) for (let i = 0; i < total; i++) dst[i] /= weights[i];
  return out;
}
