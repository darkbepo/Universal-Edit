// The website video editor's export: every frame composed like playback (compose.ts → draw.ts on
// an OffscreenCanvas) and handed to Mediabunny's CanvasSource; the sound mixed in 1 s chunks
// (mixWindow + one Limiter, as the player) and added after the frames they cover, so the muxer
// never holds the whole sound. Runs on the page or in a worker (no DOM besides OffscreenCanvas).
// Frames are read with a blocking FrameReader (one per video item), not playback's FrameStream:
// export waits for every frame instead of showing the last one it had.
import { frameAt, type Layer } from "../../videoedit/compose";
import { frameCount, pictureLastUse } from "../../videoedit/export";
import { Limiter, SAMPLE_RATE, mixWindow } from "../../videoedit/mix";
import { length, outputSize, type Project } from "../../videoedit/project";
import { textAt } from "../../videoedit/textlayout";
import { JobCancelled } from "../../worker/client";
import { drawFrame, drawText, type Picture, type Surfaces } from "./draw";
import { hasFont, loadBundledFonts } from "./fonts";
import type { Timed } from "./framestream";
import { audioNeeds } from "./playmath";
import { SoundStore } from "./sound";
import type { SourceBank } from "./sources";

const BEHIND = 1; // seconds behind the last returned frame that count as a seek
const AHEAD_JUMP = 2; // seconds past the read position that count as a seek
const EPS = 1e-6;

/** Reads a clip's frames in order and waits for them (export's counterpart of FrameStream).
 *  One call at a time. */
export class FrameReader<T> {
  private iterator: AsyncIterator<Timed<T>> | null = null;
  private current: Timed<T> | null = null; // newest frame ≤ the last t asked for
  private next: Timed<T> | null = null; // read but past that t
  private started = false;
  private ended = false; // ran out, threw, or couldn't open: no more reads until a reopen
  private closed = false;
  private readPos = 0;
  private returned: number | null = null;

  constructor(private readonly open: (from: number) => AsyncIterator<Timed<T>>) {}

  /** The newest frame with timestamp ≤ t (the first frame when t is before it); null when none. */
  async at(t: number): Promise<T | null> {
    if (this.closed) return null;
    const behind = this.returned !== null && t < this.returned - BEHIND;
    const jump = !this.ended && t > this.readPos + AHEAD_JUMP;
    if (!this.started || behind || jump) this.reopen(t);
    while (!this.closed) {
      if (this.next) {
        if (this.next.timestamp > t + EPS) break;
        this.current = this.next;
        this.next = null;
      }
      if (this.ended || !this.iterator) break;
      try {
        const r = await this.iterator.next();
        if (this.closed) return null;
        if (r.done) this.ended = true;
        else {
          this.next = r.value;
          this.readPos = r.value.timestamp;
        }
      } catch (e) {
        console.error("A video's frames couldn't be read", e);
        this.ended = true;
      }
    }
    if (this.closed) return null;
    const got = this.current ?? this.next;
    if (!got) return null;
    this.returned = got.timestamp;
    return got.value;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.release();
    this.current = this.next = null;
  }

  private release(): void {
    const it = this.iterator;
    this.iterator = null;
    if (it?.return) {
      try {
        Promise.resolve(it.return()).catch(() => undefined);
      } catch {
        // ignore
      }
    }
  }

  private reopen(t: number): void {
    this.release();
    this.current = this.next = null;
    this.returned = null;
    this.started = true;
    this.ended = false;
    this.readPos = t;
    try {
      this.iterator = this.open(t);
    } catch (e) {
      console.error("A video's frames couldn't be opened", e);
      this.iterator = null;
      this.ended = true;
    }
  }
}

/** The 1 s sound chunks (their start seconds) to add now: those from `audioSecondsDone` on whose
 *  end (cut to `length`) the video has reached. */
export function chunksDue(videoSecondsDone: number, audioSecondsDone: number, length: number): number[] {
  const out: number[] = [];
  for (let s = audioSecondsDone; s < length - 1e-9; s += 1) {
    if (Math.min(s + 1, length) > videoSecondsDone + 1e-9) break;
    out.push(s);
  }
  return out;
}

export type ExportQuality = "very-high" | "high" | "medium" | "low";

export interface ExportJob {
  project: Project;
  /** Even sides (the caller uses evenSize). */
  size: [number, number];
  fps: number;
  /** codec = Mediabunny's video codec ("avc", "vp9", "av1", "hevc", "vp8"); bitrate in bits/s or a quality. */
  video: { codec: string; bitrate: number | ExportQuality };
  /** bitrate in bits/s; null = no audio track. */
  audio: { codec: "aac" | "opus"; bitrate: number } | null;
  container: "mp4" | "webm" | "mkv" | "mov";
}

type MB = typeof import("mediabunny");

const exportSurfaces: Surfaces = {
  make(w, h) {
    const canvas = new OffscreenCanvas(w, h);
    return { canvas, ctx: canvas.getContext("2d", { willReadFrequently: true })! };
  },
};

function outputFormat(mb: MB, job: ExportJob): import("mediabunny").OutputFormat {
  // never "in-memory": that keeps every chunk until finalize, then writes them again (2× the file);
  // a BufferTarget and a StreamTarget can both be written anywhere, so the index goes at the end
  const fastStart = false as const;
  switch (job.container) {
    case "webm":
      return new mb.WebMOutputFormat();
    case "mkv":
      return new mb.MkvOutputFormat();
    case "mov":
      return new mb.MovOutputFormat({ fastStart });
    default:
      return new mb.Mp4OutputFormat({ fastStart });
  }
}

function quality(mb: MB, q: ExportQuality): import("mediabunny").Quality {
  return { "very-high": mb.QUALITY_VERY_HIGH, high: mb.QUALITY_HIGH, medium: mb.QUALITY_MEDIUM, low: mb.QUALITY_LOW }[q] ?? mb.QUALITY_HIGH;
}

/** Render `job.project` into `target`. `progress` gets the exported seconds. Throws JobCancelled
 *  (after `output.cancel()`) when `signal` aborts. */
export async function exportProject(
  job: ExportJob,
  bank: SourceBank,
  target: import("mediabunny").Target,
  progress: (seconds: number) => void,
  signal: AbortSignal,
): Promise<void> {
  const mb = await import("mediabunny");
  const p = job.project;
  const [w, h] = job.size;
  const fps = job.fps;
  const frames = frameCount(length(p), fps);
  const videoLength = frames / fps;
  const textScale = h / outputSize(p)[1];

  const canvas = new OffscreenCanvas(w, h);
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  const output = new mb.Output({ format: outputFormat(mb, job), target });
  const vb = job.video.bitrate;
  const videoSource = new mb.CanvasSource(canvas, {
    codec: job.video.codec as import("mediabunny").VideoCodec,
    ...(typeof vb === "number" ? { bitrate: vb } : { quality: quality(mb, vb) }),
  });
  output.addVideoTrack(videoSource, { frameRate: fps });
  let audioSource: import("mediabunny").AudioSampleSource | null = null;
  if (job.audio) {
    audioSource = new mb.AudioSampleSource({ codec: job.audio.codec, bitrate: job.audio.bitrate });
    output.addAudioTrack(audioSource);
  }

  const readers = new Map<string, FrameReader<Picture>>();
  const pictures = new Map<string, Picture | null>();
  const lastUse = pictureLastUse(p);
  const sound = audioSource ? new SoundStore(bank.audio) : null;
  const limiter = new Limiter();
  const check = () => {
    if (signal.aborted) throw new JobCancelled("Cancelled.");
  };

  const pictureOf = async (l: Layer): Promise<Picture | null> => {
    if (l.kind === "picture") {
      // a picture that can't be opened fails the export (its message says why)
      if (!pictures.has(l.sourceId)) pictures.set(l.sourceId, await bank.picture(l.sourceId, 0, h));
      return pictures.get(l.sourceId) ?? null;
    }
    let r = readers.get(l.itemId);
    if (!r) {
      const id = l.sourceId;
      r = new FrameReader<Picture>((from) => bank.frames(id, from, h));
      readers.set(l.itemId, r);
    }
    return r.at(l.time);
  };

  const addChunk = async (start: number): Promise<void> => {
    if (!audioSource || !sound) return;
    const f0 = Math.round(start * SAMPLE_RATE);
    const n = Math.round(Math.min(start + 1, videoLength) * SAMPLE_RATE) - f0;
    if (n <= 0) return;
    try {
      await sound.ensure(audioNeeds(p, start, n));
    } catch (e) {
      console.error("Sound couldn't be decoded", e); // that part stays silent
    }
    check();
    const chs = mixWindow(p, start, n, sound.lookup);
    limiter.process(chs);
    const data = new Float32Array(n * chs.length);
    chs.forEach((ch, c) => data.set(ch, c * n));
    const sample = new mb.AudioSample({ data, format: "f32-planar", numberOfChannels: chs.length, sampleRate: SAMPLE_RATE, timestamp: start });
    try {
      await audioSource.add(sample);
    } finally {
      sample.close();
    }
  };

  try {
    if (p.text_rows.some((r) => r.length)) await loadBundledFonts().catch(() => undefined);
    check();
    await output.start();
    let audioDone = 0;
    for (let i = 0; i < frames; i++) {
      check();
      const t = i / fps;
      const frame = frameAt(p, t, job.size);
      const byLayer = new Map<Layer, Picture | null>();
      const seen = new Set<string>();
      for (const pl of frame.layers) {
        if (pl.layer.kind === "video") seen.add(pl.layer.itemId);
        byLayer.set(pl.layer, await pictureOf(pl.layer));
      }
      for (const [id, r] of readers) {
        if (seen.has(id)) continue;
        r.close();
        readers.delete(id);
      }
      check();
      drawFrame(ctx, frame, (l) => byLayer.get(l) ?? null, exportSurfaces);
      drawText(ctx, textAt(p, t, job.size, hasFont), textScale);
      await videoSource.add(t, 1 / fps);
      const videoDone = (i + 1) / fps;
      for (const id of [...pictures.keys()]) {
        if ((lastUse.get(id) ?? 0) > videoDone + 1e-9) continue; // a later frame still shows it
        pictures.delete(id);
        bank.forget(id); // its bitmap is closed
      }
      for (const s of chunksDue(videoDone, audioDone, videoLength)) {
        await addChunk(s);
        audioDone = s + 1;
      }
      progress(Math.min(videoDone, length(p)));
    }
    check();
    await output.finalize();
    progress(length(p));
  } catch (e) {
    if (output.state !== "canceled" && output.state !== "finalized") await output.cancel().catch(() => undefined);
    if (signal.aborted) throw new JobCancelled("Cancelled.");
    throw e;
  } finally {
    for (const r of readers.values()) r.close();
    readers.clear();
    sound?.dispose();
  }
}
