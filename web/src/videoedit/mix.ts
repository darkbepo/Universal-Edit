// The sound of one time window of a video project, mixed in our own code: the website's preview
// plays ~0.5 s windows through Web Audio, export mixes 1 s windows. The desktop renders the same
// with FFmpeg's audio graph (src/umc/videoedit/render.py: `_audio_chain`, `_music_chain`,
// acrossfade c1=tri:c2=tri / concat, amix normalize=0, alimiter limit=0.95). Pure maths, no DOM.
//
// Timing is in samples, not output frames (FFmpeg's audio filters work in samples), measured from
// the item spans `layout()` gives — the same spans the picture side uses. A window is addressed by
// its first sample (`round(start · 48000)`) and every position inside it is worked out from that
// integer, so a span mixed as two windows gives the same samples as one window.

import { stretch } from "./tempo";
import { clipDuration, layout, length, sourceOf, type AudioClip, type Project } from "./project";

export const SAMPLE_RATE = 48000;
/** Planar stereo 48 kHz for source seconds [from, from + frames / 48000); null = not decoded (yet) → silence. */
export type AudioLookup = (sourceId: string, from: number, frames: number) => Float32Array[] | null;

const SR = SAMPLE_RATE;
/** Sample positions computed from seconds can land a hair past a whole sample (1 · 48000 · (1 + ε)). */
const EPS = 1e-6;
const firstSample = (s: number): number => Math.ceil(s - EPS);

/** Linear ramps, as reciprocal lengths in samples (0 = none), multiplied together. */
interface Ramps {
  volume: number;
  /** Rising from the item's start (its own fade in, and the transition into it). */
  up1: number;
  up2: number;
  /** Falling to the item's end (its own fade out, and the transition into the next item). */
  down1: number;
  down2: number;
}

const rate = (seconds: number): number => (seconds > 0 ? 1 / (seconds * SR) : 0);

/**
 * Adds `n` samples of `chs`, shaped by `r`, to `out` from index `at`. `pos` is the first sample's
 * place in the item (samples since the item's start, may be fractional), `dur` the item's length
 * in samples.
 */
function accumulate(out: Float32Array[], at: number, chs: Float32Array[], n: number, pos: number, dur: number, r: Ramps): void {
  if (chs.length === 0) return;
  const left = chs[0];
  const right = chs.length > 1 ? chs[1] : chs[0];
  const count = Math.min(n, left.length, right.length);
  const outL = out[0];
  const outR = out[1];
  const { volume, up1, up2, down1, down2 } = r;
  const plain = up1 === 0 && up2 === 0 && down1 === 0 && down2 === 0;
  for (let i = 0; i < count; i++) {
    let g = volume;
    if (!plain) {
      const u = pos + i;
      const d = dur - u;
      if (up1 !== 0) g *= Math.min(1, u * up1);
      if (up2 !== 0) g *= Math.min(1, u * up2);
      if (down1 !== 0) g *= Math.min(1, d * down1);
      if (down2 !== 0) g *= Math.min(1, d * down2);
      if (g < 0) g = 0;
    }
    outL[at + i] += left[i] * g;
    outR[at + i] += right[i] * g;
  }
}

/** The main row: video clips with sound, joined by crossfades (transitions) or one after another. */
function mixMain(p: Project, out: Float32Array[], s0: number, s1: number, audio: AudioLookup): void {
  const spans = layout(p);
  p.main.forEach((item, i) => {
    if (item.type !== "video" || item.mute || !sourceOf(p, item.source).has_audio) return;
    const span = spans.get(item.id)!;
    const aS = span.start * SR;
    const g0 = Math.max(s0, firstSample(aS));
    const g1 = Math.min(s1, firstSample(span.end * SR));
    if (g1 <= g0) return;
    const n = g1 - g0;
    const next = p.main[i + 1];
    const trIn = i > 0 && item.transition_in.kind !== "none" ? item.transition_in.duration : 0;
    const trOut = next && next.transition_in.kind !== "none" ? next.transition_in.duration : 0;
    const ramps: Ramps = {
      volume: item.volume / 100,
      up1: rate(item.fade_in),
      up2: rate(trIn),
      down1: rate(item.fade_out),
      down2: rate(trOut),
    };
    let chs: Float32Array[] | null;
    if (item.speed === 1) {
      chs = audio(item.source, item.in_ + (g0 - aS) / SR, n);
    } else {
      // Whole source samples, so the next window asks for exactly the following range.
      const from = Math.round((g0 - aS) * item.speed);
      const to = Math.round((g1 - aS) * item.speed);
      const raw = audio(item.source, item.in_ + from / SR, to - from);
      chs = raw && stretch(raw, item.speed, n);
    }
    if (chs) accumulate(out, g0 - s0, chs, n, g0 - aS, clipDuration(item) * SR, ramps);
  });
}

/** One music clip: starts at `start` (before 0 = part-way into it), loops until `loop_until`. */
function mixMusic(clip: AudioClip, out: Float32Array[], s0: number, s1: number, audio: AudioLookup): void {
  const startS = clip.start * SR;
  const durS = clipDuration(clip) * SR;
  const g0 = Math.max(s0, firstSample(startS));
  const g1 = Math.min(s1, firstSample(startS + durS));
  if (g1 <= g0) return;
  const ramps: Ramps = { volume: clip.volume / 100, up1: rate(clip.fade_in), up2: 0, down1: rate(clip.fade_out), down2: 0 };
  // The desktop repeats round((out − in_) · 48000) samples (aloop).
  const period = Math.round((clip.out - clip.in_) * SR);
  if (clip.loop_until <= 0 || period <= 0) {
    const chs = audio(clip.source, clip.in_ + (g0 - startS) / SR, g1 - g0);
    if (chs) accumulate(out, g0 - s0, chs, g1 - g0, g0 - startS, durS, ramps);
    return;
  }
  for (let g = g0; g < g1; ) {
    const pos = g - startS;
    const k = pos - Math.floor(pos / period) * period;
    const run = Math.min(g1 - g, Math.max(1, Math.ceil(period - k - EPS)));
    const chs = audio(clip.source, clip.in_ + k / SR, run);
    if (chs) accumulate(out, g - s0, chs, run, pos, durS, ramps);
    g += run;
  }
}

/**
 * Stereo 48 kHz samples of timeline seconds [start, start + frames / 48000): the main row's sound
 * plus every music clip, summed and **not** limited (run one `Limiter` across consecutive windows).
 */
export function mixWindow(p: Project, start: number, frames: number, audio: AudioLookup): Float32Array[] {
  const out = [new Float32Array(frames), new Float32Array(frames)];
  const s0 = Math.round(start * SR);
  const s1 = Math.min(s0 + frames, firstSample(length(p) * SR));
  if (s1 <= s0) return out;
  mixMain(p, out, s0, s1, audio);
  for (const row of p.music_rows) for (const clip of row) mixMusic(clip, out, s0, s1, audio);
  return out;
}

const RELEASE_SECONDS = 0.05;

/**
 * Keeps |x| ≤ limit (instant attack, one gain for both channels) and lets the gain back up to 1
 * over at most 50 ms. Sound that never needs it passes through untouched. Its state carries over
 * from one `process` call to the next, so a signal split into windows comes out the same as whole.
 */
export class Limiter {
  private gain = 1;
  private readonly step = 1 / (RELEASE_SECONDS * SR);

  constructor(private readonly limit = 0.95) {}

  process(chs: Float32Array[]): void {
    if (chs.length === 0) return;
    const n = chs[0].length;
    const limit = this.limit;
    const step = this.step;
    let g = this.gain;
    for (let i = 0; i < n; i++) {
      let peak = 0;
      for (let c = 0; c < chs.length; c++) {
        const x = Math.abs(chs[c][i]);
        if (x > peak) peak = x;
      }
      const target = peak > limit ? limit / peak : 1;
      if (target < g) g = target;
      else if (g < 1) g = Math.min(target, g + step);
      if (g !== 1) for (let c = 0; c < chs.length; c++) chs[c][i] *= g;
    }
    this.gain = g;
  }

  reset(): void {
    this.gain = 1;
  }
}
