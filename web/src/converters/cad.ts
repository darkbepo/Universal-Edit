// CAD drawings on the web: DXF → SVG / PNG / JPG with the `dxf` package (MIT) and resvg.
// Same target ids as the desktop app (converters/cad.py). DWG (LibreDWG), → PDF and DXF version
// changes (ezdxf) are desktop-only.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type OutputFile } from "../core/types";
import { baseName } from "./util";

const RENDER: Record<string, string> = { "cad:svg": "SVG", "cad:png": "PNG", "cad:jpg": "JPG" };
const RENDER_TARGETS = Object.keys(RENDER);
const MIME: Record<string, string> = { svg: "image/svg+xml", png: "image/png", jpg: "image/jpeg" };

const options = [
  opt.choice("colours", "Colours", "black_on_white", [["black_on_white", "Black lines on white (for printing)"], ["colour_light", "Layer colours on white"], ["colour_dark", "Layer colours on black (like the CAD screen)"]], { targets: RENDER_TARGETS }),
  opt.float("lineweight", "Line thickness", 1, { min: 0.1, max: 10, decimals: 1, unit: "×", targets: RENDER_TARGETS }),
  opt.int("width_px", "Image width", 2000, { min: 100, max: 20000, unit: "px", targets: ["cad:png", "cad:jpg"] }),
  opt.slider("margin_mm", "Margin", 10, 0, 50, { unit: "mm", targets: RENDER_TARGETS, help: "Space around the drawing (as a share of its size: 10 ≈ 3 %)." }),
];

const isLight = (r: number, g: number, b: number) => 0.299 * r + 0.587 * g + 0.114 * b > 200;

/** dxf's SVG → a sized, styled SVG (colour mode, line thickness, margin, background). */
export function styleSvg(raw: string, o: Values, outWidthPx = 1000): string {
  const vb = /viewBox="([-\d.e]+) ([-\d.e]+) ([-\d.e]+) ([-\d.e]+)"/.exec(raw);
  if (!vb) throw new ConversionError("This DXF drawing is empty.");
  let [x, y, w, h] = vb.slice(1).map(Number);
  if (!(w > 0 && h > 0)) throw new ConversionError("This DXF drawing is empty.");
  const pad = (Math.max(w, h) * Number(o.margin_mm)) / 300;
  [x, y, w, h] = [x - pad, y - pad, w + 2 * pad, h + 2 * pad];
  const mode = String(o.colours);
  const dark = mode === "colour_dark";
  let svg = raw.replace(/stroke="(rgb\((\d+), (\d+), (\d+)\)|#[0-9a-fA-F]{6})"/g, (m, _c, r, g, b) => {
    const hex = m.slice(9, 15); // stroke="#rrggbb"
    const rgb = r ? [Number(r), Number(g), Number(b)] : [0, 2, 4].map((k) => parseInt(hex.slice(k, k + 2), 16));
    if (mode === "black_on_white") return 'stroke="#000000"';
    if (!dark && isLight(rgb[0], rgb[1], rgb[2])) return 'stroke="#000000"'; // white lines on white paper
    if (dark && rgb.every((v) => v < 40)) return 'stroke="#ffffff"'; // black lines on the dark screen
    return m;
  });
  // ~1.5 output pixels per line (× the thickness setting), whatever the drawing's units
  const stroke = ((w / outWidthPx) * 1.5 * Number(o.lineweight)).toPrecision(4);
  svg = svg.replace(/stroke-width="[^"]*"/g, `stroke-width="${stroke}"`);
  svg = svg.replace(/viewBox="[^"]*"/, `viewBox="${x} ${y} ${w} ${h}"`).replace('width="100%" height="100%"', `width="${w}" height="${h}"`);
  const bg = `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${dark ? "#000000" : "#ffffff"}"/>`;
  return svg.replace(/(<svg[^>]*>)/, `$1${bg}`);
}

export const cadConverter: Converter = {
  id: "cad",
  family: "cad",
  options,
  targets(files) {
    if (files.some((f) => f.ext !== "dxf")) return []; // registry explains: DWG is desktop only
    return RENDER_TARGETS.map((id) => ({ id, label: RENDER[id], ext: id.split(":")[1], group: id === "cad:svg" ? "Vector" : "Image", converterId: "cad" }));
  },
  async run(files, target, o, ctx) {
    const { Helper } = await import("dxf");
    const out: OutputFile[] = [];
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Drawing ${f.name}`);
      let raw: string;
      try {
        raw = new Helper(await f.file.text()).toSVG();
      } catch (e) {
        throw new ConversionError(`Couldn't read “${f.name}”. Is it a valid DXF drawing?`, String(e));
      }
      const svg = styleSvg(raw, o, target.ext === "svg" ? 1000 : Number(o.width_px));
      const stem = baseName(f.name);
      if (target.ext === "svg") {
        out.push({ name: `${stem}.svg`, data: new TextEncoder().encode(svg), type: MIME.svg });
        continue;
      }
      const { renderSvg } = await import("../engines/vector");
      let data = await renderSvg(new TextEncoder().encode(svg), Number(o.width_px), f.name);
      if (target.ext === "jpg") {
        const { getMagick, copy } = await import("../engines/magick");
        const M = await getMagick((m) => ctx.progress(0, m));
        data = M.ImageMagick.read(data, (img) => {
          img.quality = 92;
          return img.write("JPEG" as import("@imagemagick/magick-wasm").MagickFormat, copy);
        });
      }
      out.push({ name: `${stem}.${target.ext}`, data, type: MIME[target.ext] });
    }
    ctx.progress(1, "Done");
    return out;
  },
};
