import { describe, expect, it } from "vitest";
import { AudioDoc } from "../src/audioedit/document";
import * as record from "../src/audioedit/record";

describe("recording (same cases as tests/test_audioedit_record.py)", () => {
  it("keeps the sound within the limit", () => {
    expect(record.room(1000, 0, 0, 0, 2)).toBe(500);
    expect(record.room(1000, 300, 1, 0, 2)).toBe(700);
    expect(record.room(1000, 300, 2, 100, 1)).toBe(300);
    expect(record.room(1000, 600, 2, 0, 2)).toBe(0);
  });

  it("collects chunks until full", () => {
    const take = new record.Take(8000, 2, 10);
    expect(take.add([new Float32Array(4).fill(0.25), new Float32Array(4).fill(0.25)])).toBe(true);
    expect(take.level).toBeCloseTo(0.25);
    expect(take.add([new Float32Array(8).fill(0.1), new Float32Array(8).fill(-0.75)])).toBe(false);
    expect(take.frames).toBe(10);
    expect(take.full).toBe(true);
    expect(take.level).toBeCloseTo(0.75);
    const s = take.samples();
    expect(s.length).toBe(2);
    expect(s[0].length).toBe(10);
    expect(s[1][9]).toBeCloseTo(-0.75);
    expect(take.add([new Float32Array(3).fill(1), new Float32Array(3).fill(1)])).toBe(false);
    expect(take.frames).toBe(10);
    expect(take.seconds).toBeCloseTo(10 / 8000);
    expect(new record.Take(8000, 1, 10).samples()[0].length).toBe(0);
  });

  it("goes in at the cursor as one step", () => {
    const doc = new AudioDoc(8000, [new Float32Array(100), new Float32Array(100)]);
    doc.setSelection(40);
    expect(doc.paste([new Float32Array(10).fill(1), new Float32Array(10).fill(1)], record.LABEL)).toBe(true);
    expect(doc.frames).toBe(110);
    expect(doc.selection).toEqual([40, 50]);
    expect(doc.undoLabel()).toBe("Record");
    doc.undo();
    expect(doc.frames).toBe(100);
  });
});
