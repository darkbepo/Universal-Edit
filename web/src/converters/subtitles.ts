// Subtitles (FFmpeg): SRT/VTT/ASS/TTML/LRC ↔ each other, extract from video, burn into / add to
// video. A port of the desktop converters/subtitles.py — same target ids, options and rules.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type RunContext, type Target } from "../core/types";
import { baseName } from "./util";

type Engine = import("../engines/ffmpeg").Engine;

const OUT: Record<string, string> = { srt: "SRT", vtt: "VTT", ass: "ASS", ttml: "TTML", lrc: "LRC" };
const MUXER: Record<string, string> = { srt: "srt", vtt: "webvtt", ass: "ass", ttml: "ttml", lrc: "lrc" };
const CODEC: Record<string, string> = { srt: "subrip", vtt: "webvtt", ass: "ass", ttml: "ttml" };
const TEXT_CODECS = new Set(["subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "subviewer", "sami", "realtext", "jacosub", "mpl2", "pjs", "vplayer", "stl", "ttml"]);
const STYLE = ["ass", "burn:mp4"];
const TRACK = ["subtrack:mkv", "subtrack:mp4"];
const POSITIONS: Record<string, number> = { bottom: 2, top: 8, middle: 5 };
const MIME: Record<string, string> = { srt: "application/x-subrip", vtt: "text/vtt", ass: "text/x-ssa", ttml: "application/ttml+xml", lrc: "text/plain", mp4: "video/mp4", mkv: "video/x-matroska" };
export const ISO639_2: Record<string, string> = {
  da: "dan", en: "eng", de: "ger", fr: "fre", es: "spa", it: "ita", pt: "por", nl: "dut", sv: "swe", no: "nor", nb: "nob",
  nn: "nno", fi: "fin", is: "ice", pl: "pol", cs: "cze", sk: "slo", hu: "hun", ro: "rum", el: "gre", tr: "tur", ru: "rus",
  uk: "ukr", ar: "ara", he: "heb", hi: "hin", fa: "per", zh: "chi", ja: "jpn", ko: "kor", vi: "vie", th: "tha", id: "ind",
  hr: "hrv", sr: "srp", bg: "bul", et: "est", lv: "lav", lt: "lit", sl: "slv", ca: "cat", eu: "baq",
};
const CHARSETS: [string, string][] = [
  ["windows-1252", "Western European (Windows-1252)"], ["iso-8859-1", "Latin-1 (ISO-8859-1)"],
  ["windows-1250", "Central European (Windows-1250)"], ["windows-1251", "Cyrillic (Windows-1251)"],
  ["windows-1253", "Greek (Windows-1253)"], ["windows-1254", "Turkish (Windows-1254)"], ["windows-1256", "Arabic (Windows-1256)"],
  ["shift_jis", "Japanese (Shift-JIS)"], ["gb18030", "Chinese Simplified (GB18030)"], ["big5", "Chinese Traditional (Big5)"],
  ["euc-kr", "Korean (EUC-KR)"],
];

const options = [
  opt.float("shift", "Shift timing", 0, { min: -3600, max: 3600, decimals: 2, unit: "s", help: "Positive = subtitles appear later, negative = earlier." }),
  opt.choice("charset", "Text encoding of old files", "windows-1252", CHARSETS, { help: "UTF-8 is detected automatically; this is used only for files that aren't UTF-8." }),
  opt.slider("font_size", "Font size", 48, 12, 120, { unit: "px", targets: STYLE, help: "At 1080p height; scales with the video." }),
  opt.color("colour", "Text colour", "#ffffff", { targets: STYLE }),
  opt.color("outline_colour", "Outline colour", "#000000", { targets: STYLE }),
  opt.float("outline", "Outline thickness", 2.5, { min: 0, max: 10, decimals: 1, targets: STYLE }),
  opt.choice("position", "Position", "bottom", [["bottom", "Bottom"], ["top", "Top"], ["middle", "Middle"]], { targets: STYLE }),
  opt.slider("margin", "Distance from the edge", 40, 0, 300, { unit: "px", targets: STYLE }),
  opt.bool("restyle_ass", "Also restyle ASS files", false, { targets: STYLE, help: "ASS/SSA subtitles bring their own look; tick to use the settings above instead." }),
  opt.slider("crf", "Video quality (CRF, lower = better)", 23, 0, 40, { targets: ["burn:mp4"] }),
  opt.text("language", "Subtitle language", "auto", { targets: TRACK, help: "3-letter code like eng, dan, deu. Auto = from the file name (movie.da.srt)." }),
  opt.bool("default_track", "Show the first subtitle track by default", true, { targets: TRACK }),
];

// ------------------------------------------------------------------------- helpers

export function decode(data: Uint8Array, fallback: string): string {
  if (data[0] === 0xef && data[1] === 0xbb && data[2] === 0xbf) return new TextDecoder("utf-8").decode(data.subarray(3));
  if (data[0] === 0xff && data[1] === 0xfe) return new TextDecoder("utf-16le").decode(data.subarray(2));
  if (data[0] === 0xfe && data[1] === 0xff) return new TextDecoder("utf-16be").decode(data.subarray(2));
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(data);
  } catch {
    return new TextDecoder(fallback).decode(data);
  }
}

export function guessLanguage(name: string): string {
  const m = /\.([a-z]{2,3})(?:[._-](?:forced|sdh|cc|hi))?$/i.exec(baseName(name));
  if (!m) return "und";
  const code = m[1].toLowerCase();
  return code.length === 2 ? ISO639_2[code] ?? code : code;
}

const assColour = (hex: string, alpha = 0) => {
  const h = hex.replace("#", "");
  return `&H${alpha.toString(16).padStart(2, "0")}${h.slice(4, 6)}${h.slice(2, 4)}${h.slice(0, 2)}`.toUpperCase();
};

/** Set font, size, colours, outline and position of the "Default" style (and PlayResY). */
export function restyle(ass: string, o: Values, playResY = 1080): string {
  const lines = ass.split(/\r?\n/);
  let fmt: string[] = [];
  let inStyles = false;
  lines.forEach((line, i) => {
    const low = line.trim().toLowerCase();
    if (low.startsWith("[")) inStyles = low === "[v4+ styles]" || low === "[v4 styles]";
    else if (low.startsWith("playresy:")) lines[i] = `PlayResY: ${playResY}`;
    else if (inStyles && low.startsWith("format:")) fmt = line.split(":").slice(1).join(":").split(",").map((f) => f.trim().toLowerCase());
    else if (inStyles && low.startsWith("style:") && fmt.length) {
      const values = line.split(":").slice(1).join(":").split(",").map((v) => v.trim());
      if (values.length !== fmt.length || (values[0].toLowerCase() !== "default" && !o.restyle_ass)) return;
      const set: Record<string, string> = {
        fontname: "DejaVu Sans", fontsize: String(o.font_size), primarycolour: assColour(String(o.colour)),
        outlinecolour: assColour(String(o.outline_colour)), backcolour: assColour(String(o.outline_colour), 0x80),
        outline: String(Number(o.outline)), shadow: "0", borderstyle: "1", alignment: String(POSITIONS[String(o.position)]),
        marginv: String(o.margin),
      };
      lines[i] = "Style: " + fmt.map((name, k) => set[name] ?? values[k]).join(",");
    }
  });
  let text = lines.join("\n");
  if (!/^PlayResY:/im.test(text)) text = text.replace(/^\[Script Info\][^\n]*\n/im, (m) => `${m}PlayResY: ${playResY}\nPlayResX: ${Math.floor((playResY * 16) / 9)}\n`);
  return text.endsWith("\n") ? text : text + "\n";
}

let workCounter = 0;

/** Write a UTF-8 copy of a subtitle file into FFmpeg's file system. */
async function normalise(ff: Engine, f: InputFile, o: Values): Promise<string> {
  const text = decode(new Uint8Array(await f.file.arrayBuffer()), String(o.charset));
  const path = `/sub${++workCounter}.${f.ext}`;
  await ff.ff.writeFile(path, new TextEncoder().encode(text));
  return path;
}

async function convertSub(ff: Engine, src: string, dest: string, fmt: string, o: Values, name: string, map = "0:s:0"): Promise<Uint8Array> {
  const args = [];
  if (Number(o.shift)) args.push("-itsoffset", Number(o.shift).toFixed(3));
  args.push("-i", src, "-map", map);
  if (CODEC[fmt]) args.push("-c:s", CODEC[fmt]);
  args.push("-f", MUXER[fmt], dest);
  await ff.run(args, 0, () => {}, `Couldn't convert “${name}”.`);
  const data = await ff.readAndDelete(dest);
  if (!data.byteLength) throw new ConversionError(`“${name}” doesn't contain any subtitles FFmpeg can read.`);
  return data;
}

async function engine(ctx: RunContext): Promise<Engine> {
  const { getFFmpeg } = await import("../engines/ffmpeg");
  return getFFmpeg((m) => ctx.progress(0, m));
}

// ------------------------------------------------------------------------- converters

export const subtitleConverter: Converter = {
  id: "subtitles",
  family: "subtitle",
  options,
  targets(files) {
    const single = files.length === 1 ? files[0].ext : "";
    return Object.entries(OUT).filter(([e]) => e !== single).map(([e, label]) => ({ id: e, label, ext: e, group: "Subtitles", converterId: "subtitles" }));
  },
  async run(files, target, o, ctx) {
    const ff = await engine(ctx);
    const fmt = target.ext;
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name}`);
      const src = await normalise(ff, f, o);
      let data = await convertSub(ff, src, `/out.${fmt}`, fmt, o, f.name);
      await ff.ff.deleteFile(src).catch(() => {});
      if (fmt === "ass" && (!["ass", "ssa"].includes(f.ext) || o.restyle_ass)) data = new TextEncoder().encode(restyle(new TextDecoder().decode(data), o));
      out.push({ name: `${baseName(f.name)}.${fmt}`, data, type: MIME[fmt] });
    }
    ctx.progress(1, "Done");
    return out;
  },
};

export const subtitleExtractConverter: Converter = {
  id: "subtitles-extract",
  family: "video",
  options,
  targets() {
    return ["srt", "vtt", "ass"].map((e) => ({ id: `subs:${e}`, label: OUT[e], ext: e, group: "Subtitles", converterId: "subtitles-extract", hint: "Save the subtitle tracks inside the video" }));
  },
  async run(files, target, o, ctx) {
    const ff = await engine(ctx);
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Reading subtitle tracks of ${f.name}`);
      const { path, release } = await ff.mount(f.file);
      try {
        const streams = await ff.subtitleStreams(path);
        const text = streams.filter((s) => TEXT_CODECS.has(s.codec));
        if (!text.length) {
          throw new ConversionError(`“${f.name}” has no text subtitles.${streams.length ? " Its subtitles are pictures (Blu-ray/DVD style), which can't be turned into text." : ""}`);
        }
        for (const s of text) {
          const data = await convertSub(ff, path, `/out.${target.ext}`, target.ext, o, f.name, `0:s:${s.index}`);
          out.push({ name: `${baseName(f.name)}.${s.language || `track${s.index + 1}`}.${target.ext}`, data, type: MIME[target.ext] });
        }
      } finally {
        await release();
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};

const split = (files: InputFile[]) => ({ videos: files.filter((f) => f.family === "video"), subs: files.filter((f) => f.family === "subtitle") });

export const subtitleVideoConverter: Converter = {
  id: "subtitles-video",
  family: "video", // unused: anyFamily offers it for the mixed video + subtitles set
  anyFamily: true,
  options,
  targets(files) {
    const { videos, subs } = split(files);
    if (videos.length !== 1 || !subs.length || videos.length + subs.length !== files.length) return [];
    const t = (id: string, label: string, ext: string, hint: string): Target => ({ id, label, ext, hint, group: "Subtitles", converterId: "subtitles-video" });
    return [
      ...(subs.length === 1 ? [t("burn:mp4", "Burn into video", "mp4", "Draw the subtitles onto the picture (always visible)")] : []),
      t("subtrack:mkv", "Add track (MKV)", "mkv", "Add as a subtitle track you can switch on/off"),
      t("subtrack:mp4", "Add track (MP4)", "mp4", "Add as a subtitle track you can switch on/off"),
    ];
  },
  async run(files, target, o, ctx) {
    const ff = await engine(ctx);
    const { videos: [video], subs } = split(files);
    const { path, release } = await ff.mount(video.file);
    const stem = baseName(video.name);
    try {
      const info = await ff.probe(path);
      if (target.id === "burn:mp4") {
        ctx.progress(0.02, "Preparing subtitles");
        const src = await normalise(ff, subs[0], o);
        let ass = new TextDecoder().decode(await convertSub(ff, src, "/subs-out.ass", "ass", o, subs[0].name));
        if (!["ass", "ssa"].includes(subs[0].ext) || o.restyle_ass) ass = restyle(ass, o);
        await ff.ff.writeFile("/subs.ass", new TextEncoder().encode(ass));
        await ensureFont(ff);
        const enc = ff.pick(["libx264", "libopenh264", "mpeg4"]) ?? "mpeg4";
        const args = ["-i", path, "-vf", "ass=/subs.ass:fontsdir=/fonts,format=yuv420p", "-c:v", enc];
        if (enc === "libx264") args.push("-crf", String(o.crf), "-preset", "veryfast");
        if (info.hasAudio) args.push("-c:a", "aac", "-b:a", "192k");
        args.push("-movflags", "+faststart", "/out.mp4");
        await ff.run(args, info.duration, (p) => ctx.progress(0.05 + 0.95 * p, `Burning subtitles into ${video.name}`), `Couldn't burn the subtitles into “${video.name}”.`);
        ctx.progress(1, "Done");
        return [{ name: `${stem} (subtitled).mp4`, data: await ff.readAndDelete("/out.mp4"), type: MIME.mp4 }];
      }
      const container = target.ext;
      const args = ["-i", path];
      const maps = ["-map", "0:v?", "-map", "0:a?"];
      const meta: string[] = [];
      for (const [n, sub] of subs.entries()) {
        const src = await normalise(ff, sub, o);
        const fmt = container === "mkv" && ["ass", "ssa"].includes(sub.ext) ? "ass" : "srt";
        const staged = `/track${n}.${fmt}`;
        await ff.ff.writeFile(staged, await convertSub(ff, src, `/staged.${fmt}`, fmt, o, sub.name));
        args.push("-i", staged);
        maps.push("-map", `${n + 1}:0`);
        const chosen = String(o.language ?? "auto").trim().toLowerCase();
        const lang = chosen && chosen !== "auto" ? ISO639_2[chosen] ?? chosen : guessLanguage(sub.name);
        meta.push(`-metadata:s:s:${n}`, `language=${lang}`, `-metadata:s:s:${n}`, `title=${baseName(sub.name)}`, `-disposition:s:${n}`, n === 0 && o.default_track ? "default" : "0");
      }
      const dest = `/out.${container}`;
      try {
        await ff.run([...args, ...maps, "-c:v", "copy", "-c:a", "copy", "-c:s", container === "mp4" ? "mov_text" : "copy", ...meta, dest],
          info.duration, (p) => ctx.progress(p, `Adding subtitles to ${video.name}`), `Couldn't add the subtitles to “${video.name}”.`);
      } catch (e) {
        if (container === "mp4" && /codec/i.test((e as ConversionError).details ?? "")) {
          throw new ConversionError(`The video in “${video.name}” can't go into MP4 without re-encoding. Use “Add track (MKV)”.`, (e as ConversionError).details);
        }
        throw e;
      }
      ctx.progress(1, "Done");
      return [{ name: `${stem} (with subtitles).${container}`, data: await ff.readAndDelete(dest), type: MIME[container] }];
    } finally {
      await release();
    }
  },
};

let fontReady: Promise<void> | null = null;
function ensureFont(ff: Engine): Promise<void> {
  if (!fontReady) {
    fontReady = (async () => {
      const { default: url } = await import("dejavu-fonts-ttf/ttf/DejaVuSans.ttf?url");
      const data = new Uint8Array(await (await fetch(url)).arrayBuffer());
      await ff.ff.createDir("/fonts").catch(() => {});
      await ff.ff.writeFile("/fonts/DejaVuSans.ttf", data);
    })();
    fontReady.catch(() => (fontReady = null));
  }
  return fontReady;
}
