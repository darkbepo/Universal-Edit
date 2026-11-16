// Changing a video's details on the website — a line-by-line port of the desktop's
// properties/video.py (both suites check tests/fixtures/video_cases.json): title, description,
// date recorded, the rotation flag (MP4 / MOV / M4V) and each sound / subtitle track's language
// and default flag. FFmpeg copies every track as it is (-c copy); only the details change.
// The desktop reads the file with ffprobe; here ``probeFromLog`` turns FFmpeg's ``-i`` output into
// the same JSON (only the fields used), so ``detailsFromProbe`` is shared.
import { wellFormed, wsTrim } from "./exif";

export const WRITABLE_EXTS = new Set(["mp4", "mov", "m4v", "mkv", "webm"]);
export const ROTATING_EXTS = new Set(["mp4", "mov", "m4v"]);
const MP4_LIKE = ROTATING_EXTS;
export const FIELDS = ["title", "description", "date", "rotation"] as const;
export type Field = (typeof FIELDS)[number];
export const ROTATIONS = [0, 90, 180, 270]; // clockwise
export const TRACK_KINDS = ["audio", "subtitle"] as const;
export type TrackKind = (typeof TRACK_KINDS)[number];
const MP4_SUBTITLES = new Set(["mov_text", "dvd_subtitle"]);
const WEBM_SUBTITLES = new Set(["webvtt"]);
/** The browser keeps the whole new file in memory; above this it's the desktop app's job. */
export const MAX_BYTES = 1.5 * 1024 ** 3;

export const LANGUAGES: [string, string][] = [
  ["ara", "Arabic"], ["baq", "Basque"], ["bul", "Bulgarian"], ["cat", "Catalan"],
  ["chi", "Chinese"], ["hrv", "Croatian"], ["cze", "Czech"], ["dan", "Danish"],
  ["dut", "Dutch"], ["eng", "English"], ["est", "Estonian"], ["fin", "Finnish"],
  ["fre", "French"], ["ger", "German"], ["gre", "Greek"], ["heb", "Hebrew"],
  ["hin", "Hindi"], ["hun", "Hungarian"], ["ice", "Icelandic"], ["ind", "Indonesian"],
  ["ita", "Italian"], ["jpn", "Japanese"], ["kor", "Korean"], ["lav", "Latvian"],
  ["lit", "Lithuanian"], ["nor", "Norwegian"], ["nob", "Norwegian Bokmål"],
  ["nno", "Norwegian Nynorsk"], ["per", "Persian"], ["pol", "Polish"],
  ["por", "Portuguese"], ["rum", "Romanian"], ["rus", "Russian"], ["srp", "Serbian"],
  ["slo", "Slovak"], ["slv", "Slovenian"], ["spa", "Spanish"], ["swe", "Swedish"],
  ["tha", "Thai"], ["tur", "Turkish"], ["ukr", "Ukrainian"], ["vie", "Vietnamese"],
];
const LANGUAGE_ALIASES: Record<string, string> = {
  zho: "chi", ces: "cze", nld: "dut", fra: "fre", deu: "ger", ell: "gre",
  isl: "ice", fas: "per", ron: "rum", slk: "slo", eus: "baq", sve: "swe",
};
const MOV_SPELLING: Record<string, string> = { fre: "fra", swe: "sve", rum: "ron", cze: "ces", slo: "slk" };
const MOV_MISSING = new Set(["hrv", "srp", "nob", "nno"]);
const MOV_LANGUAGE_TEXT = "MOV videos can't store that language — choose another or save as MP4.";

const DATE_RE = /^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/;
export const DATE_TEXT = "Write the date like 2026-11-15, or 2026-11-15 14:30 with the time.";
const DATE_RANGE_TEXT = "The date has to be between the years 1904 and 2100.";
const ROTATION_TEXT = "The rotation has to be 0, 90, 180 or 270 degrees.";
const NOT_ROTATING_TEXT = "MKV and WebM videos can't store a rotation — only MP4, MOV and M4V can.";
const LANGUAGE_TEXT = "A language has to be a 3-letter code like eng.";
export const FORMAT_TEXT = "Only MP4, MOV, M4V, MKV and WebM videos can be changed here.";

export class VideoError extends Error {}

export interface Track { index: number; kind: TrackKind; codec: string; language: string; default: boolean; title: string }
export interface StreamInfo { index: number; type: string; codec: string; cover: boolean; keep: boolean }
export interface VideoDetails {
  container: string; title: string; description: string;
  /** UTC "2026-11-15T14:30:00Z", "" = none */
  date: string;
  /** clockwise */
  rotation: number;
  tracks: Track[]; lost: string[]; streams: StreamInfo[];
}
export interface VideoEdit {
  values: Partial<Record<Field, string>>;
  /** stream index → code ("" = not set) */
  languages: Record<number, string>;
  /** kind → stream index, or null for none */
  defaults: Partial<Record<TrackKind, number | null>>;
}
export type Clean = { title?: string; description?: string; date?: string; rotation?: number };
type Parts = [number, number, number, number, number, number];

export const canChange = (ext: string) => WRITABLE_EXTS.has(ext.toLowerCase());
export const allowsNoDefault = (ext: string) => !MP4_LIKE.has(ext.toLowerCase());
export const isEmpty = (e: VideoEdit) =>
  !Object.keys(e.values).length && !Object.keys(e.languages).length && !Object.keys(e.defaults).length;

// ---------------------------------------------------------------------------- values
const pad = (n: number, w = 2) => String(n).padStart(w, "0");

export function parseDate(text: string): Parts | null {
  const t = wsTrim(text);
  const m = DATE_RE.exec(t);
  if (!m || m[0].length !== t.length) return null;
  const parts = m.slice(1).map((p) => (p === undefined ? 0 : Number(p))) as Parts;
  const [y, mo, d, h, mi, s] = parts;
  if (y < 1 || mo < 1 || mo > 12 || d < 1) return null;
  const days = new Date(Date.UTC(2000, mo, 0)).getUTCDate(); // days in that month (leap year 2000)
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  if (d > (mo === 2 ? (leap ? 29 : 28) : days)) return null;
  if (h > 23 || mi > 59 || s > 59) return null;
  return parts;
}

const utcMs = (p: Parts) => {
  const d = new Date(0);
  d.setUTCFullYear(p[0], p[1] - 1, p[2]);
  d.setUTCHours(p[3], p[4], p[5], 0);
  return d.getTime();
};
const isoOf = (ms: number) => {
  const d = new Date(ms);
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
};

/** Local date parts at ``offset`` minutes east of UTC → "YYYY-MM-DDTHH:MM:SSZ". */
export const utcText = (parts: Parts, offset: number) => isoOf(utcMs(parts) - offset * 60_000);

/** A UTC "…Z" date → "YYYY-MM-DD HH:MM" (":SS" when not 0) at ``offset`` minutes. */
export function localText(iso: string, offset: number): string {
  const m = DATE_RE.exec(wsTrim(iso));
  const parts = m ? parseDate(m[0]) : null;
  if (!parts) return "";
  const d = new Date(utcMs(parts) + offset * 60_000);
  const s = d.getUTCSeconds();
  return `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}${s ? `:${pad(s)}` : ""}`;
}

/** This device's offset from UTC (minutes) at that local time. */
export function localOffset(p: Parts): number {
  const d = new Date(p[0], p[1] - 1, p[2], p[3], p[4], p[5]);
  d.setFullYear(p[0]);
  return Number.isNaN(d.getTime()) ? 0 : -d.getTimezoneOffset();
}

/** This device's offset from UTC (minutes) at that UTC moment. */
export function offsetAtUtc(iso: string): number {
  const m = DATE_RE.exec(wsTrim(iso));
  const parts = m ? parseDate(m[0]) : null;
  if (!parts) return 0;
  return -new Date(utcMs(parts)).getTimezoneOffset();
}

/** A file's creation_time → "…Z"; "" when missing or a zero date (1904-01-01 / 1970-01-01 00:00). */
export function storedDate(stamp: string): string {
  const m = DATE_RE.exec(wsTrim(stamp));
  const parts = m ? parseDate(m[0]) : null;
  if (!parts || parts[0] <= 1904 || parts.join() === "1970,1,1,0,0,0") return "";
  return utcText(parts, 0);
}

/** What the form shows for a stored UTC date (this device's local time). */
export const shownDate = (iso: string) => (iso ? localText(iso, offsetAtUtc(iso)) : "");

export function cleanValues(values: Partial<Record<Field, string>>, offset: (p: Parts) => number = localOffset): Clean {
  const out: Clean = {};
  for (const name of FIELDS) {
    if (!(name in values)) continue;
    const text = wsTrim(wellFormed(values[name] ?? ""));
    if (name === "date") {
      if (!text) {
        out.date = "";
        continue;
      }
      const parts = parseDate(text);
      if (!parts) throw new VideoError(DATE_TEXT);
      if (parts[0] < 1904 || parts[0] > 2100) throw new VideoError(DATE_RANGE_TEXT);
      out.date = utcText(parts, offset(parts));
    } else if (name === "rotation") {
      if (!/^[+-]?\d+$/.test(text)) throw new VideoError(ROTATION_TEXT);
      const turn = Number(text);
      if (!ROTATIONS.includes(turn)) throw new VideoError(ROTATION_TEXT);
      out.rotation = turn;
    } else {
      out[name] = text;
    }
  }
  return out;
}

export function cleanLanguage(code: string): string {
  const c = wsTrim(code).toLowerCase();
  if (c === "" || c === "und") return "";
  if (!/^[a-z]{3}$/.test(c)) throw new VideoError(LANGUAGE_TEXT);
  return LANGUAGE_ALIASES[c] ?? c;
}

export function common(details: Pick<VideoDetails, Field>[]): Record<Field, string | null> {
  const out = {} as Record<Field, string | null>;
  for (const name of FIELDS) {
    const values = new Set(details.map((d) => String(d[name])));
    out[name] = values.size === 1 ? [...values][0] : null;
  }
  return out;
}

// ---------------------------------------------------------------------------- reading
type Dict = Record<string, unknown>;
export interface ProbeStream {
  index?: number; codec_type?: string; codec_name?: string; codec_tag_string?: string;
  disposition?: Record<string, number>; tags?: Record<string, string>; side_data_list?: Dict[];
}
export interface Probe { format?: { tags?: Record<string, string> }; streams?: ProbeStream[] }

const lowerTags = (t?: Record<string, string>) => Object.fromEntries(Object.entries(t ?? {}).map(([k, v]) => [k.toLowerCase(), String(v)]));
const mod360 = (n: number) => ((n % 360) + 360) % 360;

/** How far a player turns the picture, clockwise (display matrix is counter-clockwise). */
export function clockwise(s: ProbeStream): number {
  for (const sd of s.side_data_list ?? []) {
    if ("rotation" in sd) {
      const r = Number(sd.rotation);
      return Number.isFinite(r) ? mod360(Math.round(-r)) : 0;
    }
  }
  const r = Number(s.tags?.rotate ?? 0);
  return Number.isFinite(r) ? mod360(Math.round(r)) : 0;
}

function keepStream(ext: string, s: ProbeStream): [boolean, string | null] {
  const kind = s.codec_type ?? "";
  const codec = s.codec_name ?? "";
  if (kind === "video" || kind === "audio") return [true, null];
  if (kind === "subtitle") {
    if (MP4_LIKE.has(ext) && !MP4_SUBTITLES.has(codec)) return [false, "a subtitle track this format can't hold"];
    if (ext === "webm" && !WEBM_SUBTITLES.has(codec)) return [false, "a subtitle track this format can't hold"];
    return [true, null];
  }
  if (kind === "attachment") return ext === "mkv" ? [true, null] : [false, "an attached file"];
  if (kind === "data") {
    if ((s.codec_tag_string ?? "").toLowerCase() === "tmcd") return [false, null];
    return [false, "a data track (like GPS from an action camera)"];
  }
  return [false, "a track of an unknown kind"];
}

export function detailsFromProbe(data: Probe, ext0: string): VideoDetails {
  const ext = ext0.toLowerCase();
  if (!WRITABLE_EXTS.has(ext)) throw new VideoError(FORMAT_TEXT);
  const fmt = lowerTags(data.format?.tags);
  const d: VideoDetails = {
    container: ext, title: fmt.title ?? "", description: fmt.comment || fmt.description || "",
    date: storedDate(fmt.creation_time ?? ""), rotation: 0, tracks: [], lost: [], streams: [],
  };
  const lost: string[] = [];
  let firstVideo = true;
  for (const s of data.streams ?? []) {
    const index = Number(s.index ?? 0);
    const kind = s.codec_type ?? "";
    const codec = s.codec_name ?? "";
    const disposition = s.disposition ?? {};
    const [keep, why] = keepStream(ext, s);
    const cover = Boolean(disposition.attached_pic);
    d.streams.push({ index, type: kind, codec, cover, keep });
    if (why && !lost.includes(why)) lost.push(why);
    if (kind === "video" && !cover && firstVideo) {
      firstVideo = false;
      d.rotation = clockwise(s);
    }
    if ((kind === "audio" || kind === "subtitle") && keep) {
      const tags = lowerTags(s.tags);
      const lang = tags.language ?? "";
      d.tracks.push({
        index, kind, codec,
        language: lang === "" || lang === "und" ? "" : LANGUAGE_ALIASES[lang] ?? lang,
        default: Boolean(disposition.default), title: tags.title ?? "",
      });
    }
  }
  if (Object.keys(fmt).some((k) => k.startsWith("com.apple.quicktime."))) lost.push("the camera details (make, model, place)");
  d.lost = lost;
  return d;
}

const FORMAT_KEYS = (k: string) => ["title", "comment", "description", "creation_time"].includes(k.toLowerCase()) || k.startsWith("com.apple");
const STREAM_KEYS = (k: string) => ["language", "title", "rotate"].includes(k.toLowerCase());
const DISPOSITIONS: Record<string, string> = { default: "default", "attached pic": "attached_pic" };

/** FFmpeg's ``-i`` output → the parts of ffprobe's JSON that ``detailsFromProbe`` uses (only the
 * first input). */
export function probeFromLog(text: string): Probe {
  const format: Record<string, string> = {};
  const streams: ProbeStream[] = [];
  let target: Record<string, string> | null = null;
  let keyFilter = FORMAT_KEYS;
  let indent = -1;
  let lastKey = "";
  let stream: ProbeStream | null = null;
  let sideData = false;
  let started = false;
  for (const line of text.split(/\r?\n/)) {
    if (/^Input #0\b/.test(line)) {
      started = true;
      continue;
    }
    if (!started) continue;
    if (/^(Input|Output) #[1-9]|^Output #0|^At least one output/.test(line)) break;
    const lead = /^ */.exec(line)?.[0].length ?? 0;
    const st = /^\s+Stream #0:(\d+)(?:\[\w+\])?(?:\((\w+)\))?(?:, \d+, \d+\/\d+)?: (\w+): (\S+)(.*)$/.exec(line);
    if (st) {
      const [, idx, lang, type, codec0, rest] = st;
      const codec = codec0.replace(/,$/, "");
      stream = { index: Number(idx), codec_type: type.toLowerCase(), codec_name: codec, tags: {} };
      if (lang) stream.tags!.language = lang;
      const tag = /\((\w{4}) \/ 0x[0-9A-Fa-f]+\)/.exec(rest);
      if (stream.codec_type === "data" && tag) stream.codec_tag_string = tag[1];
      const disposition: Record<string, number> = {};
      for (const m of rest.matchAll(/\(([a-z ]+)\)/g)) if (DISPOSITIONS[m[1]]) disposition[DISPOSITIONS[m[1]]] = 1;
      if (Object.keys(disposition).length) stream.disposition = disposition;
      streams.push(stream);
      target = null;
      sideData = false;
      continue;
    }
    if (/^\s+Metadata:\s*$/.test(line)) {
      target = stream ? (stream.tags ??= {}) : format;
      keyFilter = stream ? STREAM_KEYS : FORMAT_KEYS;
      indent = lead + 2;
      lastKey = "";
      sideData = false;
      continue;
    }
    if (/^\s+Side data:\s*$/.test(line)) {
      target = null;
      sideData = Boolean(stream);
      continue;
    }
    if (sideData && stream) {
      const rot = /displaymatrix: rotation of (-?[\d.]+) degrees/.exec(line);
      if (rot) (stream.side_data_list ??= []).push({ rotation: Math.round(Number(rot[1])) });
      continue;
    }
    const cont = target && lead > indent ? /^\s+: ?(.*)$/.exec(line) : null;
    if (target && cont && lastKey) {
      if (keyFilter(lastKey)) target[lastKey] += `\n${cont[1]}`;
      continue;
    }
    if (target && lead === indent) {
      const kv = /^\s+(\S[^:]*?)\s*: ?(.*)$/.exec(line);
      if (kv) {
        lastKey = kv[1];
        if (keyFilter(kv[1])) target[kv[1]] = kv[2];
        continue;
      }
    }
    if (lead < indent) target = null;
  }
  for (const s of streams) if (s.tags && !Object.keys(s.tags).length) delete s.tags;
  return { format: { tags: format }, streams };
}

// ---------------------------------------------------------------------------- changing
const mainVideo = (d: VideoDetails) => d.streams.find((s) => s.type === "video" && !s.cover)?.index ?? null;

export function checkEdit(details: VideoDetails, edit: VideoEdit): void {
  const tracks = new Map(details.tracks.map((t) => [t.index, t]));
  for (const [index, code] of Object.entries(edit.languages)) {
    if (!tracks.has(Number(index))) throw new VideoError("That track isn't in this video any more.");
    cleanLanguage(code);
  }
  for (const [kind, chosen] of Object.entries(edit.defaults)) {
    if (!(TRACK_KINDS as readonly string[]).includes(kind)) throw new VideoError("Only sound and subtitle tracks have a default.");
    if (chosen === null && !allowsNoDefault(details.container)) throw new VideoError("MP4, MOV and M4V videos always have a default track of each kind.");
    if (chosen !== null && chosen !== undefined && (!tracks.has(chosen) || tracks.get(chosen)!.kind !== kind)) {
      throw new VideoError("That track isn't in this video any more.");
    }
  }
}

/** FFmpeg arguments that copy ``src`` to ``dst`` with ``edit`` applied (``major`` = FFmpeg's major
 * version, null = new; the browser's is 5). */
export function ffmpegArgs(ext0: string, src: string, dst: string, details: VideoDetails, edit: VideoEdit, clean: Clean, major: number | null): string[] {
  const ext = ext0.toLowerCase();
  if (!WRITABLE_EXTS.has(ext)) throw new VideoError(FORMAT_TEXT);
  if (clean.rotation !== undefined && !ROTATING_EXTS.has(ext)) throw new VideoError(NOT_ROTATING_TEXT);
  const kept = details.streams.filter((s) => s.keep);
  if (!kept.some((s) => s.type === "video" && !s.cover)) throw new VideoError("This file has no picture to keep.");
  const outOf = new Map(kept.map((s, n) => [s.index, n]));
  const video = mainVideo(details);
  const newStyle = major === null || major >= 6;
  const args: string[] = [];
  const turn = clean.rotation;
  if (turn !== undefined && newStyle && video !== null) args.push(`-display_rotation:${video}`, String((360 - turn) % 360));
  args.push("-i", src);
  for (const s of kept) args.push("-map", `0:${s.index}`);
  args.push("-c", "copy", "-map_metadata", "0", "-map_chapters", "0");
  if (turn !== undefined && !newStyle && video !== null) {
    // FFmpeg 5 (ffmpeg.wasm) reads the old "rotate" tag counter-clockwise too (4 and older: clockwise)
    const tag = major === 5 ? (360 - turn) % 360 : turn;
    args.push(`-metadata:s:${outOf.get(video)}`, `rotate=${tag}`);
  }
  if (clean.title !== undefined) args.push("-metadata", `title=${clean.title}`);
  if (clean.description !== undefined) args.push("-metadata", `comment=${clean.description}`, "-metadata", `description=${clean.description}`);
  if (clean.date !== undefined) args.push("-metadata", `creation_time=${clean.date}`);
  const tracks = new Set(details.tracks.map((t) => t.index));
  for (const index of Object.keys(edit.languages).map(Number).sort((a, b) => a - b)) {
    if (!tracks.has(index)) continue;
    let code = cleanLanguage(edit.languages[index]);
    if (ext === "mov") {
      if (MOV_MISSING.has(code)) throw new VideoError(MOV_LANGUAGE_TEXT);
      code = MOV_SPELLING[code] ?? code;
    }
    args.push(`-metadata:s:${outOf.get(index)}`, `language=${code || "und"}`);
  }
  for (const kind of TRACK_KINDS) {
    if (!(kind in edit.defaults)) continue;
    const chosen = edit.defaults[kind];
    for (const t of details.tracks) {
      if (t.kind === kind) args.push(`-disposition:${outOf.get(t.index)}`, t.index === chosen ? "+default" : "-default");
    }
  }
  args.push(dst);
  return args;
}
