// The video editor's helper in the worker (never offered on the convert page): a near-lossless
// copy of a source the browser can't read or decode itself (AVI / FLV / WMV, HEIC / TIFF / RAW /
// PSD, a codec WebCodecs lacks). Videos → MP4 H.264 CRF 12 + AAC 320k, sound → FLAC, pictures →
// PNG. Only the page decides when this runs (videoedit/limits.ts `needsCopy`, `sourceProblem`),
// so a plain MP4 / PNG / MP3 project never loads FFmpeg.wasm. FFmpeg's thread counts are capped
// by engines/ffmpeg.ts (`limitThreads`) like every other run.
//
// Its second helper, "video-edit-export", renders a project (ui/videoeditor/exporter.ts) in the
// worker: options `project` (the .umcvideo JSON text), `values` (the export window's values),
// `handle` (optional FileSystemFileHandle: written straight to disk, returns []) and `ids` (the
// source id of each input file, in the same order). GIF = a WebM first, then the GIF converter.
import type { Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type Target } from "../core/types";
import { gifConverter } from "./video";

export const SOURCE_TARGET: Target = { id: "source", label: "Video editor", ext: "", group: "", converterId: "video-edit-source" };
export const EXPORT_TARGET: Target = { id: "export", label: "Video editor export", ext: "", group: "", converterId: "video-edit-export" };

const MIME: Record<string, string> = { mp4: "video/mp4", webm: "video/webm", mkv: "video/x-matroska", mov: "video/quicktime" };
const GIF_SHARE = 0.7; // of the progress bar: the WebM, then the GIF

/** `m:ss` for the progress message. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds + 1e-6));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A WritableStream for Mediabunny's StreamTarget that commits the file only through `commit()`:
 *  Mediabunny closes its stream on cancel / error too, which would keep the half file. */
function guarded(real: FileSystemWritableFileStream): { stream: WritableStream; commit(): Promise<void>; discard(): Promise<void> } {
  let done = false;
  return {
    stream: new WritableStream({
      write: (chunk) => real.write(chunk),
      close: () => undefined,
      abort: () => undefined,
    }),
    async commit() {
      done = true;
      await real.close();
    },
    async discard() {
      if (done) return;
      done = true;
      await real.abort().catch(() => undefined);
    },
  };
}

export const videoEditExportConverter: Converter = {
  id: "video-edit-export",
  family: "project",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const opts = o as unknown as { project: string; values: Values; handle?: FileSystemFileHandle | null; ids: string[] };
    const [{ projectFromJson }, ex, { exportProject }, { SourceBank }, { loadBundledFonts }, mb] = await Promise.all([
      import("../videoedit/projectfile"),
      import("../videoedit/export"),
      import("../ui/videoeditor/exporter"),
      import("../ui/videoeditor/sources"),
      import("../ui/videoeditor/fonts"),
      import("mediabunny"),
    ]);
    const { length } = await import("../videoedit/project");
    const p = projectFromJson(opts.project);
    const values = opts.values;
    const gif = values.container === "gif";
    const total = length(p);
    const share = gif ? GIF_SHARE : 1;
    const message = (s: number) => `Exporting… ${clock(s)} of ${clock(total)}`;
    ctx.progress(0, message(0));

    await loadBundledFonts().catch(() => undefined);
    const bank = new SourceBank(null);
    const controller = new AbortController(); // Cancel terminates the worker; kept for completeness
    try {
      for (const [i, f] of files.entries()) {
        await bank.open(f.file, opts.ids[i], () => undefined);
      }
      const size = gif ? ex.gifSize(p, Number(values.gif_width ?? 480)) : ex.evenSize(ex.exportSize(p, String(values.resolution ?? "project")));
      const chosen = await ex.chooseCodecs(values, ex.hasSound(p), {
        video: (c) => mb.canEncodeVideo(c, { width: size[0], height: size[1] }).catch(() => false),
        audio: (c) => mb.canEncodeAudio(c).catch(() => false),
      });
      if ("problem" in chosen) throw new ConversionError(chosen.problem);
      const progress = (s: number) => ctx.progress(total > 0 ? (s / total) * share : share, message(s));

      if (!gif && opts.handle) {
        const file = guarded(await opts.handle.createWritable());
        try {
          const job = ex.jobFor(p, values, chosen, false);
          await exportProject(job, bank, new mb.StreamTarget(file.stream, { chunked: true }), progress, controller.signal);
          await file.commit();
        } catch (e) {
          await file.discard();
          throw e;
        }
        ctx.progress(1, "Done");
        return [];
      }

      const job = ex.jobFor(p, values, chosen, true);
      const target = new mb.BufferTarget();
      await exportProject(job, bank, target, progress, controller.signal);
      const data = new Uint8Array(target.buffer!);
      if (!gif) {
        ctx.progress(1, "Done");
        return [{ name: ex.exportName(p, job.container), data, type: MIME[job.container] }];
      }
      bank.dispose(); // the sources aren't needed for the GIF
      const webm: InputFile = { file: new File([data as BlobPart], "export.webm", { type: "video/webm" }), name: "export.webm", ext: "webm", family: "video" };
      const gifValues: Values = {
        fps: Number(values.gif_fps ?? 15), width: "original", start: "", end: "", speed: 1, loop: 0, max_colors: 256, dither: "sierra2_4a",
      };
      const out: OutputFile[] = await gifConverter.run([webm], gifConverter.targets([webm])[0], gifValues, {
        progress: (f, msg = "") =>
          ctx.progress(GIF_SHARE + f * (1 - GIF_SHARE), msg.startsWith("Loading") ? msg : "Making the GIF…"),
      });
      if (!out.length) throw new ConversionError("Couldn't make the GIF.");
      ctx.progress(1, "Done");
      return [{ name: ex.exportName(p, "gif"), data: out[0].data, type: "image/gif" }];
    } finally {
      bank.dispose();
    }
  },
};

export const videoEditSourceConverter: Converter = {
  id: "video-edit-source",
  family: "video",
  options: [],
  targets: () => [],
  async run(files, _target, o, ctx) {
    const f = files[0];
    const kind = String((o as Record<string, unknown>).kind);
    const msg = `Converting “${f.name}” for this browser…`;
    ctx.progress(0, msg);
    if (kind === "image") {
      const { getMagick, copy, readImage } = await import("../engines/magick");
      const M = await getMagick((m) => ctx.progress(0, m));
      const bytes = new Uint8Array(await f.file.arrayBuffer());
      let data: Uint8Array;
      try {
        data = readImage(M, bytes, f.ext, (img) => {
          img.autoOrient();
          return img.write(M.MagickFormat.Png, copy);
        });
      } catch (e) {
        throw new ConversionError(`Couldn't open “${f.name}”.`, String(e));
      }
      ctx.progress(1, "Done");
      return [{ name: "source.png", data, type: "image/png" }];
    }
    if (kind !== "video" && kind !== "audio") throw new ConversionError(`Unknown kind “${kind}”.`);
    const { getFFmpeg } = await import("../engines/ffmpeg");
    const ff = await getFFmpeg((m) => ctx.progress(0, m));
    ctx.progress(0, msg);
    const { path, release } = await ff.mount(f.file);
    try {
      const info = await ff.probe(path);
      const error = `Couldn't open “${f.name}”.`;
      if (kind === "video" && info.hasVideo) {
        // even sizes for yuv420p (a no-op for even ones); FFmpeg turns rotated clips upright
        await ff.run(["-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-crf", "12", "-preset", "veryfast",
          "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "/source.mp4"],
        info.duration, (p) => ctx.progress(p, msg), error);
        const data = await ff.readAndDelete("/source.mp4");
        ctx.progress(1, "Done");
        return [{ name: "source.mp4", data, type: "video/mp4" }];
      }
      if (!info.hasAudio) throw new ConversionError(error);
      await ff.run(["-i", path, "-map", "0:a:0", "-c:a", "flac", "/source.flac"], info.duration, (p) => ctx.progress(p, msg), error);
      const data = await ff.readAndDelete("/source.flac");
      ctx.progress(1, "Done");
      return [{ name: "source.flac", data, type: "audio/flac" }];
    } finally {
      await release();
    }
  },
};
