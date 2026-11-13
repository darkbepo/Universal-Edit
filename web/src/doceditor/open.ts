/**
 * Open any editable document as the editor's HTML subset (port of the desktop's
 * `src/umc/doceditor/io.py`). ODT and RTF are turned into DOCX by the page's converter first.
 */

import { canonicalExt } from "../core/detect";
import { unzipPackage } from "./docx-package";
import { readDocx } from "./docx-read";
import { hasDroppedFormats, hasMarkdownExtras, mdToHtml, splitFrontMatter } from "./markdown";
import { hasDotDot, isEditableMarkdownZip, markdownZipName } from "./mdzip";
import { escAttr, pyStrip } from "./pycompat";
import { htmlTitle, normaliseHtmlReport, textLength } from "./subset";
import {
  MAX_CHARS,
  MAX_FILE_BYTES,
  TOO_LARGE,
  W_MDEXTRA,
  W_MDFORMAT,
  WEB_EDITABLE_DOCUMENT_EXTS,
  type OpenedDocument,
} from "./types";

/**
 * Converts `bytes` from one format to another (the page runs it in the conversion worker).
 * `options` are the target converter's option values, e.g. the doc:pdf paper settings.
 */
export type Convert = (
  bytes: Uint8Array,
  fromExt: string,
  toExt: string,
  name: string,
  options?: Record<string, string | number | boolean>,
) => Promise<Uint8Array>;

export { isEditableMarkdownZip };

export const EMPTY = "<p></p>";
export const CANT_EDIT = "This file type can't be edited.";
const NBSP = "\xa0";
// Bytes Windows-1252 leaves undefined: Python's cp1252 refuses them, so the desktop reads
// such a file as Latin-1.
const NOT_CP1252 = new Set([0x81, 0x8d, 0x8f, 0x90, 0x9d]);

/** Text the way the desktop reads it: UTF-8 (byte order mark dropped), else Windows-1252,
 * else Latin-1. */
export function decodeText(bytes: Uint8Array): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    // not UTF-8
  }
  if (!bytes.some((b) => NOT_CP1252.has(b))) return new TextDecoder("windows-1252").decode(bytes);
  const parts: string[] = [];
  for (let i = 0; i < bytes.length; i += 0x8000) {
    parts.push(String.fromCharCode.apply(null, Array.from(bytes.subarray(i, i + 0x8000))));
  }
  return parts.join("");
}

/** A space at the start of the line or after another space becomes a no-break space. */
function keepSpaces(line: string): string {
  if (!line.includes(" ")) return line;
  const out: string[] = [];
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    out.push(c === " " && (i === 0 || line[i - 1] === " ") ? NBSP : c);
  }
  return out.join("");
}

/** Plain text as paragraphs, one per line. */
export function txtToHtml(text: string): string {
  const lines = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  if (!lines.length) return EMPTY;
  return lines.map((line) => `<p>${escAttr(keepSpaces(line))}</p>`).join("");
}

function opened(html: string, sourceFormat: string, extra: Partial<OpenedDocument> = {}): OpenedDocument {
  return {
    html,
    images: new Map(),
    sourceFormat,
    warnings: [],
    frontMatter: "",
    title: "",
    template: null,
    ...extra,
  };
}


/** Open a Markdown ZIP: the `.md`, its pictures read from `<stem>_files/` in the ZIP. */
function openMarkdownZip(bytes: Uint8Array, md: string): OpenedDocument {
  const files = unzipPackage(bytes);
  const folder = `${md.slice(0, -3)}_files/`;
  const [front, text] = splitFrontMatter(decodeText(files.get(md) ?? new Uint8Array()));
  // Only exact names inside the folder are ever looked up.
  const resolveImage = (rel: string) =>
    rel.startsWith(folder) && !hasDotDot(rel) ? (files.get(rel) ?? null) : null;
  const { html, images, warnings } = normaliseHtmlReport(mdToHtml(text), { resolveImage });
  if (hasMarkdownExtras(text)) warnings.push(W_MDEXTRA);
  if (hasDroppedFormats(text)) warnings.push(W_MDFORMAT);
  return opened(html, "md", { images, warnings, frontMatter: front });
}

/** Open `file` for editing; ODT / RTF go through DOCX with `convert`. */
export async function openDocument(file: File, convert: Convert): Promise<OpenedDocument> {
  if (file.size > MAX_FILE_BYTES) throw new Error(TOO_LARGE); // before reading anything
  const ext = canonicalExt(file.name);
  const isZip = ext === "zip";
  if (!isZip && !WEB_EDITABLE_DOCUMENT_EXTS.includes(ext)) throw new Error(CANT_EDIT);
  const bytes = new Uint8Array(await file.arrayBuffer());
  let doc: OpenedDocument;
  if (isZip) {
    const md = markdownZipName(bytes);
    if (md === null) throw new Error(CANT_EDIT);
    doc = openMarkdownZip(bytes, md);
  } else if (ext === "docx") {
    doc = readDocx(bytes);
  } else if (ext === "md") {
    const [front, text] = splitFrontMatter(decodeText(bytes));
    const { html, images, warnings } = normaliseHtmlReport(mdToHtml(text));
    if (hasMarkdownExtras(text)) warnings.push(W_MDEXTRA);
    if (hasDroppedFormats(text)) warnings.push(W_MDFORMAT);
    doc = opened(html, "md", { images, warnings, frontMatter: front });
  } else if (ext === "txt") {
    doc = opened(txtToHtml(decodeText(bytes)), "txt");
  } else if (ext === "html") {
    const source = decodeText(bytes);
    const { html, images, warnings } = normaliseHtmlReport(source);
    doc = opened(html, "html", { images, warnings, title: htmlTitle(source) });
  } else {
    // odt / rtf
    doc = readDocx(await convert(bytes, ext, "docx", file.name));
    doc.sourceFormat = ext;
    doc.template = null;
  }
  if (!pyStrip(doc.html)) doc.html = EMPTY;
  if (textLength(doc.html) > MAX_CHARS) throw new Error(TOO_LARGE);
  return doc;
}
