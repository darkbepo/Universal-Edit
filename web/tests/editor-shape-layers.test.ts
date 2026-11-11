import { describe, expect, it } from "vitest";
import { defaults, type Values } from "../src/core/options";
import type { EditorDoc } from "../src/editor/document";
import { History } from "../src/editor/history";
import { makeLayer, solid, type RGBA } from "../src/editor/layer";
import * as ops from "../src/editor/ops";
import { readOra, writeOra } from "../src/editor/ora";
import type { Tool, ToolContext } from "../src/editor/tools/base";
import { ArrowTool, canvasPoints, EllipseTool, LineTool, RectTool, shapePaintedOver, type ShapeSpec } from "../src/editor/tools/shapes";
import { fakePng } from "./editor-ora.test";
import { stubRaster } from "./editor-shapes.test";

// mirrors the "shape layers" tests in tests/test_editor_annotate.py
const doc = (): EditorDoc => ({ width: 120, height: 80, layers: [makeLayer("bg", 120, 80, solid(120, 80, [255, 255, 255, 255]))], active: 0, selection: null, dirty: false });
const ctx = (tool: Tool, options: Values = {}, color: RGBA = [255, 0, 0, 255]): ToolContext => ({ primary: color, secondary: [255, 255, 255, 255], options: { ...defaults(tool.options), ...options }, shift: false, button: "left", font: "" });
const stroke = (t: Tool, d: EditorDoc, a: [number, number], b: [number, number], c: ToolContext) => {
  t.press(d, a[0], a[1], c);
  t.move(d, b[0], b[1], c);
  return t.release(d, b[0], b[1], c);
};

describe("shape layers", () => {
  it("each shape is its own layer with its spec", () => {
    const d = doc();
    const h = new History(d);
    const t = new RectTool(stubRaster);
    h.push(stroke(t, d, [10, 10], [40, 30], ctx(t, { width: 2, mode: "both" })));
    expect([d.layers.length, d.active]).toEqual([2, 1]);
    const l = d.layers[1];
    expect((l.shape as ShapeSpec).kind).toBe("rect");
    expect(canvasPoints(l)).toEqual([10, 10, 40, 30]);
    expect(Array.from(d.layers[0].data.subarray((20 * 120 + 20) * 4, (20 * 120 + 20) * 4 + 4))).toEqual([255, 255, 255, 255]); // the photo untouched
    h.undo();
    expect(d.layers.length).toBe(1);
  });

  it("dragging a corner reshapes and is one undo step", () => {
    const d = doc();
    const h = new History(d);
    const t = new EllipseTool(stubRaster);
    h.push(stroke(t, d, [10, 10], [40, 30], ctx(t)));
    expect(t.handles(d)[2]).toEqual([40, 30]);
    const c = ctx(t);
    t.press(d, 40, 30, c);
    t.move(d, 60, 50, c);
    expect(canvasPoints(d.layers[d.active])).toEqual([10, 10, 60, 50]);
    h.push(t.release(d, 60, 50, c));
    expect(canvasPoints(d.layers[d.active])).toEqual([10, 10, 60, 50]);
    expect([h.undoLabel, d.layers.length]).toEqual(["Edit shape", 2]);
    h.undo();
    expect(canvasPoints(d.layers[d.active])).toEqual([10, 10, 40, 30]);
  });

  it("moving the layer keeps its handles on it", () => {
    const d = doc();
    const t = new LineTool(stubRaster);
    stroke(t, d, [10, 10], [50, 20], ctx(t));
    ops.nudgeLayers(d, [d.active], 5, 7);
    expect(t.handles(d)).toEqual([[15, 17], [55, 27]]);
  });

  it("painted shapes and transforms become pixels", () => {
    const d = doc();
    const t = new RectTool(stubRaster);
    stroke(t, d, [10, 10], [40, 30], ctx(t));
    expect(shapePaintedOver(stubRaster, d.layers[d.active])).toBe(false);
    d.layers[d.active].data[3] = 7;
    expect(shapePaintedOver(stubRaster, d.layers[d.active])).toBe(true);
    t.press(d, 10, 10, ctx(t)); // on a handle: refused, it's pixels now
    expect(t.notice).toContain("painted on");
    const h = new History(d);
    h.push(ops.rasterizeLayer(d));
    expect(d.layers[d.active].shape ?? null).toBeNull();
    expect(h.undoLabel).toBe("Rasterize layer");
    stroke(t, d, [60, 10], [90, 30], ctx(t));
    ops.rotate(d, 90, true);
    expect(d.layers[d.active].shape ?? null).toBeNull();
  });

  it("stays editable through an .ora file", async () => {
    const d = doc();
    const t = new ArrowTool(stubRaster);
    stroke(t, d, [10, 40], [100, 40], ctx(t, { width: 5, heads: "both" }));
    const back = await readOra(await writeOra(d, fakePng), fakePng);
    const s = back.layers[1].shape as ShapeSpec;
    expect([s.kind, s.heads, s.width]).toEqual(["arrow", "both", 5]);
    expect(canvasPoints(back.layers[1])).toEqual([10, 40, 100, 40]);
  });
});

// mirrors the review-fix tests in tests/test_editor_annotate.py
describe("shape layers: review fixes", () => {
  it("clicking a handle without dragging changes nothing", () => {
    const d = doc();
    const t = new RectTool(stubRaster);
    stroke(t, d, [10, 10], [40, 30], ctx(t));
    const c = ctx(t);
    t.press(d, 42, 32, c);
    expect(t.release(d, 42, 32, c)).toBeNull();
    t.press(d, 42, 32, c);
    t.move(d, 52, 32, c);
    t.release(d, 52, 32, c);
    expect(canvasPoints(d.layers[d.active])).toEqual([10, 10, 50, 30]);
  });

  it("a flat rectangle adds no layer", () => {
    const d = doc();
    const t = new RectTool(stubRaster);
    expect(stroke(t, d, [10, 10], [40, 10], ctx(t, { mode: "filled", width: 1 }))).toBeNull();
    expect(d.layers.length).toBe(1);
  });

  it("reshaping keeps the mask on the same picture pixels", () => {
    const d = doc();
    const t = new RectTool(stubRaster);
    stroke(t, d, [10, 10], [40, 30], ctx(t, { mode: "filled" }));
    const l = d.layers[1];
    const mask = solid(l.width, l.height, [255, 255, 255, 255]);
    mask.set([0, 0, 0, 255], ((20 - l.y) * l.width + (20 - l.x)) * 4);
    d.layers[1] = { ...l, mask };
    const c = ctx(t, { mode: "filled" });
    t.press(d, 10, 10, c);
    t.move(d, 2, 4, c);
    t.release(d, 2, 4, c);
    const n = d.layers[1];
    expect(n.mask?.length).toBe(n.data.length);
    expect(n.mask?.[((20 - n.y) * n.width + (20 - n.x)) * 4]).toBe(0);
    expect(n.mask?.[((6 - n.y) * n.width + (4 - n.x)) * 4]).toBe(255);
  });

  it("files keep a recipe only while it matches, and ignore shapes they can't draw", async () => {
    const d = doc();
    const t = new RectTool(stubRaster);
    stroke(t, d, [10, 10], [40, 30], ctx(t));
    expect((await readOra(await writeOra(d, fakePng, stubRaster), fakePng, "a", stubRaster)).layers[1].shape).toBeTruthy();
    d.layers[1].data.set([0, 255, 0, 255], 0); // painted on
    const back = await readOra(await writeOra(d, fakePng, stubRaster), fakePng, "a", stubRaster);
    expect(back.layers[1].shape).toBeFalsy();
  });
});
