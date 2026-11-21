// Thumbnails and waveforms for the timeline (port of the desktop's player.py MediaScanner): one
// source at a time, thumbnails first, then the waveform; results are cached per source id and
// sent again on `want`. Everything reads the bank's decodable file (never FFmpeg.wasm). Each
// asynchronous step checks that its scan is still wanted before it sends anything.
// The scanner owns the bitmaps it sends: `forget` / `dispose` close them, so the receiver must
// let go of them first.
import type { Source } from "../../videoedit/project";
import { PeakAccumulator, THUMB_HEIGHT, thumbCount, thumbTimes, waveBuckets } from "./playmath";
import type { SourceBank } from "./sources";

export interface ScanEvents {
  thumbs(id: string, images: ImageBitmap[]): void;
  wave(id: string, peaks: Float32Array): void;
  /** A scan of a source finished (or gave up). */
  done?(id: string): void;
}

interface Result { thumbs: ImageBitmap[] | null; wave: Float32Array | null }

function closeAll(images: ImageBitmap[]): void {
  for (const b of images) b.close();
}

export class Scanner {
  private results = new Map<string, Result>();
  private queue: Source[] = [];
  private running = false;
  private gone = false;

  constructor(private bank: () => SourceBank, private on: ScanEvents) {}

  /** Scan sources not seen yet (one after the other); cached results are sent again. */
  want(sources: readonly Source[]): void {
    if (this.gone) return;
    for (const s of sources) {
      const have = this.results.get(s.id);
      if (have) {
        if (have.thumbs) this.on.thumbs(s.id, have.thumbs);
        if (have.wave) this.on.wave(s.id, have.wave);
        continue;
      }
      this.results.set(s.id, { thumbs: null, wave: null });
      this.queue.push(s);
    }
    if (!this.running) void this.run();
  }

  /** A source was relinked or removed: its result goes and a later `want` scans it again. */
  forget(id: string): void {
    const have = this.results.get(id);
    this.results.delete(id);
    this.queue = this.queue.filter((s) => s.id !== id);
    if (have?.thumbs) closeAll(have.thumbs);
  }

  /** Stop the running scan; nothing is sent afterwards. */
  dispose(): void {
    this.gone = true;
    this.queue = [];
    for (const r of this.results.values()) if (r.thumbs) closeAll(r.thumbs);
    this.results.clear();
  }

  private async run(): Promise<void> {
    this.running = true;
    try {
      while (!this.gone && this.queue.length) {
        const s = this.queue.shift()!;
        const mine = this.results.get(s.id);
        if (!mine) continue;
        try {
          await this.scan(s, mine);
        } catch {
          // a source that can't be read just shows no thumbnails / waveform
        }
        if (this.alive(s.id, mine)) {
          // a scan that found nothing isn't kept: a later `want` (after some change) tries again
          if (!mine.thumbs && !mine.wave) this.results.delete(s.id);
          this.on.done?.(s.id);
        }
      }
    } finally {
      this.running = false;
    }
  }

  private alive(id: string, mine: Result): boolean {
    return !this.gone && this.results.get(id) === mine;
  }

  private async scan(s: Source, mine: Result): Promise<void> {
    const bank = this.bank();
    if (s.kind !== "audio") {
      const images: ImageBitmap[] = [];
      for (const t of thumbTimes(s, thumbCount(s))) {
        const got = await bank.picture(s.id, t, THUMB_HEIGHT);
        if (!this.alive(s.id, mine)) return closeAll(images);
        if (!got) continue;
        const bitmap = await createImageBitmap(got.image, { resizeHeight: THUMB_HEIGHT, resizeQuality: "medium" });
        if (!this.alive(s.id, mine)) {
          bitmap.close();
          return closeAll(images);
        }
        images.push(bitmap);
      }
      if (images.length) {
        mine.thumbs = images;
        this.on.thumbs(s.id, images);
      }
    }
    if (s.kind === "image" || !s.has_audio) return;
    let acc: PeakAccumulator | null = null;
    for await (const piece of bank.samples(s.id, 0, Infinity)) {
      if (!this.alive(s.id, mine)) return;
      acc ??= new PeakAccumulator(waveBuckets(s), Math.round(s.duration * piece.rate));
      const n = piece.channels[0]?.length ?? 0;
      const mono = new Float32Array(n);
      for (const ch of piece.channels) for (let i = 0; i < n; i++) mono[i] += ch[i];
      if (piece.channels.length > 1) for (let i = 0; i < n; i++) mono[i] /= piece.channels.length;
      acc.add(mono);
    }
    const peaks = acc?.result() ?? null;
    if (peaks && this.alive(s.id, mine)) {
      mine.wave = peaks;
      this.on.wave(s.id, peaks);
    }
  }
}
