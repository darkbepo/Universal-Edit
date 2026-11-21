// Decoded sound → the planar stereo 48 kHz the preview mixer works in. Pure maths, no DOM.
import { SAMPLE_RATE } from "./mix";

/** Mono is copied to both sides, more than two channels keep the first two; linear interpolation to 48 kHz. */
export function toStereo48k(chs: Float32Array[], rate: number): Float32Array[] {
  const left = chs[0] ?? new Float32Array(0);
  const right = chs.length > 1 ? chs[1] : left;
  if (rate === SAMPLE_RATE) return [left.slice(), right.slice()];
  return [resample(left, rate), right === left ? resample(left, rate) : resample(right, rate)];
}

/** `src` at `from` Hz → `to` Hz (round(n · to / from) frames), linear interpolation. */
export function resample(src: Float32Array, from: number, to = SAMPLE_RATE): Float32Array {
  const n = src.length;
  const out = new Float32Array(Math.round((n * to) / from));
  const step = from / to;
  for (let i = 0; i < out.length; i++) {
    const pos = i * step;
    const i0 = Math.min(n - 1, Math.floor(pos));
    const i1 = Math.min(n - 1, i0 + 1);
    const f = pos - i0;
    out[i] = src[i0] + (src[i1] - src[i0]) * f;
  }
  return out;
}
