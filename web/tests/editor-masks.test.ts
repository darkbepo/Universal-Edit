// Layer masks, the mask brush, the gradient tool and background blur — the same checks as
// tests/test_editor_masks.py.
import { describe, expect, it } from "vitest";
import { composite, type EditorDoc } from "../src/editor/document";
import * as effects from "../src/editor/effects";
import * as filters from "../src/editor/filters";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";
import * as ops from "../src/editor/ops";
import * as selection from "../src/editor/selection";
import type { ToolContext } from "../src/editor/tools/base";
import { GradientTool } from "../src/editor/tools/gradient";
import { MaskBrushTool } from "../src/editor/tools/paint";

const doc = (): EditorDoc => ({ width: 20, height: 10, active: 1, selection: null, dirty: false, layers: [makeLayer("bg", 20, 10, solid(20, 10, [0, 0, 255, 255])), makeLayer("top", 20, 10, solid(20, 10, [255, 0, 0, 255]))] });
const ctx = (options: Record<string, unknown>, button: "left" | "right" = "left"): ToolContext => ({ primary: [255, 255, 255, 255], secondary: [0, 0, 0, 255], options: options as ToolContext["options"], shift: false, button, font: "" });
const at = (d: Uint8ClampedArray, w: number, x: number, y: number) => Array.from(d.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));
const hideLeft = (d: EditorDoc, upTo: number) => {
  const l = d.layers[d.active];
  const m = l.mask as Uint8ClampedArray;
  for (let y = 0; y < l.height; y++) for (let x = 0; x < upTo; x++) m.fill(0, (y * l.width + x) * 4, (y * l.width + x) * 4 + 3);
};

describe("layer masks", () => {
  it("hide parts of a layer; invert; undo", () => {
    const d = doc();
    const h = new History(d);
    h.push(ops.addMask(d));
    expect(ops.addMask(d)).toBeNull();
    hideLeft(d, 10);
    const out = composite(d);
    expect([at(out, 20, 5, 5).slice(0, 3), at(out, 20, 15, 5).slice(0, 3)]).toEqual([[0, 0, 255], [255, 0, 0]]);
    h.push(ops.invertMask(d));
    expect(at(composite(d), 20, 5, 5).slice(0, 3)).toEqual([255, 0, 0]);
    h.undo();
    h.undo();
    expect(d.layers[1].mask ?? null).toBeNull();
  });

  it("from selection, apply, delete", () => {
    const d = doc();
    d.selection = new Rect(0, 0, 5, 10);
    ops.addMask(d, true);
    const m = d.layers[1].mask as Uint8ClampedArray;
    expect([m[(2 * 20 + 2) * 4], m[(2 * 20 + 10) * 4]]).toEqual([255, 0]);
    ops.applyMask(d);
    expect(d.layers[1].mask ?? null).toBeNull();
    expect(at(d.layers[1].data, 20, 10, 2)[3]).toBe(0);
    expect(ops.deleteMask(d)).toBeNull();
  });

  it("follow flip, rotate, resize, crop and duplicate; merge bakes them", () => {
    const d = doc();
    ops.addMask(d);
    hideLeft(d, 5);
    ops.flip(d, true);
    const m = () => d.layers[1].mask as Uint8ClampedArray;
    expect([m()[(5 * 20 + 19) * 4], m()[(5 * 20) * 4]]).toEqual([0, 255]);
    ops.rotate(d, 90);
    expect(m().length).toBe(d.layers[1].data.length);
    ops.resizeImage(d, 5, 10, "nearest");
    expect(m().length).toBe(d.layers[1].data.length);
    ops.crop(d, new Rect(0, 0, 5, 5));
    expect(m().length).toBe(d.layers[1].data.length);
    ops.duplicateLayer(d);
    expect(d.layers[2].mask && d.layers[2].mask !== d.layers[1].mask).toBe(true);
    const d2 = doc();
    ops.addMask(d2);
    hideLeft(d2, 10);
    ops.mergeDown(d2);
    expect(d2.layers[0].mask ?? null).toBeNull();
    expect([at(d2.layers[0].data, 20, 5, 5).slice(0, 3), at(d2.layers[0].data, 20, 15, 5).slice(0, 3)]).toEqual([[0, 0, 255], [255, 0, 0]]);
  });

  it("the mask brush hides and reveals, with undo; adds a mask on the first stroke", () => {
    const d = doc();
    ops.addMask(d);
    const h = new History(d);
    const o = { size: 6, hardness: 100, opacity: 100 };
    const t = new MaskBrushTool();
    t.press(d, 5, 5, ctx(o));
    h.push(t.release(d, 5, 5, ctx(o)));
    const m = () => (d.layers[1].mask as Uint8ClampedArray)[(5 * 20 + 5) * 4];
    expect(m()).toBe(0);
    expect(at(d.layers[1].data, 20, 5, 5)).toEqual([255, 0, 0, 255]);
    t.press(d, 5, 5, ctx(o, "right"));
    h.push(t.release(d, 5, 5, ctx(o, "right")));
    expect(m()).toBe(255);
    h.undo();
    expect(m()).toBe(0);
    // no mask yet: the first stroke adds one, in the same undo step
    const d2 = doc();
    const h2 = new History(d2);
    t.press(d2, 5, 5, ctx(o));
    h2.push(t.release(d2, 5, 5, ctx(o)));
    expect((d2.layers[1].mask as Uint8ClampedArray)[(5 * 20 + 5) * 4]).toBe(0);
    expect((d2.layers[1].mask as Uint8ClampedArray)[(8 * 20 + 15) * 4]).toBe(255);
    h2.undo();
    expect(d2.layers[1].mask ?? null).toBeNull();
  });
});

const gradient = (d: EditorDoc, a: [number, number], b: [number, number], o: Record<string, unknown> = {}) => {
  const t = new GradientTool();
  const c = ctx({ shape: "linear", target: "colour", colours: "front_back", ...o });
  t.press(d, a[0], a[1]);
  t.move(d, b[0], b[1], c);
  return t.release(d, b[0], b[1], c);
};

describe("gradient tool", () => {
  it("paints front → back, with undo; front → see-through", () => {
    const d = doc();
    const h = new History(d);
    h.push(gradient(d, [0, 5], [20, 5]));
    const l = d.layers[1];
    expect(at(l.data, 20, 0, 5)[0]).toBeGreaterThan(240);
    expect(at(l.data, 20, 19, 5)[0]).toBeLessThan(15);
    h.undo();
    expect(at(d.layers[1].data, 20, 0, 5)).toEqual([255, 0, 0, 255]);
    gradient(d, [0, 5], [20, 5], { colours: "front_clear" });
    const [r, g, b] = at(d.layers[1].data, 20, 19, 5);
    expect(r === 255 && g < 15 && b < 15).toBe(true);
  });

  it("shapes the mask; makes a soft selection an adjustment fades through", () => {
    const d = doc();
    gradient(d, [0, 0], [20, 0], { target: "mask" });
    const m = d.layers[1].mask as Uint8ClampedArray;
    expect(m[(5 * 20) * 4]).toBeGreaterThan(240);
    expect(m[(5 * 20 + 19) * 4]).toBeLessThan(15);
    gradient(d, [0, 0], [0, 10], { target: "selection" });
    const w = selection.weight(d, d.selection as Rect) as Float32Array;
    expect(w[0]).toBeGreaterThan(0.9);
    expect(w[w.length - 1]).toBeLessThan(0.1);
    d.active = 0;
    effects.applyToDocument(d, filters.look("bw"), "Look");
    const top = at(d.layers[0].data, 20, 5, 0);
    const bottom = at(d.layers[0].data, 20, 5, 9);
    expect(top[0]).toBeGreaterThan(bottom[0]); // the look shows strongest at the top
  });

  it("round, and a click does nothing", () => {
    const d = doc();
    expect(gradient(d, [10, 5], [10, 5])).toBeNull();
    gradient(d, [10, 5], [20, 5], { shape: "radial" });
    expect(at(d.layers[1].data, 20, 10, 5)[0]).toBeGreaterThan(220);
    expect(at(d.layers[1].data, 20, 0, 5)[0]).toBeLessThan(15);
  });
});

describe("background blur", () => {
  it("blurs only outside the selection, keeps the selection, undo", () => {
    const d: EditorDoc = { width: 40, height: 20, active: 0, selection: new Rect(0, 0, 10, 20), dirty: false, layers: [makeLayer("photo", 40, 20)] };
    const l = d.layers[0];
    for (let y = 0; y < 20; y++) for (let x = 0; x < 40; x++) l.data.set(x % 2 ? [0, 0, 0, 255] : [255, 255, 255, 255], (y * 40 + x) * 4);
    const before = new Uint8ClampedArray(l.data);
    const h = new History(d);
    h.push(effects.applyToDocument(d, filters.backgroundBlur(3, 0), "Background blur"));
    for (let y = 0; y < 20; y++) for (let x = 0; x < 8; x++) expect(at(l.data, 40, x, y)).toEqual(at(before, 40, x, y));
    const spread = (a: Uint8ClampedArray) => {
      const v = Array.from({ length: 10 }, (_, i) => a[(10 * 40 + 30 + i) * 4]);
      return Math.max(...v) - Math.min(...v);
    };
    expect(spread(l.data)).toBeLessThan(spread(before) / 3);
    expect(d.selection?.equals(new Rect(0, 0, 10, 20)) && !d.selectionMask).toBe(true);
    h.undo();
    expect(Array.from(d.layers[0].data)).toEqual(Array.from(before));
  });

  it("does nothing when everything is selected", () => {
    const d: EditorDoc = { width: 10, height: 10, active: 0, selection: new Rect(0, 0, 10, 10), dirty: false, layers: [makeLayer("photo", 10, 10, solid(10, 10, [9, 9, 9, 255]))] };
    expect(effects.applyToDocument(d, filters.backgroundBlur(3, 0), "Background blur")).toBeNull();
  });

  it("does nothing without a selection", () => {
    const d: EditorDoc = { width: 10, height: 10, active: 0, selection: null, dirty: false, layers: [makeLayer("photo", 10, 10, solid(10, 10, [9, 9, 9, 255]))] };
    expect(effects.applyToDocument(d, filters.backgroundBlur(3, 4), "Background blur")).toBeNull();
  });
});
