import { describe, expect, it } from "vitest";
import { checkSize, composite, EditorError, newDoc, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History, LayerListCommand, PatchCommand } from "../src/editor/history";
import { cropData, layerBounds, makeLayer, solid } from "../src/editor/layer";

const px = (data: Uint8ClampedArray, w: number, x: number, y: number) => Array.from(data.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));

describe("document", () => {
  it("composites with opacity and hidden layers", () => {
    const red = makeLayer("r", 4, 4, solid(4, 4, [255, 0, 0, 255]));
    const blue = makeLayer("b", 4, 4, solid(4, 4, [0, 0, 255, 255]), { opacity: 0.5 });
    const hidden = makeLayer("h", 4, 4, solid(4, 4, [0, 255, 0, 255]), { visible: false });
    const doc: EditorDoc = { width: 4, height: 4, layers: [red, blue, hidden], active: 0, selection: null, dirty: false };
    const [r, g, b, a] = px(composite(doc), 4, 0, 0);
    expect(a).toBe(255);
    expect(Math.abs(r - 128)).toBeLessThanOrEqual(1);
    expect(Math.abs(b - 127)).toBeLessThanOrEqual(1);
    expect(g).toBe(0);
  });

  it("composites offset layers and sub-rectangles", () => {
    const small = makeLayer("s", 2, 2, solid(2, 2, [0, 0, 0, 255]), { x: 2, y: 2 });
    const doc: EditorDoc = { width: 4, height: 4, layers: [small], active: 0, selection: null, dirty: false };
    const img = composite(doc);
    expect(px(img, 4, 1, 1)[3]).toBe(0);
    expect(px(img, 4, 3, 3)).toEqual([0, 0, 0, 255]);
    const part = composite(doc, new Rect(2, 2, 4, 4));
    expect(part.length).toBe(2 * 2 * 4);
    expect(px(part, 2, 0, 0)).toEqual([0, 0, 0, 255]);
  });

  it("enforces the size limit", () => {
    expect(() => checkSize(16_001, 10)).toThrow(/too large/);
    expect(() => checkSize(16_000, 16_000)).toThrow(EditorError);
    expect(() => checkSize(16_000, 100)).not.toThrow();
  });

  it("does rectangle maths", () => {
    expect(Rect.fromPoints(5, 5, 1, 2).equals(new Rect(1, 2, 5, 5))).toBe(true);
    expect(new Rect(0, 0, 4, 4).intersect(new Rect(6, 6, 8, 8)).empty).toBe(true);
    expect(new Rect(0, 0, 2, 2).union(new Rect(1, 1, 3, 3)).equals(new Rect(0, 0, 3, 3))).toBe(true);
    expect(new Rect(1, 1, 3, 4).offset(1, -1).equals(new Rect(2, 0, 4, 3))).toBe(true);
    expect(new Rect(0, 0, 0, 0).union(new Rect(1, 1, 2, 2)).equals(new Rect(1, 1, 2, 2))).toBe(true);
  });

  it("makes new documents", () => {
    const doc = newDoc(30, 20);
    expect(doc.layers.length).toBe(1);
    expect(doc.layers[0].name).toBe("Background");
    expect(doc.dirty).toBe(false);
    expect(layerBounds(doc.layers[0]).equals(new Rect(0, 0, 30, 20))).toBe(true);
  });
});

function paint(doc: EditorDoc, x: number, y: number, color = [255, 0, 0, 255]) {
  const layer = doc.layers[doc.active];
  const rect = new Rect(x, y, x + 1, y + 1);
  const before = cropData(layer.data, layer.width, rect);
  layer.data.set(color, (y * layer.width + x) * 4);
  return PatchCommand.fromRegion("Brush", doc, doc.active, rect, before);
}

describe("history", () => {
  it("undoes and redoes patches", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    h.push(paint(doc, 3, 3));
    expect(h.canUndo && h.undoLabel === "Brush").toBe(true);
    h.undo();
    expect(px(doc.layers[0].data, 10, 3, 3)[3]).toBe(0);
    expect(h.canRedo).toBe(true);
    h.redo();
    expect(px(doc.layers[0].data, 10, 3, 3)).toEqual([255, 0, 0, 255]);
  });

  it("clears redo on push", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    h.push(paint(doc, 1, 1));
    h.undo();
    h.push(paint(doc, 2, 2));
    expect(h.canRedo).toBe(false);
  });

  it("drops the oldest steps over the memory cap", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc, 10); // one 1x1 patch = 8 bytes
    h.push(paint(doc, 1, 1));
    h.push(paint(doc, 2, 2));
    h.undo();
    expect(h.canUndo).toBe(false);
    expect(px(doc.layers[0].data, 10, 1, 1)[3]).toBe(255);
  });

  it("tracks the saved position", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    h.push(paint(doc, 1, 1));
    expect(doc.dirty).toBe(true);
    h.markSaved();
    expect(doc.dirty).toBe(false);
    h.push(paint(doc, 2, 2));
    expect(doc.dirty).toBe(true);
    h.undo();
    expect(doc.dirty).toBe(false);
    h.undo();
    expect(doc.dirty).toBe(true);
  });

  it("treats the saved marker as a state, not a length", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    for (let i = 0; i < 3; i++) h.push(paint(doc, i, 0));
    const pos = h.position;
    h.undo();
    h.push(paint(doc, 5, 5));
    h.markSaved(pos);
    expect(doc.dirty).toBe(true);
  });

  it("keeps the saved marker right when steps are dropped", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc, 20);
    h.markSaved();
    for (let i = 0; i < 4; i++) h.push(paint(doc, i, 0));
    while (h.canUndo) h.undo();
    expect(doc.dirty).toBe(true);
  });

  it("undoes layer-list changes", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    const before = [...doc.layers];
    doc.layers = [...doc.layers, makeLayer("Layer 2", 10, 10)];
    doc.active = 1;
    h.push(new LayerListCommand("Add layer", before, doc.layers, 0, 1));
    h.undo();
    expect(doc.layers.length).toBe(1);
    expect(doc.active).toBe(0);
    h.redo();
    expect(doc.layers.length).toBe(2);
    expect(doc.active).toBe(1);
  });
});
