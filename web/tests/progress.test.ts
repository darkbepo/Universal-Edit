import { describe, expect, it } from "vitest";
import { fileFractions, rowStates } from "../src/core/progress";
import { groupIcon } from "../src/ui/icons";

describe("per-file progress", () => {
  it("splits the overall fraction into equal slices", () => {
    expect(fileFractions(0, 3)).toEqual([0, 0, 0]);
    expect(fileFractions(0.5, 2)).toEqual([1, 0]);
    expect(fileFractions(0.6, 4).map((f) => Math.round(f * 100) / 100)).toEqual([1, 1, 0.4, 0]);
    expect(fileFractions(1, 3)).toEqual([1, 1, 1]);
    expect(fileFractions(1.7, 1)).toEqual([1]);
    expect(fileFractions(-1, 1)).toEqual([0]);
    expect(fileFractions(0.5, 0)).toEqual([]);
  });
  it("marks finished, current and waiting rows", () => {
    expect(rowStates([0, 0, 0])).toEqual(["running", "waiting", "waiting"]);
    expect(rowStates([1, 0.5, 0])).toEqual(["done", "running", "waiting"]);
    expect(rowStates([1, 1, 0])).toEqual(["done", "done", "running"]);
    expect(rowStates([1, 1])).toEqual(["done", "done"]);
  });
});

describe("icons", () => {
  it("pick an icon from the target group, like the desktop app", () => {
    expect(groupIcon("Image")).toBe("photo");
    expect(groupIcon("Slideshow video")).toBe("slideshow");
    expect(groupIcon("Images (one per page)")).toBe("files");
    expect(groupIcon("OCR (recognise text)")).toBe("scan");
    expect(groupIcon("PDF tools")).toBe("pdf");
    expect(groupIcon("Something new")).toBe("file");
  });
});
