import { describe, expect, it } from "vitest";
import { defaults, type Values } from "../src/core/options";
import type { EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { contentAwareFill, fillMask } from "../src/editor/inpaint";
import { makeLayer, solid } from "../src/editor/layer";
import type { ToolContext } from "../src/editor/tools/base";
import { CloneStampTool } from "../src/editor/tools/paint";

// mirrors tests/test_editor_retouch.py
const halves = (): EditorDoc => {
  const data = solid(100, 60, [255, 0, 0, 255]);
  for (let y = 0; y < 60; y++) for (let x = 50; x < 100; x++) data.set([0, 0, 255, 255], (y * 100 + x) * 4);
  return { width: 100, height: 60, layers: [makeLayer("bg", 100, 60, data)], active: 0, selection: null, dirty: false };
};
const px = (doc: EditorDoc, x: number, y: number, i = doc.active) => Array.from(doc.layers[i].data.subarray((y * doc.layers[i].width + x) * 4, (y * doc.layers[i].width + x) * 4 + 4));
const ctx = (t: CloneStampTool, options: Values = {}, alt = false): ToolContext => ({ primary: [0, 0, 0, 255], secondary: [255, 255, 255, 255], options: { ...defaults(t.options), ...options }, shift: false, alt, button: "left", font: "DejaVu Sans" });
const click = (t: CloneStampTool, doc: EditorDoc, x: number, y: number, c: ToolContext) => {
  t.press(doc, x, y, c);
  return t.release(doc, x, y, c);
};

describe("clone stamp", () => {
  it("needs a source first", () => {
    const doc = halves();
    const t = new CloneStampTool();
    expect(click(t, doc, 20, 30, ctx(t))).toBeNull();
    expect(t.notice).toContain("Alt+click");
  });

  it("copies from the source and undoes", () => {
    const doc = halves();
    const h = new History(doc);
    const t = new CloneStampTool();
    click(t, doc, 75, 30, ctx(t, {}, true));
    h.push(click(t, doc, 20, 30, ctx(t, { size: 10, hardness: 100 })));
    expect(px(doc, 20, 30)).toEqual([0, 0, 255, 255]);
    expect(px(doc, 5, 30)).toEqual([255, 0, 0, 255]);
    expect(t.marker(30, 30)).toEqual([85, 30]);
    h.undo();
    expect(px(doc, 20, 30)).toEqual([255, 0, 0, 255]);
  });

  it("keeps the offset when aligned, restarts when not", () => {
    const doc = halves();
    const t = new CloneStampTool();
    click(t, doc, 75, 30, ctx(t, {}, true));
    click(t, doc, 10, 10, ctx(t, { size: 6, hardness: 100 }));
    expect(t.offset).toEqual([-65, -20]);
    click(t, doc, 30, 40, ctx(t, { size: 6, hardness: 100 }));
    expect(t.offset).toEqual([-65, -20]);
    click(t, doc, 30, 40, ctx(t, { size: 6, hardness: 100, aligned: false }));
    expect(t.offset).toEqual([-45, 10]);
  });

  it("copies from all layers", () => {
    const doc = halves();
    doc.layers.push(makeLayer("top", 100, 60));
    doc.active = 1;
    const t = new CloneStampTool();
    click(t, doc, 75, 30, ctx(t, {}, true));
    click(t, doc, 20, 30, ctx(t, { size: 8, hardness: 100 }));
    expect(px(doc, 20, 30)[3]).toBe(0);
    click(t, doc, 20, 30, ctx(t, { size: 8, hardness: 100, sample: "all", aligned: false }));
    expect(px(doc, 20, 30)).toEqual([0, 0, 255, 255]);
  });
});

describe("content-aware fill", () => {
  it("weights reach past the selection", () => {
    const doc = halves();
    doc.selection = new Rect(10, 10, 20, 20);
    const m = fillMask(doc, new Rect(5, 5, 25, 25));
    expect([m.length, m[10 * 20 + 10], m[0]]).toEqual([400, 1, 0]);
  });

  it("removes a spot and undoes", () => {
    const data = solid(80, 60, [40, 160, 60, 255]);
    for (let y = 20; y < 36; y++) for (let x = 30; x < 46; x++) data.set([250, 250, 250, 255], (y * 80 + x) * 4);
    const doc: EditorDoc = { width: 80, height: 60, layers: [makeLayer("bg", 80, 60, data)], active: 0, selection: new Rect(27, 17, 49, 39), dirty: false };
    const h = new History(doc);
    h.push(contentAwareFill(doc));
    const mid = px(doc, 38, 28);
    expect(Math.max(...[40, 160, 60].map((v, c) => Math.abs(mid[c] - v)))).toBeLessThanOrEqual(3);
    expect(px(doc, 5, 5)).toEqual([40, 160, 60, 255]);
    h.undo();
    expect(px(doc, 38, 28)).toEqual([250, 250, 250, 255]);
  });

  it("fills a gradient smoothly", () => {
    const data = new Uint8ClampedArray(100 * 20 * 4);
    for (let y = 0; y < 20; y++) for (let x = 0; x < 100; x++) data.set([x * 2.5, 0, 0, 255], (y * 100 + x) * 4);
    const doc: EditorDoc = { width: 100, height: 20, layers: [makeLayer("bg", 100, 20, data)], active: 0, selection: new Rect(40, 5, 60, 15), dirty: false };
    contentAwareFill(doc);
    const r = px(doc, 50, 10)[0];
    expect(r > 100 && r < 150).toBe(true);
  });

  it("needs a selection with room around it", () => {
    const doc = halves();
    expect(() => contentAwareFill(doc)).toThrow();
    doc.selection = new Rect(0, 0, 100, 60);
    expect(() => contentAwareFill(doc)).toThrow();
  });
});
