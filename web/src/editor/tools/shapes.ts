// Line, arrow, rectangle and ellipse (port of tools/shapes.py): previewed while dragging; each
// becomes its own layer that remembers its geometry and style (ShapeSpec on Layer.shape), so its
// corners / ends can be dragged again until the layer is rasterised, merged or transformed.
// The browser's 2D canvas rasterises (anti-aliased) through the injected Rasterizer.
import { opt } from "../../core/options";
import type { EditorDoc } from "../document";
import { Rect } from "../geometry";
import type { Command } from "../history";
import { layerBounds, makeLayer, withProps, type Layer, type RGBA } from "../layer";
import * as ops from "../ops";
import type { DrawItem, Rasterizer } from "../raster";
import { colorOf, Tool, type Preview, type ToolContext } from "./base";

/** Screen pixels: how close to a corner / end counts as grabbing it. */
export const HANDLE = 8;

/** Everything needed to draw a shape layer again (same fields as ShapeSpec in shapes.py).
 *  `points` (x0, y0, x1, y1) are relative to the layer's x / y while stored on a layer. */
export interface ShapeSpec {
  kind: "line" | "arrow" | "rect" | "ellipse";
  points: [number, number, number, number];
  color: RGBA;
  fill: RGBA;
  width: number;
  mode: "outline" | "filled" | "both";
  heads: "end" | "both";
  opacity: number;
}

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
    return this.drawPoints(this.points(ctx), ctx, this.canvas);
  }

  /** The shape through `pts` (canvas coordinates). With `canvas` the preview is limited to it
   *  (plus the stroke); a layer keeps the whole shape. */
  drawPoints(pts: [number, number, number, number], ctx: ToolContext, canvas?: [number, number]): Preview | null {
    const pad = this.pad(ctx);
    const b = Rect.fromPoints(...pts);
    let box = new Rect(b.x0 - pad, b.y0 - pad, b.x1 + pad, b.y1 + pad);
    if (canvas) box = box.intersect(new Rect(-pad, -pad, canvas[0] + pad, canvas[1] + pad));
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

  /** A tool needs something first (the canvas shows it in the status line). */
  notice: string | null = null;
  /** The shape layer being reshaped: which handle, the layer as it was, and where it sits. */
  private grab: { hit: number; old: Layer; index: number } | null = null;

  override press(doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Rect | null {
    this.canvas = [doc.width, doc.height];
    this.overlay = null;
    this.grab = null;
    const hit = this.handleAt(doc, x, y, ctx?.zoom ?? 1);
    if (hit !== null) {
      const layer = doc.layers[doc.active];
      if (shapePaintedOver(this.raster, layer)) this.notice = "This shape was painted on, so it's pixels now (its handles are off).";
      else {
        this.grab = { hit, old: layer, index: doc.active }; // reshape it instead of drawing a new one
        return null;
      }
    }
    this.start = [x, y];
    this.end = [x, y];
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    if (this.grab) return this.reshape(doc, x, y);
    if (!this.start) return null;
    const old = this.overlayRect();
    this.end = [x, y];
    this.overlay = this.render(ctx);
    const now = this.overlayRect();
    return now ? now.union(old) : old;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    if (this.grab) {
      this.reshape(doc, x, y);
      const { old, index } = this.grab;
      this.grab = null;
      const fresh = doc.layers[index];
      doc.layers = [...doc.layers];
      doc.layers[index] = old; // the live preview goes; the undoable change comes
      return fresh === old ? null : ops.replaceLayer(doc, index, "Edit shape", fresh);
    }
    if (!this.start) return null;
    this.end = [x, y];
    const moved = Math.hypot(x - this.start[0], y - this.start[1]) >= 1;
    const pts = this.points(ctx);
    this.start = null;
    this.overlay = null;
    if (!moved) return null;
    const layer = shapeLayer(this.raster, ops.uniqueName(doc, this.label), specFrom(this.shapeKind(), pts, ctx));
    return layer ? ops.addLayers(doc, [layer], this.label) : null;
  }

  override cancel(doc?: EditorDoc): Rect | null {
    const old = this.overlayRect();
    if (this.grab && doc) {
      const { old: layer, index } = this.grab;
      this.grab = null;
      const dirty = layerBounds(doc.layers[index]).union(layerBounds(layer));
      doc.layers = [...doc.layers];
      doc.layers[index] = layer;
      return dirty;
    }
    this.start = null;
    this.overlay = null;
    return old;
  }

  /** "line" | "arrow" | "rect" | "ellipse" — what ShapeSpec.kind this tool makes. */
  protected shapeKind(): ShapeSpec["kind"] {
    return this.id as ShapeSpec["kind"];
  }

  /** The corners / ends of the active layer's shape (canvas coordinates), drawn by the canvas
   *  while this tool is chosen; dragging one reshapes the layer. */
  handles(doc: EditorDoc): [number, number][] {
    const l = doc.layers[doc.active];
    return l && l.shape && l.visible ? handlePoints(l) : [];
  }

  private handleAt(doc: EditorDoc, x: number, y: number, zoom: number): number | null {
    const r = HANDLE / Math.max(zoom, 1e-6);
    const hs = this.handles(doc);
    for (let i = 0; i < hs.length; i++) if (Math.abs(hs[i][0] - x) <= r && Math.abs(hs[i][1] - y) <= r) return i;
    return null;
  }

  private reshape(doc: EditorDoc, x: number, y: number): Rect | null {
    const { hit, old, index } = this.grab as { hit: number; old: Layer; index: number };
    const spec = old.shape as ShapeSpec;
    const pts = canvasPoints(old);
    if (spec.kind === "line" || spec.kind === "arrow") {
      pts[2 * hit] = x;
      pts[2 * hit + 1] = y;
    } else {
      // corners 0 1 2 3 = top-left, top-right, bottom-right, bottom-left of (x0 y0 x1 y1)
      pts[hit === 0 || hit === 3 ? 0 : 2] = x;
      pts[hit === 0 || hit === 1 ? 1 : 3] = y;
    }
    const fresh = shapeLayer(this.raster, old.name, { ...spec, points: pts }, old);
    if (!fresh) return null;
    const cur = doc.layers[index];
    doc.layers = [...doc.layers];
    doc.layers[index] = fresh;
    return layerBounds(cur).union(layerBounds(fresh));
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

// ------------------------------------------------------------------ shape layers

/** The shape a tool just drew (canvas points) with its settings and colours. */
export function specFrom(kind: ShapeSpec["kind"], pts: [number, number, number, number], ctx: ToolContext): ShapeSpec {
  const o = ctx.options;
  const line = kind === "line" || kind === "arrow";
  return {
    kind,
    points: [...pts] as [number, number, number, number],
    color: [...(line ? colorOf(ctx) : ctx.primary)] as RGBA,
    fill: [...ctx.secondary] as RGBA,
    width: Number(o.width),
    mode: ((o.mode as string) ?? "outline") as ShapeSpec["mode"],
    heads: ((o.heads as string) ?? "end") as ShapeSpec["heads"],
    opacity: Number(o.opacity ?? 100),
  };
}

function toolFor(raster: Rasterizer, kind: ShapeSpec["kind"]): ShapeTool {
  return kind === "line" ? new LineTool(raster) : kind === "arrow" ? new ArrowTool(raster) : kind === "rect" ? new RectTool(raster) : new EllipseTool(raster);
}

/** (pixels, x, y) of a shape whose points are in canvas coordinates. */
export function renderShape(raster: Rasterizer, spec: ShapeSpec): Preview | null {
  const ctx: ToolContext = {
    primary: spec.color,
    secondary: spec.fill,
    options: { width: spec.width, mode: spec.mode, heads: spec.heads, opacity: spec.opacity },
    shift: false,
    button: "left",
    font: "",
  };
  return toolFor(raster, spec.kind).drawPoints(spec.points, ctx);
}

/** A layer holding the shape (`spec` in canvas coordinates). Redrawing `old` keeps what the user
 *  set on it: visibility, opacity and its mask when the size still fits. */
export function shapeLayer(raster: Rasterizer, name: string, spec: ShapeSpec, old: Layer | null = null): Layer | null {
  const d = renderShape(raster, spec);
  if (!d) return null;
  const p = spec.points;
  const local: ShapeSpec = { ...spec, points: [p[0] - d.x, p[1] - d.y, p[2] - d.x, p[3] - d.y] };
  const layer = makeLayer(name, d.width, d.height, new Uint8ClampedArray(d.data), { x: d.x, y: d.y, shape: local });
  if (!old) return layer;
  const keepMask = !!old.mask && old.width === d.width && old.height === d.height;
  return withProps(layer, { opacity: old.opacity, visible: old.visible, mask: keepMask ? old.mask : null });
}

export function canvasPoints(layer: Layer): [number, number, number, number] {
  const p = (layer.shape as ShapeSpec).points;
  return [p[0] + layer.x, p[1] + layer.y, p[2] + layer.x, p[3] + layer.y];
}

export function handlePoints(layer: Layer): [number, number][] {
  const [x0, y0, x1, y1] = canvasPoints(layer);
  const kind = (layer.shape as ShapeSpec).kind;
  return kind === "line" || kind === "arrow" ? [[x0, y0], [x1, y1]] : [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** True when a shape layer was changed with other tools since it was drawn (reshaping draws it
 *  again, so those changes would go). Same as shape_painted_over in shapes.py. */
export function shapePaintedOver(raster: Rasterizer, layer: Layer): boolean {
  if (!layer.shape) return false;
  const d = renderShape(raster, { ...(layer.shape as ShapeSpec), points: canvasPoints(layer) });
  if (!d || d.width !== layer.width || d.height !== layer.height) return true;
  for (let i = 0; i < d.data.length; i++) if (d.data[i] !== layer.data[i]) return true;
  return false;
}
