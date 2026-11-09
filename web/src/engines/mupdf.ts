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

export const MAGIC: Record<string, string> = {
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

export interface PaperSpec {
  size: [number, number]; // mm, portrait
  orientation: string; // auto | portrait | landscape
  margin: number; // mm
  scaleUp: boolean;
}

/** Re-render pages through a PDF writer, optionally re-placed onto a fixed paper size. */
export function writePages(
  mupdf: MuPDF,
  doc: import("mupdf").Document,
  pages: number[] | null,
  paper: PaperSpec | null,
): Uint8Array {
  const buf = new mupdf.Buffer();
  const writer = new mupdf.DocumentWriter(buf, "pdf", "");
  const list = pages ?? [...Array(doc.countPages()).keys()];
  for (const i of list) {
    const page = doc.loadPage(i);
    const [x0, y0, x1, y1] = page.getBounds();
    const pw = x1 - x0;
    const ph = y1 - y0;
    if (!paper) {
      const dev = writer.beginPage([0, 0, pw, ph]);
      page.run(dev, mupdf.Matrix.translate(-x0, -y0));
      writer.endPage();
      continue;
    }
    const landscape = paper.orientation === "landscape" || (paper.orientation === "auto" && pw > ph);
    const [mw, mh] = landscape ? [paper.size[1], paper.size[0]] : paper.size;
    const W = mw * MM;
    const H = mh * MM;
    const m = paper.margin * MM;
    if (W - 2 * m <= 0 || H - 2 * m <= 0) throw new ConversionError("The margins are larger than the page.");
    let scale = Math.min((W - 2 * m) / pw, (H - 2 * m) / ph);
    if (!paper.scaleUp) scale = Math.min(scale, 1);
    const matrix = mupdf.Matrix.concat(
      mupdf.Matrix.concat(mupdf.Matrix.translate(-x0, -y0), mupdf.Matrix.scale(scale, scale)),
      mupdf.Matrix.translate((W - pw * scale) / 2, (H - ph * scale) / 2),
    );
    const dev = writer.beginPage([0, 0, W, H]);
    page.run(dev, matrix);
    writer.endPage();
  }
  writer.close();
  return buf.asUint8Array().slice();
}

export const savePdf = (pdf: import("mupdf").PDFDocument) =>
  pdf.saveToBuffer("garbage,compress").asUint8Array().slice();

// MuPDF's HTML engine: Pandoc's default stylesheet makes tables `display: block` (for scrolling
// in browsers), which breaks MuPDF's table layout — restore real tables and give them borders.
const PRINT_CSS =
  "table{display:table!important;border-collapse:collapse;margin:1em 0}th,td{border:1px solid #999;padding:3px 8px}" +
  "img{max-width:100%}pre{white-space:pre-wrap}";

/** Lay out an HTML document onto pages and return a PDF. */
export function htmlToPdf(
  mupdf: MuPDF,
  html: string,
  paper: { size: [number, number]; landscape: boolean; marginMm: number },
): Uint8Array {
  let css = PRINT_CSS;
  if (paper.marginMm > 0) css += `body{margin:${paper.marginMm}mm!important;padding:0!important;max-width:none!important}`;
  const withCss = html.includes("</head>")
    ? html.replace("</head>", `<style>${css}</style></head>`)
    : `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${html}</body></html>`;
  const doc = mupdf.Document.openDocument(new TextEncoder().encode(withCss), "text/html");
  const [w, h] = paper.landscape ? [paper.size[1], paper.size[0]] : paper.size;
  doc.layout(w * MM, h * MM, 11);
  return writePages(mupdf, doc, null, null);
}

const escapeHtml = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);

/** Plain text → simple HTML (monospace, wrapped). Decodes UTF-8, falling back to Windows-1252. */
export function textToHtml(bytes: Uint8Array, title: string): string {
  let text = new TextDecoder("utf-8").decode(bytes);
  if (text.includes("\uFFFD")) text = new TextDecoder("windows-1252").decode(bytes);
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title>` +
    `<style>body{font-family:monospace;font-size:10pt;margin:20mm}pre{white-space:pre-wrap;margin:0}</style>` +
    `</head><body><pre>${escapeHtml(text)}</pre></body></html>`;
}

/** Extracted PDF text → paragraphs of HTML. */
export function textToParagraphHtml(text: string, title: string): string {
  const paras = text.split(/\n\s*\n|\f/).map((p) => p.trim()).filter(Boolean);
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title></head><body>` +
    paras.map((p) => `<p>${escapeHtml(p).replace(/\n/g, " ")}</p>`).join("\n") + "</body></html>";
}
