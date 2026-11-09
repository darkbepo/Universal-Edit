// Codec → encoder candidates, container rules and rate-control args (port of the desktop
// app's codecs.py, trimmed to what works in ffmpeg.wasm).
//
// Tested in Chromium with @ffmpeg/core 0.12.10: libvpx-vp9 crashes the tab, libx265 hangs and
// there is no AV1 encoder — so VP9 / H.265 / AV1 go through WebCodecs instead
// (engines/webcodecs.ts, Mediabunny); they have no ffmpeg.wasm encoder entry here.
import { ConversionError } from "../core/types";

export const VIDEO_ENCODERS: Record<string, string[]> = {
  h264: ["libx264"],
  vp8: ["libvpx"],
  mpeg4: ["mpeg4"],
};
export const AUDIO_ENCODERS: Record<string, string[]> = {
  aac: ["aac"],
  mp3: ["libmp3lame"],
  opus: ["libopus", "opus"],
  vorbis: ["libvorbis", "vorbis"],
  flac: ["flac"],
  pcm: ["pcm_s16le"],
  pcm_be: ["pcm_s16be"],
  wma: ["wmav2"],
  ac3: ["ac3"],
};
export const VIDEO_CODEC_LABELS: Record<string, string> = {
  h264: "H.264 (most compatible)", vp8: "VP8", mpeg4: "MPEG-4 Part 2",
  vp9: "VP9 (browser encoder)", av1: "AV1 (browser encoder, slow)", h265: "H.265 / HEVC (if your device supports it)",
};
/** Codecs encoded with WebCodecs (Mediabunny) rather than ffmpeg.wasm. */
export const WEBCODECS_VIDEO = new Set(["vp9", "av1", "h265"]);
export const AUDIO_CODEC_LABELS: Record<string, string> = {
  aac: "AAC", mp3: "MP3", opus: "Opus", vorbis: "Vorbis", flac: "FLAC (lossless)", pcm: "PCM (uncompressed)",
};

// container → [default video, default audio, allowed video, allowed audio]
export const CONTAINERS: Record<string, [string, string, string[], string[]]> = {
  mp4: ["h264", "aac", ["h264", "mpeg4", "vp9", "av1", "h265"], ["aac", "mp3", "opus", "flac"]],
  m4v: ["h264", "aac", ["h264", "mpeg4"], ["aac"]],
  mov: ["h264", "aac", ["h264", "mpeg4", "h265"], ["aac", "pcm", "mp3"]],
  mkv: ["h264", "aac", ["h264", "vp8", "mpeg4", "vp9", "av1", "h265"], ["aac", "mp3", "opus", "vorbis", "flac", "pcm"]],
  webm: ["vp8", "opus", ["vp8", "vp9", "av1"], ["opus", "vorbis"]],
  avi: ["mpeg4", "mp3", ["mpeg4", "h264"], ["mp3", "pcm"]],
};

export const X264_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"];

export function resolveVideoCodec(container: string, choice: string): string {
  const [def, , allowed] = CONTAINERS[container];
  const codec = choice === "auto" ? def : choice;
  if (!allowed.includes(codec)) {
    throw new ConversionError(
      `${VIDEO_CODEC_LABELS[codec] ?? codec} can't be saved as .${container}. Choose “Auto” or one of: ` +
        allowed.map((c) => VIDEO_CODEC_LABELS[c]).join(", ") + ".",
    );
  }
  return codec;
}

export function resolveAudioCodec(container: string, choice: string): string {
  const [, def, , allowed] = CONTAINERS[container];
  const codec = choice === "auto" ? def : choice;
  if (!allowed.includes(codec)) {
    throw new ConversionError(`${AUDIO_CODEC_LABELS[codec] ?? codec} audio can't be saved in .${container}. Choose “Auto”.`);
  }
  return codec;
}

export function pick(available: Set<string>, candidates: string[], label: string): string {
  const enc = candidates.find((c) => available.has(c));
  if (!enc) throw new ConversionError(`The browser version has no encoder for ${label}. Choose another codec.`);
  return enc;
}

/** Rate control. ``crf`` is on the x264 scale (0 best … 51 worst). */
export function qualityArgs(encoder: string, crf: number, preset: string, bitrateKbps: number | null): string[] {
  if (bitrateKbps) {
    const args = ["-b:v", `${bitrateKbps}k`];
    if (encoder === "libx264" || encoder === "libx265") args.push("-preset", preset);
    return args;
  }
  switch (encoder) {
    case "libx264":
      return ["-crf", String(crf), "-preset", preset];
    case "libvpx":
      return ["-crf", String(Math.min(63, Math.max(4, crf + 8))), "-b:v", "10M", "-cpu-used", "4"];
    default:
      return ["-q:v", String(2 + Math.round((crf * 29) / 51))];
  }
}
