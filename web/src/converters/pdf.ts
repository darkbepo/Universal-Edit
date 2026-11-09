// Documents in the browser: Pandoc (Word, ODT, RTF, Markdown, HTML, EPUB, TXT, CSV/XLSX/PPTX…)
// and MuPDF (PDF and e-books, plus HTML/text → PDF layout). Routes come from docroute.ts.
import { opt, parsePageGroups, parsePageRange, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type RunContext } from "../core/types";
import { MUPDF_OPEN, plan, reachable } from "./docroute";
import { baseName, MIME, uniqueNamer } from "./util";

const TEXT_TARGETS = ["pdf", "docx", "odt", "rtf", "txt", "md", "html", "epub", "pptx", "rst", "tex"];
const LABELS: Record<string, string> = { md: "Markdown", tex: "LaTeX", pptx: "PPTX (slides)" };
const DOC_MIME: Record<string, string> = {
  ...MIME,
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  odt: "application/vnd.oasis.opendocument.text",
  rtf: "application/rtf",
  md: "text/markdown",
  html: "text/html",
  epub: "application/epub+zip",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  rst: "text/plain",
  tex: "application/x-tex",
};

const common = (files: InputFile[]): Set<string> => {
  const sets = files.map((f) => reachable(f.ext));
  if (!sets.length) return new Set();
  return new Set([...sets[0]].filter((x) => sets.every((r) => r.has(x))));
};

interface Step {
  bytes: Uint8Array;
  ext: string;
}

/** Follow the planned route from ``input`` to ``dst``. */
async function runRoute(
  input: Step,
  dst: string,
  stem: string,
  ctx: RunContext,
  o: { toc?: boolean; keepMedia?: boolean; paper?: { size: [number, number]; landscape: boolean; marginMm: number } },
): Promise<{ bytes: Uint8Array; media: Record<string, Uint8Array> }> {
  const route = plan(input.ext, dst);
  if (!route) throw new ConversionError(`Can't convert ${input.ext.toUpperCase()} to ${dst.toUpperCase()} in the browser.`);
  let cur = input;
  let media: Record<string, Uint8Array> = {};
  for (const [i, edge] of route.entries()) {
    const last = i === route.length - 1;
    ctx.progress(0.1 + (0.8 * i) / route.length, `Converting ${edge.src.toUpperCase()} → ${edge.dst.toUpperCase()}`);
    if (edge.engine === "pandoc") {
      const { runPandoc } = await import("../engines/pandoc");
      const r = await runPandoc(cur.bytes, edge.src, edge.dst, {
        stem, toc: last && o.toc, keepMedia: last && o.keepMedia, onStatus: (m) => ctx.progress(0.1, m),
      });
      media = r.media;
      cur = { bytes: r.data, ext: edge.dst };
      continue;
    }
    const mu = await import("../engines/mupdf");
    const mupdf = await mu.getMuPDF((m) => ctx.progress(0.1, m));
    const paper = o.paper ?? { size: mu.PAPER_MM.a4, landscape: false, marginMm: 0 };
    let out: Uint8Array;
    if (edge.src === "html" && edge.dst === "pdf") {
      out = mu.htmlToPdf(mupdf, new TextDecoder().decode(cur.bytes), paper);
    } else if (edge.src === "txt" && edge.dst === "pdf") {
      out = mu.htmlToPdf(mupdf, mu.textToHtml(cur.bytes, stem), { ...paper, marginMm: paper.marginMm || 20 });
    } else {
      const doc = mupdf.Document.openDocument(cur.bytes, mu.MAGIC[edge.src] ?? edge.src);
      if (edge.dst === "pdf") {
        out = mu.writePages(mupdf, doc, null, null);
      } else {
        const text = [...Array(doc.countPages()).keys()].map((p) => doc.loadPage(p).toStructuredText("preserve-whitespace").asText()).join("\n\f");
        out = new TextEncoder().encode(edge.dst === "html" ? mu.textToParagraphHtml(text, stem) : text);
      }
    }
    cur = { bytes: out, ext: edge.dst };
  }
  return { bytes: cur.bytes, media };
}

const fileBytes = async (f: InputFile) => new Uint8Array(await f.file.arrayBuffer());

/** Any supported document as PDF bytes (used by page images and split). */
async function asPdf(f: InputFile, ctx: RunContext): Promise<Uint8Array> {
  const bytes = await fileBytes(f);
  if (f.ext === "pdf") return bytes;
  return (await runRoute({ bytes, ext: f.ext }, "pdf", baseName(f.name), ctx, {})).bytes;
}

const PAPER = (v: Values) => v.paper !== "original";

export const documentConverter: Converter = {
  id: "document",
  family: "document",
  options: [
    opt.text("page_range", "Pages", "all", { help: "For PDF and e-book sources. e.g. 1-3, 5, 8- (all = every page)" }),
    opt.bool("toc", "Add a table of contents", false, { targets: ["doc:html", "doc:epub", "doc:docx", "doc:odt", "doc:md"] }),
    opt.choice("paper", "Page size", "original", [["original", "Keep original (A4 for text documents)"], ["a4", "A4"], ["letter", "US Letter"], ["legal", "US Legal"], ["a3", "A3"], ["a5", "A5"]], { targets: ["doc:pdf"] }),
    opt.choice("orientation", "Orientation", "auto", [["auto", "Match each page"], ["portrait", "Portrait"], ["landscape", "Landscape"]], { targets: ["doc:pdf"], showIf: PAPER }),
    opt.slider("margin_mm", "Margins", 0, 0, 50, { unit: "mm", targets: ["doc:pdf"], showIf: PAPER }),
    opt.bool("scale_up", "Enlarge small pages to fill the paper", true, { targets: ["doc:pdf"], showIf: PAPER, help: "For PDF sources." }),
  ],
  targets(files) {
    const ok = common(files);
    const ext = files.length === 1 ? files[0].ext : "";
    return TEXT_TARGETS.filter((t) => ok.has(t) && (t !== ext || t === "pdf")).map((t) => ({
      id: `doc:${t}`, label: LABELS[t] ?? t.toUpperCase(), ext: t, group: "Document", converterId: "document",
    }));
  },
  async run(files, target, o, ctx) {
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    const dst = target.ext;
    for (const [i, f] of files.entries()) {
      const msg = `Converting ${f.name} (${i + 1}/${files.length})`;
      ctx.progress(i / files.length, msg);
      const sub: RunContext = { progress: (p, m) => ctx.progress((i + p) / files.length, m || msg) };
      const stem = baseName(f.name);
      const mu = await import("../engines/mupdf");
      let step: Step = { bytes: await fileBytes(f), ext: f.ext };

      // Page selection / fixed-page handling for PDF and e-books.
      if (MUPDF_OPEN.includes(f.ext)) {
        const mupdf = await mu.getMuPDF((m) => sub.progress(0, m));
        const doc = await mu.openDocument(f.file, f.ext);
        const pages = parsePageRange(o.page_range, doc.countPages());
        const all = pages.length === doc.countPages();
        if (dst === "pdf") {
          let data: Uint8Array;
          const pdf = doc.asPDF();
          if (PAPER(o)) {
            data = mu.writePages(mupdf, doc, pages, { size: mu.PAPER_MM[String(o.paper)], orientation: String(o.orientation), margin: Number(o.margin_mm), scaleUp: Boolean(o.scale_up) });
          } else if (pdf && all) {
            data = mu.savePdf(pdf);
          } else if (pdf) {
            const dstDoc = new mupdf.PDFDocument();
            for (const p of pages) dstDoc.graftPage(-1, pdf, p);
            data = mu.savePdf(dstDoc);
          } else {
            data = mu.writePages(mupdf, doc, pages, null);
          }
          out.push({ name: unique(`${stem}.pdf`), data, type: "application/pdf" });
          continue;
        }
        if (!all) {
          step = { bytes: mu.writePages(mupdf, doc, pages, null), ext: "pdf" };
        }
      }

      const landscape = o.orientation === "landscape";
      const paper = PAPER(o) ? { size: mu.PAPER_MM[String(o.paper)], landscape, marginMm: Number(o.margin_mm) } : undefined;
      const { bytes, media } = await runRoute(step, dst, stem, sub, { toc: Boolean(o.toc), keepMedia: true, paper });
      out.push({ name: unique(`${stem}.${dst}`), data: bytes, type: DOC_MIME[dst] ?? "application/octet-stream" });
      for (const [path, data] of Object.entries(media)) out.push({ name: path, data, type: "application/octet-stream" });
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
    if (!common(files).has("pdf")) return [];
    return [
      { id: "pages:png", label: "PNG", ext: "png", group: "Images (one per page)", converterId: "pages" },
      { id: "pages:jpg", label: "JPG", ext: "jpg", group: "Images (one per page)", converterId: "pages" },
    ];
  },
  async run(files, target, o, ctx) {
    const out: OutputFile[] = [];
    const mu = await import("../engines/mupdf");
    const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Preparing ${f.name}`);
      const doc = mupdf.Document.openDocument(await asPdf(f, ctx), "application/pdf");
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
    if (!common(files).has("pdf")) return [];
    return [{ id: "split:pdf", label: "Split into PDFs", ext: "pdf", group: "Split", converterId: "split", hint: "Split each document into several PDF files" }];
  },
  async run(files, _t, o, ctx) {
    const out: OutputFile[] = [];
    const mu = await import("../engines/mupdf");
    const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Splitting ${f.name}`);
      const pdf = mupdf.Document.openDocument(await asPdf(f, ctx), "application/pdf").asPDF()!;
      const groups = pageGroups(o, pdf.countPages());
      const stem = baseName(f.name);
      for (const [k, pages] of groups.entries()) {
        const dst = new mupdf.PDFDocument();
        for (const p of pages) dst.graftPage(-1, pdf, p);
        out.push({ name: `${stem}_split/${groupName(stem, k, groups.length, pages, String(o.naming))}.pdf`, data: mu.savePdf(dst), type: "application/pdf" });
        ctx.progress((i + (k + 1) / groups.length) / files.length, `Splitting ${f.name}: document ${k + 1} of ${groups.length}`);
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};
