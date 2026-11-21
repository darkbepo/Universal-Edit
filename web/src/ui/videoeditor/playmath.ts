// Playback, thumbnail and waveform maths of the video editor page (ports of the desktop's
// gui/videoedit/player.py and the painting rules of timeline.py). No DOM.
import { SAMPLE_RATE, mixWindow } from "../../videoedit/mix";
import type { Project, Source } from "../../videoedit/project";
import type { Rect } from "./timelinemath";

export const THUMB_HEIGHT = 56, THUMB_EVERY_PX = 120, THUMB_MAX = 40, THUMB_PPS = 80;
export const WAVE_PER_SECOND = 50, WAVE_MAX = 4000, WAVE_H = 16;
export const CHUNK_SECONDS = 0.5, LEAD_SECONDS = 1.0, END_SLACK = 0.02;
export const NO_SOUND = "No sound device — preview is silent";

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v));

export function thumbCount(s: Source): number {
  if (s.kind === "image") return 1;
  return clamp(Math.ceil((s.duration * THUMB_PPS) / THUMB_EVERY_PX), 1, THUMB_MAX);
}

export function waveBuckets(s: Source): number {
  return clamp(Math.round(s.duration * WAVE_PER_SECOND), 50, WAVE_MAX);
}

/** Where each thumbnail is taken: the middle of its share of the source. */
export function thumbTimes(s: Source, count: number): number[] {
  if (s.kind === "image") return [0];
  return Array.from({ length: count }, (_, i) => ((i + 0.5) * s.duration) / count);
}

/** Where Play starts: the play head, or the beginning when it sits at the end. */
export function startTime(playhead: number, total: number): number {
  return playhead < total - END_SLACK ? playhead : 0;
}

/** What playback is timed by: the sound's own clock when there is an AudioContext (frames
 *  follow the sound, like the desktop's player), else the wall clock (silent preview). */
export function pickClock(hasAudioContext: boolean): "audio" | "wall" {
  return hasAudioContext ? "audio" : "wall";
}

/** A stretch of one source (source seconds) the mixer asked for. */
export interface Need { sourceId: string; from: number; to: number }

/** The source sound a window of the project needs, merged per source. */
export function audioNeeds(p: Project, start: number, frames: number): Need[] {
  const ranges = new Map<string, [number, number][]>();
  mixWindow(p, start, frames, (sourceId, from, n) => {
    const list = ranges.get(sourceId) ?? [];
    list.push([from, from + n / SAMPLE_RATE]);
    ranges.set(sourceId, list);
    return null;
  });
  const needs: Need[] = [];
  for (const [sourceId, list] of ranges) {
    list.sort((a, b) => a[0] - b[0]);
    let [from, to] = list[0];
    for (const [f, t] of list.slice(1)) {
      if (f <= to + 1e-9) to = Math.max(to, t);
      else {
        needs.push({ sourceId, from, to });
        [from, to] = [f, t];
      }
    }
    needs.push({ sourceId, from, to });
  }
  return needs;
}

/** The thumbnails drawn along a clip (timeline.py `_paint_thumbs`): only those inside the visible part. */
export function thumbSlots(rect: Rect, aspect: number, count: number, header: number, width: number): { index: number; rect: Rect }[] {
  const h = rect.h - WAVE_H;
  const w = Math.max(1, Math.round(h * aspect));
  const n = Math.max(1, Math.ceil(rect.w / w));
  const first = Math.max(0, Math.floor((header - rect.x) / w));
  const last = Math.min(n, Math.floor((width - rect.x) / w) + 1);
  const out: { index: number; rect: Rect }[] = [];
  for (let i = first; i < last; i++) {
    out.push({ index: Math.min(count - 1, Math.floor((i * count) / n)), rect: { x: rect.x + i * w, y: rect.y, w, h } });
  }
  return out;
}

/** The waveform bars of a clip (timeline.py `_paint_wave`): x and half the bar's height, every 2 px. */
export function waveBars(rect: Rect, peaks: ArrayLike<number>, in_: number, out: number, duration: number, header: number, width: number): { x: number; half: number }[] {
  const bars: { x: number; half: number }[] = [];
  if (peaks.length === 0) return bars;
  const dur = Math.max(duration, 1e-6);
  let x = Math.max(rect.x, header);
  x -= (((x - rect.x) % 2) + 2) % 2;
  const end = Math.min(rect.x + rect.w - 1, width);
  for (; x <= end; x += 2) {
    const frac = (x - rect.x) / Math.max(1, rect.w);
    const idx = Math.trunc(((in_ + frac * (out - in_)) / dur) * peaks.length);
    const v = peaks[clamp(idx, 0, peaks.length - 1)];
    bars.push({ x, half: Math.max(1, Math.trunc((v * rect.h) / 2)) });
  }
  return bars;
}

/** Peak |sample| per bucket over a stream of `expected` samples, fed in chunks (preview.waveform). */
export class PeakAccumulator {
  private readonly peaks: Float32Array;
  private seen = 0;
  constructor(private readonly buckets: number, private readonly expected: number) {
    this.peaks = new Float32Array(buckets);
  }
  add(mono: Float32Array): void {
    const last = this.buckets - 1;
    for (let i = 0; i < mono.length; i++) {
      const b = Math.min(last, Math.floor(((this.seen + i) * this.buckets) / Math.max(1, this.expected)));
      const v = Math.abs(mono[i]);
      if (v > this.peaks[b]) this.peaks[b] = v;
    }
    this.seen += mono.length;
  }
  result(): Float32Array | null {
    return this.seen === 0 ? null : this.peaks;
  }
}
