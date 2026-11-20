// The video editor's helper in the worker (never offered on the convert page): a near-lossless
// copy of a source the browser can't read or decode itself (AVI / FLV / WMV, HEIC / TIFF / RAW /
// PSD, a codec WebCodecs lacks). Videos → MP4 H.264 CRF 12 + AAC 320k, sound → FLAC, pictures →
// PNG. Only the page decides when this runs (videoedit/limits.ts `needsCopy`, `sourceProblem`),
// so a plain MP4 / PNG / MP3 project never loads FFmpeg.wasm. FFmpeg's thread counts are capped
// by engines/ffmpeg.ts (`limitThreads`) like every other run.
import { ConversionError, type Converter, type Target } from "../core/types";

export const SOURCE_TARGET: Target = { id: "source", label: "Video editor", ext: "", group: "", converterId: "video-edit-source" };

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
