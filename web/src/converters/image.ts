// Image ↔ image (ImageMagick) and images → PDF.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type OutputFile } from "../core/types";
import { baseName, uniqueNamer } from "./util";

// target id → [label, ImageMagick format, mime]
export const IMAGE_FORMATS: Record<string, [string, string, string]> = {
  png: ["PNG", "PNG", "image/png"],
  jpg: ["JPG", "JPEG", "image/jpeg"],
  webp: ["WEBP", "WEBP", "image/webp"],
  avif: ["AVIF", "AVIF", "image/avif"],
  jxl: ["JXL", "JXL", "image/jxl"],
  gif: ["GIF", "GIF", "image/gif"],
  bmp: ["BMP", "BMP", "image/bmp"],
  tiff: ["TIFF", "TIFF", "image/tiff"],
  ico: ["ICO", "ICO", "image/x-icon"],
  tga: ["TGA", "TGA", "image/x-tga"],
  qoi: ["QOI", "QOI", "image/qoi"],
};
const LOSSY = ["jpg", "webp", "avif", "jxl"];
const NO_ALPHA = ["jpg", "bmp"];
const ANIMATED = ["gif", "webp", "png"];

export const imageConverter: Converter = {
  id: "image",
  family: "image",
  options: [
    opt.slider("quality", "Quality", 90, 1, 100, { unit: "%", targets: LOSSY, help: "Higher = better looking, bigger file." }),
    opt.choice("resize", "Resize", "none", [
      ["none", "Original size"], ["percent", "Scale by percentage"],
      ["fit", "Fit within width × height"], ["exact", "Exact width × height (stretch)"],
    ]),
    opt.slider("scale_percent", "Scale", 100, 1, 400, { unit: "%", showIf: (v) => v.resize === "percent" }),
    opt.int("width", "Width", 1920, { min: 1, max: 30000, unit: "px", showIf: (v) => v.resize === "fit" || v.resize === "exact" }),
    opt.int("height", "Height", 1080, { min: 1, max: 30000, unit: "px", showIf: (v) => v.resize === "fit" || v.resize === "exact" }),
    opt.choice("ico_size", "Largest icon size", 256, [16, 24, 32, 48, 64, 128, 256].map((s) => [s, `${s} × ${s}`]), { targets: ["ico"] }),
    opt.bool("grayscale", "Convert to black & white", false),
    opt.bool("auto_rotate", "Auto-rotate (use camera orientation)", true),
    opt.bool("keep_metadata", "Keep metadata (EXIF, colour profile)", true),
    opt.bool("keep_animation", "Keep animation (GIF/WEBP/APNG)", true, { targets: ANIMATED }),
    opt.color("background", "Background for transparency", "#ffffff", { help: "Used where the output format can't store transparency." }),
  ],
  targets() {
    return Object.entries(IMAGE_FORMATS).map(([id, [label]]) => ({
      id, label, ext: id, group: "Image", converterId: "image",
    }));
  },
  async run(files, target, o, ctx) {
    const { getMagick, copy } = await import("../engines/magick");
    const M = await getMagick((m) => ctx.progress(0, m));
    const [, format, mime] = IMAGE_FORMATS[target.id];
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name} (${i + 1}/${files.length})`);
      const bytes = new Uint8Array(await f.file.arrayBuffer());
      let data: Uint8Array;
      try {
        data = M.ImageMagick.readCollection(bytes, (images) => {
          const animated = Boolean(o.keep_animation) && ANIMATED.includes(target.id) && images.length > 1;
          if (animated) images.coalesce();
          const frames = animated ? [...images] : [images[0]];
          for (const img of frames) process(M, img, o, target.id);
          if (animated) {
            while (images.length > frames.length) images.pop();
            return images.write(format as import("@imagemagick/magick-wasm").MagickFormat, copy);
          }
          return frames[0].write(format as import("@imagemagick/magick-wasm").MagickFormat, copy);
        });
      } catch (e) {
        throw new ConversionError(`Couldn't convert “${f.name}” to ${target.label}.`, String(e));
      }
      out.push({ name: unique(`${baseName(f.name)}.${target.ext}`), data, type: mime });
    }
    ctx.progress(1, "Done");
    return out;
  },
};

type M = typeof import("@imagemagick/magick-wasm");
type Img = import("@imagemagick/magick-wasm").IMagickImage;

function process(M: M, img: Img, o: Values, target: string): void {
  if (o.auto_rotate) img.autoOrient();
  if (o.resize === "percent" && o.scale_percent !== 100) {
    img.resize(new M.MagickGeometry(`${o.scale_percent}%`));
  } else if (o.resize === "fit") {
    img.resize(new M.MagickGeometry(Number(o.width), Number(o.height)));
  } else if (o.resize === "exact") {
    const g = new M.MagickGeometry(Number(o.width), Number(o.height));
    g.ignoreAspectRatio = true;
    img.resize(g);
  }
  if (o.grayscale) img.grayscale();
  if (!o.keep_metadata) img.strip();
  if (NO_ALPHA.includes(target) && img.hasAlpha) {
    img.backgroundColor = new M.MagickColor(String(o.background || "#ffffff"));
    img.alpha(M.AlphaAction.Remove);
  }
  if (LOSSY.includes(target)) img.quality = Number(o.quality);
  if (target === "ico") {
    const size = Number(o.ico_size);
    img.resize(new M.MagickGeometry(size, size));
    img.backgroundColor = new M.MagickColor("none");
    img.extent(size, size, M.Gravity.Center);
    const sizes = [16, 24, 32, 48, 64, 128, 256].filter((s) => s <= size).join(",");
    img.settings.setDefine("icon:auto-resize", sizes);
  }
}

// ------------------------------------------------------------------------- images → PDF

const PAGE_MM: Record<string, [number, number]> = { a4: [210, 297], letter: [215.9, 279.4], a5: [148, 210], a3: [297, 420] };

export const imagesToPdfConverter: Converter = {
  id: "images-pdf",
  family: "image",
  options: [
    opt.bool("combine", "Combine all images into one PDF", true, { help: "Off = one PDF per image." }),
    opt.choice("page_size", "Page size", "image", [["image", "Same as image"], ["a4", "A4"], ["letter", "US Letter"], ["a5", "A5"], ["a3", "A3"]]),
    opt.choice("orientation", "Orientation", "auto", [["auto", "Match each image"], ["portrait", "Portrait"], ["landscape", "Landscape"]], { showIf: (v) => v.page_size !== "image" }),
    opt.slider("margin_mm", "Margin", 10, 0, 50, { unit: "mm", showIf: (v) => v.page_size !== "image" }),
    opt.slider("quality", "JPEG quality", 90, 10, 100, { unit: "%" }),
    opt.color("background", "Page / transparency background", "#ffffff"),
  ],
  targets() {
    return [{ id: "images:pdf", label: "PDF", ext: "pdf", group: "Document", converterId: "images-pdf", hint: "Put the images into a PDF document" }];
  },
  async run(files, _target, o, ctx) {
    const { getMagick, copy } = await import("../engines/magick");
    const { getMuPDF } = await import("../engines/mupdf");
    const M = await getMagick((m) => ctx.progress(0, m));
    const mupdf = await getMuPDF();
    const dpi = 150;
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    let doc = new mupdf.PDFDocument();
    const finish = (name: string) => {
      out.push({ name: unique(name), data: doc.saveToBuffer("garbage,compress").asUint8Array().slice(), type: "application/pdf" });
      doc = new mupdf.PDFDocument();
    };
    for (const [i, f] of files.entries()) {
      ctx.progress((i / files.length) * 0.95, `Adding ${f.name}`);
      const bytes = new Uint8Array(await f.file.arrayBuffer());
      const [jpeg, w, h] = M.ImageMagick.read(bytes, (img) => {
        img.autoOrient();
        img.backgroundColor = new M.MagickColor(String(o.background));
        img.alpha(M.AlphaAction.Remove);
        img.quality = Number(o.quality);
        return [img.write(M.MagickFormat.Jpeg, copy), img.width, img.height] as const;
      });
      let pw = (w / dpi) * 72;
      let ph = (h / dpi) * 72;
      let box: [number, number, number, number] = [0, 0, pw, ph];
      if (o.page_size !== "image") {
        let [mw, mh] = PAGE_MM[String(o.page_size)];
        const landscape = o.orientation === "landscape" || (o.orientation === "auto" && w > h);
        if (landscape) [mw, mh] = [mh, mw];
        pw = (mw / 25.4) * 72;
        ph = (mh / 25.4) * 72;
        const m = (Number(o.margin_mm) / 25.4) * 72;
        const scale = Math.min((pw - 2 * m) / w, (ph - 2 * m) / h);
        const iw = w * scale;
        const ih = h * scale;
        box = [(pw - iw) / 2, (ph - ih) / 2, iw, ih];
      }
      const image = doc.addImage(new mupdf.Image(jpeg));
      const resources = doc.addObject({ XObject: { I0: image } });
      const contents = `q ${box[2]} 0 0 ${box[3]} ${box[0]} ${box[1]} cm /I0 Do Q`;
      const page = doc.addPage([0, 0, pw, ph], 0, resources, contents);
      doc.insertPage(-1, page);
      if (!o.combine) finish(`${baseName(f.name)}.pdf`);
    }
    if (o.combine) {
      const first = baseName(files[0].name);
      finish(files.length === 1 ? `${first}.pdf` : `${first} + ${files.length - 1} more.pdf`);
    }
    ctx.progress(1, "Done");
    return out;
  },
};
