// Typed selection times (same cases as tests/test_audioedit.py).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/audioedit_time_cases.json";
import { editedSelection, parseTime } from "../src/audioedit/ops";

describe("parseTime", () => {
  for (const [text, want] of cases.parse as [string, number | null][]) {
    it(JSON.stringify(text), () => {
      const got = parseTime(text);
      if (want === null) expect(got).toBeNull();
      else expect(got).toBeCloseTo(want, 9);
    });
  }
});

describe("editedSelection", () => {
  for (const c of cases.edit) {
    it(c.name, () => {
      expect(editedSelection(c.which as "start" | "end" | "length", c.seconds, c.selection as [number, number], 1000, 10_000)).toEqual(c.want);
    });
  }
});
