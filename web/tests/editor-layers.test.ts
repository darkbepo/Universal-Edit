// Several selected layers: reorder (arrows / drag), duplicate, delete, merge, show/hide.
// Same cases as tests/test_editor_layers.py on the desktop.
import { describe, expect, it } from "vitest";
import * as ops from "../src/editor/ops";
import { selectedIndices, type EditorDoc } from "../src/editor/document";
import { History, type Command } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";

function doc5(n = 5): EditorDoc {
  const layers = Array.from({ length: n }, (_, i) => makeLayer(`L${i}`, 4, 4, solid(4, 4, [i * 40, 0, 0, 255])));
  return { width: 4, height: 4, layers, active: 0, selection: null, dirty: false };
}
const names = (d: EditorDoc) => d.layers.map((l) => l.name);
const state = (d: EditorDoc) => JSON.stringify([names(d), d.active, selectedIndices(d), d.layers.map((l) => l.visible)]);
const px = (d: EditorDoc, i: number, x: number, y: number) => {
  const l = d.layers[i];
  return Array.from(l.data.subarray((y * l.width + x) * 4, (y * l.width + x) * 4 + 4));
};

describe("layer selection", () => {
  it("always contains the active layer", () => {
    const d = doc5();
    expect(selectedIndices(d)).toEqual([0]);
    ops.selectLayers(d, [1, 3]);
    expect([selectedIndices(d), d.active]).toEqual([[1, 3], 3]);
    ops.selectLayers(d, [1, 3], 1);
    expect(d.active).toBe(1);
    ops.selectLayer(d, 4);
    expect(selectedIndices(d)).toEqual([4]);
  });
});

describe("moving layers", () => {
  it("moves into a gap keeping order and selection", () => {
    const d = doc5();
    ops.selectLayers(d, [0, 2], 2);
    ops.moveLayersTo(d, selectedIndices(d), 5);
    expect(names(d)).toEqual(["L1", "L3", "L4", "L0", "L2"]);
    expect([selectedIndices(d), d.active]).toEqual([[3, 4], 4]);
    ops.moveLayersTo(d, selectedIndices(d), 0);
    expect(names(d)).toEqual(["L0", "L2", "L1", "L3", "L4"]);
  });

  it("a gap inside the selection is nothing", () => {
    const d = doc5();
    ops.selectLayers(d, [1, 2]);
    for (const gap of [1, 2, 3]) expect(ops.moveLayersTo(d, selectedIndices(d), gap)).toBeNull();
  });

  it("moves a group one step and stops at the top", () => {
    const d = doc5();
    ops.selectLayers(d, [2, 4]);
    ops.moveLayersBy(d, selectedIndices(d), 1);
    expect(names(d)).toEqual(["L0", "L1", "L3", "L2", "L4"]);
    expect(selectedIndices(d)).toEqual([3, 4]);
    expect(ops.moveLayersBy(d, selectedIndices(d), 1)).toBeNull();
    ops.moveLayersBy(d, selectedIndices(d), -1);
    expect(names(d)).toEqual(["L0", "L1", "L2", "L4", "L3"]);
  });
});

describe("acting on several layers", () => {
  it("duplicates each above itself", () => {
    const d = doc5(3);
    ops.selectLayers(d, [0, 2], 0);
    ops.duplicateLayers(d, selectedIndices(d));
    expect(names(d)).toEqual(["L0", "L0 copy", "L1", "L2", "L2 copy"]);
    expect([selectedIndices(d), d.active]).toEqual([[1, 4], 1]);
    expect(d.layers[1].data).not.toBe(d.layers[0].data);
  });

  it("deletes several but keeps one", () => {
    const d = doc5();
    ops.selectLayers(d, [1, 3]);
    ops.deleteLayers(d, selectedIndices(d));
    expect([names(d), selectedIndices(d)]).toEqual([["L0", "L2", "L4"], [0]]);
    expect(ops.deleteLayers(d, [0, 1, 2])).toBeNull();
  });

  it("merges several, dropping hidden ones", () => {
    const d: EditorDoc = {
      width: 4, height: 4, active: 0, selection: null, dirty: false,
      layers: [
        makeLayer("bottom", 4, 4, solid(4, 4, [255, 0, 0, 255])),
        makeLayer("middle", 2, 2, solid(2, 2, [0, 255, 0, 255]), { x: 2, y: 2 }),
        makeLayer("hidden", 4, 4, solid(4, 4, [0, 0, 255, 255]), { visible: false }),
        makeLayer("top", 1, 1, solid(1, 1, [0, 0, 255, 255])),
      ],
    };
    ops.mergeLayers(d, [1, 2, 3]);
    expect([names(d), d.active]).toEqual([["bottom", "top"], 1]);
    const m = d.layers[1];
    expect([m.x, m.y, m.width, m.height]).toEqual([0, 0, 4, 4]);
    expect(px(d, 1, 0, 0)).toEqual([0, 0, 255, 255]);
    expect(px(d, 1, 3, 3)).toEqual([0, 255, 0, 255]);
    expect(px(d, 1, 0, 3)[3]).toBe(0);
    expect(ops.mergeLayers(d, [1])).toBeNull();
  });

  it("shows / hides and sets opacity of several", () => {
    const d = doc5();
    ops.setVisibleLayers(d, [1, 2], false);
    expect(d.layers.map((l) => l.visible)).toEqual([true, false, false, true, true]);
    expect(ops.setVisibleLayers(d, [1, 2], false)).toBeNull();
    ops.setOpacityLayers(d, [0, 4], 0.5);
    expect(d.layers.map((l) => l.opacity)).toEqual([0.5, 1, 1, 1, 0.5]);
  });
});

describe("undo restores layers and selection", () => {
  const cases: [string, (d: EditorDoc) => Command | null][] = [
    ["move to", (d) => ops.moveLayersTo(d, [0, 2], 5)],
    ["move by", (d) => ops.moveLayersBy(d, [1, 3], 1)],
    ["duplicate", (d) => ops.duplicateLayers(d, [1, 3])],
    ["delete", (d) => ops.deleteLayers(d, [1, 3])],
    ["merge", (d) => ops.mergeLayers(d, [1, 3])],
    ["hide", (d) => ops.setVisibleLayers(d, [1, 3], false)],
  ];
  for (const [name, fn] of cases) {
    it(name, () => {
      const d = doc5();
      ops.selectLayers(d, [1, 3], 1);
      const h = new History(d);
      const before = state(d);
      h.push(fn(d));
      const after = state(d);
      expect(after).not.toBe(before);
      h.undo();
      expect(state(d)).toBe(before);
      h.redo();
      expect(state(d)).toBe(after);
    });
  }
});

describe("show only this layer", () => {
  it("hides the others, then shows all again (mirrors test_editor_layers.py)", async () => {
    const { soloLayer, isSolo } = await import("../src/editor/ops");
    const { makeLayer } = await import("../src/editor/layer");
    const { History } = await import("../src/editor/history");
    const d = { width: 4, height: 4, layers: ["a", "b", "c"].map((n) => makeLayer(n, 4, 4)), active: 0, selection: null, dirty: false };
    d.layers[2] = { ...d.layers[2], visible: false };
    const h = new History(d);
    h.push(soloLayer(d, 2));
    expect(d.layers.map((l) => l.visible)).toEqual([false, false, true]);
    expect(isSolo(d, 2)).toBe(true);
    h.push(soloLayer(d, 2));
    expect(d.layers.map((l) => l.visible)).toEqual([true, true, true]);
    h.undo();
    h.undo();
    expect(d.layers.map((l) => l.visible)).toEqual([true, true, false]);
  });
});
