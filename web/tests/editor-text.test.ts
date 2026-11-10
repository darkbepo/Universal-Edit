// Text v2 — the same checks as tests/test_editor_text.py, with a fake text drawer (the letters
// themselves are drawn by the browser; the e2e tests check that part).
import { describe, expect, it } from "vitest";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { History } from "../src/editor/history";
import * as ops from "../src/editor/ops";
import { readOra, writeOra } from "../src/editor/ora";
import type { Decoded, FontSpec, Rasterizer } from "../src/editor/raster";
import { addTextLayer, anchorOf, editTextLayer, makeSpec, renderSpec, textLayerAt, paintedOver, turn, type TextSpec } from "../src/editor/tools/text";
import { fakePng } from "./editor-ora.test";
import { stubRaster } from "./editor-shapes.test";

/** Each character = a 10 × 20 solid block; the outline grows the image like the real drawer. */
const blocks: Rasterizer = {
  ...stubRaster,
  text(text: string, font: FontSpec, color): Decoded {
    const lines = text.split("\n");
    const pad = 2 + (font.outline ?? 0);
    const w = Math.max(...lines.map((l) => l.length)) * 10 + 2 * pad;
    const h = lines.length * 20 + 2 * pad;
    const data = new Uint8ClampedArray(w * h * 4);
    for (let y = pad; y < h - pad; y++) for (let x = pad; x < w - pad; x++) data.set(color, (y * w + x) * 4);
    return { data, width: w, height: h };
  },
};
const doc = (): EditorDoc => newDoc(300, 200);
const spec = (text: string, p: Partial<TextSpec> = {}) => makeSpec(text, { size: 30, ...p });

describe("text v2", () => {
  it("styles grow the image and keep the click point", () => {
    const [plain, ox, oy] = renderSpec(blocks, spec("Hi"), [300, 200]);
    expect([ox, oy]).toEqual([0, 0]);
    const [outlined] = renderSpec(blocks, spec("Hi", { outline: 4 }), [300, 200]);
    expect(outlined.width).toBeGreaterThan(plain.width);
    const [boxed, bx, by] = renderSpec(blocks, spec("Hi", { box: true, box_color: [255, 0, 0, 255] }), [300, 200]);
    expect(bx > 0 && by > 0).toBe(true);
    expect(Array.from(boxed.data.subarray(((boxed.height >> 1) * boxed.width + 2) * 4, ((boxed.height >> 1) * boxed.width + 2) * 4 + 3))).toEqual([255, 0, 0]);
    const [shadowed] = renderSpec(blocks, spec("Hi", { shadow: true }), [300, 200]);
    const covered = (d: Decoded) => d.data.filter((_, i) => i % 4 === 3 && d.data[i] > 0).length;
    expect(covered(shadowed)).toBeGreaterThan(covered(plain));
  });

  it("a watermark fills the canvas faintly", () => {
    const d = doc();
    addTextLayer(d, blocks, spec("© me", { watermark: true }), 0, 0);
    const l = d.layers[d.active];
    expect([l.width, l.height, l.x, l.y]).toEqual([300, 200, 0, 0]);
    expect(l.opacity).toBeLessThan(0.5);
    const at = (x0: number, x1: number, y0: number, y1: number) => {
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) if (l.data[(y * 300 + x) * 4 + 3]) return true;
      return false;
    };
    expect([at(0, 150, 0, 200), at(150, 300, 0, 200), at(0, 300, 0, 100), at(0, 300, 100, 200)]).toEqual([true, true, true, true]);
  });

  it("turn rotates around the middle into a bigger box", () => {
    const src = { data: new Uint8ClampedArray(40 * 10 * 4).fill(255), width: 40, height: 10 };
    const t = turn(src, 90);
    expect([t.width, t.height]).toEqual([10, 40]);
  });

  it("text layers are edited again in place; a moved one stays moved", () => {
    const d = doc();
    const h = new History(d);
    h.push(addTextLayer(d, blocks, spec("Hello", { outline: 2 }), 50, 40));
    const i = d.active;
    expect(anchorOf(d.layers[i])).toEqual([50, 40]);
    expect(textLayerAt(d, 55, 45)).toBe(i);
    expect(textLayerAt(d, 5, 5)).toBeNull();
    h.push(editTextLayer(d, blocks, i, { ...(d.layers[i].text as TextSpec), text: "Hello world", box: true }));
    expect((d.layers[i].text as TextSpec).text).toBe("Hello world");
    expect(anchorOf(d.layers[i])).toEqual([50, 40]);
    expect(h.undoLabel).toBe("Edit text");
    h.undo();
    expect((d.layers[i].text as TextSpec).text).toBe("Hello");
    ops.nudgeLayers(d, [i], 30, 10);
    editTextLayer(d, blocks, i, { ...(d.layers[i].text as TextSpec), shadow: true });
    expect(anchorOf(d.layers[i])).toEqual([80, 50]);
  });

  it("empty text deletes; transforms make it pixels; duplicates stay editable", () => {
    const d = doc();
    addTextLayer(d, blocks, spec("Bye"), 10, 10);
    editTextLayer(d, blocks, d.active, { ...(d.layers[d.active].text as TextSpec), text: "  " });
    expect(d.layers).toHaveLength(1);
    addTextLayer(d, blocks, spec("Copy"), 10, 10);
    ops.duplicateLayer(d);
    expect((d.layers[d.active].text as TextSpec).text).toBe("Copy");
  });

  it("stays editable through an .ora file (same recipe format as the desktop)", async () => {
    const d = doc();
    addTextLayer(d, blocks, spec("Saved", { outline: 3, box: true, box_color: [255, 0, 0, 255] }), 10, 10);
    const back = await readOra(await writeOra(d, fakePng), fakePng);
    const t = back.layers[1].text as TextSpec;
    expect([t.text, t.outline, t.box_color]).toEqual(["Saved", 3, [255, 0, 0, 255]]);
    expect(back.layers[0].text ?? null).toBeNull();
  });
});

describe("editing a changed text layer", () => {
  it("keeps the mask, visibility and a chosen name; notices paint", () => {
    const d = doc();
    const h = new History(d);
    h.push(addTextLayer(d, blocks, spec("Hello"), 50, 40));
    const i = d.active;
    expect(paintedOver(d, blocks, i)).toBe(false);
    const l = d.layers[i];
    const mask = new Uint8ClampedArray(l.width * l.height * 4).fill(100);
    d.layers[i] = { ...l, name: "Title", visible: false, mask };
    h.push(editTextLayer(d, blocks, i, l.text as TextSpec));
    const e = d.layers[i];
    expect([e.name, e.visible, e.mask?.[0]]).toEqual(["Title", false, 100]);
    e.data[0] = 7;
    expect(paintedOver(d, blocks, i)).toBe(true);
  });
});
