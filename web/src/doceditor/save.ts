/**
 * Save the editor's content to any document format the website can write (port of the
 * desktop's `src/umc/doceditor/save.py`). The result is a download: a browser can't write a
 * pictures folder next to a Markdown file, so Markdown with pictures is saved as a ZIP.
 */

import { zipSync } from "fflate";
import { writeDocx } from "./docx-write";
import { toMarkdown } from "./md-writer";
import type { Convert } from "./open";
import { PY_WS, escAttr } from "./pycompat";
import type { DocImages, SaveContent } from "./types";

export const SAVE_FORMATS: [string, string][] = [
  ["docx", "Word (DOCX)"],
  ["odt", "OpenDocument (ODT)"],
  ["rtf", "Rich Text (RTF)"],
  ["pdf", "PDF"],
  ["md", "Markdown"],
  ["txt", "Plain text (TXT)"],
  ["html", "Web page (HTML)"],
];
export const CANT_SAVE = "The document can't be saved in this format here.";

const MIME_TYPES: Record<string, string> = {
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  odt: "application/vnd.oasis.opendocument.text",
  rtf: "application/rtf",
  pdf: "application/pdf",
  md: "text/markdown;charset=utf-8",
  txt: "text/plain;charset=utf-8",
  html: "text/html;charset=utf-8",
  zip: "application/zip",
};
const PICTURE_MIME: Record<string, string> = {
  jpg: "jpeg",
  png: "png",
  gif: "gif",
  bmp: "bmp",
  webp: "webp",
};
const NAME = "[\\p{L}\\p{N}_.-]"; // Python's [\w.-]
const HTML_SRC = new RegExp(`(?<![\\p{L}\\p{N}_])(src=")(img/${NAME}+)(")`, "gu");
const MD_LINK = new RegExp(`((?<!\\\\)\\]\\()(img/${NAME}+)((?:[${PY_WS}]+"[^"]*")?\\))`, "gu");
const MD_IMG = new RegExp(`(<img src=")(img/${NAME}+)(")`, "gu"); // sized pictures (raw HTML)

/** The standalone web page the desktop writes (`htmlpdf._page`). */
export function htmlPage(title: string, body: string): string {
  return (
    '<!DOCTYPE html>\n<html><head><meta charset="utf-8">' +
    `<title>${escAttr(title)}</title></head><body>\n${body}\n</body></html>\n`
  );
}

function base64(data: Uint8Array): string {
  const parts: string[] = [];
  for (let i = 0; i < data.length; i += 0x8000) {
    parts.push(String.fromCharCode.apply(null, Array.from(data.subarray(i, i + 0x8000))));
  }
  return btoa(parts.join(""));
}

/** The pictures written into the HTML as data: URIs. */
export function withDataUris(html: string, images: DocImages): string {
  return html.replace(HTML_SRC, (whole, start: string, name: string, end: string) => {
    const data = images.get(name);
    if (data === undefined) return whole;
    const mime = PICTURE_MIME[name.slice(name.lastIndexOf(".") + 1).toLowerCase()] ?? "png";
    return `${start}data:image/${mime};base64,${base64(data)}${end}`;
  });
}

/** Python's urllib.parse.quote(text, safe="()"). */
function quote(text: string): string {
  const out: string[] = [];
  for (const b of new TextEncoder().encode(text)) {
    const c = String.fromCharCode(b);
    out.push(/[A-Za-z0-9_.~()-]/.test(c) ? c : "%" + b.toString(16).toUpperCase().padStart(2, "0"));
  }
  return out.join("");
}

/** The Markdown text, with picture links pointing into `folder`. */
function markdownText(content: SaveContent, folder: string): string {
  const link = quote(folder);
  const toFolder = (whole: string, start: string, name: string, end: string) =>
    content.images.has(name) ? `${start}${link}/${name.slice("img/".length)}${end}` : whole;
  let text = toMarkdown(content.html).replace(MD_LINK, toFolder).replace(MD_IMG, toFolder);
  const front = content.frontMatter;
  if (front) {
    // kept exactly as it was read
    text = front + (front.endsWith("\n") ? "" : "\n") + (text ? "\n" + text : "");
  }
  return text;
}

const blobOf = (data: Uint8Array | string, ext: string) =>
  new Blob([data as BlobPart], { type: MIME_TYPES[ext] });

export interface SaveOptions {
  /** Called with a plain-language notice for anything that saved, but not as asked. */
  onWarning?: (message: string) => void;
}

/**
 * `content` as `fmt`, ready to download as `name` (`stem.fmt`, or `stem.zip` for Markdown
 * with pictures: `stem.md` plus the pictures in `stem_files/`).
 */
export async function saveDocument(
  content: SaveContent,
  fmt: string,
  stem: string,
  convert: Convert,
  opts: SaveOptions = {},
): Promise<{ blob: Blob; name: string }> {
  fmt = fmt.toLowerCase().replace(/^\.+/, "");
  const named = (data: Uint8Array | string, ext = fmt) => ({
    blob: blobOf(data, ext),
    name: `${stem}.${ext}`,
  });
  const docx = () =>
    writeDocx(content.html, content.images, content.template, { onWarning: opts.onWarning });
  switch (fmt) {
    case "docx":
      return named(docx());
    case "html":
      return named(htmlPage(content.title || stem, withDataUris(content.html, content.images)));
    case "txt":
      return named(content.plainText ?? "");
    case "md": {
      const folder = `${stem}_files`;
      const text = markdownText(content, folder);
      if (!content.images.size) return named(text);
      const files: Record<string, Uint8Array> = { [`${stem}.md`]: new TextEncoder().encode(text) };
      for (const [name, data] of content.images) {
        const file = name.startsWith("img/") ? name.slice(4) : name;
        files[`${folder}/${file}`] = data;
      }
      return named(zipSync(files, { level: 6 }), "zip");
    }
    case "pdf": {
      const page = htmlPage(stem, withDataUris(content.html, content.images));
      return named(await convert(new TextEncoder().encode(page), "html", "pdf", `${stem}.html`));
    }
    case "odt":
    case "rtf":
      return named(await convert(docx(), "docx", fmt, `${stem}.docx`));
    default:
      throw new Error(CANT_SAVE);
  }
}
