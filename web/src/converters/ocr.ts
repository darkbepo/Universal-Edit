// OCR: scanned PDFs and photos → searchable PDF (scan + invisible text) or plain text.
// Same options and target ids as the desktop app (converters/ocr.py).
import { opt, parsePageRange, type Values } from "../core/options";
import { ConversionError, type Converter, type Family, type InputFile, type OutputFile, type RunContext, type Target } from "../core/types";
import { LANGUAGES } from "../engines/tesseract";
import { baseName, uniqueNamer } from "./util";

const TARGETS: [string, string, string, string][] = [
  ["ocr:pdf", "Searchable PDF", "pdf", "Recognise the text and keep the scan; text becomes selectable"],
  ["ocr:txt", "Text (OCR)", "txt", "Recognise the text and save it as plain text"],
];
const LANG_CHOICES: [string, string][] = LANGUAGES.map(([c, n]) => [c, n]);

const options = [
  opt.choice("language", "Language", "eng", LANG_CHOICES, { help: "The language of the text. Each language downloads once (1–8 MB) and is then kept in your browser." }),
  opt.choice("language2", "Second language (optional)", "none", [["none", "None"], ...LANG_CHOICES], { help: "For documents that mix two languages. Slower." }),
  opt.bool("combine", "Combine all images into one file", true, { help: "When several images are added. Off = one output file per image." }),
  opt.text("page_range", "Pages (PDF)", "all", { help: "e.g. 1-3, 5, 8- (all = every page)" }),
  opt.bool("skip_text_pages", "Keep pages that already have text (PDF)", true, { help: "Only scanned pages are recognised; pages with real text are copied as they are." }),
  opt.choice("dpi", "Scan resolution (PDF)", "300", [["200", "200 DPI (faster)"], ["300", "300 DPI (recommended)"], ["400", "400 DPI (small print)"]]),
];

interface Page { png: Uint8Array; dpi: number }

/** PNG + DPI for an image file (small images are upscaled: Tesseract reads small text badly). */
async function imagePage(f: InputFile, ctx: RunContext): Promise<Page> {
  const { getMagick, readImage, copy } = await import("../engines/magick");
  const M = await getMagick((m) => ctx.progress(0, m));
  const bytes = new Uint8Array(await f.file.arrayBuffer());
  return readImage(M, bytes, f.ext, (img) => {
    let dpi = Math.round(img.density.x || 0);
    if (img.density.units === M.DensityUnit.PixelsPerCentimeter) dpi = Math.round(img.density.x * 2.54);
    if (dpi < 72) dpi = 300;
    img.backgroundColor = new M.MagickColor("white");
    img.alpha(M.AlphaAction.Remove);
    if (Math.min(img.width, img.height) < 1000) {
      img.resize(img.width * 2, img.height * 2);
      dpi *= 2;
    }
    return img.write(M.MagickFormat.Png, (d) => ({ png: copy(d), dpi }));
  });
}

async function run(files: InputFile[], target: Target, o: Values, ctx: RunContext): Promise<OutputFile[]> {
  const { getTesseract } = await import("../engines/tesseract");
  const mu = await import("../engines/mupdf");
  const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
  const codes = [String(o.language)];
  if (o.language2 !== "none" && o.language2 !== o.language) codes.push(String(o.language2));
  const asPdf = target.ext === "pdf";
  const unique = uniqueNamer();
  const total = files.length;
  let done = 0;
  let pageProgress = (_p: number) => {};
  const tess = await getTesseract(codes, (m) => ctx.progress(0, m), (p) => pageProgress(p));

  const recognise = async (page: Page, out: import("mupdf").PDFDocument, texts: string[]) => {
    await tess.setParameters({ user_defined_dpi: String(page.dpi) });
    const r = await tess.recognize(page.png, {}, { text: true, pdf: asPdf });
    texts.push(r.data.text.trim());
    if (asPdf) {
      if (!r.data.pdf) throw new ConversionError("Text recognition (OCR) didn't produce a PDF.");
      const one = mupdf.Document.openDocument(Uint8Array.from(r.data.pdf), "application/pdf").asPDF()!;
      out.graftPage(-1, one, 0);
    }
  };

  const combine = Boolean(o.combine) && files.length > 1 && files.every((f) => f.ext !== "pdf");
  const groups = combine ? [files] : files.map((f) => [f]);
  const outputs: OutputFile[] = [];
  for (const group of groups) {
    const out = new mupdf.PDFDocument();
    const texts: string[] = [];
    for (const f of group) {
      const base = done / total;
      ctx.progress(base, `Recognising text in ${f.name} (${done + 1}/${total})`);
      if (f.ext === "pdf") {
        const src = (await mu.openDocument(f.file, "pdf")).asPDF()!;
        if (src.needsPassword()) throw new ConversionError(`“${f.name}” is password-protected. Use “Remove password” on it first.`);
        const pages = parsePageRange(String(o.page_range), src.countPages());
        const dpi = Number(o.dpi);
        for (const [n, i] of pages.entries()) {
          const at = base + n / pages.length / total;
          pageProgress = (p) => ctx.progress(at + p / pages.length / total, `${f.name}: page ${n + 1} of ${pages.length}`);
          pageProgress(0);
          const page = src.loadPage(i);
          const existing = page.toStructuredText("").asText().trim();
          if (o.skip_text_pages && existing) {
            texts.push(existing);
            if (asPdf) out.graftPage(-1, src, i);
            continue;
          }
          const pix = page.toPixmap(mupdf.Matrix.scale(dpi / 72, dpi / 72), mupdf.ColorSpace.DeviceRGB, false);
          await recognise({ png: pix.asPNG(), dpi }, out, texts);
        }
      } else {
        pageProgress = (p) => ctx.progress(base + p / total, `Recognising text in ${f.name} (${done + 1}/${total})`);
        await recognise(await imagePage(f, ctx), out, texts);
      }
      done++;
    }
    const first = baseName(group[0].name);
    const stem = group.length > 1 ? `${first} + ${group.length - 1} more` : first;
    if (asPdf) outputs.push({ name: unique(`${stem}_ocr.pdf`), data: mu.savePdf(out), type: "application/pdf" });
    else outputs.push({ name: unique(`${stem}.txt`), data: new TextEncoder().encode(texts.join("\n\n") + "\n"), type: "text/plain" });
  }
  ctx.progress(1, "Done");
  return outputs;
}

function make(family: Family, accepts: (files: InputFile[]) => boolean): Converter {
  return {
    id: `ocr-${family}`,
    family,
    options,
    targets(files) {
      if (!accepts(files)) return [];
      return TARGETS.map(([id, label, ext, hint]) => ({ id, label, ext, hint, group: "OCR (recognise text)", converterId: `ocr-${family}` }));
    },
    run,
  };
}

export const ocrImageConverter = make("image", () => true);
export const ocrPdfConverter = make("document", (files) => files.every((f) => f.ext === "pdf"));
