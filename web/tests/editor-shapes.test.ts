import { describe, expect, it } from "vitest";
import { defaults, type Values } from "../src/core/options";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History, type Command } from "../src/editor/history";
import { makeLayer, solid, type RGBA } from "../src/editor/layer";
import type { Rasterizer } from "../src/editor/raster";
import type { Tool, ToolContext } from "../src/editor/tools/base";
import { makeTools } from "../src/editor/tools/index";
import { clearSelection, CropTool, deselect, selectAll, SelectTool } from "../src/editor/tools/select";
import { EllipseTool, LineTool, RectTool } from "../src/editor/tools/shapes";
import { addTextLayer } from "../src/editor/tools/text";

const lpx = (doc: EditorDoc, x: number, y: number, i = doc.active) => {
  const l = doc.layers[i];
  return Array.from(l.data.subarray((y * l.width + x) * 4, (y * l.width + x) * 4 + 4));
};
const ctx = (tool: Tool, options: Values = {}, color: RGBA = [255, 0, 0, 255], extra: Partial<ToolContext> = {}): ToolContext => ({
  primary: color,
  secondary: [255, 255, 255, 255],
  options: { ...defaults(tool.options), ...options },
  shift: false,
  button: "left",
  font: "DejaVu Sans",
  ...extra,
});
function stroke(tool: Tool, doc: EditorDoc, pts: [number, number][], c: ToolContext): Command | null {
  tool.press(doc, pts[0][0], pts[0][1], c);
  for (const p of pts.slice(1)) tool.move(doc, p[0], p[1], c);
  const last = pts[pts.length - 1];
  return tool.release(doc, last[0], last[1], c);
}
const docOf = (w: number, h: number, color: number[]): EditorDoc => ({ width: w, height: h, layers: [makeLayer("bg", w, h, solid(w, h, color))], active: 0, selection: null, dirty: false });

/** Hard-edged stand-in for the canvas rasteriser (node has no canvas). */
export const stubRaster: Rasterizer = {
  shape(kind, box, [x0, y0, x1, y1], style) {
    const out = new Uint8ClampedArray(box.width * box.height * 4);
    const [l, t, r, b] = [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
    const outline = style.mode === "outline" || style.mode === "both";
    const fill = style.mode === "filled" || style.mode === "both";
    for (let y = box.y0; y < box.y1; y++)
      for (let x = box.x0; x < box.x1; x++) {
        const cx = x + 0.5;
        const cy = y + 0.5;
        let c: number[] | null = null;
        if (kind === "line") {
          const dx = x1 - x0;
          const dy = y1 - y0;
          const k = Math.max(0, Math.min(1, ((cx - x0) * dx + (cy - y0) * dy) / (dx * dx + dy * dy || 1)));
          if (Math.hypot(cx - (x0 + k * dx), cy - (y0 + k * dy)) <= style.width / 2) c = style.stroke;
        } else {
          const ell = (inset: number) => ((cx - (l + r) / 2) / ((r - l) / 2 - inset)) ** 2 + ((cy - (t + b) / 2) / ((b - t) / 2 - inset)) ** 2 <= 1;
          const inside = kind === "rect" ? cx >= l && cx < r && cy >= t && cy < b : ell(0);
          if (!inside) continue;
          const onEdge = kind === "rect" ? Math.min(cx - l, r - cx, cy - t, b - cy) < style.width : !ell(style.width);
          if (outline && onEdge) c = style.stroke;
          else if (fill) c = style.fill;
        }
        if (c) out.set(c, ((y - box.y0) * box.width + (x - box.x0)) * 4);
      }
    return out;
  },
  text(text, font, color) {
    const width = 10 * Math.max(1, text.length);
    const height = font.size;
    const data = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < data.length; i += 4) data.set(color, i);
    return { width, height, data };
  },
};

describe("shapes", () => {
  it("draws outline vs filled rectangles", () => {
    const doc = newDoc(40, 40);
    const t = new RectTool(stubRaster);
    stroke(t, doc, [[5, 5], [35, 35]], ctx(t, { width: 2, mode: "outline" }));
    expect(lpx(doc, 20, 20)[3]).toBe(0);
    expect(lpx(doc, 5, 20)[3]).toBeGreaterThan(200);
    const doc2 = newDoc(40, 40);
    stroke(t, doc2, [[5, 5], [35, 35]], ctx(t, { mode: "filled" }));
    expect(lpx(doc2, 20, 20)).toEqual([255, 255, 255, 255]);
  });

  it("draws a filled ellipse that misses the corners", () => {
    const doc = newDoc(40, 40);
    const t = new EllipseTool(stubRaster);
    stroke(t, doc, [[0, 0], [40, 40]], ctx(t, { mode: "both" }));
    expect(lpx(doc, 20, 20)).toEqual([255, 255, 255, 255]);
    expect(lpx(doc, 1, 1)[3]).toBe(0);
  });

  it("snaps lines to 45° with shift", () => {
    const doc = newDoc(50, 50);
    const t = new LineTool(stubRaster);
    stroke(t, doc, [[10, 10], [40, 12]], ctx(t, { width: 3 }, undefined, { shift: true }));
    expect(lpx(doc, 39, 10)[3]).toBeGreaterThan(200);
    expect(lpx(doc, 39, 13)[3]).toBe(0);
  });

  it("previews while dragging and commits on release", () => {
    const doc = newDoc(40, 40);
    const t = new RectTool(stubRaster);
    const c = ctx(t, { mode: "filled" });
    t.press(doc, 5, 5, c);
    t.move(doc, 30, 30, c);
    expect(t.preview(doc)).not.toBeNull();
    expect(lpx(doc, 15, 15)[3]).toBe(0);
    const h = new History(doc);
    h.push(t.release(doc, 30, 30, c));
    expect(t.preview(doc)).toBeNull();
    expect(lpx(doc, 15, 15)[3]).toBe(255);
    h.undo();
    expect(lpx(doc, 15, 15)[3]).toBe(0);
  });

  it("keeps the preview bounded on a big canvas", () => {
    const doc = newDoc(4000, 3000);
    const t = new RectTool(stubRaster);
    const c = ctx(t);
    t.press(doc, -1000, -1000, c);
    t.move(doc, 5000, 4000, c);
    const p = t.preview(doc);
    expect(p !== null && p.width <= 4020 && p.height <= 3020).toBe(true);
    t.release(doc, 5000, 4000, c);
    expect(lpx(doc, 0, 1500)[3]).toBe(0);
  });

  it("applies the shape opacity", () => {
    const doc = newDoc(20, 20);
    const t = new RectTool(stubRaster);
    stroke(t, doc, [[0, 0], [20, 20]], ctx(t, { mode: "filled", opacity: 50 }));
    const a = lpx(doc, 10, 10)[3];
    expect(a >= 120 && a <= 135).toBe(true);
  });
});

describe("selection, crop and text", () => {
  it("selects and clears", () => {
    const doc = docOf(20, 20, [0, 0, 0, 255]);
    const h = new History(doc);
    const t = new SelectTool();
    h.push(stroke(t, doc, [[0, 0], [10, 10]], ctx(t)));
    expect(doc.selection?.equals(new Rect(0, 0, 10, 10))).toBe(true);
    h.push(clearSelection(doc));
    expect(lpx(doc, 5, 5)[3]).toBe(0);
    expect(lpx(doc, 15, 15)[3]).toBe(255);
    h.undo();
    expect(lpx(doc, 5, 5)[3]).toBe(255);
    h.undo();
    expect(doc.selection).toBeNull();
  });

  it("clips the selection to the canvas; a click clears it", () => {
    const doc = newDoc(20, 20);
    const t = new SelectTool();
    stroke(t, doc, [[10, 10], [50, -5]], ctx(t));
    expect(doc.selection?.equals(new Rect(10, 0, 20, 10))).toBe(true);
    stroke(t, doc, [[3, 3], [3, 3]], ctx(t));
    expect(doc.selection).toBeNull();
  });

  it("selects all and deselects", () => {
    const doc = newDoc(8, 6);
    const h = new History(doc);
    h.push(selectAll(doc));
    expect(doc.selection?.equals(new Rect(0, 0, 8, 6))).toBe(true);
    h.push(deselect(doc));
    expect(doc.selection).toBeNull();
    expect(deselect(doc)).toBeNull();
    expect(clearSelection(doc)).toBeNull();
  });

  it("remembers a pending crop", () => {
    const doc = newDoc(30, 30);
    const t = new CropTool();
    stroke(t, doc, [[2, 3], [12, 20]], ctx(t));
    expect(t.pending?.equals(new Rect(2, 3, 12, 20))).toBe(true);
    expect(doc.selection).toBeNull();
  });

  it("adds text as a new layer", () => {
    const doc = newDoc(200, 100);
    const h = new History(doc);
    const font = { family: "DejaVu Sans", size: 40, bold: false, italic: false };
    h.push(addTextLayer(doc, stubRaster, "Hello", 10, 10, font, [255, 0, 0, 255]));
    expect(doc.layers.length).toBe(2);
    expect(doc.active).toBe(1);
    expect(doc.layers[1].name).toBe("Hello");
    expect([doc.layers[1].x, doc.layers[1].y]).toEqual([10, 10]);
    expect(lpx(doc, 0, 0)).toEqual([255, 0, 0, 255]);
    h.undo();
    expect(doc.layers.length).toBe(1);
    expect(addTextLayer(doc, stubRaster, "   ", 0, 0, font, [0, 0, 0, 255])).toBeNull();
  });

  it("lists the tools in tool-bar order", () => {
    expect(makeTools(stubRaster).map((t) => t.id)).toEqual(["move", "brush", "eraser", "fill", "picker", "line", "rect", "ellipse", "text", "select", "ellipse_select", "lasso", "polygon_lasso", "wand", "crop"]);
  });
});
