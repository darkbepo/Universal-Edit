// Carries a compose.ts Frame (and textlayout.ts boxes) out on any Canvas 2D context: the page's
// preview canvas, or an OffscreenCanvas in the export worker. The order follows the desktop's
// FFmpeg graph (src/umc/videoedit/render.py `_geometry` → `_fit`, then fades and transitions):
// crop, rotate / flip, grade (the picture only, not the letterbox bars), fit, fade.
import { gradeRgb, layerRect, type Frame, type Grade, type Layer, type Placed } from "../../videoedit/compose";
import { FALLBACK_FONT, type TextBox } from "../../videoedit/textlayout";

export type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
/** A decoded source frame / bitmap. */
export interface Picture {
  image: CanvasImageSource;
  width: number;
  height: number;
}
/** Scratch canvases: OffscreenCanvas on the page and in the worker, a fake in vitest. */
export interface Surfaces {
  make(w: number, h: number): { canvas: CanvasImageSource; ctx: Ctx2D };
}

/** DejaVu Sans ascent + descent per em: libass's line step. */
export const LINE_HEIGHT = 1.164;

/** In place: every pixel through `gradeRgb` (FFmpeg's eq on yuv420p); alpha untouched. */
export function gradePixels(data: Uint8ClampedArray, g: Grade): void {
  // neighbouring pixels are often equal: remember the last answer
  let key = -1;
  let out: [number, number, number] = [0, 0, 0];
  for (let i = 0; i < data.length; i += 4) {
    const k = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
    if (k !== key) {
      key = k;
      out = gradeRgb([data[i], data[i + 1], data[i + 2]], g);
    }
    data[i] = out[0];
    data[i + 1] = out[1];
    data[i + 2] = out[2];
  }
}

/** In place: every RGB channel − lift, clamped at 0 (fadeblack's black below RGB black). */
export function liftPixels(data: Uint8ClampedArray, lift: number): void {
  for (let i = 0; i < data.length; i += 4) {
    data[i] -= lift;
    data[i + 1] -= lift;
    data[i + 2] -= lift;
  }
}

export function textAlign(column: 0 | 1 | 2): CanvasTextAlign {
  return column === 0 ? "left" : column === 1 ? "center" : "right";
}

/** Top of the first line: the anchor y is the top (row 0), middle (1) or bottom (2) of the block. */
export function lineTop(row: 0 | 1 | 2, y: number, lines: number, lineH: number): number {
  const total = lines * lineH;
  return row === 0 ? y : row === 1 ? y - total / 2 : y - total;
}

/** The layer's picture cropped, turned and flipped, drawn at the size it will have in the frame
 *  (so the grade works on as few pixels as needed); plus the scale it was drawn at. */
function turnedPicture(l: Layer, p: Picture, f: Frame, s: Surfaces) {
  const r = layerRect(l, p.width, p.height, f.width, f.height);
  const cx = p.width * l.crop.left;
  const cy = p.height * l.crop.top;
  const cw = p.width * (1 - l.crop.left - l.crop.right);
  const ch = p.height * (1 - l.crop.top - l.crop.bottom);
  const turned = l.rotate === 90 || l.rotate === 270;
  const tw = turned ? ch : cw;
  const th = turned ? cw : ch;
  const w = Math.max(1, Math.round(tw * (r.dw / r.sw)));
  const h = Math.max(1, Math.round(th * (r.dh / r.sh)));
  const out = s.make(w, h);
  const c = out.ctx;
  c.translate(w / 2, h / 2);
  if (l.flipH || l.flipV) c.scale(l.flipH ? -1 : 1, l.flipV ? -1 : 1); // after the rotation
  if (l.rotate) c.rotate((l.rotate * Math.PI) / 180); // clockwise (y points down)
  const uw = turned ? h : w;
  const uh = turned ? w : h;
  c.drawImage(p.image, cx, cy, cw, ch, -uw / 2, -uh / 2, uw, uh);
  if (l.grade) {
    const img = c.getImageData(0, 0, w, h);
    gradePixels(img.data, l.grade);
    c.putImageData(img, 0, 0);
  }
  return { canvas: out.canvas, r, kx: w / tw, ky: h / th };
}

/** One layer as a whole frame: background, picture, fade. */
function layerSurface(pl: Placed, f: Frame, pictureOf: (l: Layer) => Picture | null, s: Surfaces) {
  const l = pl.layer;
  const out = s.make(f.width, f.height);
  const c = out.ctx;
  c.fillStyle = f.background;
  c.fillRect(0, 0, f.width, f.height);
  const p = pictureOf(l);
  if (p && p.width > 0 && p.height > 0) {
    const t = turnedPicture(l, p, f, s);
    const { r } = t;
    c.drawImage(t.canvas, r.sx * t.kx, r.sy * t.ky, r.sw * t.kx, r.sh * t.ky, r.dx, r.dy, r.dw, r.dh);
    if (l.fade > 0) {
      c.globalAlpha = l.fade;
      c.fillRect(0, 0, f.width, f.height);
      c.globalAlpha = 1;
    }
  }
  return out.canvas;
}

export function drawFrame(ctx: Ctx2D, f: Frame, pictureOf: (l: Layer) => Picture | null, s: Surfaces): void {
  ctx.globalAlpha = 1;
  ctx.globalCompositeOperation = "source-over";
  ctx.fillStyle = f.mode === "add" ? "#000" : f.background;
  ctx.fillRect(0, 0, f.width, f.height);
  for (const pl of f.layers) {
    const canvas = layerSurface(pl, f, pictureOf, s);
    ctx.save();
    ctx.globalAlpha = pl.weight;
    ctx.globalCompositeOperation = f.mode === "add" ? "lighter" : "source-over";
    if (pl.clipX) {
      ctx.beginPath();
      ctx.rect(pl.clipX[0], 0, pl.clipX[1] - pl.clipX[0], f.height);
      ctx.clip();
    }
    if (pl.offsetX) ctx.translate(pl.offsetX, 0);
    ctx.drawImage(canvas, 0, 0);
    ctx.restore();
  }
  if (f.lift > 0) {
    const img = ctx.getImageData(0, 0, f.width, f.height);
    liftPixels(img.data, f.lift);
    ctx.putImageData(img, 0, 0);
  }
}

/** Text boxes on top of a drawn frame. `textAt` already gives font size and x / y at the drawn
 *  size; `scale` = drawn height / output height applies only to the outline width and the box
 *  padding (those are in output pixels). */
export function drawText(ctx: Ctx2D, boxes: TextBox[], scale: number): void {
  for (const b of boxes) {
    const px = b.fontPx;
    const step = Math.round(px * LINE_HEIGHT);
    const top = lineTop(b.row, b.y, b.lines.length, step);
    const x = b.x;
    ctx.save();
    ctx.globalCompositeOperation = "source-over";
    ctx.font = `${b.italic ? "italic " : ""}${b.bold ? "bold " : ""}${px}px "${b.family}", "${FALLBACK_FONT}"`;
    ctx.textAlign = textAlign(b.column);
    ctx.textBaseline = "top";
    if (b.box) {
      const pad = b.box.padding * scale;
      ctx.globalAlpha = b.box.opacity * b.alpha;
      ctx.fillStyle = b.box.colour;
      b.lines.forEach((line, i) => {
        const w = ctx.measureText(line).width;
        const left = b.column === 0 ? x : b.column === 1 ? x - w / 2 : x - w;
        ctx.fillRect(left - pad, top + i * step - pad, w + 2 * pad, step + 2 * pad);
      });
    }
    ctx.globalAlpha = b.alpha;
    if (!b.box && b.outlineWidth > 0) {
      ctx.strokeStyle = b.outlineColour;
      ctx.lineWidth = 2 * b.outlineWidth * scale; // the stroke is centred on the outline
      ctx.lineJoin = "round";
      b.lines.forEach((line, i) => ctx.strokeText(line, x, top + i * step));
    }
    ctx.fillStyle = b.colour;
    b.lines.forEach((line, i) => ctx.fillText(line, x, top + i * step));
    ctx.restore();
  }
}
