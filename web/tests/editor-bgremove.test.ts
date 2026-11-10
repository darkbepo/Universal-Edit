import { describe, expect, it } from "vitest";
import { bgremoveConverter, MASK_TARGET } from "../src/converters/bgremove";
import { targetsFor } from "../src/core/registry";
import type { InputFile } from "../src/core/types";
import type { EditorDoc } from "../src/editor/document";
import { History } from "../src/editor/history";
import { makeLayer, solid } from "../src/editor/layer";
import { maskFrom } from "../src/editor/ops";

// mirrors tests/test_editor_bgremove.py
describe("background removal", () => {
  it("sets and combines the layer mask, keeping the pixels", () => {
    const doc: EditorDoc = { width: 4, height: 2, layers: [makeLayer("bg", 4, 2, solid(4, 2, [9, 9, 9, 255]))], active: 0, selection: null, dirty: false };
    const h = new History(doc);
    const a = new Uint8Array(8).fill(255);
    a[0] = 0;
    h.push(maskFrom(doc, a));
    const l = () => doc.layers[0];
    expect(l().mask?.[0]).toBe(0);
    expect(Array.from(l().data.subarray(0, 4))).toEqual([9, 9, 9, 255]);
    const b = new Uint8Array(8).fill(255);
    b[1] = 0;
    h.push(maskFrom(doc, b));
    expect([l().mask?.[0], l().mask?.[4], l().mask?.[8]]).toEqual([0, 0, 255]);
    h.undo();
    h.undo();
    expect(l().mask ?? null).toBeNull();
    expect(() => maskFrom(doc, new Uint8Array(3))).toThrow();
  });

  it("is never offered on the convert page", () => {
    const f = { name: "a.png", ext: "png", family: "image" } as InputFile;
    expect(bgremoveConverter.targets([f])).toEqual([]);
    expect(targetsFor([f]).targets.some((t) => t.converterId === "bgremove")).toBe(false);
    expect(MASK_TARGET.converterId).toBe(bgremoveConverter.id);
  });
});
