// The website video editor's export maths: option schema, output size, file name, which encoders to
// use and a size estimate. Pure (the browser's encoders are asked through `Encoders`), no DOM.
// `exportSize` is a port of export_size in src/umc/videoedit/export.py.
import { opt, type OptionSpec, type Values } from "../core/options";
import { WEBCODECS_LABELS, type WebCodec } from "../engines/webcodecs";
import { estimateBytes, type Quality } from "./limits";
import type { ExportJob, ExportQuality } from "../ui/videoeditor/exporter";
import { layout, length, outputFps, outputSize, type Project } from "./project";

const isGif = (v: Values): boolean => v.container === "gif";
const notGif = (v: Values): boolean => !isGif(v);

export const EXPORT_OPTIONS: OptionSpec[] = [
  opt.choice("container", "Format", "mp4", [
    ["mp4", "MP4"], ["webm", "WebM"], ["mkv", "MKV"], ["mov", "MOV"], ["gif", "GIF"],
  ]),
  opt.choice("video_codec", "Video codec", "auto", [
    ["auto", "Automatic"], ["h264", "H.264"], ["vp9", "VP9"], ["av1", "AV1"], ["h265", "H.265"],
  ], { showIf: notGif }),
  opt.choice("quality", "Quality", "high", [
    ["very-high", "Very high"], ["high", "High"], ["medium", "Medium"], ["low", "Low"],
  ], { showIf: (v) => notGif(v) && !v.video_bitrate }),
  opt.int("video_bitrate", "Video bitrate", 0, {
    min: 0, max: 200000, unit: "kbps", help: "0 = use the quality setting.", showIf: notGif,
  }),
  opt.choice("audio_codec", "Audio codec", "auto", [
    ["auto", "Automatic"], ["aac", "AAC"], ["opus", "Opus"],
  ], { showIf: (v) => notGif(v) && !v.remove_audio }),
  opt.int("audio_bitrate", "Audio bitrate", 192, {
    min: 32, max: 512, unit: "kbps", showIf: (v) => notGif(v) && !v.remove_audio,
  }),
  opt.choice("resolution", "Resolution", "project", [
    ["project", "Project size"], ["480p", "480p"], ["720p", "720p"], ["1080p", "1080p"], ["4k", "4K"],
  ], { help: "Scales the project size to this height." }),
  opt.bool("remove_audio", "Remove audio", false, { showIf: notGif }),
  opt.slider("gif_fps", "GIF frames per second", 15, 1, 50, { unit: "fps", showIf: isGif }),
  opt.int("gif_width", "GIF width", 480, { min: 16, max: 4096, unit: "px", showIf: isGif }),
];

const HEIGHTS: Record<string, number> = { "480p": 480, "720p": 720, "1080p": 1080, "4k": 2160 };

/** Python's round(): halves go to the even number (the desktop's export_size uses it). */
function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

/** The project size, or scaled to a preset height (width kept even). */
export function exportSize(p: Project, resolution: string): [number, number] {
  const [w, h] = outputSize(p);
  const height = Object.hasOwn(HEIGHTS, resolution) ? HEIGHTS[resolution] : undefined;
  if (height === undefined) return [w, h];
  const width = pyRound((w * height) / h);
  return [Math.max(2, width - (width % 2)), height];
}

/** Each side rounded down to even, at least 2 (encoders refuse odd sizes). */
export function evenSize([w, h]: [number, number]): [number, number] {
  const e = (n: number): number => Math.max(2, Math.floor(n) - (Math.floor(n) % 2));
  return [e(w), e(h)];
}

/** GIF size: the width as given (even), the height from the project's aspect (even). */
export function gifSize(p: Project, width: number): [number, number] {
  const [w, h] = outputSize(p);
  return evenSize([width, (width * h) / w]);
}

/** `<first source's name without extension> edit.<ext>`. */
export function exportName(p: Project, ext: string): string {
  const first = p.sources[0];
  if (!first) return `video edit.${ext}`;
  const file = first.path.split(/[\\/]/).pop() ?? "";
  const dot = file.lastIndexOf(".");
  const stem = (dot > 0 ? file.slice(0, dot) : file) || "video";
  return `${stem} edit.${ext}`;
}

export type VideoEncoder = "avc" | "vp9" | "av1" | "hevc" | "vp8";
export type Encoders = {
  video(codec: VideoEncoder): Promise<boolean>;
  audio(codec: "aac" | "opus"): Promise<boolean>;
};

const NO_AUDIO_ENCODER = "Export with sound needs Chrome or Edge — export without sound, or use the desktop app.";
const CHOICE_TO_ENCODER: Record<string, VideoEncoder> = { h264: "avc", vp9: "vp9", av1: "av1", h265: "hevc" };
const WEBM_CHOICES = ["auto", "vp9", "av1"]; // WebM holds VP8 / VP9 / AV1 only
const ENCODER_TO_CHOICE: Record<string, WebCodec> = { avc: "h264", vp9: "vp9", av1: "av1", hevc: "h265" };

async function firstEncodable(enc: Encoders, order: VideoEncoder[]): Promise<VideoEncoder | null> {
  for (const c of order) if (await enc.video(c)) return c;
  return null;
}

export async function chooseCodecs(
  values: Values, hasSound: boolean, enc: Encoders,
): Promise<{ video: string; audio: "aac" | "opus" | null } | { problem: string }> {
  const container = String(values.container ?? "mp4");
  const problem = (c: VideoEncoder): { problem: string } => {
    const label = WEBCODECS_LABELS[ENCODER_TO_CHOICE[c]].replace(/ \(.*\)/, "");
    return { problem: `This browser or device can't encode ${label}. Try another codec, or use the desktop app.` };
  };
  if (container === "gif") {
    const video = await firstEncodable(enc, ["vp9", "vp8"]);
    return video ? { video, audio: null } : { problem: "This browser or device can't encode video. Use the desktop app." };
  }
  const choice = String(values.video_codec ?? "auto");
  if (container === "webm" && !WEBM_CHOICES.includes(choice)) {
    const label = WEBCODECS_LABELS[(choice as WebCodec)]?.replace(/ \(.*\)/, "") ?? choice;
    return { problem: `WebM can't hold ${label} — choose VP9 or AV1, or another format.` };
  }
  let video: VideoEncoder | null;
  if (choice === "auto") {
    video = await firstEncodable(enc, container === "webm" ? ["vp9", "av1", "vp8"] : ["avc", "hevc", "vp9", "av1"]);
    if (!video) return { problem: "This browser or device can't encode video. Use the desktop app." };
  } else {
    video = CHOICE_TO_ENCODER[choice] ?? "avc";
    if (!(await enc.video(video))) return problem(video);
  }
  if (values.remove_audio || !hasSound) return { video, audio: null };
  const want = container === "webm" ? "opus" : String(values.audio_codec ?? "auto") === "opus" ? "opus" : "aac";
  const order: ("aac" | "opus")[] = want === "aac" ? ["aac", "opus"] : ["opus"];
  for (const a of order) if (await enc.audio(a)) return { video, audio: a };
  return { problem: NO_AUDIO_ENCODER };
}

/** The `video_codec` choices to show: "auto" first, then those the browser can encode and the
 *  container can hold (WebM: VP9 / AV1 only). */
export async function availableCodecs(enc: Encoders, container: string): Promise<string[]> {
  const out = ["auto"];
  for (const choice of ["h264", "vp9", "av1", "h265"]) {
    if (container === "webm" && !WEBM_CHOICES.includes(choice)) continue;
    if (await enc.video(CHOICE_TO_ENCODER[choice])) out.push(choice);
  }
  return out;
}

/** Whether the project makes any sound: a video clip with sound that isn't muted, or music. */
export function hasSound(p: Project): boolean {
  const withAudio = new Set(p.sources.filter((s) => s.has_audio).map((s) => s.id));
  if (p.main.some((it) => it.type === "video" && !it.mute && withAudio.has(it.source))) return true;
  return p.music_rows.some((row) => row.length > 0);
}

/** The exporter's job for these export values (`codecs` from chooseCodecs). GIF = a WebM at the
 *  GIF's size and fps, very high quality, no sound (the GIF is made from it afterwards). */
export function jobFor(
  p: Project, values: Values, codecs: { video: string; audio: "aac" | "opus" | null },
): ExportJob {
  if (isGif(values)) {
    return {
      project: p, size: gifSize(p, Number(values.gif_width ?? 480)), fps: Number(values.gif_fps ?? 15),
      video: { codec: codecs.video, bitrate: "very-high" }, audio: null, container: "webm",
    };
  }
  const kbps = Number(values.video_bitrate ?? 0);
  const container = String(values.container ?? "mp4") as ExportJob["container"];
  return {
    project: p,
    size: evenSize(exportSize(p, String(values.resolution ?? "project"))),
    fps: outputFps(p),
    video: { codec: codecs.video, bitrate: kbps > 0 ? kbps * 1000 : (String(values.quality ?? "high") as ExportQuality) },
    audio: codecs.audio && !values.remove_audio
      ? { codec: codecs.audio, bitrate: Number(values.audio_bitrate ?? 192) * 1000 }
      : null,
    container,
  };
}

/** The sources the timeline uses (main clips and music), in the project's source order: the
 *  only ones the export sends to the worker. */
export function usedSourceIds(p: Project): string[] {
  const used = new Set<string>();
  for (const it of p.main) used.add(it.source);
  for (const row of p.music_rows) for (const a of row) used.add(a.source);
  return p.sources.filter((s) => used.has(s.id)).map((s) => s.id);
}

/** For each picture source: when its last main-row item ends (its bitmap can go after that). */
export function pictureLastUse(p: Project): Map<string, number> {
  const spans = layout(p);
  const out = new Map<string, number>();
  for (const it of p.main) {
    if (it.type !== "picture") continue;
    const end = spans.get(it.id)!.end;
    out.set(it.source, Math.max(out.get(it.source) ?? 0, end));
  }
  return out;
}

/** The size the export decodes a w × h picture at for frames of `size`: twice what covering the
 *  frame needs (in either orientation, so cropped / turned pictures keep detail), never larger
 *  than the picture, aspect kept. */
export function pictureDecodeSize(w: number, h: number, [fw, fh]: [number, number]): [number, number] {
  const s = Math.min(1, (2 * Math.max(fw, fh)) / Math.max(1, Math.min(w, h)));
  return s >= 1 ? [w, h] : [Math.max(1, Math.round(w * s)), Math.max(1, Math.round(h * s))];
}

/** About how many bytes the export will be. GIF is a rough guess (half a bit per pixel). */
export function exportEstimate(p: Project, values: Values): number {
  const seconds = length(p);
  if (values.container === "gif") {
    const [w, h] = gifSize(p, Number(values.gif_width ?? 480));
    return (w * h * Number(values.gif_fps ?? 15) * seconds * 0.5) / 8;
  }
  const [width, height] = exportSize(p, String(values.resolution ?? "project"));
  const bitrate = Number(values.video_bitrate ?? 0);
  return estimateBytes({
    seconds, width, height, fps: outputFps(p),
    quality: (String(values.quality ?? "high") as Quality),
    ...(bitrate > 0 ? { videoBitrate: bitrate * 1000 } : {}),
    audioBitrate: Number(values.audio_bitrate ?? 192) * 1000,
    audio: !values.remove_audio,
  });
}

export function frameCount(seconds: number, fps: number): number {
  return Math.max(1, Math.round(seconds * fps));
}
