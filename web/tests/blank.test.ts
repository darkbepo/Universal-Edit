import { describe, expect, it } from "vitest";
import { blankDocument } from "../src/editor/blank";
import { EDITORS } from "../src/core/editors";

describe("new picture", () => {
  it("fills the background layer", () => {
    const doc = blankDocument(3, 2, "white");
    expect([doc.width, doc.height, doc.layers.length]).toEqual([3, 2, 1]);
    expect(Array.from(doc.layers[0].data)).toEqual(new Array(6).fill([255, 255, 255, 255]).flat());
    expect(Array.from(blankDocument(1, 1, "black").layers[0].data)).toEqual([0, 0, 0, 255]);
    expect(Array.from(blankDocument(1, 1, "transparent").layers[0].data)).toEqual([0, 0, 0, 0]);
  });
  it("refuses impossible sizes", () => {
    expect(() => blankDocument(0, 10)).toThrow();
    expect(() => blankDocument(20000, 10)).toThrow();
  });
});

describe("editor tiles", () => {
  it("only the image editor and 3D viewer are ready", () => {
    expect(EDITORS.filter((e) => e.available).map((e) => e.id)).toEqual(["image", "model"]);
  });
});
