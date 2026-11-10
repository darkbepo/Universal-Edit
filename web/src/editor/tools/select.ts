// Selection tools (rectangle, ellipse, lasso, polygon lasso, magic wand), crop rectangle and
// clearing the selected area — port of tools/select.py. The maths is in ../selection.ts.
import { docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { opt } from "../../core/options";
import { PatchCommand, type Command } from "../history";
import { combine, deselect, ellipseShape, modeFor, polygonShape, rectShape, selectAll, wandShape, type Mode } from "../selection";
import { cropData } from "../layer";
import { clipRect, restrict, Tool, type ToolContext } from "./base";

abstract class DragRectTool extends Tool {
  protected start: [number, number] | null = null;

  protected rect(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    const [sx, sy] = this.start as [number, number];
    if (Math.hypot(x - sx, y - sy) < 1) return null;
    // snap to the nearest pixel edge (the pointer is usually between two pixels)
    const r = Rect.fromPoints(Math.round(sx), Math.round(sy), Math.round(x), Math.round(y)).intersect(docBounds(doc));
    return r.empty ? null : r;
  }
}

export type Outline = ["rect" | "ellipse", Rect] | ["poly", [number, number][]];

/** Drag a shape; Shift adds to the selection, Alt subtracts, both intersect. While dragging,
 *  `outline` is what the canvas draws; the selection only changes on release (one undo step). */
abstract class ShapeSelectTool extends DragRectTool {
  protected abstract readonly shape: "rect" | "ellipse";
  outline: Outline | null = null;
  private mode: Mode = "replace";

  override press(_doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Rect | null {
    this.start = [x, y];
    this.mode = modeFor(!!ctx?.shift, !!ctx?.alt);
    this.outline = null;
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    if (this.start) {
      const r = this.rect(doc, x, y);
      this.outline = r ? [this.shape, r] : null;
    }
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Command | null {
    if (!this.start) return null;
    const r = this.rect(doc, x, y);
    this.start = null;
    this.outline = null;
    if (!r) return this.mode === "replace" ? deselect(doc) : null; // a click
    return combine(doc, this.shape === "rect" ? rectShape(doc, r) : ellipseShape(doc, r), this.mode);
  }

  override cancel(): Rect | null {
    this.start = null;
    this.outline = null;
    return null;
  }
}

export class SelectTool extends ShapeSelectTool {
  readonly id = "select";
  readonly label = "Rectangle select";
  readonly shortcut = "M";
  protected readonly shape = "rect" as const;
}

export class EllipseSelectTool extends ShapeSelectTool {
  readonly id = "ellipse_select";
  readonly label = "Ellipse select";
  readonly shortcut = "M";
  protected readonly shape = "ellipse" as const;
}

/** Freehand: hold the button and draw around the area. */
export class LassoTool extends Tool {
  readonly id = "lasso";
  readonly label = "Lasso";
  readonly shortcut = "Q";
  points: [number, number][] = [];
  outline: Outline | null = null;
  private mode: Mode = "replace";
  private down = false;

  override press(_doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Rect | null {
    this.down = true;
    this.mode = modeFor(!!ctx?.shift, !!ctx?.alt);
    this.points = [[x, y]];
    this.outline = ["poly", this.points];
    return null;
  }

  override move(_doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    const last = this.points[this.points.length - 1];
    if (this.down && Math.hypot(x - last[0], y - last[1]) >= 1) this.points.push([x, y]);
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Command | null {
    if (!this.down) return null;
    this.move(doc, x, y);
    const pts = this.points;
    this.points = [];
    this.outline = null;
    this.down = false;
    if (pts.length < 3) return this.mode === "replace" ? deselect(doc) : null;
    return combine(doc, polygonShape(doc, pts), this.mode);
  }

  override cancel(): Rect | null {
    this.points = [];
    this.outline = null;
    this.down = false;
    return null;
  }
}

/** Click corner after corner; click the first corner again, double-click or press Enter to
 *  finish; Esc cancels. The mode (Shift / Alt) comes from the first click. */
export class PolygonLassoTool extends Tool {
  readonly id = "polygon_lasso";
  readonly label = "Polygon lasso";
  readonly shortcut = "Q";
  static readonly CLOSE = 8; // screen pixels: how close to the first corner counts as closing
  points: [number, number][] = [];
  outline: Outline | null = null;
  private mode: Mode = "replace";

  override press(_doc: EditorDoc, _x: number, _y: number, ctx?: ToolContext): Rect | null {
    if (!this.points.length) this.mode = modeFor(!!ctx?.shift, !!ctx?.alt);
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Command | null {
    if (this.points.length >= 3) {
      const [fx, fy] = this.points[0];
      if (Math.hypot(x - fx, y - fy) * Math.max(ctx?.zoom ?? 1, 1e-6) <= PolygonLassoTool.CLOSE) return this.finish(doc);
    }
    const last = this.points[this.points.length - 1];
    if (!last || last[0] !== x || last[1] !== y) this.points.push([x, y]);
    this.outline = ["poly", this.points];
    return null;
  }

  /** Close the polygon and select it (Enter / double-click). */
  finish(doc: EditorDoc): Command | null {
    const pts = this.points;
    this.points = [];
    this.outline = null;
    if (pts.length < 3) return null;
    return combine(doc, polygonShape(doc, pts), this.mode);
  }

  override cancel(): Rect | null {
    this.points = [];
    this.outline = null;
    return null;
  }
}

/** Click a colour: select it where it touches (or everywhere, without "Only touching areas"). */
export class MagicWandTool extends Tool {
  readonly id = "wand";
  readonly label = "Magic wand";
  readonly shortcut = "W";
  override readonly options = [
    opt.slider("tolerance", "Tolerance", 32, 0, 255),
    opt.bool("contiguous", "Only touching areas", true),
    opt.bool("sample_all", "Sample all layers", false),
  ];
  private mode: Mode = "replace";
  private down = false;

  override press(_doc: EditorDoc, _x: number, _y: number, ctx?: ToolContext): Rect | null {
    this.mode = modeFor(!!ctx?.shift, !!ctx?.alt);
    this.down = true;
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx?: ToolContext): Command | null {
    if (!this.down || !ctx) return null;
    this.down = false;
    const o = ctx.options;
    return combine(doc, wandShape(doc, x, y, Number(o.tolerance), !!o.contiguous, !!o.sample_all), this.mode, "Magic wand");
  }

  override cancel(): Rect | null {
    this.down = false;
    return null;
  }
}

/** Drag a rectangle; the page applies it with ops.crop on Enter. */
export class CropTool extends DragRectTool {
  readonly id = "crop";
  readonly label = "Crop";
  readonly shortcut = "C";
  pending: Rect | null = null;

  override press(_doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.start = [x, y];
    this.pending = null;
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    if (this.start) this.pending = this.rect(doc, x, y);
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Command | null {
    this.move(doc, x, y);
    this.start = null;
    return null;
  }

  override cancel(_doc?: EditorDoc): Rect | null {
    this.start = null;
    this.pending = null;
    return null;
  }
}

export { deselect, selectAll };

/** Make the selected part of the active layer transparent. */
export function clearSelection(doc: EditorDoc): Command | null {
  if (!doc.selection) return null;
  const layer = doc.layers[doc.active];
  const region = clipRect(doc, layer);
  if (region.empty) return null;
  const before = cropData(layer.data, layer.width, region);
  for (let y = region.y0; y < region.y1; y++) layer.data.fill(0, (y * layer.width + region.x0) * 4, (y * layer.width + region.x1) * 4);
  restrict(doc, layer, region, before); // shaped / soft selections clear only that much
  return PatchCommand.fromRegion("Clear selection", doc, doc.active, region, before);
}
