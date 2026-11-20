import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/audioedit_speed_cases.json";
import * as speed from "../src/audioedit/speed";
import { AudioDoc } from "../src/audioedit/document";

describe("speed / tempo / pitch (shared cases with the desktop app)", () => {
  for (const c of cases.cases) {
    it(`${c.mode} ${c.value} at ${c.rate} Hz`, () => {
      const mode = c.mode as speed.SpeedMode;
      expect(speed.filterChain(mode, c.value, c.rate)).toBe(c.filter);
      expect(Math.abs(speed.newSeconds(mode, c.value, 10, c.rate) - c.seconds)).toBeLessThan(1e-9);
      expect(speed.isIdentity(mode, c.value)).toBe(c.identity);
      expect(speed.expectedFrames(mode, c.value, 123457, c.rate)).toBe(c.frames);
    });
  }

  it("chains atempo within 0.5…2", () => {
    for (const f of [0.25, 0.3, 0.75, 3, 4]) {
      const parts = speed.atempoChain(f).map((p) => Number(p.split("=")[1]));
      expect(parts.every((x) => x >= 0.5 && x <= 2)).toBe(true);
      expect(parts.reduce((a, b) => a * b, 1)).toBeCloseTo(f, 5);
    }
  });
});

describe("AudioDoc.replacePart", () => {
  it("swaps in a part of another length, selects it, and undoes in one step", () => {
    const doc = new AudioDoc(1000, [Float32Array.from({ length: 10 }, (_, i) => i)]);
    expect(doc.replacePart("Change tempo", 2, 6, [Float32Array.of(9, 9)])).toBe(true);
    expect(Array.from(doc.samples[0])).toEqual([0, 1, 9, 9, 6, 7, 8, 9]);
    expect(doc.selection).toEqual([2, 4]);
    expect(doc.undoLabel()).toBe("Change tempo");
    doc.undo();
    expect(Array.from(doc.samples[0])).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
    expect(doc.replacePart("x", 3, 3, [Float32Array.of(1)])).toBe(false);
  });
});
