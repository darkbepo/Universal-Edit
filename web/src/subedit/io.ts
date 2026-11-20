// Subtitle files ↔ documents — a port of src/umc/subedit/io.py. The text goes through
// codeedit/textfile so a save keeps the file's encoding, BOM and line endings, and an untouched
// document saves the bytes as read. A file reopened with a chosen charset is saved as UTF-8
// (TextEncoder writes nothing else).

import * as textfile from "../codeedit/textfile";
import { parse, SubtitleError, write, type SubDoc } from "./formats";

export { CHARSETS } from "../converters/subtitles";
export const MAX_OPEN_BYTES = 20 * 1024 * 1024;

export interface Opened {
  doc: SubDoc; // as read (the history's first snapshot)
  file: textfile.TextFile;
  charset: string | null; // chosen by "Reopen with encoding", else null
}

/** Throws SubtitleError with a message for the user. */
export function openBytes(data: Uint8Array, ext: string, charset: string | null = null): Opened {
  if (data.length > MAX_OPEN_BYTES) throw new SubtitleError("This file is too big for the subtitle editor (over 20 MB).");
  let f: textfile.TextFile;
  if (charset) {
    let text: string;
    try {
      text = new TextDecoder(charset).decode(data);
    } catch {
      throw new SubtitleError(`Unknown encoding ${charset}.`);
    }
    text = text.replace(/^﻿/, "");
    const [newline, mixed] = textfile.newlines(text);
    f = { text: text.replaceAll("\r\n", "\n").replaceAll("\r", "\n"), encoding: "utf-8", bom: false, newline, mixed, raw: data };
  } else {
    try {
      f = textfile.decode(data);
    } catch {
      throw new SubtitleError("This doesn't look like a subtitle file (it isn't text).");
    }
  }
  return { doc: parse(f.text, ext), file: f, charset };
}

export interface Saved {
  data: Uint8Array;
  utf8: boolean; // written as UTF-8 although the file was something else
}

function sameDoc(a: SubDoc, b: SubDoc): boolean {
  return a === b || JSON.stringify(a) === JSON.stringify(b);
}

/** The bytes for `doc` saved as `fmt` (see io.py). */
export function toBytes(opened: Opened, doc: SubDoc, fmt: string): Saved {
  const f = opened.file;
  if (fmt === opened.doc.format && opened.charset === null && sameDoc(doc, opened.doc)) return { data: f.raw, utf8: false };
  const text = write(doc, fmt);
  if (opened.charset === null && (fmt !== "vtt" || f.encoding === "utf-8")) {
    try {
      return { data: textfile.encode(f, text), utf8: false };
    } catch {
      /* the encoding can't hold the text: UTF-8 below */
    }
  }
  const body = f.newline === "\n" ? text : text.replaceAll("\n", f.newline);
  const out = new TextEncoder().encode(body);
  if (!(f.bom && f.encoding === "utf-8")) return { data: out, utf8: f.encoding !== "utf-8" || opened.charset !== null };
  const withBom = new Uint8Array(out.length + 3);
  withBom.set([0xef, 0xbb, 0xbf]);
  withBom.set(out, 3);
  return { data: withBom, utf8: false };
}

/** An empty document (the hub tile); Save asks for a name first. */
export function newDocument(fmt = "srt"): Opened {
  if (!["srt", "vtt", "lrc"].includes(fmt)) throw new SubtitleError(`Can't start an empty .${fmt} file.`);
  const doc = parse(fmt === "vtt" ? "WEBVTT\n" : "", fmt);
  return { doc, file: { text: "", encoding: "utf-8", bom: false, newline: "\n", mixed: false, raw: new Uint8Array() }, charset: null };
}
