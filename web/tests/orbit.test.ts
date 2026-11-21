// The badges orbiting the empty drop zone: the web port gives the desktop's positions.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/orbit_cases.json";
import { layout } from "../src/ui/orbit";

describe("orbit layout", () => {
  for (const c of cases as { args: (number | boolean)[]; spots: Record<string, number>[] }[]) {
    it(`tau ${c.args[0]}, ${c.args[1]}×${c.args[2]}, pull ${c.args[5]}, burst ${c.args[6]}, still ${c.args[7] ?? false}`, () => {
      const [tau, w, h, tw, th, pull, burst] = c.args as number[];
      const got = layout(tau, w, h, tw, th, pull, burst, (c.args[7] as boolean | undefined) ?? false);
      expect(got.length).toBe(c.spots.length);
      got.forEach((s, i) => {
        for (const k of ["x", "y", "scale", "alpha", "z"] as const) expect(s[k]).toBeCloseTo(c.spots[i][k], 9);
        expect(s.badge).toBe(c.spots[i].badge);
      });
    });
  }
});

it("no file type is ever on screen twice", () => {
  for (let step = 0; step < 7200; step++) {
    const shown = layout(step * 0.5, 1640, 570, 330, 110).filter((s) => s.alpha > 0).map((s) => s.badge);
    expect(new Set(shown).size).toBe(shown.length);
  }
});
