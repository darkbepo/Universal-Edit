// The video editor's sources in the browser: each dropped file is probed and decoded on the page
// with Mediabunny (videos, sound) or createImageBitmap (pictures). Only a file the browser can't
// read or decode is copied once by the hidden worker converter "video-edit-source" (FFmpeg /
// ImageMagick), and only when `limits.sourceProblem` allows it. The copy is used for decoding;
// the Source keeps the original file's name and size, so a saved project still matches the real
// file later. Mediabunny is imported on demand (it stays out of the page bundle).
import { canonicalExt, AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS } from "../../core/detect";
import { ConversionError, type InputFile } from "../../core/types";
import { SOURCE_TARGET } from "../../converters/videoedit";
import { DIRECT_AUDIO, DIRECT_PICTURE, DIRECT_VIDEO, needsCopy, sourceProblem } from "../../videoedit/limits";
import type { Source } from "../../videoedit/project";
import { JobCancelled, type ConversionClient } from "../../worker/client";
import type { Picture } from "./draw";
import type { Timed } from "./framestream";
import type { Decoded, Decoder } from "./sound";

type MB = typeof import("mediabunny");
type InputT = import("mediabunny").Input;
type VideoTrack = import("mediabunny").InputVideoTrack;
type Sink = import("mediabunny").CanvasSink;

export const COPY_NOTE = "Converted for this browser — the desktop app reads it directly.";

export interface Opened { source: Source; file: File; copy: boolean; note: string | null }

export interface Probe { duration: number; width: number; height: number; fps: number; hasAudio: boolean }

export function sourceFromProbe(id: string, name: string, size: number, kind: Source["kind"], probe: Probe): Source {
  return {
    id, path: name, kind, duration: probe.duration, width: probe.width, height: probe.height,
    fps: Math.round(probe.fps * 1000) / 1000, has_audio: probe.hasAudio, size, relpath: null,
  };
}

// Picture types ImageMagick reads but the editor has no use for (layered projects, icons are fine).
const NOT_A_SOURCE = new Set(["ora"]);

/** Which kind of source a file type gives: the direct types, then every type the copy can read. */
export function kindOf(ext: string): Source["kind"] | null {
  const e = ext ? canonicalExt(ext.replace(/^\./, "")) : "";
  if (DIRECT_VIDEO.has(e) || VIDEO_EXTS.has(e)) return "video";
  if (DIRECT_AUDIO.has(e) || AUDIO_EXTS.has(e)) return "audio";
  if (DIRECT_PICTURE.has(e)) return "image";
  if (IMAGE_EXTS.has(e) && !NOT_A_SOURCE.has(e)) return "image";
  return null;
}

const cannotOpen = (name: string) => new ConversionError(`Couldn't open “${name}”.`);

interface Media { kind: "video" | "audio"; probe: Probe; input: InputT; video: VideoTrack | null }
interface Entry {
  kind: Source["kind"];
  input?: InputT;
  video?: VideoTrack | null;
  sinks?: Map<number, Sink>;
  bitmap?: ImageBitmap;
  file: File;
}

/** Thrown by `open` once the bank was abandoned (the page was left): nothing was kept. */
export class Abandoned extends Error {
  constructor() {
    super("Abandoned.");
    this.name = "Abandoned";
  }
}

export class SourceBank {
  private entries = new Map<string, Entry>();
  private mb: Promise<MB> | null = null;
  private gone = false;
  /** A copy of this bank's is running in the (shared) worker right now. */
  private copying = false;

  /** `client` null (the export worker): a file that needs a copy is refused. */
  constructor(private client: ConversionClient | null) {}

  private mediabunny(): Promise<MB> {
    return (this.mb ??= import("mediabunny"));
  }

  /** Probe (and if needed copy) one file. Throws ConversionError with a message for the page. */
  async open(file: File, id: string, onProgress: (f: number, msg: string) => void): Promise<Opened> {
    if (this.gone) throw new Abandoned();
    const ext = canonicalExt(file.name);
    const kind = kindOf(ext);
    if (!kind) throw cannotOpen(file.name);
    let media: Media | null = null;
    let bitmap: ImageBitmap | null = null;
    const direct = !needsCopy(ext, true);
    if (direct) {
      if (kind === "image") bitmap = await this.bitmap(file);
      else media = await this.media(file, kind);
    }
    this.check(bitmap, media);
    const decodes = kind === "image" ? bitmap !== null : media !== null;
    const copy = needsCopy(ext, decodes);
    const size = bitmap ? { width: bitmap.width, height: bitmap.height } : {};
    const problem = sourceProblem({ name: file.name, size: file.size, copy, ...size });
    if (problem) {
      bitmap?.close();
      media?.input.dispose();
      throw new ConversionError(problem);
    }
    let used = file;
    if (copy) {
      media?.input.dispose();
      const client = this.client;
      if (!client) throw new ConversionError(`${file.name} can't be read here.`);
      const input: InputFile = { file, name: file.name, ext, family: kind === "audio" ? "audio" : kind === "image" ? "image" : "video" };
      let out: { name: string; data: Uint8Array; type?: string }[];
      try {
        out = await client.run(SOURCE_TARGET, [input], { kind }, (f, msg) => {
          // The first message means the worker runs this job (the client is shared and runs one
          // job at a time): only then may cancel() stop it, as it stops whatever is running.
          this.copying = true;
          if (this.gone) this.cancelCopy();
          else onProgress(f, msg);
        });
      } catch (e) {
        if (this.gone || e instanceof JobCancelled) throw new Abandoned();
        throw e instanceof Error && e.message ? new ConversionError(e.message) : cannotOpen(file.name);
      } finally {
        this.copying = false;
      }
      this.check(null, null);
      if (!out.length) throw cannotOpen(file.name);
      used = new File([out[0].data as BlobPart], out[0].name, { type: out[0].type ?? "" });
      if (kind === "image") {
        bitmap = await this.bitmap(used);
        if (!bitmap) throw cannotOpen(file.name);
        const again = sourceProblem({ name: file.name, size: file.size, copy: false, width: bitmap.width, height: bitmap.height });
        if (again) {
          bitmap.close();
          throw new ConversionError(again);
        }
      } else {
        media = await this.media(used, kind);
        if (!media) throw cannotOpen(file.name);
      }
      this.check(bitmap, media);
    }
    let source: Source;
    this.forget(id);
    if (kind === "image") {
      source = sourceFromProbe(id, file.name, file.size, "image", { duration: 0, width: bitmap!.width, height: bitmap!.height, fps: 0, hasAudio: false });
      this.entries.set(id, { kind: "image", bitmap: bitmap!, file: used });
    } else {
      const m = media!;
      source = sourceFromProbe(id, file.name, file.size, m.kind, m.probe);
      this.entries.set(id, { kind: m.kind, input: m.input, video: m.video, sinks: new Map(), file: used });
    }
    return { source, file: used, copy, note: copy ? COPY_NOTE : null };
  }

  private async bitmap(file: Blob): Promise<ImageBitmap | null> {
    try {
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      return null;
    }
  }

  /** Mediabunny's view of a video / sound file; null when it can't read or decode every track. */
  private async media(file: Blob, kind: "video" | "audio"): Promise<Media | null> {
    const mb = await this.mediabunny();
    const input = new mb.Input({ source: new mb.BlobSource(file), formats: mb.ALL_FORMATS });
    try {
      const video = kind === "video" ? await input.getPrimaryVideoTrack() : null;
      const audio = await input.getPrimaryAudioTrack();
      if (!video && !audio) throw new Error("no tracks");
      if (video && !(await video.canDecode())) throw new Error("video can't be decoded");
      if (audio && !(await audio.canDecode())) throw new Error("sound can't be decoded");
      const duration = await input.computeDuration();
      if (video) {
        const stats = await video.computePacketStats();
        return {
          kind: "video", input, video,
          probe: { duration, width: video.displayWidth, height: video.displayHeight, fps: stats.averagePacketRate, hasAudio: audio !== null },
        };
      }
      return { kind: "audio", input, video: null, probe: { duration, width: 0, height: 0, fps: 0, hasAudio: true } };
    } catch {
      input.dispose();
      return null;
    }
  }

  has(id: string): boolean {
    return this.entries.has(id);
  }

  /** A video's frame at or before `time`, `height` px high (upright); a picture's bitmap. */
  async picture(id: string, time: number, height: number): Promise<Picture | null> {
    const e = this.entries.get(id);
    if (!e) return null;
    if (e.bitmap) return { image: e.bitmap, width: e.bitmap.width, height: e.bitmap.height };
    if (!e.video || !e.sinks) return null;
    const h = Math.max(1, Math.round(Math.min(height, e.video.displayHeight)));
    let sink = e.sinks.get(h);
    if (!sink) {
      const mb = await this.mediabunny();
      sink = new mb.CanvasSink(e.video, { height: h, fit: "contain" });
      e.sinks.set(h, sink);
      if (e.sinks.size > 2) e.sinks.delete(e.sinks.keys().next().value!);
    } else if (e.sinks.size > 1 && [...e.sinks.keys()].pop() !== h) {
      e.sinks.delete(h); // re-using a height makes it the newest
      e.sinks.set(h, sink);
    }
    try {
      const first = await e.video.getFirstTimestamp();
      const got = await sink.getCanvas(Math.max(first, time));
      if (!got) return null;
      return { image: got.canvas, width: got.canvas.width, height: got.canvas.height };
    } catch {
      return null; // disposed meanwhile (forgotten / left), or a broken frame
    }
  }

  /** A video's frames from source second `from` on, `height` px high, for playback (a sink of its
   *  own, so a still asked for meanwhile doesn't move it). Ends quietly when the source is gone. */
  async *frames(id: string, from: number, height: number): AsyncGenerator<Timed<Picture>> {
    const e = this.entries.get(id);
    if (!e?.video) return;
    const video = e.video;
    try {
      const mb = await this.mediabunny();
      const h = Math.max(1, Math.round(Math.min(height, video.displayHeight)));
      // No canvas pool (`poolSize`): a pooled canvas is painted over again after poolSize more
      // frames. FrameStream's `ahead` limit counts only frames past the wanted time; frames at or
      // before it can pile up between two draws when decoding lags, until it reopens past
      // AHEAD_JUMP (2 s): roughly AHEAD_JUMP · fps + ahead (~65 at 30 fps), plus the shown frame
      // and the player's `last`. A pool that large gains nothing, and a smaller one could paint
      // over a frame still shown; a fresh canvas per frame costs only an allocation.
      const sink = new mb.CanvasSink(video, { height: h, fit: "contain" });
      const first = await video.getFirstTimestamp();
      for await (const got of sink.canvases(Math.max(first, from))) {
        if (!this.entries.has(id)) return;
        const c = got.canvas;
        yield { timestamp: got.timestamp, duration: got.duration, value: { image: c, width: c.width, height: c.height } };
      }
    } catch (err) {
      if (this.entries.get(id) !== e) return; // disposed meanwhile (forgotten / left)
      throw err;
    }
  }

  /** The decodable file of a source (the copy when one was made). */
  file(id: string): File | null {
    return this.entries.get(id)?.file ?? null;
  }

  /** A source's sound as decoded pieces between two source times; ends quietly when the source is gone. */
  readonly audio: Decoder = (id, from, to) => this.samples(id, from, to);

  async *samples(id: string, from: number, to: number): AsyncGenerator<Decoded> {
    const input = this.entries.get(id)?.input;
    if (!input) return;
    try {
      const track = await input.getPrimaryAudioTrack();
      if (!track) return;
      const mb = await this.mediabunny();
      const sink = new mb.AudioSampleSink(track);
      for await (const sample of sink.samples(from, to)) {
        try {
          const channels: Float32Array[] = [];
          for (let c = 0; c < sample.numberOfChannels; c++) {
            const arr = new Float32Array(sample.numberOfFrames);
            sample.copyTo(arr, { planeIndex: c, format: "f32-planar" });
            channels.push(arr);
          }
          yield { timestamp: sample.timestamp, rate: sample.sampleRate, channels };
        } finally {
          sample.close();
        }
      }
    } catch (e) {
      if (!this.entries.has(id)) return; // disposed meanwhile (forgotten / left)
      throw e;
    }
  }

  /** Drop a source's decoders (relink / remove). */
  forget(id: string): void {
    const e = this.entries.get(id);
    if (!e) return;
    this.entries.delete(id);
    e.bitmap?.close();
    e.input?.dispose();
  }

  /** Leaving the page. */
  dispose(): void {
    for (const id of [...this.entries.keys()]) this.forget(id);
  }

  /** Leaving for good: dispose, stop a running copy, and make an `open` still under way keep
   *  nothing (it throws Abandoned). A copy still waiting for the worker is stopped as it starts. */
  abandon(): void {
    this.gone = true;
    this.dispose();
    if (this.copying) this.cancelCopy();
  }

  get abandoned(): boolean {
    return this.gone;
  }

  private cancelCopy(): void {
    this.copying = false;
    this.client?.cancel();
  }

  /** After each wait inside `open`: once abandoned, free what was decoded and stop. */
  private check(bitmap: ImageBitmap | null, media: Media | null): void {
    if (!this.gone) return;
    bitmap?.close();
    media?.input.dispose();
    throw new Abandoned();
  }
}
