// PDF toolkit: merge, compress, rotate, reorder/delete pages, add/remove password (MuPDF).
import { opt, parsePageRange } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile } from "../core/types";
import { reachable } from "./docroute";
import { baseName, uniqueNamer } from "./util";

const TOOLS: Record<string, [string, string, string]> = {
  "pdf:merge": ["Merge into one PDF", "Combine all files into a single PDF", ""],
  "pdf:compress": ["Compress", "Make the PDF smaller", "_compressed"],
  "pdf:rotate": ["Rotate pages", "Turn some or all pages", "_rotated"],
  "pdf:organize": ["Reorder / delete pages", "Keep only some pages, in any order", "_pages"],
  "pdf:protect": ["Add password", "Encrypt the PDF (AES-256)", "_protected"],
  "pdf:unlock": ["Remove password", "Save an unprotected copy", "_unlocked"],
};

// Compression presets: [image DPI, JPEG quality]; null = lossless clean-up only.
const LEVELS: Record<string, [number, number] | null> = { lossless: null, recommended: [150, 75], strong: [110, 60], extreme: [72, 45] };

// PDF permission bits (PDF 32000-1, table 22); reserved bits must be set.
const PERM = { print: 4 | 2048, modify: 8 | 32 | 256 | 1024, copy: 16, accessibility: 512 };

type MuPDF = typeof import("mupdf");
type PDFDoc = import("mupdf").PDFDocument;

function save(pdf: PDFDoc, extra = ""): Uint8Array {
  return pdf.saveToBuffer(`garbage=4,compress,compress-fonts,clean${extra ? "," + extra : ""}`).asUint8Array().slice();
}

async function openPdf(f: InputFile, password?: string): Promise<{ mupdf: MuPDF; pdf: PDFDoc }> {
  const mu = await import("../engines/mupdf");
  const mupdf = await mu.getMuPDF();
  const pdf = (await mu.openDocument(f.file, "pdf")).asPDF();
  if (!pdf) throw new ConversionError(`“${f.name}” isn't a PDF.`);
  if (pdf.needsPassword()) {
    if (password === undefined) throw new ConversionError(`“${f.name}” is password-protected. Use “Remove password” on it first.`);
    if (!pdf.authenticatePassword(password)) throw new ConversionError(`That password doesn't open “${f.name}”.`);
  }
  return { mupdf, pdf };
}

/** Downsample + JPEG-recompress every embedded photo larger than the target DPI. */
function recompressImages(mupdf: MuPDF, pdf: PDFDoc, dpi: number, quality: number): void {
  let maxPagePt = 0;
  for (let i = 0; i < pdf.countPages(); i++) {
    const [x0, y0, x1, y1] = pdf.loadPage(i).getBounds();
    maxPagePt = Math.max(maxPagePt, x1 - x0, y1 - y0);
  }
  const maxPx = Math.ceil((maxPagePt / 72) * dpi); // a full-page image needs no more than this
  for (let n = 1; n < pdf.countObjects(); n++) {
    const ref = pdf.newIndirect(n);
    const obj = ref.resolve();
    if (!obj.isStream() || obj.get("Subtype").asName?.() !== "Image") continue;
    if (obj.get("ImageMask").isNull() === false && obj.get("ImageMask").asString?.() === "true") continue;
    const bpc = obj.get("BitsPerComponent");
    if (bpc.isNumber() && bpc.asNumber() < 8) continue; // line art / masks: leave alone
    let image;
    try {
      image = pdf.loadImage(ref);
    } catch {
      continue;
    }
    const w = image.getWidth();
    const h = image.getHeight();
    const scale = Math.min(1, maxPx / Math.max(w, h));
    let pix = image.toPixmap();
    const cs = pix.getColorSpace();
    if (!cs || (!cs.isRGB() && !cs.isGray())) pix = pix.convertToColorSpace(mupdf.ColorSpace.DeviceRGB);
    if (pix.getAlpha()) continue; // transparency lives in /SMask; keep those images as they are
    const nw = Math.max(1, Math.round(w * scale));
    const nh = Math.max(1, Math.round(h * scale));
    if (scale < 1) pix = pix.warp([[0, 0], [w, 0], [w, h], [0, h]], nw, nh);
    const jpeg = pix.asJPEG(quality, false);
    if (jpeg.length >= obj.readRawStream().getLength()) continue; // not worth it
    const gray = pix.getColorSpace()?.isGray();
    obj.writeRawStream(jpeg);
    obj.put("Filter", pdf.newName("DCTDecode"));
    obj.put("Width", nw);
    obj.put("Height", nh);
    obj.put("BitsPerComponent", 8);
    obj.put("ColorSpace", pdf.newName(gray ? "DeviceGray" : "DeviceRGB"));
    obj.delete("DecodeParms");
    obj.delete("Decode");
  }
}

export const pdfToolsConverter: Converter = {
  id: "pdftools",
  family: "document",
  options: [
    opt.choice("order", "File order", "selected", [["selected", "As listed (drag to reorder)"], ["name", "By file name"]], { targets: ["pdf:merge"] }),
    opt.bool("bookmarks", "Add a bookmark for each file", true, { targets: ["pdf:merge"] }),
    opt.choice("level", "Compression", "recommended", [["lossless", "Lossless (clean up only)"], ["recommended", "Recommended (images 150 DPI)"], ["strong", "Strong (images 110 DPI)"], ["extreme", "Extreme (images 72 DPI, lower quality)"]], { targets: ["pdf:compress"] }),
    opt.choice("angle", "Rotate by", 90, [[90, "90° clockwise"], [180, "180°"], [270, "90° counter-clockwise"]], { targets: ["pdf:rotate"] }),
    opt.text("rotate_pages", "Pages", "all", { targets: ["pdf:rotate"], help: "e.g. 1-3, 5, 8- (all = every page)" }),
    opt.text("keep", "Pages to keep, in order", "all", { targets: ["pdf:organize"], help: "e.g. 3, 1-2, 5- puts page 3 first and drops page 4. Leave out pages to delete them." }),
    opt.bool("reverse", "Reverse the order", false, { targets: ["pdf:organize"] }),
    opt.password("password", "Password", { targets: ["pdf:protect", "pdf:unlock"] }),
    opt.password("owner_password", "Owner password (optional)", { targets: ["pdf:protect"], help: "Lets you change the permissions below later. Empty = they can never be changed." }),
    opt.bool("allow_print", "Allow printing", true, { targets: ["pdf:protect"] }),
    opt.bool("allow_copy", "Allow copying text", true, { targets: ["pdf:protect"] }),
    opt.bool("allow_edit", "Allow editing", false, { targets: ["pdf:protect"] }),
  ],
  targets(files) {
    const t = (id: string) => ({ id, label: TOOLS[id][0], ext: "pdf", group: "PDF tools", converterId: "pdftools", hint: TOOLS[id][1] });
    const out = [];
    if (files.length > 1 && files.every((f) => reachable(f.ext).has("pdf"))) out.push(t("pdf:merge"));
    if (files.every((f) => f.ext === "pdf")) for (const id of Object.keys(TOOLS)) if (id !== "pdf:merge") out.push(t(id));
    return out;
  },
  async run(files, target, o, ctx) {
    const tool = target.id;
    const unique = uniqueNamer();
    if (tool === "pdf:merge") {
      const { asPdf } = await import("./pdf");
      const mu = await import("../engines/mupdf");
      const mupdf = await mu.getMuPDF((m) => ctx.progress(0, m));
      const list = o.order === "name" ? [...files].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })) : files;
      const out = new mupdf.PDFDocument();
      const marks: { title: string; page: number }[] = [];
      for (const [i, f] of list.entries()) {
        ctx.progress((0.9 * i) / list.length, `Adding ${f.name}`);
        const src = mupdf.Document.openDocument(await asPdf(f, ctx), "application/pdf").asPDF()!;
        if (src.needsPassword()) throw new ConversionError(`“${f.name}” is password-protected. Use “Remove password” on it first.`);
        marks.push({ title: baseName(f.name), page: out.countPages() });
        for (let p = 0; p < src.countPages(); p++) out.graftPage(-1, src, p);
      }
      if (o.bookmarks) {
        const it = out.outlineIterator();
        for (const m of marks) {
          it.insert({ title: m.title, uri: `#page=${m.page + 1}`, open: false });
          it.next();
        }
      }
      ctx.progress(1, "Done");
      return [{ name: `${baseName(list[0].name)} + ${list.length - 1} more.pdf`, data: save(out), type: "application/pdf" }];
    }
    const outputs: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `${TOOLS[tool][0]}: ${f.name} (${i + 1}/${files.length})`);
      const { mupdf, pdf } = await openPdf(f, tool === "pdf:unlock" ? String(o.password ?? "") : undefined);
      let data: Uint8Array;
      if (tool === "pdf:compress") {
        const preset = LEVELS[String(o.level)];
        if (preset) recompressImages(mupdf, pdf, preset[0], preset[1]);
        pdf.subsetFonts();
        data = save(pdf);
      } else if (tool === "pdf:rotate") {
        for (const p of parsePageRange(o.rotate_pages, pdf.countPages())) {
          const page = pdf.loadPage(p).getObject();
          const cur = page.getInheritable("Rotate");
          page.put("Rotate", ((cur.isNumber() ? cur.asNumber() : 0) + Number(o.angle)) % 360);
        }
        data = save(pdf);
      } else if (tool === "pdf:organize") {
        const pages = parsePageRange(o.keep, pdf.countPages());
        if (o.reverse) pages.reverse();
        pdf.rearrangePages(pages);
        data = save(pdf);
      } else if (tool === "pdf:protect") {
        const pw = String(o.password ?? "");
        if (!pw) throw new ConversionError("Enter a password to protect the PDF with.");
        let owner = String(o.owner_password ?? "");
        if (/[,=]/.test(pw + owner)) throw new ConversionError("In the browser version, passwords can't contain commas or “=”.");
        // The owner password unlocks everything, so it must differ from the open password.
        if (!owner || owner === pw) owner = [...crypto.getRandomValues(new Uint8Array(18))].map((b) => b.toString(36)).join("");
        let perms = PERM.accessibility | 0xfffff0c0;
        if (o.allow_print) perms |= PERM.print;
        if (o.allow_copy) perms |= PERM.copy;
        if (o.allow_edit) perms |= PERM.modify;
        data = save(pdf, `encrypt=aes-256,user-password=${pw},owner-password=${owner},permissions=${perms | 0}`);
      } else {
        data = save(pdf, "decrypt");
      }
      outputs.push({ name: unique(`${baseName(f.name)}${TOOLS[tool][2]}.pdf`), data, type: "application/pdf" });
    }
    ctx.progress(1, "Done");
    return outputs;
  },
};
