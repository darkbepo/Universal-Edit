// The badges orbiting the empty drop zone: the web port gives the desktop's positions.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/orbit_cases.json";
import { layout } from "../src/ui/orbit";

describe("orbit layout", () => {
  for (const c of cases as { args: number[]; spots: Record<string, number>[] }[]) {
    it(`tau ${c.args[0]}, ${c.args[1]}×${c.args[2]}, pull ${c.args[5]}, burst ${c.args[6]}`, () => {
      const [tau, w, h, tw, th, pull, burst] = c.args;
      const got = layout(tau, w, h, tw, th, pull, burst);
      expect(got.length).toBe(c.spots.length);
      got.forEach((s, i) => {
        for (const k of ["x", "y", "scale", "alpha", "z"] as const) expect(s[k]).toBeCloseTo(c.spots[i][k], 9);
      });
    });
  }
});
