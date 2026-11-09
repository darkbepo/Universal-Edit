// Detect the media family of a file from its extension, falling back to magic bytes.
import type { Family, InputFile } from "./types";

const ALIASES: Record<string, string> = {
  jpeg: "jpg", jpe: "jpg", jfif: "jpg", tif: "tiff", htm: "html", markdown: "md",
  heif: "heic", mpeg: "mpg", text: "txt",
};

export const IMAGE_EXTS = new Set([
  "png", "jpg", "webp", "bmp", "tiff", "gif", "ico", "heic", "avif", "tga", "ppm", "pgm",
  "pbm", "pcx", "dds", "psd", "jp2", "qoi", "jxl", "svg",
]);
export const VIDEO_EXTS = new Set([
  "mp4", "mkv", "mov", "avi", "webm", "flv", "wmv", "m4v", "mpg", "ts", "mts", "m2ts", "3gp",
  "ogv", "vob", "asf",
]);
/** Documents the web version can open itself (MuPDF). */
export const WEB_DOCUMENT_EXTS = new Set(["pdf", "epub", "xps", "mobi", "fb2", "cbz"]);
/** Documents recognised but (for now) only convertible in the desktop app. */
export const DESKTOP_ONLY_DOCUMENT_EXTS = new Set([
  "docx", "doc", "odt", "rtf", "txt", "md", "html", "rst", "tex", "org", "wpd",
  "xlsx", "xls", "ods", "csv", "pptx", "ppt", "odp",
]);

export function canonicalExt(nameOrExt: string): string {
  const dot = nameOrExt.lastIndexOf(".");
  const ext = (dot >= 0 ? nameOrExt.slice(dot + 1) : nameOrExt).toLowerCase();
  return ALIASES[ext] ?? ext;
}

export function familyOfExt(ext: string): Family | null {
  if (IMAGE_EXTS.has(ext)) return "image";
  if (VIDEO_EXTS.has(ext)) return "video";
  if (WEB_DOCUMENT_EXTS.has(ext) || DESKTOP_ONLY_DOCUMENT_EXTS.has(ext)) return "document";
  return null;
}

export function sniff(head: Uint8Array): string | null {
  const s = (a: number, b: number) => String.fromCharCode(...head.slice(a, b));
  if (s(0, 4) === "%PDF") return "pdf";
  if (head[0] === 0x89 && s(1, 4) === "PNG") return "png";
  if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "jpg";
  if (s(0, 4) === "GIF8") return "gif";
  if (s(0, 4) === "RIFF" && s(8, 12) === "WEBP") return "webp";
  if (s(0, 4) === "RIFF" && s(8, 12) === "AVI ") return "avi";
  if (s(0, 2) === "BM") return "bmp";
  if (head[0] === 0x1a && head[1] === 0x45 && head[2] === 0xdf && head[3] === 0xa3) {
    return s(0, 64).includes("webm") ? "webm" : "mkv";
  }
  if (s(4, 8) === "ftyp") {
    const brand = s(8, 12);
    if (["heic", "heix", "mif1", "msf1", "hevc"].includes(brand)) return "heic";
    if (brand === "avif" || brand === "avis") return "avif";
    if (brand === "qt  ") return "mov";
    return "mp4";
  }
  return null;
}

export async function detect(file: File): Promise<InputFile | null> {
  let ext = canonicalExt(file.name);
  let family = file.name.includes(".") ? familyOfExt(ext) : null;
  if (!family) {
    const head = new Uint8Array(await file.slice(0, 64).arrayBuffer());
    const sniffed = sniff(head);
    if (!sniffed) return null;
    ext = sniffed;
    family = familyOfExt(sniffed);
    if (!family) return null;
  }
  return { file, name: file.name, ext, family };
}
