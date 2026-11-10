import { describe, expect, it } from "vitest";
import * as ops from "../src/editor/ops";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History, PatchCommand, type Command } from "../src/editor/history";
import { cropData, makeLayer, solid } from "../src/editor/layer";

const pxOf = (doc: EditorDoc, i: number, x: number, y: number) => {
  const l = doc.layers[i];
  return Array.from(l.data.subarray((y * l.width + x) * 4, (y * l.width + x) * 4 + 4));
};

const state = (doc: EditorDoc) =>
  JSON.stringify([doc.width, doc.height, doc.active, doc.layers.map((l) => [l.name, l.x, l.y, l.width, l.height, l.opacity, l.visible, Array.from(l.data)])]);

function checkUndoRedo(doc: EditorDoc, fn: (d: EditorDoc) => Command | null) {
  const h = new History(doc);
  const before = state(doc);
  const cmd = fn(doc);
  expect(cmd).not.toBeNull();
  const after = state(doc);
  expect(after).not.toBe(before);
  h.push(cmd);
  h.undo();
  expect(state(doc)).toBe(before);
  h.redo();
  expect(state(doc)).toBe(after);
}

function sample(): EditorDoc {
  const marked = new Uint8ClampedArray(10 * 4 * 4);
  marked.set([255, 0, 0, 255], 0); // top-left corner marker
  const small = solid(2, 2, [0, 0, 255, 255]);
  small.set([0, 255, 0, 255], 0); // so flips / rotations show
  return { width: 10, height: 4, layers: [makeLayer("base", 10, 4, marked), makeLayer("small", 2, 2, small, { x: 6, y: 1 })], active: 1, selection: null, dirty: false };
}

describe("canvas operations", () => {
  it("crop shifts offsets", () => {
    const doc = sample();
    ops.crop(doc, new Rect(5, 0, 10, 4));
    expect([doc.width, doc.height]).toEqual([5, 4]);
    expect([doc.layers[1].x, doc.layers[1].y]).toEqual([1, 1]);
    expect([doc.layers[0].width, doc.layers[0].height]).toEqual([5, 4]);
  });

  it("crop clips to the canvas and drops the selection", () => {
    const doc = sample();
    doc.selection = new Rect(0, 0, 3, 3);
    ops.crop(doc, new Rect(-5, -5, 3, 3));
    expect([doc.width, doc.height]).toEqual([3, 3]);
    expect(doc.selection).toBeNull();
  });

  it("resize scales offsets", () => {
    const doc = sample();
    ops.resizeImage(doc, 20, 8);
    expect([doc.width, doc.height]).toEqual([20, 8]);
    const s = doc.layers[1];
    expect([s.x, s.y, s.width, s.height]).toEqual([12, 2, 4, 4]);
  });

  it("resize refuses huge sizes", () => {
    expect(() => ops.resizeImage(sample(), 20_000, 10)).toThrow(/too large/);
  });

  it("canvas size anchors", () => {
    const doc: EditorDoc = { width: 4, height: 4, layers: [makeLayer("a", 4, 4, solid(4, 4, [0, 0, 0, 255]))], active: 0, selection: null, dirty: false };
    ops.canvasSize(doc, 8, 8, "center");
    expect([doc.width, doc.height, doc.layers[0].x, doc.layers[0].y, doc.layers[0].width]).toEqual([8, 8, 2, 2, 4]);
    ops.canvasSize(doc, 10, 9, "bottom-right");
    expect([doc.layers[0].x, doc.layers[0].y]).toEqual([4, 3]);
  });

  it("rotates 90° clockwise", () => {
    const doc = sample();
    ops.rotate(doc, 90);
    expect([doc.width, doc.height]).toEqual([4, 10]);
    expect(pxOf(doc, 0, 3, 0)).toEqual([255, 0, 0, 255]);
    expect([doc.layers[1].x, doc.layers[1].y]).toEqual([1, 6]);
  });

  it("rotates 180° and 270°", () => {
    let doc = sample();
    ops.rotate(doc, 180);
    expect(pxOf(doc, 0, 9, 3)).toEqual([255, 0, 0, 255]);
    expect([doc.layers[1].x, doc.layers[1].y]).toEqual([2, 1]);
    doc = sample();
    ops.rotate(doc, 270);
    expect([doc.width, doc.height]).toEqual([4, 10]);
    expect(pxOf(doc, 0, 0, 9)).toEqual([255, 0, 0, 255]);
  });

  it("rotates one layer only", () => {
    const doc: EditorDoc = { width: 10, height: 10, layers: [makeLayer("a", 4, 2, solid(4, 2, [0, 0, 0, 255]), { x: 2, y: 3 })], active: 0, selection: null, dirty: false };
    ops.rotate(doc, 90, true);
    const l = doc.layers[0];
    expect([doc.width, doc.height, l.width, l.height, l.x, l.y]).toEqual([10, 10, 2, 4, 3, 2]);
  });

  it("flips", () => {
    let doc = sample();
    ops.flip(doc, true);
    expect(pxOf(doc, 0, 9, 0)).toEqual([255, 0, 0, 255]);
    expect(doc.layers[1].x).toBe(2);
    doc = sample();
    ops.flip(doc, false, true);
    expect(doc.layers[1].x).toBe(6);
  });
});

describe("adjustments", () => {
  it("brightness / contrast values", () => {
    const img = solid(2, 2, [100, 100, 100, 77]);
    expect(Math.abs(ops.brightnessContrast(img, 50, 0)[0] - 228)).toBeLessThanOrEqual(1);
    const out = ops.brightnessContrast(img, 0, -100);
    expect([out[0], out[3]]).toEqual([128, 77]);
    expect(ops.brightnessContrast(img, 0, 100)[0]).toBe(72);
  });

  it("applies brightness inside the selection", () => {
    const doc: EditorDoc = { width: 4, height: 4, layers: [makeLayer("a", 4, 4, solid(4, 4, [100, 100, 100, 255]))], active: 0, selection: new Rect(0, 0, 2, 4), dirty: false };
    ops.applyBrightnessContrast(doc, 50, 0);
    expect(pxOf(doc, 0, 0, 0)[0]).toBeGreaterThan(200);
    expect(pxOf(doc, 0, 3, 0)[0]).toBe(100);
    expect(ops.applyBrightnessContrast(doc, 0, 0)).toBeNull();
  });
});

describe("layer operations", () => {
  it("merge down respects opacity", () => {
    const doc: EditorDoc = {
      width: 4,
      height: 4,
      layers: [makeLayer("lower", 4, 4, solid(4, 4, [255, 0, 0, 255])), makeLayer("upper", 2, 2, solid(2, 2, [0, 0, 255, 255]), { x: 1, y: 1, opacity: 0.5 })],
      active: 1,
      selection: null,
      dirty: false,
    };
    ops.mergeDown(doc);
    expect(doc.layers.length).toBe(1);
    expect(doc.active).toBe(0);
    const m = doc.layers[0];
    expect([m.name, m.opacity]).toEqual(["lower", 1]);
    const p = pxOf(doc, 0, 1 - m.x, 1 - m.y);
    expect(Math.abs(p[0] - 128)).toBeLessThanOrEqual(1);
    expect(Math.abs(p[2] - 127)).toBeLessThanOrEqual(1);
    expect(ops.mergeDown(doc)).toBeNull();
  });

  it("refuses to delete the last layer", () => {
    expect(ops.deleteLayer(newDoc(4, 4))).toBeNull();
  });

  it("moves a layer and the active index with it", () => {
    const doc = sample();
    ops.moveLayer(doc, -1);
    expect(doc.layers.map((l) => l.name)).toEqual(["small", "base"]);
    expect(doc.active).toBe(0);
    expect(ops.moveLayer(doc, -1)).toBeNull();
  });

  it("adds a layer above the active one", () => {
    const doc = sample();
    doc.active = 0;
    ops.addLayer(doc);
    expect(doc.layers[1].name).toBe("Layer 3");
    expect(doc.active).toBe(1);
    expect([doc.layers[1].width, doc.layers[1].height]).toEqual([10, 4]);
  });
});

describe("every operation undoes and redoes", () => {
  const cases: [string, (d: EditorDoc) => Command | null][] = [
    ["crop", (d) => ops.crop(d, new Rect(1, 1, 8, 3))],
    ["resize", (d) => ops.resizeImage(d, 5, 2, "nearest")],
    ["canvas size", (d) => ops.canvasSize(d, 12, 6, "top-left")],
    ["rotate", (d) => ops.rotate(d, 90)],
    ["rotate layer", (d) => ops.rotate(d, 180, true)],
    ["flip", (d) => ops.flip(d, true)],
    ["flip layer", (d) => ops.flip(d, false, true)],
    ["brightness", (d) => ops.applyBrightnessContrast(d, 20, 30)],
    ["add", ops.addLayer],
    ["duplicate", ops.duplicateLayer],
    ["delete", ops.deleteLayer],
    ["merge", ops.mergeDown],
    ["move", (d) => ops.moveLayer(d, -1)],
    ["rename", (d) => ops.renameLayer(d, 0, "renamed")],
    ["opacity", (d) => ops.setOpacity(d, 1, 0.25)],
    ["visible", (d) => ops.setVisible(d, 0, false)],
  ];
  for (const [name, fn] of cases) it(name, () => checkUndoRedo(sample(), fn));

  it("keeps undo order with mixed commands", () => {
    const doc = newDoc(10, 10);
    const h = new History(doc);
    const l = doc.layers[0];
    const before = cropData(l.data, 10, new Rect(8, 8, 9, 9));
    l.data.set([255, 0, 0, 255], (8 * 10 + 8) * 4);
    h.push(PatchCommand.fromRegion("Brush", doc, 0, new Rect(8, 8, 9, 9), before));
    h.push(ops.crop(doc, new Rect(0, 0, 5, 5)));
    expect([doc.width, doc.height]).toEqual([5, 5]);
    h.undo();
    expect([doc.width, doc.height]).toEqual([10, 10]);
    expect(pxOf(doc, 0, 8, 8)).toEqual([255, 0, 0, 255]);
    h.undo();
    expect(pxOf(doc, 0, 8, 8)[3]).toBe(0);
    h.redo();
    h.redo();
    expect([doc.width, doc.height]).toEqual([5, 5]);
  });
});
