// MuPDF compiled to WebAssembly (the same library the desktop app uses via PyMuPDF).
import { ConversionError } from "../core/types";

type MuPDF = typeof import("mupdf");
let ready: Promise<MuPDF> | null = null;

export function getMuPDF(onStatus?: (msg: string) => void): Promise<MuPDF> {
  if (!ready) {
    onStatus?.("Loading PDF engine…");
    ready = import("mupdf");
    ready.catch(() => (ready = null));
  }
  return ready;
}

const MAGIC: Record<string, string> = {
  pdf: "application/pdf",
  epub: "application/epub+zip",
  xps: "application/oxps",
  mobi: "application/x-mobipocket-ebook",
  fb2: "application/x-fictionbook",
  cbz: "application/x-cbz",
};

export async function openDocument(file: File, ext: string) {
  const mupdf = await getMuPDF();
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    return mupdf.Document.openDocument(bytes, MAGIC[ext] ?? ext);
  } catch (e) {
    throw new ConversionError(`Couldn't open “${file.name}”. Is it a valid document?`, String(e));
  }
}

export const PAPER_MM: Record<string, [number, number]> = {
  a3: [297, 420],
  a4: [210, 297],
  a5: [148, 210],
  letter: [215.9, 279.4],
  legal: [215.9, 355.6],
};
export const MM = 72 / 25.4;
