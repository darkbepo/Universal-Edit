// Blur, sharpen and one-click looks — the same checks as tests/test_editor_filters.py.
import { describe, expect, it } from "vitest";
import type { EditorDoc } from "../src/editor/document";
import * as effects from "../src/editor/effects";
import * as filters from "../src/editor/filters";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";

const W = 20;
const H = 10;
function edge(): Uint8ClampedArray {
  const d = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) d.set(x < W / 2 ? [0, 0, 0, 255] : [255, 255, 255, 255], (y * W + x) * 4);
  return d;
}
const px = (d: Uint8ClampedArray, w: number, x: number, y: number) => Array.from(d.subarray((y * w + x) * 4, (y * w + x) * 4 + 4));

describe("blur", () => {
  it("softens edges, keeps flat areas", () => {
    const out = filters.blur(2).apply(edge(), W, H);
    const row = Array.from({ length: W }, (_, x) => px(out, W, x, 5)[0]);
    expect([row[0], row[W - 1]]).toEqual([0, 255]);
    expect(row[9] > 0 && row[9] < 255 && row[10] > 0 && row[10] < 255).toBe(true);
    const flat = solid(8, 8, [40, 80, 120, 255]);
    expect(Array.from(filters.blur(3).apply(flat, 8, 8))).toEqual(Array.from(flat));
    expect(filters.blur(0).identity).toBe(true);
  });

  it("has no dark fringe at transparent edges", () => {
    const d = new Uint8ClampedArray(20 * 20 * 4);
    for (let y = 5; y < 15; y++) for (let x = 5; x < 15; x++) d.set([255, 0, 0, 255], (y * 20 + x) * 4);
    const [r, g, b, a] = px(filters.blur(2).apply(d, 20, 20), 20, 4, 10);
    expect(a > 0 && a < 255 && r > 240 && g === 0 && b === 0).toBe(true);
  });

  it("scale shrinks the radius for previews", () => {
    const soft = (d: Uint8ClampedArray) => Array.from({ length: W }, (_, x) => px(d, W, x, 5)[0]).filter((v) => v > 0 && v < 255).length;
    expect(soft(filters.blur(4).apply(edge(), W, H, 1))).toBeGreaterThan(soft(filters.blur(4).apply(edge(), W, H, 0.25)));
  });
});

describe("sharpen", () => {
  it("raises edge contrast; threshold keeps smooth areas", () => {
    const soft = filters.blur(1.5).apply(edge(), W, H);
    const sharp = filters.sharpen(200, 2).apply(soft, W, H);
    expect(px(sharp, W, 8, 5)[0]).toBeLessThan(px(soft, W, 8, 5)[0]);
    expect(px(sharp, W, 11, 5)[0]).toBeGreaterThan(px(soft, W, 11, 5)[0]);
    const noise = solid(10, 10, [100, 100, 100, 255]);
    noise.set([104, 104, 104, 255], (5 * 10 + 5) * 4);
    expect(Array.from(filters.sharpen(300, 1, 10).apply(noise, 10, 10))).toEqual(Array.from(noise));
    expect(filters.sharpen(0).identity).toBe(true);
  });
});

describe("looks", () => {
  it("every look changes a photo and keeps alpha", () => {
    let seed = 3;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) % 256);
    const d = new Uint8ClampedArray(16 * 16 * 4).map(() => rnd());
    for (const name of Object.keys(filters.LOOKS)) {
      const out = filters.look(name).apply(d, 16, 16);
      expect(Array.from(out)).not.toEqual(Array.from(d));
      for (let i = 3; i < d.length; i += 4) expect(out[i]).toBe(d[i]);
    }
  });

  it("black & white is grey, sepia is brown, strength mixes", () => {
    const d = solid(4, 4, [200, 60, 30, 255]);
    for (const name of ["bw", "noir"]) {
      const [r, g, b] = px(filters.look(name).apply(d, 4, 4), 4, 0, 0);
      expect(r === g && g === b).toBe(true);
    }
    const [r, g, b] = px(filters.look("sepia").apply(d, 4, 4), 4, 0, 0);
    expect(r > g && g > b).toBe(true);
    const full = px(filters.look("bw").apply(d, 4, 4), 4, 0, 0);
    const half = px(filters.look("bw", 0.5).apply(d, 4, 4), 4, 0, 0);
    expect(Math.abs(half[0] - (200 + full[0]) / 2)).toBeLessThanOrEqual(1);
    expect(filters.look("bw", 0).identity && filters.look("nope").identity).toBe(true);
  });
});

describe("on the document", () => {
  it("applies inside the selection, using pixels beyond it; undo", () => {
    const doc: EditorDoc = { width: W, height: H, active: 0, selection: new Rect(0, 0, 10, 10), dirty: false, layers: [makeLayer("bg", W, H, edge())] };
    const h = new History(doc);
    h.push(effects.applyToDocument(doc, filters.blur(3), "Blur"));
    const l = doc.layers[0];
    expect(px(l.data, W, 9, 5)[0]).toBeGreaterThan(0); // white bleeds in from outside
    expect(px(l.data, W, 12, 5)[0]).toBe(255); // outside untouched
    h.undo();
    expect(px(doc.layers[0].data, W, 9, 5)[0]).toBe(0);
  });

  it("big previews are worked out on a smaller copy", () => {
    const d = new Uint8ClampedArray(400 * 300 * 4);
    for (let y = 0; y < 300; y++) for (let x = 0; x < 400; x++) d.set([x % 256, y % 256, 128, 255], (y * 400 + x) * 4);
    const doc: EditorDoc = { width: 400, height: 300, active: 0, selection: null, dirty: false, layers: [makeLayer("bg", 400, 300, d)] };
    const fast = effects.preview(doc, filters.look("bw"), 10_000);
    const exact = effects.preview(doc, filters.look("bw"));
    let diff = 0;
    for (let i = 0; i < fast.length; i++) diff += Math.abs(fast[i] - exact[i]);
    expect(diff / fast.length).toBeLessThan(3);
    for (let i = 3; i < fast.length; i += 4) expect(fast[i]).toBe(255);
  });
});

describe("review fixes", () => {
  it("small radii and shrunk previews still blur / sharpen", () => {
    const soft = filters.blur(1).apply(edge(), W, H);
    expect(Array.from(filters.sharpen(300, 0.5).apply(soft, W, H))).not.toEqual(Array.from(soft));
    expect(Array.from(filters.blur(2).apply(edge(), W, H, 0.25))).not.toEqual(Array.from(edge())); // previewed at 0.5 px
    expect(Array.from(filters.blur(0.4).apply(edge(), W, H))).not.toEqual(Array.from(edge()));
  });

  it("blurring noise keeps its average brightness (no double rounding)", () => {
    let seed = 7;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) % 256);
    const n = 64;
    const d = new Uint8ClampedArray(n * n * 4);
    for (let i = 0; i < d.length; i += 4) d.set([rnd(), rnd(), rnd(), 255], i);
    const mean = (a: Uint8ClampedArray) => {
      let s = 0;
      for (let i = 0; i < a.length; i += 4) s += a[i];
      return s / (a.length / 4);
    };
    for (const sigma of [0.5, 2, 6]) expect(Math.abs(mean(filters.blurData(d, n, n, sigma)) - mean(d))).toBeLessThan(0.1);
  });

  it("sharpen keeps a cut-out's colour and alpha", () => {
    const d = new Uint8ClampedArray(12 * 4);
    [60, 120, 180, 240, 255, 255, 255, 255].forEach((a, i) => d.set([200, 200, 200, a], (i + 2) * 4));
    const out = filters.sharpen(200, 1).apply(d, 12, 1);
    for (let x = 2; x < 10; x++) expect(px(out, 12, x, 0)).toEqual([200, 200, 200, d[x * 4 + 3]]);
  });

  it("blur on an offset layer with a selection at its edge; radius bigger than the layer", () => {
    const ld = new Uint8ClampedArray(10 * 10 * 4);
    for (let y = 0; y < 10; y++) for (let x = 0; x < 10; x++) ld.set(x < 5 ? [0, 0, 0, 255] : [255, 255, 255, 255], (y * 10 + x) * 4);
    const doc: EditorDoc = { width: 30, height: 20, active: 0, selection: new Rect(12, 6, 17, 16), dirty: false, layers: [makeLayer("small", 10, 10, ld, { x: 12, y: 6 })] };
    const h = new History(doc);
    h.push(effects.applyToDocument(doc, filters.blur(40), "Blur"));
    const l = doc.layers[0];
    expect(px(l.data, 10, 4, 5)[0]).toBeGreaterThan(0);
    expect(px(l.data, 10, 7, 5)[0]).toBe(255);
    h.undo();
    expect(px(doc.layers[0].data, 10, 4, 5)[0]).toBe(0);
  });

  it("a reduced blur preview softens edges like apply", () => {
    const d = new Uint8ClampedArray(200 * 200 * 4);
    for (let y = 50; y < 150; y++) for (let x = 50; x < 150; x++) d.set([255, 0, 0, 255], (y * 200 + x) * 4);
    const doc: EditorDoc = { width: 200, height: 200, active: 0, selection: null, dirty: false, layers: [makeLayer("bg", 200, 200, d)] };
    const fast = effects.preview(doc, filters.blur(6), 2_000);
    const exact = effects.preview(doc, filters.blur(6));
    expect(px(fast, 200, 47, 100)[3]).toBeGreaterThan(0);
    let diff = 0;
    for (let i = 3; i < fast.length; i += 4) diff += Math.abs(fast[i] - exact[i]);
    expect(diff / (fast.length / 4)).toBeLessThan(8);
  });
});
