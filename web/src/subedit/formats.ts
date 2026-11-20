// Subtitle files as a list of cues — a line-by-line port of src/umc/subedit/formats.py (same
// cases: tests/fixtures/subedit_cases.json). A cue's text keeps the file's own markup (<i> in
// SRT / VTT / SMI, {\i1} in ASS / SSA, none in LRC) with "\n" between lines.

export const READ_FORMATS = ["srt", "vtt", "ass", "ssa", "lrc", "smi"];
export const WRITE_FORMATS = ["srt", "vtt", "ass", "ssa", "lrc"];
export const LRC_LAST_MS = 4000; // an LRC line with nothing after it is shown this long
export const MAX_CUES = 100_000;

export type Extra = [string, string][];

export interface Cue {
  start: number; // ms
  end: number; // ms
  text: string; // "\n" between lines, markup as in the file
  extra: Extra; // format fields kept as written (ASS fields, VTT id / settings)
}

export interface SubDoc {
  format: string;
  cues: Cue[];
  header: string; // ASS / SSA up to the events' Format line, VTT's WEBVTT block, LRC tags
  footer: string; // ASS / SSA sections after [Events]
  fields: string[]; // ASS / SSA event field names
}

export class SubtitleError extends Error {}

export const cue = (start: number, end: number, text: string, extra: Extra = []): Cue => ({ start, end, text, extra });
export const extraGet = (c: Cue, key: string, fallback = "") => c.extra.find(([k]) => k === key)?.[1] ?? fallback;
const isComment = (c: Cue) => extraGet(c, "kind") === "Comment";

export function markupKind(fmt: string): string {
  return ({ ass: "ass", ssa: "ass", lrc: "plain" } as Record<string, string>)[fmt] ?? "html";
}

// ------------------------------------------------------------------------------------ times
const CLOCK = /^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.:](\d{1,3}))?$/;
const ARROW = /^\s*((?:\d+:)?\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)\s*-->\s*((?:\d+:)?\d{1,2}:\d{1,2}(?:[,.]\d{1,3})?)(.*)$/;

function clockMs(text: string): number | null {
  const m = CLOCK.exec(text.trim());
  if (!m) return null;
  const [, h, mi, s, frac] = m;
  const ms = Number((frac ?? "0").padEnd(3, "0"));
  return ((Number(h ?? 0) * 60 + Number(mi)) * 60 + Number(s)) * 1000 + ms;
}

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

function split3(ms: number): [number, number, number, number] {
  ms = Math.max(0, ms);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  return [h, m, s, ms % 1000];
}

function hms(ms: number, sep: string): string {
  const [h, m, s, rest] = split3(ms);
  return `${pad(h)}:${pad(m)}:${pad(s)}${sep}${pad(rest, 3)}`;
}

function assTime(ms: number): string {
  const cs = Math.floor((Math.max(0, ms) + 5) / 10);
  const h = Math.floor(cs / 360_000);
  const m = Math.floor((cs % 360_000) / 6000);
  const s = Math.floor((cs % 6000) / 100);
  return `${h}:${pad(m)}:${pad(s)}.${pad(cs % 100)}`;
}

function lrcTime(ms: number): string {
  const cs = Math.floor((Math.max(0, ms) + 5) / 10);
  const m = Math.floor(cs / 6000);
  const s = Math.floor((cs % 6000) / 100);
  return `[${pad(m)}:${pad(s)}.${pad(cs % 100)}]`;
}

/** How the editor shows a time: 0:01:02.500. */
export function clock(ms: number): string {
  const [h, m, s, rest] = split3(ms);
  return `${h}:${pad(m)}:${pad(s)}.${pad(rest, 3)}`;
}

// ----------------------------------------------------------------------------------- markup
const ASS_BLOCK = /\{([^}]*)\}/g;
const ASS_BASIC = /\\([ibus])([01])(?![0-9])/g;
const HTML_TAG = /<(\/?)([a-zA-Z]+)(?:[\s.][^>]*)?>|<[0-9:.]+>/g;
const BASIC = new Set(["i", "b", "u", "s"]);

function assToHtml(text: string): string {
  return text
    .replace(ASS_BLOCK, (_, body: string) =>
      [...body.matchAll(ASS_BASIC)].map(([, tag, on]) => `<${on === "1" ? "" : "/"}${tag}>`).join(""),
    )
    .replaceAll("\\h", " ");
}

function htmlBasic(text: string): string {
  return text.replace(HTML_TAG, (_, slash: string | undefined, name: string | undefined) => {
    const n = (name ?? "").toLowerCase();
    return BASIC.has(n) ? `<${slash ?? ""}${n}>` : "";
  });
}

function htmlToAss(text: string): string {
  return text
    .replaceAll("{", "(")
    .replaceAll("}", ")")
    .replace(HTML_TAG, (_, slash: string | undefined, name: string | undefined) => {
      const n = (name ?? "").toLowerCase();
      return BASIC.has(n) ? `{\\${n}${slash ? "0" : "1"}}` : "";
    });
}

/** The words a viewer reads (markup removed). */
export function plainText(text: string, fmt: string): string {
  const kind = markupKind(fmt);
  if (kind === "ass") return text.replace(ASS_BLOCK, "").replaceAll("\\h", " ");
  if (kind === "html") return text.replace(HTML_TAG, "");
  return text;
}

export function convertMarkup(text: string, src: string, dst: string): string {
  const a = markupKind(src), b = markupKind(dst);
  if (src === dst || (a === "ass" && b === "ass")) return text;
  if (b === "plain") return plainText(text, src);
  if (a === "ass") return b === "html" ? assToHtml(text) : text;
  if (b === "ass") return htmlToAss(text);
  return htmlBasic(text);
}

// ---------------------------------------------------------------------------------- parsing
const isBlank = (s: string) => s.trim() === "";

function blocks(text: string): string[][] {
  const out: string[][] = [];
  let cur: string[] = [];
  for (const line of text.split("\n")) {
    if (!isBlank(line)) cur.push(line.trimEnd());
    else if (cur.length) {
      out.push(cur);
      cur = [];
    }
  }
  if (cur.length) out.push(cur);
  return out;
}

function firstIndex(lines: string[], test: (s: string) => boolean): number | null {
  const i = lines.slice(0, 2).findIndex(test);
  return i < 0 ? null : i;
}

function parseSrt(text: string): SubDoc {
  const cues: Cue[] = [];
  for (const block of blocks(text)) {
    const at = firstIndex(block, (l) => ARROW.test(l));
    if (at === null) {
      if (cues.length) {
        const last = cues[cues.length - 1];
        cues[cues.length - 1] = { ...last, text: last.text + "\n" + block.join("\n") };
      }
      continue;
    }
    const m = ARROW.exec(block[at])!;
    const start = clockMs(m[1]), end = clockMs(m[2]);
    if (start === null || end === null) continue;
    const extra: Extra = m[3].trim() ? [["pos", m[3].trimEnd()]] : [];
    cues.push(cue(start, end, block.slice(at + 1).join("\n"), extra));
  }
  return { format: "srt", cues, header: "", footer: "", fields: [] };
}

function parseVtt(text: string): SubDoc {
  const bl = blocks(text.replace(/^﻿+/, ""));
  if (!bl.length || !bl[0][0].startsWith("WEBVTT")) {
    throw new SubtitleError("This isn't a WebVTT file: it doesn't start with WEBVTT.");
  }
  const header = [bl[0].join("\n")];
  const cues: Cue[] = [];
  for (const block of bl.slice(1)) {
    const at = firstIndex(block, (l) => l.includes("-->"));
    if (at === null) {
      if (!cues.length) header.push(block.join("\n"));
      continue;
    }
    const m = ARROW.exec(block[at]);
    if (!m) continue;
    const start = clockMs(m[1]), end = clockMs(m[2]);
    if (start === null || end === null) continue;
    const extra: Extra = [];
    if (at === 1) extra.push(["id", block[0]]);
    if (m[3].trim()) extra.push(["settings", m[3].trim()]);
    cues.push(cue(start, end, block.slice(at + 1).join("\n"), extra));
  }
  return { format: "vtt", cues, header: header.join("\n\n"), footer: "", fields: [] };
}

/** Python's str.split(sep, maxsplit). */
function splitMax(s: string, sep: string, max: number): string[] {
  const out: string[] = [];
  let rest = s;
  while (out.length < max) {
    const i = rest.indexOf(sep);
    if (i < 0) break;
    out.push(rest.slice(0, i));
    rest = rest.slice(i + sep.length);
  }
  out.push(rest);
  return out;
}

function parseAss(text: string, fmt: string): SubDoc {
  const lines = text.replace(/^﻿+/, "").split("\n");
  const events = lines.findIndex((l) => l.trim().toLowerCase() === "[events]");
  if (events < 0) throw new SubtitleError("This file has no [Events] section, so it holds no subtitles.");
  let fields: string[] = [];
  let headEnd = events + 1;
  let i = events + 1;
  const cues: Cue[] = [];
  for (; i < lines.length; i++) {
    const line = lines[i];
    const stripped = line.trim();
    if (stripped.startsWith("[") && stripped.endsWith("]")) break;
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const name = line.slice(0, colon).trim();
    const rest = line.slice(colon + 1);
    if (name.toLowerCase() === "format" && !fields.length) {
      fields = rest.split(",").map((f) => f.trim());
      headEnd = i + 1;
    } else if ((name === "Dialogue" || name === "Comment") && fields.length) {
      const values = splitMax(rest.replace(/^ +/, ""), ",", fields.length - 1);
      if (values.length !== fields.length) continue;
      const d = new Map(fields.map((f, n) => [f, values[n]]));
      const start = clockMs(d.get("Start") ?? ""), end = clockMs(d.get("End") ?? "");
      if (start === null || end === null) continue;
      const extra: Extra = [["kind", name]];
      fields.forEach((f, n) => {
        if (f !== "Start" && f !== "End" && f !== "Text") extra.push([f, values[n]]);
      });
      cues.push(cue(start, end, (d.get("Text") ?? "").replaceAll("\\N", "\n"), extra));
    }
  }
  if (!fields.length) throw new SubtitleError("The [Events] section has no Format line.");
  const header = lines.slice(0, headEnd).join("\n");
  const footer = lines.slice(i).join("\n").replace(/^\n+|\n+$/g, "");
  return { format: fmt, cues, header, footer, fields };
}

const LRC_STAMP = /\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]/y;
const LRC_TAG = /^\[[a-zA-Z#][^\]]*\]\s*$/;

function parseLrc(text: string): SubDoc {
  const header: string[] = [];
  const stamps: [number, string][] = [];
  for (const raw of text.replace(/^﻿+/, "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const times: number[] = [];
    let pos = 0;
    for (;;) {
      LRC_STAMP.lastIndex = pos;
      const m = LRC_STAMP.exec(line);
      if (!m) break;
      times.push((Number(m[1]) * 60 + Number(m[2])) * 1000 + Number((m[3] ?? "0").padEnd(3, "0").slice(0, 3)));
      pos = LRC_STAMP.lastIndex;
    }
    if (times.length) {
      const words = line.slice(pos).trim();
      for (const t of times) stamps.push([t, words]);
    } else if (LRC_TAG.test(line) && !stamps.length) header.push(line);
  }
  stamps.sort((a, b) => a[0] - b[0]);
  const cues: Cue[] = [];
  stamps.forEach(([t, words], n) => {
    if (!words) return;
    const nxt = n + 1 < stamps.length ? stamps[n + 1][0] : t + LRC_LAST_MS;
    cues.push(cue(t, Math.max(t, nxt), words));
  });
  return { format: "lrc", cues, header: header.join("\n"), footer: "", fields: [] };
}

const SYNC = /<sync\b[^>]*?\bstart\s*=\s*["']?(\d+)[^>]*>/i;
const BR = /<br\s*\/?>/gi;
const ENTITY: [RegExp, string][] = [
  [/&nbsp;/gi, " "],
  [/&amp;/gi, "&"],
  [/&lt;/gi, "<"],
  [/&gt;/gi, ">"],
  [/&quot;/gi, '"'],
];

function afterFirst(text: string, re: RegExp): string {
  const m = re.exec(text);
  return m ? text.slice(m.index + m[0].length) : text;
}

function beforeFirst(text: string, re: RegExp): string {
  const m = re.exec(text);
  return m ? text.slice(0, m.index) : text;
}

function parseSmi(text: string): SubDoc {
  const body = afterFirst(text, /<body\b[^>]*>/i);
  const parts = body.split(new RegExp(SYNC.source, "i"));
  const syncs: [number, string][] = [];
  for (let n = 1; n < parts.length - 1; n += 2) {
    let chunk = beforeFirst(parts[n + 1], /<\/body>/i);
    chunk = chunk.replaceAll("\n", " ").replace(BR, "\n");
    chunk = chunk.replace(/<(?!\/?[ibu]>)[^>]*>/gi, "");
    for (const [re, ch] of ENTITY) chunk = chunk.replace(re, ch);
    const lines = chunk.split("\n").map((l) => l.split(/\s+/).filter(Boolean).join(" "));
    syncs.push([Number(parts[n]), lines.filter(Boolean).join("\n")]);
  }
  syncs.sort((a, b) => a[0] - b[0]);
  const cues: Cue[] = [];
  syncs.forEach(([t, words], n) => {
    if (!words.trim()) return;
    const nxt = n + 1 < syncs.length ? syncs[n + 1][0] : t + LRC_LAST_MS;
    cues.push(cue(t, Math.max(t, nxt), words));
  });
  return { format: "smi", cues, header: "", footer: "", fields: [] };
}

/** `text` with "\n" line endings. Throws SubtitleError for a file that isn't `fmt`. */
export function parse(text: string, fmt: string): SubDoc {
  fmt = fmt.toLowerCase();
  let doc: SubDoc;
  if (fmt === "srt") doc = parseSrt(text);
  else if (fmt === "vtt") doc = parseVtt(text);
  else if (fmt === "ass" || fmt === "ssa") doc = parseAss(text, fmt);
  else if (fmt === "lrc") doc = parseLrc(text);
  else if (fmt === "smi") doc = parseSmi(text);
  else throw new SubtitleError(`The subtitle editor can't open .${fmt} files.`);
  if (doc.cues.length > MAX_CUES) throw new SubtitleError("This file has more than 100,000 subtitles.");
  return doc;
}

// ---------------------------------------------------------------------------------- writing
export const ASS_HEADER = `[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,50,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text`;
const ASS_FIELDS = ["Layer", "Start", "End", "Style", "Name", "MarginL", "MarginR", "MarginV", "Effect", "Text"];
const ASS_DEFAULTS: Record<string, string> = { Layer: "0", Marked: "Marked=0", Style: "Default", MarginL: "0", MarginR: "0", MarginV: "0" };

const noBlankLines = (text: string) => text.split("\n").filter((l) => !isBlank(l)).join("\n");
const shown = (doc: SubDoc) => doc.cues.filter((c) => !isComment(c));

function writeSrt(doc: SubDoc): string {
  return shown(doc)
    .map((c, n) => {
      const text = noBlankLines(convertMarkup(c.text, doc.format, "srt"));
      const pos = doc.format === "srt" ? extraGet(c, "pos") : "";
      return `${n + 1}\n${hms(c.start, ",")} --> ${hms(c.end, ",")}${pos}\n${text}\n`;
    })
    .join("\n");
}

function writeVtt(doc: SubDoc): string {
  const header = doc.format === "vtt" && doc.header ? doc.header : "WEBVTT";
  const out = [header + "\n"];
  for (const c of shown(doc)) {
    const text = noBlankLines(convertMarkup(c.text, doc.format, "vtt")).replaceAll("-->", "->");
    const ident = doc.format === "vtt" ? extraGet(c, "id") : "";
    const settings = doc.format === "vtt" ? extraGet(c, "settings") : "";
    const line = `${hms(c.start, ".")} --> ${hms(c.end, ".")}` + (settings ? ` ${settings}` : "");
    out.push((ident ? `${ident}\n` : "") + `${line}\n${text}\n`);
  }
  return out.join("\n");
}

function writeAss(doc: SubDoc, fmt: string): string {
  const same = markupKind(doc.format) === "ass" && !!doc.header;
  if (fmt === "ssa" && !(same && doc.format === "ssa")) {
    throw new SubtitleError("Only SSA files can be saved as SSA; choose ASS instead.");
  }
  const header = same ? doc.header : ASS_HEADER;
  const fields = same ? doc.fields : ASS_FIELDS;
  const lines = [header];
  for (const c of same ? doc.cues : shown(doc)) {
    const text = convertMarkup(c.text, doc.format, fmt).replaceAll("\n", "\\N");
    const kept = new Map(same ? c.extra : []);
    const values = fields.map((f) =>
      f === "Start" ? assTime(c.start) : f === "End" ? assTime(c.end) : f === "Text" ? text : kept.get(f) ?? ASS_DEFAULTS[f] ?? "",
    );
    lines.push(`${kept.get("kind") ?? "Dialogue"}: ` + values.join(","));
  }
  let body = lines.join("\n") + "\n";
  if (same && doc.footer) body += "\n" + doc.footer + "\n";
  return body;
}

function writeLrc(doc: SubDoc): string {
  const lines = doc.format === "lrc" && doc.header ? [doc.header] : [];
  const cues = [...shown(doc)].sort((a, b) => a.start - b.start);
  cues.forEach((c, n) => {
    const words = convertMarkup(c.text, doc.format, "lrc").split(/\s+/).filter(Boolean).join(" ");
    lines.push(lrcTime(c.start) + words);
    const last = n + 1 === cues.length;
    const nxt = last ? c.start + LRC_LAST_MS : cues[n + 1].start;
    if (c.end < nxt || (last && c.end !== nxt)) lines.push(lrcTime(c.end)); // LRC can't show an overlap
  });
  return lines.join("\n") + "\n";
}

/** The file's text with "\n" line endings. */
export function write(doc: SubDoc, fmt: string): string {
  fmt = fmt.toLowerCase();
  if (fmt === "srt") return writeSrt(doc);
  if (fmt === "vtt") return writeVtt(doc);
  if (fmt === "ass" || fmt === "ssa") return writeAss(doc, fmt);
  if (fmt === "lrc") return writeLrc(doc);
  throw new SubtitleError(`The subtitle editor can't save .${fmt} files.`);
}

/** Formats Save as offers for this document, its own first when it can be written. */
export function saveFormats(doc: SubDoc): string[] {
  const out = ["srt", "vtt", "ass", "lrc"];
  if (doc.format === "ssa") out.unshift("ssa");
  const i = out.indexOf(doc.format);
  if (i >= 0) {
    out.splice(i, 1);
    out.unshift(doc.format);
  }
  return out;
}

const hasMatch = (re: RegExp, text: string) => new RegExp(re.source).test(text);

/** What saving `doc` as `fmt` leaves out, in plain words (empty = nothing). */
export function lostParts(doc: SubDoc, fmt: string): string[] {
  if (fmt === doc.format) return [];
  const out: string[] = [];
  const src = markupKind(doc.format), dst = markupKind(fmt);
  if (src === "ass" && dst !== "ass") {
    out.push("styles, positions and effects");
    if (doc.cues.some(isComment)) out.push("comment lines");
  }
  if (doc.format === "vtt" && (doc.cues.some((c) => c.extra.length) || doc.header.includes("\n"))) {
    out.push("cue positions, names and notes");
  }
  if (dst === "plain" && doc.format !== "lrc") {
    if (doc.cues.some((c) => hasMatch(ASS_BLOCK, c.text) || hasMatch(HTML_TAG, c.text))) out.push("italic / bold text");
    if (doc.cues.some((c) => c.text.includes("\n"))) out.push("line breaks (LRC has one line per subtitle)");
  }
  if (doc.format === "lrc" && doc.header) out.push("song details (title, artist …)");
  return out;
}
