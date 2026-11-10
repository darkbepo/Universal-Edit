// Line, rectangle and ellipse: previewed while dragging, drawn on release (port of tools/shapes.py).
// The browser's 2D canvas rasterises (anti-aliased) through the injected Rasterizer.
import { opt } from "../../core/options";
import type { EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData } from "../layer";
import type { DrawItem, Rasterizer } from "../raster";
import { clipRect, colorOf, overPixel, restrict, Tool, type Preview, type ToolContext } from "./base";

const lineOptions = [
  opt.slider("width", "Width", 4, 1, 200, { unit: "px" }),
  opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
];
const shapeOptions = [
  opt.slider("width", "Outline width", 4, 1, 200, { unit: "px" }),
  opt.choice("mode", "Style", "outline", [["outline", "Outline"], ["filled", "Filled"], ["both", "Outline + fill"]]),
  opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
];

abstract class ShapeTool extends Tool {
  protected abstract readonly kind: "line" | "rect" | "ellipse";
  protected start: [number, number] | null = null;
  protected end: [number, number] = [0, 0];
  private canvas: [number, number] = [0, 0];
  private overlay: Preview | null = null;

  constructor(protected raster: Rasterizer) {
    super();
  }

  protected points(ctx: ToolContext): [number, number, number, number] {
    const [x0, y0] = this.start as [number, number];
    let [x1, y1] = this.end;
    if (ctx.shift) {
      const side = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
      x1 = x0 + Math.sign(x1 - x0 || 1) * side;
      y1 = y0 + Math.sign(y1 - y0 || 1) * side;
    }
    return [x0, y0, x1, y1];
  }

  private render(ctx: ToolContext): Preview | null {
    const pts = this.points(ctx);
    const pad = this.pad(ctx);
    const b = Rect.fromPoints(...pts);
    const [cw, ch] = this.canvas;
    const box = new Rect(b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad).intersect(new Rect(-pad, -pad, cw + pad, ch + pad));
    if (box.empty) return null;
    const data = this.draw(box, pts, ctx);
    const op = Number(ctx.options.opacity) / 100;
    if (op < 1) for (let i = 3; i < data.length; i += 4) data[i] *= op;
    return { data, width: box.width, height: box.height, x: box.x0, y: box.y0 };
  }

  /** How far the drawing reaches beyond its points (canvas px). */
  protected pad(ctx: ToolContext): number {
    return Number(ctx.options.width) + 2;
  }

  protected draw(box: Rect, pts: [number, number, number, number], ctx: ToolContext): Uint8ClampedArray {
    const mode = this.kind === "line" ? "line" : (ctx.options.mode as "outline" | "filled" | "both");
    return this.raster.shape(this.kind, box, pts, {
      width: Number(ctx.options.width),
      mode,
      stroke: this.kind === "line" ? colorOf(ctx) : ctx.primary,
      fill: ctx.secondary,
    });
  }

  private overlayRect(): Rect | null {
    const o = this.overlay;
    return o ? new Rect(o.x, o.y, o.x + o.width, o.y + o.height) : null;
  }

  override press(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.canvas = [doc.width, doc.height];
    this.start = [x, y];
    this.end = [x, y];
    this.overlay = null;
    return null;
  }

  override move(_doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    if (!this.start) return null;
    const old = this.overlayRect();
    this.end = [x, y];
    this.overlay = this.render(ctx);
    const now = this.overlayRect();
    return now ? now.union(old) : old;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    if (!this.start) return null;
    this.end = [x, y];
    const moved = Math.hypot(x - this.start[0], y - this.start[1]) >= 1;
    const o = moved ? this.render(ctx) : null;
    this.start = null;
    this.overlay = null;
    if (!o) return null;
    const layer = doc.layers[doc.active];
    const target = new Rect(o.x, o.y, o.x + o.width, o.y + o.height).offset(-layer.x, -layer.y);
    const region = target.intersect(clipRect(doc, layer));
    if (region.empty) return null;
    const before = cropData(layer.data, layer.width, region);
    for (let y = region.y0; y < region.y1; y++)
      for (let xx = region.x0; xx < region.x1; xx++) {
        overPixel(layer.data, (y * layer.width + xx) * 4, o.data, ((y - target.y0) * o.width + (xx - target.x0)) * 4);
      }
    restrict(doc, layer, region, before); // shaped / soft selections
    return PatchCommand.fromRegion(this.label, doc, doc.active, region, before);
  }

  override cancel(_doc?: EditorDoc): Rect | null {
    const old = this.overlayRect();
    this.start = null;
    this.overlay = null;
    return old;
  }

  override preview(_doc?: EditorDoc): Preview | null {
    return this.overlay;
  }
}

export class LineTool extends ShapeTool {
  readonly id: string = "line";
  readonly label: string = "Line";
  readonly shortcut = "L";
  protected readonly kind = "line" as const;
  override readonly options = lineOptions;

  protected override points(ctx: ToolContext): [number, number, number, number] {
    const [x0, y0] = this.start as [number, number];
    let [x1, y1] = this.end;
    if (ctx.shift) {
      const len = Math.hypot(x1 - x0, y1 - y0);
      const angle = Math.round(Math.atan2(y1 - y0, x1 - x0) / (Math.PI / 4)) * (Math.PI / 4);
      x1 = x0 + len * Math.cos(angle);
      y1 = y0 + len * Math.sin(angle);
    }
    return [x0, y0, x1, y1];
  }
}

export class RectTool extends ShapeTool {
  readonly id: string = "rect";
  readonly label: string = "Rectangle";
  readonly shortcut = "U";
  protected readonly kind: "rect" | "ellipse" = "rect";
  override readonly options = shapeOptions;
}

export class EllipseTool extends RectTool {
  override readonly id = "ellipse";
  override readonly label = "Ellipse";
  protected override readonly kind = "ellipse" as const;
}

/** (tip triangle, where the line stops) for a head at (x1, y1) pointing away from (x0, y0).
 *  Same as arrow_head in tools/shapes.py. */
export function arrowHead(x0: number, y0: number, x1: number, y1: number, width: number): [number[][], [number, number]] {
  const half = Math.max(2.2 * width, 7);
  const d = Math.hypot(x1 - x0, y1 - y0) || 1;
  const ux = (x1 - x0) / d;
  const uy = (y1 - y0) / d;
  const length = Math.min(Math.max(4 * width, 14), d * 0.6);
  const bx = x1 - ux * length;
  const by = y1 - uy * length;
  return [[[x1, y1], [bx - uy * half, by + ux * half], [bx + uy * half, by - ux * half]], [bx, by]];
}

/** A line with an arrowhead at the end (or both ends) — for pointing things out. */
export class ArrowTool extends LineTool {
  override readonly id = "arrow";
  override readonly label = "Arrow";
  override readonly options = [
    opt.slider("width", "Width", 6, 1, 200, { unit: "px" }),
    opt.choice("heads", "Arrowheads", "end", [["end", "At the end"], ["both", "Both ends"]]),
    opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
  ];

  protected override pad(ctx: ToolContext): number {
    const w = Number(ctx.options.width);
    return Math.ceil(Math.max(2.2 * w, 7)) + w + 2;
  }

  protected override draw(box: Rect, [x0, y0, x1, y1]: [number, number, number, number], ctx: ToolContext): Uint8ClampedArray {
    const w = Math.max(1, Number(ctx.options.width));
    const color = colorOf(ctx);
    const [tip, [ex, ey]] = arrowHead(x0, y0, x1, y1, w);
    const items: DrawItem[] = [{ poly: tip, fill: color }];
    let [sx, sy] = [x0, y0];
    if (ctx.options.heads === "both") {
      const [tip0, start] = arrowHead(x1, y1, x0, y0, w);
      items.push({ poly: tip0, fill: color });
      [sx, sy] = start;
    }
    items.push({ line: [[sx, sy], [ex, ey]], stroke: color, width: w });
    return this.raster.draw(box, items);
  }
}
