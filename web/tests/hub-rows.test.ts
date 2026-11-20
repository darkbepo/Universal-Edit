import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/hub_rows_cases.json";
import { hubRows, hubTileWidth } from "../src/core/editors";

describe("start-screen tile rows (same cases as tests/test_editor_hub.py)", () => {
  for (const c of cases) {
    it(`${c.count} tiles in ${c.width} px → ${c.rows.join(" + ") || "none"}`, () => {
      const rows = hubRows(c.count, c.width);
      expect(rows).toEqual(c.rows);
      expect(hubTileWidth(rows, c.width)).toBeCloseTo(c.tile, 3);
    });
  }
  it("a tile is alone only when two fit across and the count is odd (then it's centred)", () => {
    for (let n = 2; n <= 30; n++) for (let w = 260; w <= 3000; w += 37) {
      const rows = hubRows(n, w);
      expect(rows.reduce((a, b) => a + b, 0)).toBe(n);
      if (Math.min(...rows) === 1) expect(Math.max(...rows) <= 2).toBe(true);
      expect(Math.max(...rows) - Math.min(...rows)).toBeLessThanOrEqual(1);
    }
  });
});
