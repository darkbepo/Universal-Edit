// Vector graphics — same target ids and options as the desktop app (converters/vector.py):
// AI → SVG / PDF / images, SVG → images (resvg), PDF pages → SVG, images → SVG (tracing).
// EPS / PS (Ghostscript), writing EPS / PS, and SVG → PDF / SVG are desktop-only.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile, type RunContext } from "../core/types";
import { baseName } from "./util";

const RASTER: Record<string, string> = { png: "PNG", jpg: "JPG", webp: "WEBP", tiff: "TIFF" };
const RASTER_TARGETS = Object.keys(RASTER).map((e) => `vec:${e}`);
const MIME: Record<string, string> = { svg: "image/svg+xml", pdf: "application/pdf", png: "image/png", jpg: "image/jpeg", webp: "image/webp", tiff: "image/tiff" };

const vectorOptions = [
  opt.int("width_px", "Image width", 0, { min: 0, max: 20000, unit: "px", targets: RASTER_TARGETS, help: "0 = the drawing's own size (SVG) or 150 DPI (AI / PDF). Height follows." }),
  opt.bool("transparent", "Transparent background", true, { targets: ["vec:png", "vec:webp", "vec:tiff"] }),
  opt.color("background", "Background colour", "#ffffff", { targets: RASTER_TARGETS, showIf: (v) => !v.transparent }),
  opt.slider("quality", "Quality", 90, 10, 100, { unit: "%", targets: ["vec:jpg", "vec:webp"] }),
  opt.bool("text_as_paths", "Turn text into outlines", true, { targets: ["svg", "pages:svg"], help: "Looks identical everywhere. Off keeps text selectable but needs the fonts installed." }),
];

const pageNames = (stem: string, count: number, ext: string) =>
  count === 1 ? [`${stem}.${ext}`] : Array.from({ length: count }, (_, i) => `${stem}_${String(i + 1).padStart(String(count).length, "0")}.${ext}`);

const MAGICK_FORMAT: Record<string, string> = { png: "PNG", jpg: "JPEG", webp: "WEBP", tiff: "TIFF" };

/** PNG pages (SVG via resvg, everything else via MuPDF) → the chosen raster format. */
async function toRaster(f: InputFile, data: Uint8Array, fmt: string, o: Values, ctx: RunContext): Promise<OutputFile[]> {
  const keepAlpha = Boolean(o.transparent) && fmt !== "jpg";
  let pngs: Uint8Array[];
  if (f.ext === "svg") {
    const { renderSvg } = await import("../engines/vector");
    pngs = [await renderSvg(data, Number(o.width_px), f.name)];
  } else {
    const mupdf = await import("mupdf");
    const { openVector } = await import("../engines/vector");
    const doc = await openVector(mupdf, data, f.ext, f.name);
    pngs = [];
    for (let i = 0; i < doc.countPages(); i++) {
      const page = doc.loadPage(i);
      const [x0, , x1] = page.getBounds();
      const zoom = Number(o.width_px) ? Number(o.width_px) / (x1 - x0) : 150 / 72;
      pngs.push(page.toPixmap(mupdf.Matrix.scale(zoom, zoom), mupdf.ColorSpace.DeviceRGB, true).asPNG());
    }
  }
  const names = pageNames(baseName(f.name), pngs.length, fmt);
  const out: OutputFile[] = [];
  for (const [i, png] of pngs.entries()) {
    let result = png;
    if (!(fmt === "png" && keepAlpha)) {
      const { getMagick, copy } = await import("../engines/magick");
      const M = await getMagick((m) => ctx.progress(0, m));
      result = M.ImageMagick.read(png, (img) => {
        if (!keepAlpha) {
          img.backgroundColor = new M.MagickColor(o.transparent ? "#ffffff" : String(o.background));
          img.alpha(M.AlphaAction.Remove);
        }
        if (fmt === "jpg" || fmt === "webp") img.quality = Number(o.quality);
        return img.write(MAGICK_FORMAT[fmt] as import("@imagemagick/magick-wasm").MagickFormat, copy);
      });
    }
    out.push({ name: names[i], data: result, type: MIME[fmt] });
  }
  return out;
}

export const vectorConverter: Converter = {
  id: "vector",
  family: "vector",
  options: vectorOptions,
  targets(files) {
    if (files.some((f) => f.ext === "eps" || f.ext === "ps")) return []; // registry explains: desktop only
    const t = (id: string, label: string, ext: string, group: string, hint?: string) => ({ id, label, ext, group, hint, converterId: "vector" });
    // SVG → SVG / PDF needs MuPDF's SVG reader, which this build lacks: desktop only
    const anySvg = files.some((f) => f.ext === "svg");
    return [
      ...(anySvg ? [] : [t("svg", "SVG", "svg", "Vector"), t("vec:pdf", "PDF", "pdf", "Document", "Keeps everything sharp at any zoom")]),
      ...Object.entries(RASTER).map(([e, label]) => t(`vec:${e}`, label, e, "Image")),
    ];
  },
  async run(files, target, o, ctx) {
    const mupdf = await import("mupdf");
    const { openVector, writePages } = await import("../engines/vector");
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name}`);
      const data = new Uint8Array(await f.file.arrayBuffer());
      if (target.id.startsWith("vec:") && target.id !== "vec:pdf") {
        out.push(...(await toRaster(f, data, target.ext, o, ctx)));
        continue;
      }
      const doc = await openVector(mupdf, data, f.ext, f.name);
      const stem = baseName(f.name);
      if (target.id === "vec:pdf") out.push({ name: `${stem}.pdf`, data: writePages(mupdf, doc, "pdf")[0], type: MIME.pdf });
      else if (target.id === "svg") {
        const svgs = writePages(mupdf, doc, "svg", o.text_as_paths ? "text=path" : "text=text");
        pageNames(stem, svgs.length, "svg").forEach((name, k) => out.push({ name, data: svgs[k], type: MIME.svg }));
      }
    }
    ctx.progress(1, "Done");
    return out;
  },
};

export const pdfToSvgConverter: Converter = {
  id: "pdf-svg",
  family: "document",
  options: vectorOptions,
  targets(files) {
    return files.every((f) => f.ext === "pdf") ? [{ id: "pages:svg", label: "SVG", ext: "svg", group: "Vector", converterId: "pdf-svg", hint: "Each page as a scalable vector image" }] : [];
  },
  async run(files, _target, o, ctx) {
    const mupdf = await import("mupdf");
    const { writePages } = await import("../engines/vector");
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name}`);
      const doc = mupdf.Document.openDocument(new Uint8Array(await f.file.arrayBuffer()), "application/pdf");
      if (doc.needsPassword()) throw new ConversionError(`“${f.name}” is password-protected. Use “Remove password” on it first.`);
      const svgs = writePages(mupdf, doc, "svg", o.text_as_paths ? "text=path" : "text=text");
      pageNames(baseName(f.name), svgs.length, "svg").forEach((name, k) => out.push({ name, data: svgs[k], type: MIME.svg }));
    }
    ctx.progress(1, "Done");
    return out;
  },
};

// Colour detail 1–8 (desktop: vtracer's bits per channel) → number of colours here.
const COLOURS = [2, 4, 6, 8, 12, 16, 24, 32];

export const traceConverter: Converter = {
  id: "trace",
  family: "image",
  options: [
    opt.choice("colour_mode", "Colours", "color", [["color", "Full colour"], ["binary", "Black & white"]]),
    opt.slider("colour_precision", "Colour detail", 6, 1, 8, { showIf: (v) => v.colour_mode === "color", help: "Fewer = flatter, poster-like result and a smaller file." }),
    opt.slider("speckle", "Ignore specks smaller than", 4, 0, 128, { unit: "px" }),
    opt.choice("curves", "Curves", "spline", [["spline", "Smooth curves"], ["polygon", "Straight lines"], ["none", "Pixel-exact"]]),
    opt.slider("corner_threshold", "Corner sharpness", 60, 0, 180, { unit: "°", showIf: (v) => v.curves === "spline", help: "Angles sharper than this stay corners." }),
    opt.int("max_size", "Shrink big images to", 1500, { min: 100, max: 10000, unit: "px", help: "Tracing time grows fast with size; the SVG scales back up perfectly." }),
  ],
  targets() {
    return [{ id: "trace:svg", label: "SVG (trace)", ext: "svg", group: "Vector", converterId: "trace", hint: "Turn the picture into scalable shapes" }];
  },
  async run(files, _target, o, ctx) {
    const { getMagick, readImage } = await import("../engines/magick");
    const { traceImage } = await import("../engines/vector");
    const M = await getMagick((m) => ctx.progress(0, m));
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Tracing ${f.name}`);
      const bytes = new Uint8Array(await f.file.arrayBuffer());
      const { rgba, width, height, origW, origH } = readImage(M, bytes, f.ext, (img) => {
        const [origW, origH] = [img.width, img.height];
        const max = Number(o.max_size);
        if (Math.max(img.width, img.height) > max) img.resize(max, max); // keeps aspect ratio
        if (o.colour_mode === "color") {
          const q = new M.QuantizeSettings();
          q.colors = COLOURS[Number(o.colour_precision) - 1];
          q.ditherMethod = M.DitherMethod.No;
          img.quantize(q);
        }
        const rgba = img.getPixels((px) => px.toByteArray(0, 0, img.width, img.height, "RGBA"))!;
        return { rgba: new Uint8ClampedArray(rgba), width: img.width, height: img.height, origW, origH };
      });
      const inner = await traceImage(rgba, width, height, o.colour_mode === "color", { curves: String(o.curves), speckle: Number(o.speckle), cornerThreshold: Number(o.corner_threshold) },
        (done, total) => ctx.progress((i + done / total) / files.length, `Tracing ${f.name}: colour ${done} of ${total}`));
      const svg = `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="${origW}" height="${origH}" viewBox="0 0 ${width} ${height}">${inner}</svg>\n`;
      out.push({ name: `${baseName(f.name)}.svg`, data: new TextEncoder().encode(svg), type: MIME.svg });
    }
    ctx.progress(1, "Done");
    return out;
  },
};
