// Copy / cut / paste, the Move tool and nudging — same cases as tests/test_editor_clipboard.py.
import { describe, expect, it } from "vitest";
import * as clipboard from "../src/editor/clipboard";
import { selectedIndices, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";
import * as ops from "../src/editor/ops";
import type { ToolContext } from "../src/editor/tools/base";
import { MoveTool } from "../src/editor/tools/move";

function doc(): EditorDoc {
  return {
    width: 20, height: 10, active: 1, selection: null, dirty: false,
    layers: [makeLayer("bg", 20, 10, solid(20, 10, [255, 0, 0, 255])), makeLayer("blue", 4, 4, solid(4, 4, [0, 0, 255, 255]), { x: 2, y: 3 })],
  };
}
const ctx = (shift = false): ToolContext => ({ primary: [0, 0, 0, 255], secondary: [255, 255, 255, 255], options: {}, shift, button: "left", font: "" });
const px = (c: { data: Uint8ClampedArray; width: number }, x: number, y: number) => Array.from(c.data.subarray((y * c.width + x) * 4, (y * c.width + x) * 4 + 4));
const pos = (d: EditorDoc) => d.layers.map((l) => [l.x, l.y]);

describe("copy", () => {
  it("copies the whole active layer without a selection", () => {
    const c = clipboard.copy(doc()) as clipboard.Clip;
    expect([c.x, c.y, c.width, c.height]).toEqual([2, 3, 4, 4]);
    expect(px(c, 0, 0)).toEqual([0, 0, 255, 255]);
  });
  it("copies only inside the selection", () => {
    const d = doc();
    d.selection = new Rect(4, 4, 10, 10);
    const c = clipboard.copy(d) as clipboard.Clip;
    expect([c.x, c.y, c.width, c.height]).toEqual([4, 4, 2, 3]);
    d.selection = new Rect(15, 0, 20, 2);
    expect(clipboard.copy(d)).toBeNull();
  });
  it("merged copy is what you see", () => {
    const d = doc();
    d.selection = new Rect(1, 3, 3, 4);
    const c = clipboard.copy(d, true) as clipboard.Clip;
    expect([c.x, c.y]).toEqual([1, 3]);
    expect([px(c, 0, 0), px(c, 1, 0)]).toEqual([[255, 0, 0, 255], [0, 0, 255, 255]]);
  });
});

describe("cut and paste", () => {
  it("cut clears and undoes", () => {
    const d = doc();
    const h = new History(d);
    const [c, cmd] = clipboard.cut(d);
    h.push(cmd);
    expect(px(c as clipboard.Clip, 1, 1)).toEqual([0, 0, 255, 255]);
    expect(px(d.layers[1], 1, 1)[3]).toBe(0);
    h.undo();
    expect(px(d.layers[1], 1, 1)).toEqual([0, 0, 255, 255]);
  });
  it("paste goes back in place as a new selected layer", () => {
    const d = doc();
    const h = new History(d);
    h.push(clipboard.paste(d, clipboard.copy(d) as clipboard.Clip));
    expect(d.layers.map((l) => l.name)).toEqual(["bg", "blue", "Pasted 3"]);
    expect([d.layers[2].x, d.layers[2].y, d.active, selectedIndices(d)]).toEqual([2, 3, 2, [2]]);
    expect(d.layers[2].data).not.toBe(d.layers[1].data);
    h.undo();
    expect([d.layers.length, d.active]).toEqual([2, 1]);
  });
  it("paste from outside is centred", () => {
    const d = doc();
    clipboard.paste(d, { data: solid(6, 2, [255, 255, 255, 255]), width: 6, height: 2, x: null, y: null });
    expect([d.layers[d.active].x, d.layers[d.active].y]).toEqual([7, 4]);
    clipboard.paste(d, { data: new Uint8ClampedArray(16), width: 2, height: 2, x: null, y: null }, [0, 0]);
    expect([d.layers[d.active].x, d.layers[d.active].y]).toEqual([-1, -1]);
  });
  it("refuses huge images", () => {
    expect(() => clipboard.paste(doc(), { data: new Uint8ClampedArray(4), width: 16_001, height: 1, x: null, y: null })).toThrow();
  });
});

describe("move tool and nudge", () => {
  it("moves the selected layers and undoes", () => {
    const d = doc();
    d.layers.push(makeLayer("top", 2, 2, solid(2, 2, [0, 255, 0, 255])));
    ops.selectLayers(d, [1, 2], 2);
    const h = new History(d);
    const t = new MoveTool();
    t.press(d, 5.2, 5.0);
    const dirty = t.move(d, 8.4, 4.0, ctx()) as Rect;
    expect(dirty.contains(2, 3) && dirty.contains(8, 5)).toBe(true);
    h.push(t.release(d, 8.4, 4.0, ctx()));
    expect(pos(d)).toEqual([[0, 0], [5, 2], [3, -1]]);
    h.undo();
    expect(pos(d)).toEqual([[0, 0], [2, 3], [0, 0]]);
  });
  it("Shift keeps one axis; cancel restores; no move → no undo step", () => {
    const d = doc();
    const t = new MoveTool();
    t.press(d, 0, 0);
    t.move(d, 5, 2, ctx(true));
    expect([d.layers[1].x, d.layers[1].y]).toEqual([7, 3]);
    t.cancel(d);
    expect([d.layers[1].x, d.layers[1].y]).toEqual([2, 3]);
    t.press(d, 0, 0);
    expect(t.release(d, 0.3, 0.2, ctx())).toBeNull();
  });
  it("nudges", () => {
    const d = doc();
    const h = new History(d);
    h.push(ops.nudgeLayers(d, selectedIndices(d), -10, 1));
    expect([d.layers[1].x, d.layers[1].y]).toEqual([-8, 4]);
    expect(ops.nudgeLayers(d, [1], 0, 0)).toBeNull();
    h.undo();
    expect([d.layers[1].x, d.layers[1].y]).toEqual([2, 3]);
  });
});
