import { describe, expect, it } from "vitest";
import { composite, pasteboard, type EditorDoc } from "../src/editor/document";
import { Rect } from "../src/editor/geometry";
import { makeLayer, solid } from "../src/editor/layer";

// mirrors tests/test_editor_pasteboard.py
const doc = (x = 80, y = -10, w = 40, h = 20, extra = {}): EditorDoc => ({
  width: 100,
  height: 80,
  layers: [makeLayer("bg", 100, 80, solid(100, 80, [255, 255, 255, 255])), makeLayer("red", w, h, solid(w, h, [255, 0, 0, 255]), { x, y, ...extra })],
  active: 1,
  selection: null,
  dirty: false,
});

describe("pasteboard", () => {
  it("covers layers around the canvas", () => {
    expect(pasteboard(doc()).equals(new Rect(0, -10, 120, 80))).toBe(true);
    expect(pasteboard(doc(10, 10)).equals(new Rect(0, 0, 100, 80))).toBe(true);
  });

  it("is limited and rounded", () => {
    expect(pasteboard(doc(-90, 5, 100, 10)).equals(new Rect(-50, 0, 100, 80))).toBe(true);
    expect(pasteboard(doc(), 16).equals(new Rect(0, -16, 132, 80))).toBe(true);
  });

  it("ignores hidden layers", () => {
    expect(pasteboard(doc(80, -10, 40, 20, { visible: false })).equals(new Rect(0, 0, 100, 80))).toBe(true);
  });

  it("saving uses only the canvas; the view shows the rest", () => {
    const d = doc();
    expect(composite(d).length).toBe(100 * 80 * 4);
    const view = composite(d, new Rect(0, -10, 120, 80), undefined, false);
    expect(view.length).toBe(120 * 90 * 4);
    expect(Array.from(view.subarray(110 * 4, 110 * 4 + 4))).toEqual([255, 0, 0, 255]);
    expect(composite(d, new Rect(0, -10, 120, 80)).length).toBe(100 * 80 * 4);
  });
});

describe("pasteboard memory budget", () => {
  it("shrinks the margin on big pictures", async () => {
    const { pasteboardMargin } = await import("../src/editor/document");
    expect(pasteboardMargin(100, 80, 16_000_000)).toBe(50);
    const m = pasteboardMargin(4000, 3000, 16_000_000);
    expect(m > 0 && (4000 + 2 * m) * (3000 + 2 * m) <= 16_000_000).toBe(true);
    expect(pasteboardMargin(5000, 4000, 16_000_000)).toBe(0);
  });
});
