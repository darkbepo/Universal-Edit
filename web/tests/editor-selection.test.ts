// Selections of any shape — the same checks as tests/test_editor_selection.py.
import { describe, expect, it } from "vitest";
import * as clipboard from "../src/editor/clipboard";
import type { EditorDoc } from "../src/editor/document";
import * as effects from "../src/editor/effects";
import * as filters from "../src/editor/filters";
import { Rect } from "../src/editor/geometry";
import { History, type Command } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";
import * as selection from "../src/editor/selection";
import type { Tool, ToolContext } from "../src/editor/tools/base";
import { FillTool } from "../src/editor/tools/fill";
import { BrushTool } from "../src/editor/tools/paint";
import { clearSelection, EllipseSelectTool, LassoTool, MagicWandTool, PolygonLassoTool, SelectTool } from "../src/editor/tools/select";

const doc = (w = 40, h = 30, c: number[] = [10, 20, 30, 255]): EditorDoc => ({ width: w, height: h, active: 0, selection: null, dirty: false, layers: [makeLayer("bg", w, h, solid(w, h, c))] });
const ctx = (options: Record<string, unknown> = {}, extra: Partial<ToolContext> = {}): ToolContext => ({ primary: [255, 0, 0, 255], secondary: [0, 0, 255, 255], options: options as ToolContext["options"], shift: false, button: "left", font: "", ...extra });
const drag = (t: Tool, d: EditorDoc, a: [number, number], b: [number, number], extra: Partial<ToolContext> = {}): Command | null => {
  const c = ctx({}, extra);
  t.press(d, a[0], a[1], c);
  t.move(d, b[0], b[1], c);
  return t.release(d, b[0], b[1], c);
};
const on = (d: EditorDoc, x: number, y: number) => selection.fullMask(d)[y * d.width + x] > 127;
const px = (d: EditorDoc, x: number, y: number) => Array.from(d.layers[0].data.subarray((y * d.width + x) * 4, (y * d.width + x) * 4 + 4));
const twoSquares = (d: EditorDoc) => {
  drag(new SelectTool(), d, [0, 0], [10, 10]);
  drag(new SelectTool(), d, [20, 0], [30, 10], { shift: true });
};

describe("selection modes and tools", () => {
  it("rectangle add / subtract / intersect with undo", () => {
    const d = doc();
    const h = new History(d);
    h.push(drag(new SelectTool(), d, [0, 0], [10, 10]));
    expect(d.selection?.equals(new Rect(0, 0, 10, 10)) && !d.selectionMask).toBe(true);
    h.push(drag(new SelectTool(), d, [20, 0], [30, 10], { shift: true }));
    expect([on(d, 5, 5), on(d, 25, 5), on(d, 15, 5)]).toEqual([true, true, false]);
    h.push(drag(new SelectTool(), d, [5, 0], [25, 10], { alt: true }));
    expect([on(d, 2, 5), on(d, 7, 5), on(d, 27, 5)]).toEqual([true, false, true]);
    h.push(drag(new SelectTool(), d, [0, 0], [3, 10], { shift: true, alt: true }));
    expect(d.selection?.equals(new Rect(0, 0, 3, 10))).toBe(true);
    for (let i = 0; i < 3; i++) h.undo();
    expect(d.selection?.equals(new Rect(0, 0, 10, 10)) && !d.selectionMask).toBe(true);
    h.redo();
    expect(on(d, 15, 5)).toBe(false);
  });

  it("a click deselects only in replace mode", () => {
    const d = doc();
    drag(new SelectTool(), d, [0, 0], [10, 10]);
    expect(drag(new SelectTool(), d, [3, 3], [3, 3], { shift: true })).toBeNull();
    drag(new SelectTool(), d, [3, 3], [3, 3]);
    expect(d.selection).toBeNull();
  });

  it("ellipse, lasso and polygon", () => {
    const d = doc();
    drag(new EllipseSelectTool(), d, [0, 0], [20, 20]);
    expect([on(d, 10, 10), on(d, 1, 1)]).toEqual([true, false]);
    const lasso = new LassoTool();
    const c = ctx();
    lasso.press(d, 0, 0, c);
    for (const [x, y] of [[30, 0], [30, 20], [0, 20]]) lasso.move(d, x, y, c);
    lasso.release(d, 0, 20, c);
    expect([on(d, 15, 10), on(d, 15, 25)]).toEqual([true, false]);
    const poly = new PolygonLassoTool();
    for (const [x, y] of [[5, 5], [35, 5], [20, 25]]) {
      poly.press(d, x, y, c);
      expect(poly.release(d, x, y, c)).toBeNull();
    }
    expect(poly.release(d, 5.5, 5.5, ctx({}, { zoom: 4 }))).not.toBeNull();
    expect([on(d, 20, 10), on(d, 5, 24)]).toEqual([true, false]);
    poly.press(d, 1, 1, c);
    poly.release(d, 1, 1, c);
    poly.cancel();
    expect(poly.points).toEqual([]);
    expect(poly.finish(d)).toBeNull();
  });

  it("magic wand: touching or everywhere", () => {
    const d = doc(30, 10, [255, 255, 255, 255]);
    const l = d.layers[0];
    for (let y = 0; y < 10; y++) for (let x = 10; x < 12; x++) l.data.set([0, 0, 0, 255], (y * 30 + x) * 4);
    const w = new MagicWandTool();
    const c = ctx({ tolerance: 32, contiguous: true, sample_all: false });
    w.press(d, 2, 2, c);
    w.release(d, 2, 2, c);
    expect(d.selection?.equals(new Rect(0, 0, 10, 10))).toBe(true);
    const c2 = ctx({ tolerance: 32, contiguous: false, sample_all: false });
    w.press(d, 2, 2, c2);
    w.release(d, 2, 2, c2);
    expect([on(d, 20, 5), on(d, 11, 5)]).toEqual([true, false]);
  });

  it("invert and feather", () => {
    const d = doc();
    drag(new SelectTool(), d, [0, 0], [10, 30]);
    selection.invert(d);
    expect(d.selection?.equals(new Rect(10, 0, 40, 30)) && !d.selectionMask).toBe(true);
    selection.feather(d, 3);
    const w = selection.weight(d, d.selection as Rect) as Float32Array;
    const sw = (d.selection as Rect).width;
    expect(w[15 * sw] > 0 && w[15 * sw] < 0.6 && w[15 * sw + 20] > 0.99).toBe(true);
    expect((d.selection as Rect).x0).toBeLessThan(10);
    selection.deselect(d);
    expect(selection.feather(d, 3)).toBeNull();
    selection.invert(d);
    expect(d.selection?.equals(new Rect(0, 0, 40, 30))).toBe(true);
  });

  it("outline follows the shape", () => {
    const d = doc();
    drag(new SelectTool(), d, [0, 0], [10, 10]);
    expect(selection.outline(d)).toHaveLength(4);
    drag(new SelectTool(), d, [20, 0], [30, 10], { shift: true });
    const segs = selection.outline(d).map((s) => s.join(","));
    expect(segs).toContain("0,0,10,0");
    expect(segs).toContain("20,0,30,0");
    expect(segs).toContain("10,0,10,10");
  });
});

describe("everything respects the selection's shape", () => {
  it("fill and brush", () => {
    const d = doc();
    twoSquares(d);
    const f = new FillTool();
    const fc = ctx({ tolerance: 32, sample_all: false });
    f.press(d, 5, 5, fc);
    f.release(d, 5, 5, fc);
    expect([px(d, 5, 5).slice(0, 3), px(d, 15, 5), px(d, 25, 5).slice(0, 3)]).toEqual([[255, 0, 0], [10, 20, 30, 255], [255, 0, 0]]);
    const d2 = doc();
    twoSquares(d2);
    const b = new BrushTool();
    const bc = ctx({ size: 60, hardness: 100, opacity: 100 });
    b.press(d2, 15, 5, bc);
    b.release(d2, 15, 5, bc);
    expect([px(d2, 5, 5).slice(0, 3), px(d2, 15, 5)]).toEqual([[255, 0, 0], [10, 20, 30, 255]]);
  });

  it("clear, effects, copy and cut", () => {
    let d = doc();
    twoSquares(d);
    clearSelection(d);
    expect([px(d, 5, 5)[3], px(d, 15, 5)[3]]).toEqual([0, 255]);
    d = doc();
    twoSquares(d);
    effects.applyToDocument(d, filters.look("bw"), "Look");
    expect(px(d, 15, 5)).toEqual([10, 20, 30, 255]);
    const prev = effects.preview(d, filters.blur(2));
    expect(Array.from(prev.subarray((5 * 40 + 15) * 4, (5 * 40 + 15) * 4 + 4))).toEqual([10, 20, 30, 255]);
    d = doc();
    twoSquares(d);
    const c = clipboard.copy(d) as clipboard.Clip;
    expect([c.width, c.height, c.data[(5 * 30 + 5) * 4 + 3], c.data[(5 * 30 + 15) * 4 + 3]]).toEqual([30, 10, 255, 0]);
    clipboard.cut(d);
    expect([px(d, 15, 5)[3], px(d, 5, 5)[3]]).toEqual([255, 0]);
  });

  it("a soft selection mixes on premultiplied colour (no dark edge)", () => {
    const d = doc(20, 1, [0, 0, 0, 0]);
    d.selection = new Rect(0, 0, 20, 1);
    d.selectionMask = Uint8Array.from({ length: 20 }, (_, i) => Math.round((i * 255) / 19));
    const f = new FillTool();
    f.press(d, 10, 0, ctx({ tolerance: 32, sample_all: false }));
    const [r, g, b, a] = px(d, 10, 0);
    expect([r, g, b]).toEqual([255, 0, 0]);
    expect(a > 100 && a < 160).toBe(true);
  });
});
