// SVG files ↔ vector documents — a port of src/umc/vectoredit/io.py. Text goes through
// codeedit/textfile (encoding, BOM and line endings kept); .svgz is gunzipped and saved gzipped
// again. An untouched document saves the bytes as read.

import { Gunzip, gzipSync } from "fflate";
import { type TextFile, decode, encode } from "../codeedit/textfile";
import { type VectorDoc, VectorError, parse, write } from "./doc";
import { num } from "./geom";
import type { Element } from "./xmlsrc";

export const MAX_OPEN_BYTES = 20 * 1024 * 1024;
export const MAX_ELEMENTS = 100_000;

export interface Opened {
  doc: VectorDoc;
  file: TextFile;
  gz: boolean;
  new: boolean;
  data: Uint8Array;
}

const TOO_BIG = "This drawing is too big for the editor (over 20 MB).";

/** Unpacks in pieces and stops past the limit, so a tiny "zip bomb" can't fill the memory. */
function gunzip(data: Uint8Array): Uint8Array {
  const parts: Uint8Array[] = [];
  let total = 0;
  let tooBig = false;
  const stream = new Gunzip((chunk) => {
    total += chunk.length;
    if (total > MAX_OPEN_BYTES) {
      tooBig = true;
      throw new VectorError(TOO_BIG);
    }
    parts.push(chunk);
  });
  try {
    for (let i = 0; i < data.length; i += 65536) stream.push(data.subarray(i, i + 65536), i + 65536 >= data.length);
  } catch {
    throw new VectorError(tooBig ? TOO_BIG : "This compressed SVG (.svgz) is damaged.");
  }
  const out = new Uint8Array(total);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

const count = (el: Element): number => 1 + el.elements.reduce((n, c) => n + count(c), 0);

export function openBytes(data: Uint8Array): Opened {
  if (data.length > MAX_OPEN_BYTES) throw new VectorError(TOO_BIG);
  const gz = data.length >= 2 && data[0] === 0x1f && data[1] === 0x8b;
  const raw = gz ? gunzip(data) : data;
  let file: TextFile;
  try {
    file = decode(raw);
  } catch {
    throw new VectorError("This file isn't text, so it can't be an SVG drawing.");
  }
  const doc = parse(file.text);
  if (count(doc.root) > MAX_ELEMENTS) {
    throw new VectorError(`This drawing has more than ${MAX_ELEMENTS.toLocaleString("en-US")} parts: too many to edit.`);
  }
  return { doc, file, gz, new: false, data };
}

/** The file to save. Throws Error("encoding") if a character doesn't fit the file's encoding
 * (then ask, and pass utf8 = true). */
export function toBytes(opened: Opened, doc: VectorDoc, utf8 = false): Uint8Array {
  if (doc === opened.doc && !opened.new && !utf8) return opened.data;
  const body = encode(opened.file, write(doc), utf8 ? { encoding: "utf-8" } : {});
  return opened.gz ? gzipSync(body, { mtime: 0 }) : body;
}

export function newText(width: number, height: number, unit = "px"): string {
  const w = num(width);
  const h = num(height);
  const size = unit !== "px" ? `width="${w}${unit}" height="${h}${unit}"` : `width="${w}" height="${h}"`;
  return (
    '<?xml version="1.0" encoding="UTF-8"?>\n' +
    `<svg xmlns="http://www.w3.org/2000/svg" ${size} viewBox="0 0 ${w} ${h}">\n</svg>\n`
  );
}

export function newDocument(width: number, height: number, unit = "px"): Opened {
  const text = newText(width, height, unit);
  const data = new TextEncoder().encode(text);
  return { doc: parse(text), file: decode(data), gz: false, new: true, data };
}
