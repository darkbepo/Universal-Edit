// VP9 / AV1 / H.265 in the browser: the browser's own WebCodecs encoders, driven by Mediabunny
// (MPL-2.0: demux → decode → resize/rotate → encode → mux). ffmpeg.wasm can't encode these
// (libvpx-vp9 crashes, libx265 hangs, no AV1 encoder in @ffmpeg/core 0.12.10).
import { ConversionError } from "../core/types";

export type WebCodec = "vp9" | "av1" | "h265" | "h264";
const MB_CODEC = { vp9: "vp9", av1: "av1", h265: "hevc", h264: "avc" } as const;
export const WEBCODECS_LABELS: Record<WebCodec, string> = {
  vp9: "VP9 (browser encoder)",
  av1: "AV1 (browser encoder, slow)",
  h265: "H.265 / HEVC (if your device supports it)",
  // H.264 also has an ffmpeg.wasm encoder; the browser's (often hardware) one is ~10× faster,
  // so it is tried first and ffmpeg.wasm is the fallback (converters/video.ts).
  h264: "H.264",
};

export interface WebCodecsJob {
  container: string; // mp4 | mkv | webm | mov
  codec: WebCodec;
  height: number | null; // null = original
  fps: number | null;
  quality: "very-high" | "high" | "medium" | "low";
  videoKbps: number | null; // set for "target file size"
  audio: "discard" | { kbps: number };
  rotate: 0 | 90 | 180 | 270;
  flip: boolean;
  start: number;
  end: number; // 0 = to the end
}

export async function webcodecsSupported(codec: WebCodec): Promise<boolean> {
  if (typeof VideoEncoder === "undefined") return false;
  const { canEncodeVideo } = await import("mediabunny");
  return canEncodeVideo(MB_CODEC[codec], { width: 1280, height: 720 });
}

/** Convert ``data`` with WebCodecs. Throws ConversionError("UNREADABLE") if the input can't be demuxed/decoded. */
export async function webcodecsConvert(data: Blob, job: WebCodecsJob, progress: (f: number) => void): Promise<Uint8Array> {
  const mb = await import("mediabunny");
  if (!(await webcodecsSupported(job.codec))) {
    throw new ConversionError(`This browser or device can't encode ${WEBCODECS_LABELS[job.codec].replace(/ \(.*\)/, "")}. Try another codec, or use the desktop app.`);
  }
  const input = new mb.Input({ source: new mb.BlobSource(data), formats: mb.ALL_FORMATS });
  let video;
  try {
    video = await input.getPrimaryVideoTrack();
  } catch {
    throw new ConversionError("UNREADABLE");
  }
  if (!video || !(await video.canDecode())) throw new ConversionError("UNREADABLE");
  const format = job.container === "webm" ? new mb.WebMOutputFormat() : job.container === "mkv" ? new mb.MkvOutputFormat() : job.container === "mov" ? new mb.MovOutputFormat() : new mb.Mp4OutputFormat({ fastStart: "in-memory" });
  const target = new mb.BufferTarget();
  const output = new mb.Output({ format, target });
  const quality = { "very-high": mb.QUALITY_VERY_HIGH, high: mb.QUALITY_HIGH, medium: mb.QUALITY_MEDIUM, low: mb.QUALITY_LOW }[job.quality];
  // audio: Opus everywhere the browser can't encode AAC (Opus in MP4 is valid and widely played)
  let audioCodec: "aac" | "opus" = job.container === "webm" ? "opus" : "aac";
  if (audioCodec === "aac" && !(await mb.canEncodeAudio("aac"))) audioCodec = "opus";
  const conversion = await mb.Conversion.init({
    input,
    output,
    tracks: "primary",
    video: {
      codec: MB_CODEC[job.codec],
      ...(job.height ? { height: job.height } : {}),
      ...(job.fps ? { frameRate: job.fps } : {}),
      bitrate: job.videoKbps ? job.videoKbps * 1000 : quality,
      rotate: job.rotate,
      ...(job.flip ? { flip: true } : {}),
      forceTranscode: true,
    },
    audio: job.audio === "discard" ? { discard: true } : { codec: audioCodec, bitrate: job.audio.kbps * 1000 },
    trim: { ...(job.start ? { start: job.start } : {}), ...(job.end ? { end: job.end } : {}) },
  });
  if (!conversion.isValid) {
    const why = conversion.discardedTracks.map((t) => t.reason).join(", ");
    if (conversion.discardedTracks.some((t) => t.track.type === "video")) throw new ConversionError("UNREADABLE", why);
  }
  conversion.onProgress = (p) => progress(p);
  await conversion.execute();
  if (!target.buffer) throw new ConversionError("The browser encoder produced no output.");
  return new Uint8Array(target.buffer);
}
