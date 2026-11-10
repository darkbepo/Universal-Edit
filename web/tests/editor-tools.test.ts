import { describe, expect, it } from "vitest";
import { defaults, type Values } from "../src/core/options";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History, type Command } from "../src/editor/history";
import { __stats, makeLayer, solid, type RGBA } from "../src/editor/layer";
import { type Tool, type ToolContext } from "../src/editor/tools/base";
import { FillTool, PickerTool } from "../src/editor/tools/fill";
import { BrushTool, EraserTool } from "../src/editor/tools/paint";

export const px = (data: Uint8ClampedArray, w: number, x: number, y: number) => Array.from(data.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));
export const lpx = (doc: EditorDoc, x: number, y: number, i = doc.active) => px(doc.layers[i].data, doc.layers[i].width, x, y);

export function ctx(tool: Tool, options: Values = {}, color: RGBA = [255, 0, 0, 255], extra: Partial<ToolContext> = {}): ToolContext {
  return { primary: color, secondary: [255, 255, 255, 255], options: { ...defaults(tool.options), ...options }, shift: false, button: "left", font: "DejaVu Sans", ...extra };
}

export function stroke(tool: Tool, doc: EditorDoc, pts: [number, number][], c: ToolContext): Command | null {
  tool.press(doc, pts[0][0], pts[0][1], c);
  for (const p of pts.slice(1)) tool.move(doc, p[0], p[1], c);
  const last = pts[pts.length - 1];
  return tool.release(doc, last[0], last[1], c);
}

const docOf = (w: number, h: number, color: number[]): EditorDoc => ({ width: w, height: h, layers: [makeLayer("bg", w, h, solid(w, h, color))], active: 0, selection: null, dirty: false });

describe("brush and eraser", () => {
  it("paints and undoes", () => {
    const doc = newDoc(50, 50);
    const h = new History(doc);
    const t = new BrushTool();
    const cmd = stroke(t, doc, [[10, 10], [40, 10]], ctx(t, { size: 6 }));
    expect(lpx(doc, 25, 10)).toEqual([255, 0, 0, 255]);
    expect(lpx(doc, 25, 30)[3]).toBe(0);
    h.push(cmd);
    h.undo();
    expect(lpx(doc, 25, 10)[3]).toBe(0);
  });

  it("returns a small dirty rect", () => {
    const doc = newDoc(50, 50);
    const t = new BrushTool();
    const r = t.press(doc, 20, 20, ctx(t, { size: 6 }));
    expect(r && r.contains(20, 20) && r.width < 15).toBe(true);
  });

  it("doesn't stack opacity within one stroke", () => {
    const doc = newDoc(50, 50);
    const t = new BrushTool();
    stroke(t, doc, [[10, 10], [10, 10.5], [10, 11], [10, 10]], ctx(t, { size: 10, opacity: 50, hardness: 100 }));
    const a = lpx(doc, 10, 10)[3];
    expect(a >= 120 && a <= 135).toBe(true);
  });

  it("respects the selection", () => {
    const doc = newDoc(50, 50);
    doc.selection = new Rect(0, 0, 20, 50);
    const t = new BrushTool();
    stroke(t, doc, [[5, 10], [45, 10]], ctx(t, { size: 4 }));
    expect(lpx(doc, 30, 10)[3]).toBe(0);
    expect(lpx(doc, 10, 10)[3]).toBe(255);
  });

  it("works on offset layers", () => {
    const doc: EditorDoc = { width: 50, height: 50, layers: [makeLayer("s", 20, 20, undefined, { x: 30, y: 30 })], active: 0, selection: null, dirty: false };
    const t = new BrushTool();
    stroke(t, doc, [[35, 35], [35, 35]], ctx(t, { size: 4 }));
    expect(lpx(doc, 5, 5)).toEqual([255, 0, 0, 255]);
    expect(stroke(t, doc, [[5, 5], [5, 5]], ctx(t, { size: 4 }))).toBeNull();
  });

  it("fades a soft brush at the edge", () => {
    const doc = newDoc(60, 60);
    const t = new BrushTool();
    stroke(t, doc, [[30, 30]], ctx(t, { size: 40, hardness: 0 }));
    const centre = lpx(doc, 30, 30)[3];
    const edge = lpx(doc, 47, 30)[3];
    expect(centre > 200 && edge < 200 && edge > 0).toBe(true);
  });

  it("paints the secondary colour with the right button", () => {
    const doc = newDoc(20, 20);
    const t = new BrushTool();
    stroke(t, doc, [[10, 10]], ctx(t, { size: 4 }, undefined, { button: "right" }));
    expect(lpx(doc, 10, 10)).toEqual([255, 255, 255, 255]);
  });

  it("erases", () => {
    const doc = docOf(30, 30, [0, 0, 0, 255]);
    const t = new EraserTool();
    stroke(t, doc, [[5, 15], [25, 15]], ctx(t, { size: 4 }));
    expect(lpx(doc, 15, 15)[3]).toBe(0);
    expect(lpx(doc, 15, 2)[3]).toBe(255);
  });

  it("does not copy the whole layer per stroke", () => {
    const doc = newDoc(3000, 3000);
    const t = new BrushTool();
    __stats.largestCrop = 0;
    const cmd = stroke(t, doc, [[100, 100], [200, 150]], ctx(t, { size: 20 })) as unknown as { rect: Rect };
    expect(__stats.largestCrop).toBeLessThan(3000 * 3000);
    expect(cmd.rect.width).toBeLessThan(200);
  });

  it("cancel restores the original pixels", () => {
    const doc = newDoc(30, 30);
    const t = new BrushTool();
    const c = ctx(t, { size: 6 });
    t.press(doc, 10, 10, c);
    t.move(doc, 20, 10, c);
    t.cancel(doc);
    expect(lpx(doc, 15, 10)[3]).toBe(0);
  });
});

function halves(): EditorDoc {
  const d = docOf(20, 10, [10, 10, 10, 255]);
  for (let y = 0; y < 10; y++) for (let x = 10; x < 20; x++) d.layers[0].data.set([40, 40, 40, 255], (y * 20 + x) * 4);
  return d;
}

describe("fill and picker", () => {
  it("uses the tolerance", () => {
    const doc = halves();
    const t = new FillTool();
    stroke(t, doc, [[2, 2]], ctx(t, { tolerance: 5 }));
    expect(lpx(doc, 5, 5)).toEqual([255, 0, 0, 255]);
    expect(lpx(doc, 15, 5)).toEqual([40, 40, 40, 255]);
    const doc2 = halves();
    stroke(t, doc2, [[2, 2]], ctx(t, { tolerance: 40 }));
    expect(lpx(doc2, 15, 5)).toEqual([255, 0, 0, 255]);
  });

  it("fills connected pixels only", () => {
    const doc = docOf(21, 5, [0, 0, 0, 255]);
    for (let y = 0; y < 5; y++) doc.layers[0].data.set([255, 255, 255, 255], (y * 21 + 10) * 4);
    const t = new FillTool();
    stroke(t, doc, [[1, 1]], ctx(t, { tolerance: 0 }, [0, 0, 255, 255]));
    expect(lpx(doc, 5, 2)).toEqual([0, 0, 255, 255]);
    expect(lpx(doc, 15, 2)).toEqual([0, 0, 0, 255]);
  });

  it("can sample all layers", () => {
    const wall = makeLayer("wall", 21, 5);
    for (let y = 0; y < 5; y++) wall.data.set([0, 0, 0, 255], (y * 21 + 10) * 4);
    const doc: EditorDoc = { width: 21, height: 5, layers: [wall, makeLayer("paint", 21, 5)], active: 1, selection: null, dirty: false };
    const t = new FillTool();
    stroke(t, doc, [[1, 1]], ctx(t, { tolerance: 0, sample_all: true }));
    expect(lpx(doc, 5, 2)).toEqual([255, 0, 0, 255]);
    expect(lpx(doc, 15, 2)[3]).toBe(0);
  });

  it("respects the selection", () => {
    const doc = newDoc(20, 20);
    doc.selection = new Rect(0, 0, 10, 10);
    const t = new FillTool();
    stroke(t, doc, [[2, 2]], ctx(t));
    expect(lpx(doc, 15, 15)[3]).toBe(0);
    expect(lpx(doc, 5, 5)[3]).toBe(255);
  });

  it("blends a semi-transparent colour", () => {
    const doc = docOf(4, 4, [0, 0, 255, 255]);
    const t = new FillTool();
    stroke(t, doc, [[1, 1]], ctx(t, {}, [255, 0, 0, 128]));
    const [r, , b, a] = lpx(doc, 1, 1);
    expect(a).toBe(255);
    expect(r >= 120 && r <= 135 && b >= 120 && b <= 135).toBe(true);
  });

  it("fills a 4000 × 4000 transparent layer fast", () => {
    const doc = newDoc(4000, 4000);
    const t = new FillTool();
    const t0 = performance.now();
    stroke(t, doc, [[5, 5]], ctx(t, { tolerance: 0 }));
    expect(performance.now() - t0).toBeLessThan(2000);
    expect(lpx(doc, 3999, 3999)).toEqual([255, 0, 0, 255]);
  });

  it("picks a colour", () => {
    const doc = docOf(10, 10, [0, 0, 255, 255]);
    const t = new PickerTool();
    t.press(doc, 3, 3, ctx(t));
    expect(t.picked).toEqual([0, 0, 255, 255]);
    expect(t.pickedButton).toBe("left");
    expect(t.release(doc, 3, 3, ctx(t))).toBeNull();
  });

  it("ignores a click just outside the canvas", () => {
    const doc = newDoc(10, 10);
    const t = new FillTool();
    expect(stroke(t, doc, [[-0.5, 3]], ctx(t))).toBeNull();
  });
});
