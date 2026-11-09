// Detect the media family of a file from its extension, falling back to magic bytes.
import type { Family, InputFile } from "./types";

const ALIASES: Record<string, string> = {
  jpeg: "jpg", jpe: "jpg", jfif: "jpg", tif: "tiff", htm: "html", markdown: "md",
  heif: "heic", mpeg: "mpg", text: "txt", yml: "yaml", stp: "step", igs: "iges",
};

export const IMAGE_EXTS = new Set([
  "png", "jpg", "webp", "bmp", "tiff", "gif", "ico", "heic", "avif", "tga", "ppm", "pgm",
  "pbm", "pcx", "dds", "psd", "jp2", "qoi", "jxl", "svg",
  // camera RAW
  "cr2", "cr3", "crw", "nef", "nrw", "arw", "srf", "sr2", "dng", "orf", "raf", "rw2", "pef",
  "srw", "x3f", "3fr", "iiq", "mrw", "kdc", "dcr", "erf", "mef", "mos",
]);
export const VIDEO_EXTS = new Set([
  "mp4", "mkv", "mov", "avi", "webm", "flv", "wmv", "m4v", "mpg", "ts", "mts", "m2ts", "3gp",
  "ogv", "vob", "asf",
]);
export const AUDIO_EXTS = new Set([
  "mp3", "wav", "flac", "m4a", "aac", "ogg", "oga", "opus", "wma", "aiff", "aif", "ac3",
  "amr", "mka", "caf", "ape", "wv", "mp2", "au",
]);
export const MODEL_EXTS = new Set(["stl", "obj", "ply", "glb", "3mf", "step", "iges"]);
/** Documents the web version converts itself (MuPDF + Pandoc). */
export const WEB_DOCUMENT_EXTS = new Set([
  "pdf", "epub", "xps", "mobi", "fb2", "cbz", "docx", "odt", "rtf", "txt", "md", "html", "rst",
  "tex", "org", "csv", "tsv", "xlsx", "pptx", "ipynb", "json", "yaml", "xml",
]);
/** Documents recognised but only convertible in the desktop app (they need LibreOffice). */
export const DESKTOP_ONLY_DOCUMENT_EXTS = new Set(["doc", "xls", "ppt", "ods", "odp", "wpd"]);

export function canonicalExt(nameOrExt: string): string {
  const dot = nameOrExt.lastIndexOf(".");
  const ext = (dot >= 0 ? nameOrExt.slice(dot + 1) : nameOrExt).toLowerCase();
  return ALIASES[ext] ?? ext;
}

export function familyOfExt(ext: string): Family | null {
  if (IMAGE_EXTS.has(ext)) return "image";
  if (VIDEO_EXTS.has(ext)) return "video";
  if (AUDIO_EXTS.has(ext)) return "audio";
  if (MODEL_EXTS.has(ext)) return "model";
  if (WEB_DOCUMENT_EXTS.has(ext) || DESKTOP_ONLY_DOCUMENT_EXTS.has(ext)) return "document";
  return null;
}

export function sniff(head: Uint8Array): string | null {
  const s = (a: number, b: number) => String.fromCharCode(...head.slice(a, b));
  if (s(0, 4) === "%PDF") return "pdf";
  if (s(0, 4) === "glTF") return "glb";
  if (s(0, 13) === "ISO-10303-21;") return "step";
  if (head[0] === 0x89 && s(1, 4) === "PNG") return "png";
  if (head[0] === 0xff && head[1] === 0xd8 && head[2] === 0xff) return "jpg";
  if (s(0, 4) === "GIF8") return "gif";
  if (s(0, 4) === "RIFF" && s(8, 12) === "WEBP") return "webp";
  if (s(0, 4) === "RIFF" && s(8, 12) === "WAVE") return "wav";
  if (s(0, 4) === "FORM" && (s(8, 12) === "AIFF" || s(8, 12) === "AIFC")) return "aiff";
  if (s(0, 4) === "fLaC") return "flac";
  if (s(0, 4) === "OggS") return s(0, 64).includes("OpusHead") ? "opus" : "ogg";
  if (s(0, 3) === "ID3" || (head.length > 1 && head[0] === 0xff && (head[1] & 0xe0) === 0xe0)) return "mp3";
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
    if (brand === "M4A " || brand === "M4B ") return "m4a";
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
