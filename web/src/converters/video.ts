// Video ↔ video, video → GIF, video → frames, video → audio (all FFmpeg).
import { opt, parseTime, type Values } from "../core/options";
import { ConversionError, type Converter, type OutputFile, type RunContext } from "../core/types";
import {
  AUDIO_CODEC_LABELS, audioEncoderArgs, CONTAINERS, pick, qualityArgs, resolveAudioCodec,
  resolveVideoCodec, VIDEO_CODEC_LABELS, VIDEO_ENCODERS, X264_PRESETS,
} from "./codecs";
import { audioKbpsFor, TWO_PASS_ENCODERS, videoKbpsFor } from "./sizing";
import { WEBCODECS_VIDEO } from "./codecs";
import { baseName, MIME, selectExpr, uniqueNamer } from "./util";

type Engine = import("../engines/ffmpeg").Engine;

async function engine(ctx: RunContext): Promise<Engine> {
  const { getFFmpeg } = await import("../engines/ffmpeg");
  return getFFmpeg((m) => ctx.progress(0, m));
}

export function trimArgs(start: unknown, end: unknown, duration: number): [string[], number] {
  const s = parseTime(start as string);
  const e = parseTime(end as string);
  if (duration && s >= duration) throw new ConversionError("The start time is after the end of the video.");
  if (e && e <= s) throw new ConversionError("The end time must be after the start time.");
  const args: string[] = [];
  if (s) args.push("-ss", s.toFixed(3));
  if (e) args.push("-t", (e - s).toFixed(3));
  const stop = e && duration ? Math.min(e, duration) : e || duration;
  return [args, Math.max(0, stop - s)];
}

export function atempoChain(speed: number): string[] {
  const f: string[] = [];
  let s = speed;
  while (s > 2) { f.push("atempo=2.0"); s /= 2; }
  while (s < 0.5) { f.push("atempo=0.5"); s /= 0.5; }
  f.push(`atempo=${s.toFixed(4)}`);
  return f;
}

/** Shared per-file loop: mount → probe → convert → collect outputs. */
async function eachVideo(
  files: Parameters<Converter["run"]>[0],
  ctx: RunContext,
  verb: string,
  work: (ff: Engine, path: string, info: import("../engines/ffmpeg").MediaInfo, name: string,
         progress: (f: number) => void) => Promise<OutputFile[]>,
): Promise<OutputFile[]> {
  const ff = await engine(ctx);
  const out: OutputFile[] = [];
  for (const [i, f] of files.entries()) {
    const msg = `${verb} ${f.name} (${i + 1}/${files.length})`;
    ctx.progress(i / files.length, msg);
    const { path, release } = await ff.mount(f.file);
    try {
      const info = await ff.probe(path);
      if (!info.hasVideo && !info.hasAudio) throw new ConversionError(`“${f.name}” isn't a readable video.`);
      out.push(...(await work(ff, path, info, f.name, (p) => ctx.progress((i + p) / files.length, msg))));
    } finally {
      await release();
    }
  }
  ctx.progress(1, "Done");
  return out;
}

const encodes = (v: Values) => v.video_codec !== "copy";

const QUALITY_FOR_CRF = (crf: number) => (crf <= 18 ? "very-high" : crf <= 23 ? "high" : crf <= 30 ? "medium" : "low") as "very-high" | "high" | "medium" | "low";

/** VP9 / AV1 / H.265: decode + encode with the browser's WebCodecs (Mediabunny). */
async function runWebCodecs(files: Parameters<Converter["run"]>[0], container: string, o: Values, ctx: RunContext, unique: (n: string) => string): Promise<OutputFile[]> {
  if (Number(o.speed) !== 1 || Number(o.volume) !== 100) {
    throw new ConversionError("Speed and volume changes aren't available with VP9 / AV1 / H.265 in the browser. Use H.264, or the desktop app.");
  }
  const { webcodecsConvert } = await import("../engines/webcodecs");
  const codec = resolveVideoCodec(container, String(o.video_codec)) as "vp9" | "av1" | "h265";
  const out: OutputFile[] = [];
  for (const [i, f] of files.entries()) {
    const msg = `Converting ${f.name} (${i + 1}/${files.length})`;
    ctx.progress(i / files.length, msg);
    const start = parseTime(o.start as string);
    const end = parseTime(o.end as string);
    let videoKbps: number | null = null;
    let source: Blob = f.file;
    let duration = 0;
    const needFfmpeg = Number(o.target_size_mb) > 0;
    const ff = needFfmpeg ? await engine(ctx) : null;
    if (ff) {
      const { path, release } = await ff.mount(f.file);
      try { duration = (await ff.probe(path)).duration; } finally { await release(); }
      const span = Math.max(0.1, (end || duration) - start);
      videoKbps = videoKbpsFor(Number(o.target_size_mb), span, o.remove_audio ? 0 : Number(o.audio_bitrate));
    }
    const job = {
      container, codec,
      height: o.resolution === "original" ? null : Number(o.resolution),
      fps: o.fps === "original" ? null : Number(o.fps),
      quality: QUALITY_FOR_CRF(Number(o.crf)),
      videoKbps,
      audio: o.remove_audio ? ("discard" as const) : { kbps: Number(o.audio_bitrate) },
      rotate: Number(o.rotate) as 0 | 90 | 180 | 270,
      flip: Boolean(o.flip_horizontal),
      start, end,
    };
    let data: Uint8Array;
    try {
      data = await webcodecsConvert(source, job, (p) => ctx.progress((i + p) / files.length, msg));
    } catch (e) {
      if (!(e instanceof ConversionError) || e.message !== "UNREADABLE") throw e;
      // e.g. AVI / FLV / WMV: make a near-lossless H.264 + FLAC MKV with FFmpeg first
      ctx.progress(i / files.length, `Preparing ${f.name} for the browser encoder`);
      const fe = ff ?? (await engine(ctx));
      const { path, release } = await fe.mount(f.file);
      try {
        await fe.run(["-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-crf", "12", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "flac", "/webcodecs-in.mkv"], 0, () => {}, `Couldn't read “${f.name}”.`);
      } finally {
        await release();
      }
      source = new Blob([(await fe.readAndDelete("/webcodecs-in.mkv")) as BlobPart]);
      data = await webcodecsConvert(source, job, (p) => ctx.progress((i + p) / files.length, msg));
    }
    out.push({ name: unique(`${baseName(f.name)}.${container}`), data, type: MIME[container] ?? "video/*" });
  }
  ctx.progress(1, "Done");
  return out;
}

export const videoConverter: Converter = {
  id: "video",
  family: "video",
  options: [
    opt.choice("video_codec", "Video codec", "auto", [["auto", "Auto (best for format)"], ["copy", "Copy — no re-encode (fastest)"], ...Object.entries(VIDEO_CODEC_LABELS)], { group: "Video", help: "VP9, AV1 and H.265 use your browser's own encoder (H.265 only where the device supports it). Speed and volume changes aren't available with them." }),
    opt.float("target_size_mb", "Target file size", 0, { min: 0, step: 0.5, decimals: 1, unit: "MB", group: "Video", showIf: encodes, help: "0 = off. Encodes (in two passes) to land just under this size — e.g. 25 for Discord, 10 for email. Overrides the quality setting." }),
    opt.slider("crf", "Quality (CRF)", 23, 0, 51, { group: "Video", showIf: (v) => encodes(v) && !Number(v.target_size_mb), help: "Lower = better quality and bigger files. 18 ≈ visually lossless, 23 = default, 28+ = small." }),
    opt.choice("preset", "Encoding speed", "veryfast", X264_PRESETS.map((p) => [p, p]), { group: "Video", showIf: encodes, help: "Slower = smaller file at the same quality. In the browser, faster presets are recommended." }),
    opt.choice("resolution", "Resolution", "original", [["original", "Original"], [2160, "4K (2160p)"], [1440, "1440p"], [1080, "1080p"], [720, "720p"], [480, "480p"], [360, "360p"]], { group: "Video", showIf: encodes }),
    opt.choice("fps", "Frame rate", "original", [["original", "Original"], [24, "24"], [25, "25"], [30, "30"], [50, "50"], [60, "60"]], { group: "Video", showIf: encodes }),
    opt.choice("rotate", "Rotate", 0, [[0, "None"], [90, "90° clockwise"], [270, "90° counter-clockwise"], [180, "180°"]], { group: "Video", showIf: encodes }),
    opt.bool("flip_horizontal", "Mirror horizontally", false, { group: "Video", showIf: encodes }),
    opt.choice("speed", "Playback speed", 1, [[0.25, "0.25×"], [0.5, "0.5×"], [0.75, "0.75×"], [1, "1× (normal)"], [1.25, "1.25×"], [1.5, "1.5×"], [2, "2×"], [3, "3×"], [4, "4×"]], { group: "Video", showIf: encodes }),
    opt.bool("remove_audio", "Remove audio", false, { group: "Audio" }),
    opt.choice("audio_codec", "Audio codec", "auto", [["auto", "Auto"], ["copy", "Copy — no re-encode"], ...Object.entries(AUDIO_CODEC_LABELS)], { group: "Audio", showIf: (v) => !v.remove_audio }),
    opt.choice("audio_bitrate", "Audio bitrate", 192, [64, 96, 128, 160, 192, 256, 320].map((b) => [b, `${b} kbps`]), { group: "Audio", showIf: (v) => !v.remove_audio && !["copy", "flac", "pcm"].includes(String(v.audio_codec)) }),
    opt.slider("volume", "Volume", 100, 0, 300, { unit: "%", group: "Audio", showIf: (v) => !v.remove_audio && v.audio_codec !== "copy" }),
    opt.text("start", "Start at", "", { group: "Trim", help: "Seconds or mm:ss. Empty = beginning." }),
    opt.text("end", "End at", "", { group: "Trim", help: "Seconds or mm:ss. Empty = until the end." }),
  ],
  targets() {
    return Object.keys(CONTAINERS).map((c) => ({ id: c, label: c.toUpperCase(), ext: c, group: "Video", converterId: "video" }));
  },
  async run(files, target, o, ctx) {
    const unique = uniqueNamer();
    const container = target.id;
    if (o.video_codec !== "copy" && WEBCODECS_VIDEO.has(resolveVideoCodec(container, String(o.video_codec)))) {
      return runWebCodecs(files, container, o, ctx, unique);
    }
    return eachVideo(files, ctx, "Converting", async (ff, path, info, name, progress) => {
      if (!info.hasVideo) throw new ConversionError(`“${name}” has no video stream.`);
      const [seek, duration] = trimArgs(o.start, o.end, info.duration);
      const copyVideo = o.video_codec === "copy";
      let twoPass = false;
      const speed = copyVideo ? 1 : Number(o.speed);
      const args = [...seek, "-i", path, "-map", "0:v:0"];
      if (info.hasAudio && !o.remove_audio) args.push("-map", "0:a:0?");
      args.push("-map_metadata", "0", "-sn");
      if (copyVideo) {
        args.push("-c:v", "copy");
      } else {
        const codec = resolveVideoCodec(container, String(o.video_codec));
        const enc = pick(ff.encoders, VIDEO_ENCODERS[codec], VIDEO_CODEC_LABELS[codec]);
        const vf: string[] = [];
        if (o.resolution !== "original") vf.push(`scale=-2:${o.resolution}`);
        if (o.rotate === 90) vf.push("transpose=1");
        else if (o.rotate === 270) vf.push("transpose=2");
        else if (o.rotate === 180) vf.push("transpose=1,transpose=1");
        if (o.flip_horizontal) vf.push("hflip");
        if (speed !== 1) vf.push(`setpts=PTS/${speed}`);
        vf.push("scale=trunc(iw/2)*2:trunc(ih/2)*2");
        let bitrate: number | null = null;
        if (Number(o.target_size_mb) > 0) {
          const withAudio = info.hasAudio && !o.remove_audio;
          const audioKbps = withAudio ? (o.audio_codec === "copy" ? 128 : Number(o.audio_bitrate)) : 0;
          bitrate = videoKbpsFor(Number(o.target_size_mb), duration / speed, audioKbps);
          twoPass = TWO_PASS_ENCODERS.has(enc);
        }
        args.push("-vf", vf.join(","), "-c:v", enc, ...qualityArgs(enc, Number(o.crf), String(o.preset), bitrate), "-pix_fmt", "yuv420p");
        if (bitrate && !twoPass) args.push("-maxrate", `${bitrate}k`, "-bufsize", `${bitrate * 2}k`);
        if (o.fps !== "original") args.push("-r", String(o.fps));
      }
      if (o.remove_audio || !info.hasAudio) {
        args.push("-an");
      } else if (o.audio_codec === "copy") {
        args.push("-c:a", "copy");
      } else {
        const acodec = resolveAudioCodec(container, String(o.audio_codec));
        args.push(...audioEncoderArgs(ff.encoders, acodec, AUDIO_CODEC_LABELS[acodec]));
        if (!["flac", "pcm"].includes(acodec)) args.push("-b:a", `${o.audio_bitrate}k`);
        const af = speed !== 1 ? atempoChain(speed) : [];
        if (Number(o.volume) !== 100) af.push(`volume=${(Number(o.volume) / 100).toFixed(2)}`);
        if (af.length) args.push("-af", af.join(","));
      }
      if (["mp4", "m4v", "mov"].includes(container)) args.push("-movflags", "+faststart");
      const dest = `/out.${container}`;
      const error = `Couldn't convert “${name}”.`;
      if (twoPass) {
        await ff.run([...args, "-pass:v", "1", "-passlogfile:v", "/pass", "-an", "-f", "null", "-"], duration / speed, (p) => progress(p / 2), error);
        await ff.run([...args, "-pass:v", "2", "-passlogfile:v", "/pass", dest], duration / speed, (p) => progress(0.5 + p / 2), error);
      } else {
        await ff.run([...args, dest], duration / speed, progress, error);
      }
      return [{ name: unique(`${baseName(name)}.${container}`), data: await ff.readAndDelete(dest), type: MIME[container] ?? "video/*" }];
    });
  },
};

export const gifConverter: Converter = {
  id: "gif",
  family: "video",
  options: [
    opt.slider("fps", "Frames per second", 15, 1, 50, { unit: "fps", help: "More = smoother but bigger." }),
    opt.choice("width", "Width", 480, [["original", "Original"], [240, "240 px"], [320, "320 px"], [480, "480 px"], [640, "640 px"], [800, "800 px"], [1080, "1080 px"]], { help: "Height follows the aspect ratio." }),
    opt.text("start", "Start at", "", { help: "Seconds or mm:ss. Empty = beginning." }),
    opt.text("end", "End at", "", { help: "Seconds or mm:ss. Empty = until the end." }),
    opt.choice("speed", "Playback speed", 1, [[0.5, "0.5×"], [0.75, "0.75×"], [1, "1×"], [1.5, "1.5×"], [2, "2×"], [3, "3×"]]),
    opt.choice("loop", "Loop", 0, [[0, "Forever"], [-1, "Play once"], [1, "Repeat 1×"], [3, "Repeat 3×"]]),
    opt.slider("max_colors", "Max colours", 256, 2, 256),
    opt.choice("dither", "Dithering", "sierra2_4a", [["sierra2_4a", "Sierra Lite (balanced)"], ["floyd_steinberg", "Floyd–Steinberg"], ["bayer", "Bayer (pattern, small files)"], ["none", "None (smallest)"]]),
  ],
  targets() {
    return [{ id: "video:gif", label: "GIF", ext: "gif", group: "Animation", converterId: "gif", hint: "Animated GIF" }];
  },
  async run(files, _t, o, ctx) {
    const unique = uniqueNamer();
    return eachVideo(files, ctx, "Making GIF from", async (ff, path, info, name, progress) => {
      const [seek, duration] = trimArgs(o.start, o.end, info.duration);
      const speed = Number(o.speed);
      const chain = [speed !== 1 ? `setpts=PTS/${speed}` : "", `fps=${o.fps}`, o.width !== "original" ? `scale=${o.width}:-1:flags=lanczos` : ""].filter(Boolean).join(",");
      const graph = `[0:v]${chain},split[a][b];[a]palettegen=max_colors=${o.max_colors}[p];[b][p]paletteuse=dither=${o.dither}`;
      await ff.run([...seek, "-i", path, "-filter_complex", graph, "-an", "-loop", String(o.loop), "/out.gif"], duration / speed, progress, `Couldn't make a GIF from “${name}”.`);
      return [{ name: unique(`${baseName(name)}.gif`), data: await ff.readAndDelete("/out.gif"), type: "image/gif" }];
    });
  },
};

const FRAME_FORMATS: Record<string, string> = { png: "PNG", jpg: "JPG", webp: "WEBP", bmp: "BMP" };

export const framesConverter: Converter = {
  id: "frames",
  family: "video",
  options: [
    opt.slider("frames_per_30", "Frames to keep per 30 frames", 10, 1, 30, { help: "30 = every frame, 10 = every 3rd frame, 1 = one frame per 30. At 30 fps, 10 gives 10 images per second." }),
    opt.slider("quality", "Quality", 92, 1, 100, { unit: "%", targets: ["frames:jpg", "frames:webp"] }),
    opt.choice("scale", "Size", "original", [["original", "Original"], [1080, "1080p"], [720, "720p"], [480, "480p"], [360, "360p"]]),
    opt.text("start", "Start at", "", { help: "Seconds or mm:ss. Empty = beginning." }),
    opt.text("end", "End at", "", { help: "Seconds or mm:ss. Empty = until the end." }),
  ],
  targets() {
    return Object.entries(FRAME_FORMATS).map(([ext, label]) => ({
      id: `frames:${ext}`, label, ext, group: "Images (frame sequence)", converterId: "frames", hint: `Export frames as ${label} images`,
    }));
  },
  async run(files, target, o, ctx) {
    const ext = target.ext;
    return eachVideo(files, ctx, "Extracting frames from", async (ff, path, info, name, progress) => {
      if (!info.hasVideo) throw new ConversionError(`“${name}” has no video stream.`);
      const [seek, duration] = trimArgs(o.start, o.end, info.duration);
      const vf = [selectExpr(Number(o.frames_per_30)), o.scale !== "original" ? `scale=-2:${o.scale}` : ""].filter(Boolean);
      const dir = "/frames";
      await ff.ff.createDir(dir).catch(() => {});
      const args = [...seek, "-i", path, "-an", "-sn"];
      if (vf.length) args.push("-vf", vf.join(","));
      args.push("-fps_mode", "vfr", "-start_number", "1");
      if (ext === "jpg") args.push("-q:v", String(Math.round(31 - ((Number(o.quality) - 1) * 29) / 99)));
      if (ext === "webp") args.push("-quality", String(o.quality));
      await ff.run([...args, `${dir}/frame_%05d.${ext}`], duration, progress, `Couldn't extract frames from “${name}”.`);
      const stem = baseName(name);
      const out: OutputFile[] = [];
      for (const f of await ff.listFiles(dir)) {
        out.push({ name: `${stem}_frames/${stem}_${f.slice(6)}`, data: await ff.readAndDelete(`${dir}/${f}`), type: MIME[ext] });
      }
      await ff.ff.deleteDir(dir).catch(() => {});
      return out;
    });
  },
};

const AUDIO_TARGETS: Record<string, [string, string]> = {
  mp3: ["MP3", "mp3"], m4a: ["M4A (AAC)", "aac"], wav: ["WAV", "pcm"], flac: ["FLAC", "flac"],
  ogg: ["OGG", "vorbis"], opus: ["OPUS", "opus"], aiff: ["AIFF", "pcm_be"], wma: ["WMA", "wma"], ac3: ["AC-3", "ac3"],
};
const LOSSY_EXTS = ["mp3", "m4a", "ogg", "opus", "wma", "ac3"];
// Option targets cover both id styles: video → audio ("audio:mp3") and audio → audio ("mp3").
const LOSSY_AUDIO = [...LOSSY_EXTS.map((e) => `audio:${e}`), ...LOSSY_EXTS];

function makeAudioConverter(id: string, family: "video" | "audio", idPrefix: string, group: string, verb: string): Converter {
  return {
    id,
    family,
    options: [
      opt.choice("bitrate", "Bitrate", 192, [64, 96, 128, 160, 192, 256, 320].map((b) => [b, `${b} kbps`]), { targets: LOSSY_AUDIO }),
      opt.float("target_size_mb", "Target file size", 0, { min: 0, step: 0.5, decimals: 1, unit: "MB", targets: LOSSY_AUDIO, help: "0 = off. Picks the bitrate that lands just under this size (overrides Bitrate)." }),
      opt.choice("sample_rate", "Sample rate", "original", [["original", "Original"], [22050, "22.05 kHz"], [44100, "44.1 kHz"], [48000, "48 kHz"], [96000, "96 kHz"]]),
      opt.choice("channels", "Channels", "original", [["original", "Original"], [1, "Mono"], [2, "Stereo"]]),
      opt.slider("volume", "Volume", 100, 0, 300, { unit: "%" }),
      opt.bool("normalize", "Normalise loudness", false, { help: "EBU R128 loudness normalisation." }),
      opt.text("start", "Start at", "", { help: "Seconds or mm:ss. Empty = beginning." }),
      opt.text("end", "End at", "", { help: "Seconds or mm:ss. Empty = until the end." }),
    ],
    targets(files) {
      if (family === "video" && files.some((f) => f.tracks?.audio === false)) return []; // silent video
      return Object.entries(AUDIO_TARGETS).map(([ext, [label]]) => ({
        id: `${idPrefix}${ext}`, label, ext, group, converterId: id,
        hint: family === "video" ? "Extract the soundtrack" : undefined,
      }));
    },
    async run(files, target, o, ctx) {
      const unique = uniqueNamer();
      const ext = target.ext;
      const codec = AUDIO_TARGETS[ext][1];
      return eachVideo(files, ctx, verb, async (ff, path, info, name, progress) => {
        if (!info.hasAudio) throw new ConversionError(`“${name}” has no audio track.`);
        const [seek, duration] = trimArgs(o.start, o.end, info.duration);
        // -vn also drops embedded cover art, which most audio containers can't hold.
        const args = [...seek, "-i", path, "-vn", "-sn", "-map", "0:a:0", "-map_metadata", "0",
          ...audioEncoderArgs(ff.encoders, codec, AUDIO_CODEC_LABELS[codec] ?? codec.toUpperCase())];
        if (LOSSY_EXTS.includes(ext)) {
          const kbps = Number(o.target_size_mb) > 0 ? audioKbpsFor(Number(o.target_size_mb), duration) : Number(o.bitrate);
          args.push("-b:a", `${kbps}k`);
        }
        if (o.sample_rate !== "original") args.push("-ar", String(o.sample_rate));
        if (o.channels !== "original") args.push("-ac", String(o.channels));
        const af = [o.normalize ? "loudnorm" : "", Number(o.volume) !== 100 ? `volume=${(Number(o.volume) / 100).toFixed(2)}` : ""].filter(Boolean);
        if (af.length) args.push("-af", af.join(","));
        const dest = `/out.${ext}`;
        await ff.run([...args, dest], duration, progress, `Couldn't convert the audio of “${name}”.`);
        return [{ name: unique(`${baseName(name)}.${ext}`), data: await ff.readAndDelete(dest), type: MIME[ext] ?? "audio/*" }];
      });
    },
  };
}

/** Video → audio only. */
export const audioConverter = makeAudioConverter("audio", "video", "audio:", "Audio only", "Extracting audio from");
/** Audio ↔ audio. */
export const audioFileConverter = makeAudioConverter("audio-file", "audio", "", "Audio", "Converting");
