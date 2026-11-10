// The editor model's only links to the browser's 2D canvas, behind two small interfaces so the
// model runs in node tests (which pass fakes) and in the page (which passes the canvas versions).
import type { Rect } from "./geometry";
import type { RGBA } from "./layer";

export interface Decoded {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

export interface PngCodec {
  encode(data: Uint8ClampedArray, width: number, height: number): Promise<Uint8Array>;
  decode(bytes: Uint8Array): Promise<Decoded>;
}

export interface ShapeStyle {
  width: number;
  mode: "outline" | "filled" | "both" | "line";
  stroke: RGBA;
  fill: RGBA;
}

export interface FontSpec {
  family: string;
  size: number;
  bold: boolean;
  italic: boolean;
  align?: "left" | "center" | "right";
  /** Outline width in px (drawn behind the letters) and its colour. */
  outline?: number;
  outlineColor?: RGBA;
}

export interface Rasterizer {
  /** RGBA of `box` (canvas coordinates) with the shape drawn anti-aliased; `pts` = x0, y0, x1, y1. */
  shape(kind: "line" | "rect" | "ellipse", box: Rect, pts: [number, number, number, number], style: ShapeStyle): Uint8ClampedArray;
  /** Text tightly cropped (2 px padding). */
  text(text: string, font: FontSpec, color: RGBA): Decoded;
}

const css = (c: RGBA) => `rgba(${c[0]},${c[1]},${c[2]},${c[3] / 255})`;

function context(w: number, h: number): OffscreenCanvasRenderingContext2D {
  const ctx = new OffscreenCanvas(Math.max(1, w), Math.max(1, h)).getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("2D canvas not available");
  return ctx;
}

export const canvasRasterizer: Rasterizer = {
  shape(kind, box, [x0, y0, x1, y1], style) {
    const ctx = context(box.width, box.height);
    ctx.translate(-box.x0, -box.y0);
    ctx.lineCap = "round";
    ctx.lineJoin = kind === "rect" ? "miter" : "round";
    ctx.lineWidth = style.width;
    ctx.strokeStyle = css(style.stroke);
    ctx.fillStyle = css(style.fill);
    if (kind === "line") {
      ctx.beginPath();
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
      ctx.stroke();
    } else {
      const left = Math.min(x0, x1);
      const top = Math.min(y0, y1);
      const w = Math.abs(x1 - x0);
      const h = Math.abs(y1 - y0);
      const filled = style.mode === "filled" || style.mode === "both";
      const outlined = style.mode === "outline" || style.mode === "both";
      ctx.beginPath();
      if (kind === "rect") {
        // outline drawn inside the box, like the desktop app (Pillow draws inwards)
        const inset = outlined ? style.width / 2 : 0;
        ctx.rect(left + inset, top + inset, Math.max(0, w - 2 * inset), Math.max(0, h - 2 * inset));
      } else {
        const inset = outlined ? style.width / 2 : 0;
        ctx.ellipse(left + w / 2, top + h / 2, Math.max(0, w / 2 - inset), Math.max(0, h / 2 - inset), 0, 0, Math.PI * 2);
      }
      if (filled) ctx.fill();
      if (outlined) ctx.stroke();
    }
    return ctx.getImageData(0, 0, box.width, box.height).data; // read back in canvas pixels (not translated)
  },

  text(text, font, color) {
    const fontCss = `${font.italic ? "italic " : ""}${font.bold ? "bold " : ""}${font.size}px "${font.family}", sans-serif`;
    const probe = context(1, 1);
    probe.font = fontCss;
    const lines = text.split("\n");
    const lineHeight = Math.ceil(font.size * 1.2);
    let width = 1;
    let ascent = font.size;
    let descent = 0;
    for (const line of lines) {
      const m = probe.measureText(line || " ");
      width = Math.max(width, Math.ceil(m.actualBoundingBoxRight + Math.max(0, m.actualBoundingBoxLeft)));
      ascent = Math.max(ascent, Math.ceil(m.actualBoundingBoxAscent));
      descent = Math.max(descent, Math.ceil(m.actualBoundingBoxDescent));
    }
    const stroke = Math.max(0, font.outline ?? 0);
    const pad = 2 + stroke;
    const w = width + 2 * pad;
    const h = ascent + descent + (lines.length - 1) * lineHeight + 2 * pad;
    const ctx = context(w, h);
    ctx.font = fontCss;
    ctx.textBaseline = "alphabetic";
    const xOf = (line: string) => {
      const lw = probe.measureText(line || " ").width;
      return font.align === "center" ? pad + (width - lw) / 2 : font.align === "right" ? pad + width - lw : pad;
    };
    if (stroke > 0) {
      ctx.lineJoin = "round";
      ctx.lineWidth = 2 * stroke;
      ctx.strokeStyle = css(font.outlineColor ?? [255, 255, 255, 255]);
      lines.forEach((line, i) => ctx.strokeText(line, xOf(line), pad + ascent + i * lineHeight));
    }
    ctx.fillStyle = css(color);
    lines.forEach((line, i) => ctx.fillText(line, xOf(line), pad + ascent + i * lineHeight));
    return { width: w, height: h, data: ctx.getImageData(0, 0, w, h).data };
  },
};

export const canvasPng: PngCodec = {
  async encode(data, width, height) {
    const ctx = context(width, height);
    ctx.putImageData(new ImageData(new Uint8ClampedArray(data), width, height), 0, 0);
    const blob = await (ctx.canvas as OffscreenCanvas).convertToBlob({ type: "image/png" });
    return new Uint8Array(await blob.arrayBuffer());
  },
  async decode(bytes) {
    return decodeBlob(new Blob([bytes as BlobPart]));
  },
};

/** Browser decoding of anything createImageBitmap understands (PNG, JPG, WEBP, GIF, AVIF, BMP, ICO). */
export async function decodeBlob(blob: Blob): Promise<Decoded> {
  const bitmap = await createImageBitmap(blob, { premultiplyAlpha: "none", colorSpaceConversion: "none" });
  try {
    const ctx = context(bitmap.width, bitmap.height);
    ctx.drawImage(bitmap, 0, 0);
    return { width: bitmap.width, height: bitmap.height, data: ctx.getImageData(0, 0, bitmap.width, bitmap.height).data };
  } finally {
    bitmap.close();
  }
}
