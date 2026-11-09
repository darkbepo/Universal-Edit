// Vector graphics with MuPDF (SVG + PDF-based Illustrator files in; PDF / SVG / PNG / JPG out)
// and image tracing with vectortracer (MIT, vtracer). EPS / PS need Ghostscript: desktop only.
import { ConversionError } from "../core/types";

type MuPDF = typeof import("mupdf");
type Doc = import("mupdf").Document;

export async function openVector(mupdf: MuPDF, data: Uint8Array, ext: string, name: string): Promise<Doc> {
  try {
    return mupdf.Document.openDocument(data, ext === "svg" ? "image/svg+xml" : "application/pdf");
  } catch (e) {
    throw new ConversionError(
      ext === "ai" ? `“${name}” is an old (PostScript-based) Illustrator file. The desktop app can open it.` : `Couldn't read “${name}”. Is it a valid ${ext.toUpperCase()}?`,
      String(e),
    );
  }
}

/** Re-draw every page through a MuPDF document writer ("pdf" → one file, "svg" → one per page). */
export function writePages(mupdf: MuPDF, doc: Doc, format: "pdf" | "svg", options = ""): Uint8Array[] {
  const out: Uint8Array[] = [];
  let buf = new mupdf.Buffer();
  let writer = new mupdf.DocumentWriter(buf, format, options);
  const n = doc.countPages();
  for (let i = 0; i < n; i++) {
    const page = doc.loadPage(i);
    const dev = writer.beginPage(page.getBounds());
    page.run(dev, mupdf.Matrix.identity);
    writer.endPage();
    if (format === "svg") {
      writer.close();
      out.push(buf.asUint8Array().slice());
      if (i + 1 < n) {
        buf = new mupdf.Buffer();
        writer = new mupdf.DocumentWriter(buf, format, options);
      }
    }
  }
  if (format === "pdf") {
    writer.close();
    out.push(buf.asUint8Array().slice());
  }
  return out;
}

// ------------------------------------------------------------------------- tracing

let tracer: Promise<typeof import("vectortracer-bg")> | null = null;

async function vectortracer() {
  if (!tracer) {
    tracer = (async () => {
      const bg = await import("vectortracer-bg");
      const { default: url } = await import("vectortracer-wasm?url");
      const { instance } = await WebAssembly.instantiate(await (await fetch(url)).arrayBuffer(), { "./vectortracer_bg.js": bg as unknown as WebAssembly.ModuleImports });
      bg.__wbg_set_wasm(instance.exports);
      (instance.exports.__wbindgen_start as (() => void) | undefined)?.();
      return bg;
    })();
    tracer.catch(() => (tracer = null));
  }
  return tracer;
}

export interface TraceParams { curves: string; speckle: number; cornerThreshold: number }

/** Trace one black-on-white mask; returns the inner SVG (path elements), filled with ``fill``. */
async function traceMask(mask: ImageData, fill: string, p: TraceParams): Promise<string> {
  const vt = await vectortracer();
  const conv = new vt.BinaryImageConverter(mask, { debug: false, mode: p.curves, cornerThreshold: p.cornerThreshold, filterSpeckle: p.speckle, pathPrecision: 2 }, { invert: false, pathFill: fill, backgroundColor: undefined, attributes: undefined, scale: 1 });
  try {
    conv.init();
    while (!conv.tick()) { /* keep tracing */ }
    const svg = conv.getResult();
    const start = svg.indexOf(">", svg.indexOf("<svg")) + 1;
    return svg.slice(start, svg.lastIndexOf("</svg>"));
  } finally {
    conv.free();
  }
}

const hex = (r: number, g: number, b: number) => "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");

/**
 * Colour tracing without a colour tracer: the image is already reduced to a few colours; each
 * colour, from most to least common, is traced as a mask that also covers all *rarer* colours
 * ("stacked", like vtracer's own mode), so layers drawn on top leave no gaps.
 */
export async function traceImage(rgba: Uint8ClampedArray, width: number, height: number, colour: boolean, p: TraceParams, onLayer: (done: number, total: number) => void): Promise<string> {
  const n = width * height;
  const layers: string[] = [];
  if (!colour) {
    const mask = new ImageData(width, height);
    for (let i = 0; i < n; i++) {
      const lum = 0.299 * rgba[i * 4] + 0.587 * rgba[i * 4 + 1] + 0.114 * rgba[i * 4 + 2];
      const on = rgba[i * 4 + 3] > 127 && lum < 128;
      mask.data.set(on ? [0, 0, 0, 255] : [255, 255, 255, 255], i * 4);
    }
    layers.push(await traceMask(mask, "#000000", p));
  } else {
    const counts = new Map<number, number>();
    const keys = new Uint32Array(n);
    for (let i = 0; i < n; i++) {
      const k = rgba[i * 4 + 3] < 128 ? 0xffffffff : (rgba[i * 4] << 16) | (rgba[i * 4 + 1] << 8) | rgba[i * 4 + 2];
      keys[i] = k;
      if (k !== 0xffffffff) counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const order = [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    if (order.length > 64) throw new ConversionError("Too many colours to trace — lower “Colour detail”.");
    const rank = new Map(order.map((k, r) => [k, r]));
    for (const [r, k] of order.entries()) {
      const mask = new ImageData(width, height);
      for (let i = 0; i < n; i++) {
        const on = keys[i] !== 0xffffffff && rank.get(keys[i])! >= r;
        mask.data.set(on ? [0, 0, 0, 255] : [255, 255, 255, 255], i * 4);
      }
      layers.push(await traceMask(mask, hex(k >> 16, (k >> 8) & 255, k & 255), p));
      onLayer(r + 1, order.length);
    }
  }
  return layers.join("");
}

// ------------------------------------------------------------------------- SVG (resvg)

let resvg: Promise<typeof import("@resvg/resvg-wasm")> | null = null;
let fonts: Uint8Array[] = [];

/** SVG → PNG with resvg (MPL-2.0); this MuPDF build has no SVG reader. ``width`` 0 = own size. */
export async function renderSvg(svg: Uint8Array, width: number, name: string): Promise<Uint8Array> {
  if (!resvg) {
    resvg = (async () => {
      const mod = await import("@resvg/resvg-wasm");
      const { default: wasm } = await import("@resvg/resvg-wasm/index_bg.wasm?url");
      const { default: font } = await import("dejavu-fonts-ttf/ttf/DejaVuSans.ttf?url");
      await mod.initWasm(fetch(wasm));
      fonts = [new Uint8Array(await (await fetch(font)).arrayBuffer())];
      return mod;
    })();
    resvg.catch(() => (resvg = null));
  }
  const { Resvg } = await resvg;
  let r;
  try {
    r = new Resvg(svg, { fitTo: width ? { mode: "width", value: width } : { mode: "original" }, font: { fontBuffers: fonts, defaultFontFamily: "DejaVu Sans", sansSerifFamily: "DejaVu Sans" } });
  } catch (e) {
    throw new ConversionError(`Couldn't read “${name}”. Is it a valid SVG?`, String(e));
  }
  try {
    if (r.width * r.height > 400_000_000) throw new ConversionError("That output size is too large. Choose a smaller width.");
    return r.render().asPng();
  } finally {
    r.free();
  }
}
