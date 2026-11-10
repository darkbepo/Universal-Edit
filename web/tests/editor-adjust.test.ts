// Tone and colour adjustments — the same pixels as the desktop app (both read
// tests/fixtures/adjust_cases.json), plus curves, auto levels, histogram and undo.
import { describe, expect, it } from "vitest";
import shared from "../../tests/fixtures/adjust_cases.json";
import * as adjust from "../src/editor/adjust";
import type { EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";

type PyLevels = { black?: number; white?: number; gamma?: number; out_black?: number; out_white?: number };
function build(s: Record<string, unknown>): adjust.Adjustments {
  const { levels, curves, ...rest } = s as { levels?: Record<string, PyLevels>; curves?: Record<string, [number, number][]> };
  const a: adjust.Adjustments = { ...(rest as adjust.Adjustments) };
  if (levels) {
    a.levels = {};
    for (const [ch, l] of Object.entries(levels)) {
      a.levels[ch as adjust.Channel] = { black: l.black ?? 0, white: l.white ?? 255, gamma: l.gamma ?? 1, outBlack: l.out_black ?? 0, outWhite: l.out_white ?? 255 };
    }
  }
  if (curves) a.curves = curves as adjust.Adjustments["curves"];
  return a;
}
const pixels = () => new Uint8ClampedArray(shared.pixels.flat());

describe("shared cases (same pixels as the desktop app)", () => {
  for (const [name, c] of Object.entries(shared.cases)) {
    it(name, () => {
      const out = Array.from(adjust.applyData(pixels(), build(c.settings)));
      const want = c.expected.flat();
      const worst = Math.max(...out.map((v, i) => Math.abs(v - want[i])));
      expect(worst).toBeLessThanOrEqual(1); // float rounding may differ by one step
    });
  }
});

describe("adjustments", () => {
  it("neutral settings change nothing; alpha never changes", () => {
    const p = pixels();
    expect(adjust.isIdentity({ levels: { rgb: { ...adjust.NEUTRAL_LEVELS } }, curves: { r: adjust.IDENTITY_CURVE } })).toBe(true);
    expect(Array.from(adjust.applyData(p, {}))).toEqual(Array.from(p));
    const out = adjust.applyData(p, { exposure: 2, saturation: 100, contrast: 80 });
    for (let i = 3; i < p.length; i += 4) expect(out[i]).toBe(p[i]);
  });

  it("curves go through their points and never overshoot", () => {
    const pts: [number, number][] = [[0, 0], [64, 40], [192, 220], [255, 255]];
    const t = adjust.curveTable(pts);
    for (const [x, y] of pts) expect(t[x]).toBeCloseTo(y);
    for (let x = 1; x < 256; x++) expect(t[x]).toBeGreaterThanOrEqual(t[x - 1]);
    const flat = adjust.curveTable([[50, 20], [200, 230]]);
    expect([flat[0], flat[255]]).toEqual([20, 230]);
    expect(adjust.curveTable([[10, 10], [10, 99], [255, 255]])[10]).toBe(99);
  });

  it("auto levels stretches a flat photo", () => {
    const d = new Uint8ClampedArray(100 * 4);
    for (let i = 0; i < 100; i++) d.set(i < 50 ? [140, 140, 140, 255] : [100, 100, 100, 255], i * 4);
    const l = adjust.autoLevels(d);
    expect([l.black, l.white]).toEqual([100, 140]);
    const out = adjust.applyData(d, { levels: { rgb: l } });
    expect([out[0], out[99 * 4]]).toEqual([255, 0]);
    expect(adjust.autoLevels(new Uint8ClampedArray(36))).toEqual(adjust.NEUTRAL_LEVELS);
  });

  it("histogram skips transparent pixels", () => {
    const h = adjust.histogram(new Uint8ClampedArray([255, 0, 0, 255, 9, 9, 9, 0]));
    expect([h.r[255], h.r.reduce((a, b) => a + b, 0), h.rgb[76]]).toEqual([1, 1, 1]);
  });

  it("applies inside the selection as one undo step; preview leaves the layer alone", () => {
    const doc: EditorDoc = { width: 6, height: 4, active: 0, selection: new Rect(0, 0, 3, 4), dirty: false, layers: [makeLayer("bg", 6, 4, solid(6, 4, [100, 100, 100, 255]))] };
    const h = new History(doc);
    const prev = adjust.preview(doc, { brightness: 50 });
    expect(doc.layers[0].data[0]).toBe(100);
    h.push(adjust.applyToDocument(doc, { brightness: 50 }, "Quick fix"));
    const l = doc.layers[0];
    expect([l.data[0], l.data[5 * 4]]).toEqual([228, 100]);
    expect(Array.from(prev)).toEqual(Array.from(l.data));
    expect(h.undoLabel).toBe("Quick fix");
    h.undo();
    expect(doc.layers[0].data[0]).toBe(100);
    expect(adjust.applyToDocument(doc, {})).toBeNull();
  });
});
