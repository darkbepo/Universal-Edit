import { describe, expect, it } from "vitest";
import { defaults, type Values } from "../src/core/options";
import { newDoc, type EditorDoc } from "../src/editor/document";
import { History } from "../src/editor/history";
import { makeLayer, solid, type RGBA } from "../src/editor/layer";
import { addSticker, renderSticker, STICKERS, stickerItems } from "../src/editor/stickers";
import type { Tool, ToolContext } from "../src/editor/tools/base";
import { HighlighterTool } from "../src/editor/tools/paint";
import { ArrowTool, arrowHead } from "../src/editor/tools/shapes";
import { stubRaster } from "./editor-shapes.test";

const px = (doc: EditorDoc, x: number, y: number, i = doc.active) => {
  const l = doc.layers[i];
  return Array.from(l.data.subarray((y * l.width + x) * 4, (y * l.width + x) * 4 + 4));
};
const ctx = (tool: Tool, options: Values = {}, color: RGBA = [255, 0, 0, 255]): ToolContext => ({
  primary: color,
  secondary: [255, 255, 255, 255],
  options: { ...defaults(tool.options), ...options },
  shift: false,
  button: "left",
  font: "DejaVu Sans",
});
function stroke(tool: Tool, doc: EditorDoc, pts: [number, number][], c: ToolContext) {
  tool.press(doc, pts[0][0], pts[0][1], c);
  for (const p of pts.slice(1)) tool.move(doc, p[0], p[1], c);
  const last = pts[pts.length - 1];
  return tool.release(doc, last[0], last[1], c);
}
const white = (w: number, h: number): EditorDoc => ({ width: w, height: h, layers: [makeLayer("bg", w, h, solid(w, h, [255, 255, 255, 255]))], active: 0, selection: null, dirty: false });

describe("stickers", () => {
  it("has the same stickers as the desktop app", () => {
    expect(Object.keys(STICKERS)).toEqual(["check", "cross", "star", "heart", "bubble", "warning", "info", "arrow", "ring", "smiley", "pin"]);
  });

  it("scales the drawing and uses the swatch colours", () => {
    const items = stickerItems("arrow", 50, [0, 0, 255, 255], [255, 255, 255, 255]);
    expect(items.length).toBe(1);
    expect("poly" in items[0] && items[0].fill).toEqual([0, 0, 255, 255]);
    expect("poly" in items[0] && items[0].poly[3]).toEqual([47, 25]);
    const data = renderSticker(stubRaster, "arrow", 50, [0, 0, 255, 255]);
    const i = (25 * 50 + 25) * 4;
    expect(Array.from(data.subarray(i, i + 4))).toEqual([0, 0, 255, 255]);
  });

  it("renders every sticker", () => {
    for (const name of Object.keys(STICKERS)) {
      const d = renderSticker(stubRaster, name, 40);
      expect(d.some((v, k) => k % 4 === 3 && v > 0)).toBe(true);
    }
  });

  it("adds a centred layer that undoes", () => {
    const doc = newDoc(200, 100);
    const h = new History(doc);
    h.push(addSticker(doc, stubRaster, "star", 40, [100, 50]));
    expect(doc.layers.length).toBe(2);
    const l = doc.layers[1];
    expect([l.name, l.x, l.y, l.width]).toEqual(["Star 2", 80, 30, 40]);
    h.undo();
    expect(doc.layers.length).toBe(1);
  });
});

describe("arrow and highlighter", () => {
  it("points the head at the end", () => {
    const [tip, [bx, by]] = arrowHead(0, 0, 100, 0, 4);
    expect(tip[0]).toEqual([100, 0]);
    expect(bx < 100 && by === 0).toBe(true);
    expect(tip.slice(1).map((p) => Math.round(p[1])).sort()).toEqual([-9, 9]);
  });

  it("draws the shaft and a wider head", () => {
    const doc = white(120, 80);
    const t = new ArrowTool(stubRaster);
    stroke(t, doc, [[10, 40], [100, 40]], ctx(t, { width: 4 }));
    expect(px(doc, 50, 40).slice(0, 3)).toEqual([255, 0, 0]);
    expect(px(doc, 88, 45).slice(0, 3)).toEqual([255, 0, 0]);
    expect(px(doc, 50, 45).slice(0, 3)).toEqual([255, 255, 255]);
    expect(px(doc, 22, 45).slice(0, 3)).toEqual([255, 255, 255]);
    const doc2 = white(120, 80);
    stroke(t, doc2, [[10, 40], [100, 40]], ctx(t, { width: 4, heads: "both" }));
    expect(px(doc2, 22, 45).slice(0, 3)).toEqual([255, 0, 0]);
  });

  it("highlights see-through, without building up", () => {
    const doc = newDoc(100, 40);
    const t = new HighlighterTool();
    stroke(t, doc, [[10, 20], [90, 20], [10, 20], [90, 20]], ctx(t, {}, [255, 255, 0, 255]));
    const a = px(doc, 50, 20)[3];
    expect(a >= 90 && a <= 115).toBe(true);
  });
});
