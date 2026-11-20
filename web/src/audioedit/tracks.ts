// Several tracks and their mix (port of src/umc/audioedit/tracks.py). Every track keeps its own
// AudioDoc (own undo history); all share the first track's sample rate. The mix is the audible
// tracks summed track by track: fitted to the output channels, times the volume, times the
// left / right balance (stereo output only). Products and sums are rounded to float32 exactly
// as numpy does, so both apps give the same samples.
import type { AudioDoc } from "./document";
import * as ops from "./ops";
import type { Samples } from "./ops";

export const MIN_GAIN_DB = -24;
export const MAX_GAIN_DB = 12;
export const LOUD = "The mix peaks at {db} — turn a track down, or it will sound distorted.";

export interface Track {
  doc: AudioDoc;
  name: string;
  gainDb: number;
  pan: number; // −1 = left only … 0 = centre … 1 = right only
  mute: boolean;
  solo: boolean;
}

export const track = (doc: AudioDoc, name: string): Track => ({ doc, name, gainDb: 0, pan: 0, mute: false, solo: false });

/** The tracks you hear: the soloed ones if any are, never the muted ones. */
export function audible(tracks: Track[]): Track[] {
  const soloed = tracks.some((t) => t.solo);
  return tracks.filter((t) => !t.mute && (t.solo || !soloed));
}

/** Balance law: centre plays both sides at full, so a centred track is as loud as alone. */
export function panGains(pan: number): [number, number] {
  pan = Math.max(-1, Math.min(1, pan));
  return [Math.min(1, 1 - pan), Math.min(1, 1 + pan)];
}

export const gain = (db: number) => 10 ** (db / 20);

export const outChannels = (tracks: Track[]) => Math.max(1, ...tracks.map((t) => t.doc.channels));

/** Frames of the mix: the longest audible track. */
export const length = (tracks: Track[]) => Math.max(0, ...audible(tracks).map((t) => t.doc.frames));

/** The audible tracks' frames [start, end) mixed. */
export function mix(tracks: Track[], start = 0, end: number | null = null): Samples {
  const channels = outChannels(tracks);
  const stop = end ?? length(tracks);
  const frames = Math.max(0, stop - start);
  const out = Array.from({ length: channels }, () => new Float32Array(frames));
  for (const t of audible(tracks)) {
    const b = Math.min(stop, t.doc.frames);
    if (b <= start) continue;
    const x = ops.fitChannels(ops.slice(t.doc.samples, start, b), channels);
    const g = gain(t.gainDb);
    const [left, right] = channels === 2 ? panGains(t.pan) : [1, 1];
    for (let c = 0; c < channels; c++) {
      const k = Math.fround(g * (c === 0 ? left : right));
      const src = x[c], dst = out[c];
      for (let i = 0; i < b - start; i++) dst[i] = dst[i] + Math.fround(src[i] * k);
    }
  }
  return out;
}

/** The loudest sample in dB (−Infinity for silence). */
export function peakDb(x: Samples): number {
  let peak = 0;
  for (const ch of x) for (let i = 0; i < ch.length; i++) peak = Math.max(peak, Math.abs(ch[i]));
  return peak > 0 ? 20 * Math.log10(peak) : -Infinity;
}

/** A volume, pan, mute or solo away from the default (then even one track is mixed). */
export const changed = (t: Track) => t.gainDb !== 0 || t.pan !== 0 || t.mute || t.solo;

export const totalSamples = (tracks: Track[]) => tracks.reduce((n, t) => n + t.doc.channels * t.doc.frames, 0);

export function nextName(tracks: Track[]): string {
  const used = new Set(tracks.map((t) => t.name));
  let n = tracks.length + 1;
  while (used.has(`Track ${n}`)) n++;
  return `Track ${n}`;
}
