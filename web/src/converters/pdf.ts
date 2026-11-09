// Documents the browser version can handle itself, all with MuPDF:
// PDF / EPUB / XPS / MOBI / FB2 / CBZ → PDF (page range, paper size) or TXT,
// → images (one per page), and → split into several PDFs.
import { DESKTOP_ONLY_DOCUMENT_EXTS } from "../core/detect";
import { opt, parsePageGroups, parsePageRange, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type RunContext } from "../core/types";
import { baseName, uniqueNamer } from "./util";

type MuDoc = import("mupdf").Document;

const webReadable = (files: InputFile[]) => files.every((f) => !DESKTOP_ONLY_DOCUMENT_EXTS.has(f.ext));

/** Open as a PDF document, converting EPUB/XPS/… to PDF first. */
async function openAsPdf(f: InputFile, ctx: RunContext) {
  const mu = await import("../engines/mupdf");
  const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
  const doc = await mu.openDocument(f.file, f.ext);
  const pdf = doc.asPDF();
  if (pdf) return { mupdf, pdf };
  const bytes = writePages(mupdf, doc, null, null);
  return { mupdf, pdf: mupdf.Document.openDocument(bytes, "application/pdf").asPDF()! };
}

/** Re-render pages through a PDF writer, optionally onto a fixed paper size. */
function writePages(
  mupdf: typeof import("mupdf"),
  doc: MuDoc,
  pages: number[] | null,
  paper: { size: [number, number]; orientation: string; margin: number; scaleUp: boolean } | null,
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
    const W = (mw / 25.4) * 72;
    const H = (mh / 25.4) * 72;
    const m = (paper.margin / 25.4) * 72;
    if (W - 2 * m <= 0 || H - 2 * m <= 0) throw new ConversionError("The margins are larger than the page.");
    let scale = Math.min((W - 2 * m) / pw, (H - 2 * m) / ph);
    if (!paper.scaleUp) scale = Math.min(scale, 1);
    const ox = (W - pw * scale) / 2;
    const oy = (H - ph * scale) / 2;
    const matrix = mupdf.Matrix.concat(
      mupdf.Matrix.concat(mupdf.Matrix.translate(-x0, -y0), mupdf.Matrix.scale(scale, scale)),
      mupdf.Matrix.translate(ox, oy),
    );
    const dev = writer.beginPage([0, 0, W, H]);
    page.run(dev, matrix);
    writer.endPage();
  }
  writer.close();
  return buf.asUint8Array().slice();
}

const savePdf = (pdf: import("mupdf").PDFDocument) => pdf.saveToBuffer("garbage,compress").asUint8Array().slice();

export const documentConverter: Converter = {
  id: "document",
  family: "document",
  options: [
    opt.text("page_range", "Pages", "all", { help: "e.g. 1-3, 5, 8- (all = every page)" }),
    opt.choice("paper", "Page size", "original", [["original", "Keep original"], ["a4", "A4"], ["letter", "US Letter"], ["legal", "US Legal"], ["a3", "A3"], ["a5", "A5"]], { targets: ["doc:pdf"] }),
    opt.choice("orientation", "Orientation", "auto", [["auto", "Match each page"], ["portrait", "Portrait"], ["landscape", "Landscape"]], { targets: ["doc:pdf"], showIf: (v) => v.paper !== "original" }),
    opt.slider("margin_mm", "Margins", 0, 0, 50, { unit: "mm", targets: ["doc:pdf"], showIf: (v) => v.paper !== "original" }),
    opt.bool("scale_up", "Enlarge small pages to fill the paper", true, { targets: ["doc:pdf"], showIf: (v) => v.paper !== "original" }),
  ],
  targets(files) {
    if (!webReadable(files)) return [];
    return [
      { id: "doc:pdf", label: "PDF", ext: "pdf", group: "Document", converterId: "document" },
      { id: "doc:txt", label: "TXT", ext: "txt", group: "Document", converterId: "document", hint: "Extract the text" },
    ];
  },
  async run(files, target, o, ctx) {
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name} (${i + 1}/${files.length})`);
      const mu = await import("../engines/mupdf");
      const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
      const doc = await mu.openDocument(f.file, f.ext);
      const pages = parsePageRange(o.page_range, doc.countPages());
      const stem = baseName(f.name);
      if (target.ext === "txt") {
        const text = pages.map((p) => doc.loadPage(p).toStructuredText("preserve-whitespace").asText()).join("\n\f");
        out.push({ name: unique(`${stem}.txt`), data: new TextEncoder().encode(text), type: "text/plain" });
        continue;
      }
      let data: Uint8Array;
      const all = pages.length === doc.countPages();
      if (o.paper !== "original") {
        data = writePages(mupdf, doc, pages, { size: mu.PAPER_MM[String(o.paper)], orientation: String(o.orientation), margin: Number(o.margin_mm), scaleUp: Boolean(o.scale_up) });
      } else if (doc.asPDF() && all) {
        data = savePdf(doc.asPDF()!);
      } else if (doc.asPDF()) {
        const dst = new mupdf.PDFDocument();
        for (const p of pages) dst.graftPage(-1, doc.asPDF()!, p);
        data = savePdf(dst);
      } else {
        data = writePages(mupdf, doc, pages, null); // EPUB/XPS/… → PDF
      }
      out.push({ name: unique(`${stem}.pdf`), data, type: "application/pdf" });
    }
    ctx.progress(1, "Done");
    return out;
  },
};

export const pagesConverter: Converter = {
  id: "pages",
  family: "document",
  options: [
    opt.slider("dpi", "Resolution", 150, 36, 600, { unit: "DPI", help: "72 = screen, 150 = good, 300 = print quality." }),
    opt.text("page_range", "Pages", "all", { help: "e.g. 1-3, 5, 8- (all = every page)" }),
    opt.slider("quality", "Quality", 90, 1, 100, { unit: "%", targets: ["pages:jpg"] }),
    opt.bool("transparent", "Transparent background", false, { targets: ["pages:png"] }),
    opt.bool("grayscale", "Black & white", false),
  ],
  targets(files) {
    if (!webReadable(files)) return [];
    return [
      { id: "pages:png", label: "PNG", ext: "png", group: "Images (one per page)", converterId: "pages" },
      { id: "pages:jpg", label: "JPG", ext: "jpg", group: "Images (one per page)", converterId: "pages" },
    ];
  },
  async run(files, target, o, ctx) {
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      const mu = await import("../engines/mupdf");
      const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
      const doc = await mu.openDocument(f.file, f.ext);
      const pages = parsePageRange(o.page_range, doc.countPages());
      const width = Math.max(3, String(Math.max(...pages) + 1).length);
      const stem = baseName(f.name);
      const cs = o.grayscale ? mupdf.ColorSpace.DeviceGray : mupdf.ColorSpace.DeviceRGB;
      const alpha = Boolean(o.transparent) && target.ext === "png" && !o.grayscale;
      const zoom = Number(o.dpi) / 72;
      for (const [k, p] of pages.entries()) {
        ctx.progress((i + k / pages.length) / files.length, `Rendering page ${p + 1} of ${f.name}`);
        const pix = doc.loadPage(p).toPixmap(mupdf.Matrix.scale(zoom, zoom), cs, alpha, true);
        const data = target.ext === "jpg" ? pix.asJPEG(Number(o.quality), false) : pix.asPNG();
        out.push({ name: `${stem}_pages/${stem}_page${String(p + 1).padStart(width, "0")}.${target.ext}`, data: data.slice(), type: target.ext === "jpg" ? "image/jpeg" : "image/png" });
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};

export function pageGroups(o: Values, count: number): number[][] {
  if (o.mode === "every") return [...Array(count).keys()].map((p) => [p]);
  if (o.mode === "chunks") {
    const size = Math.max(1, Number(o.pages_per_document));
    const groups: number[][] = [];
    for (let s = 0; s < count; s += size) groups.push([...Array(Math.min(size, count - s)).keys()].map((k) => s + k));
    return groups;
  }
  return parsePageGroups(o.ranges, count);
}

export function groupName(stem: string, index: number, total: number, pages: number[], naming: string): string {
  if (naming === "part") return `${stem}_part_${String(index + 1).padStart(Math.max(2, String(total).length), "0")}`;
  const first = pages[0] + 1;
  const last = pages[pages.length - 1] + 1;
  const contiguous = pages.every((p, k) => p === pages[0] + k);
  if (first === last) return `${stem}_page_${first}`;
  return contiguous ? `${stem}_pages_${first}-${last}` : `${stem}_part_${String(index + 1).padStart(2, "0")}`;
}

export const splitConverter: Converter = {
  id: "split",
  family: "document",
  options: [
    opt.choice("mode", "Split", "every", [["every", "Every page into its own document"], ["chunks", "Every N pages"], ["ranges", "Custom page ranges"]]),
    opt.slider("pages_per_document", "Pages per document", 2, 1, 100, { showIf: (v) => v.mode === "chunks", help: "The last document gets whatever pages are left." }),
    opt.text("ranges", "Page ranges", "1-3, 4-", { showIf: (v) => v.mode === "ranges", help: "One document per range, separated by commas. e.g. 1-3, 4-10, 11- (“11-” means page 11 to the end)." }),
    opt.choice("naming", "File names", "pages", [["pages", "name_pages_1-3.pdf"], ["part", "name_part_01.pdf"]]),
  ],
  targets(files) {
    if (!webReadable(files)) return [];
    return [{ id: "split:pdf", label: "Split into PDFs", ext: "pdf", group: "Split", converterId: "split", hint: "Split each document into several PDF files" }];
  },
  async run(files, _t, o, ctx) {
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Splitting ${f.name}`);
      const { mupdf, pdf } = await openAsPdf(f, ctx);
      const groups = pageGroups(o, pdf.countPages());
      const stem = baseName(f.name);
      for (const [k, pages] of groups.entries()) {
        const dst = new mupdf.PDFDocument();
        for (const p of pages) dst.graftPage(-1, pdf, p);
        out.push({ name: `${stem}_split/${groupName(stem, k, groups.length, pages, String(o.naming))}.pdf`, data: savePdf(dst), type: "application/pdf" });
        ctx.progress((i + (k + 1) / groups.length) / files.length, `Splitting ${f.name}: document ${k + 1} of ${groups.length}`);
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};
