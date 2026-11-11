import { describe, expect, test } from "vitest";
import {
  escAttr,
  escText,
  firstWord,
  pyG,
  pyInt,
  pyRound,
  pyRoundTo,
  pyStrip,
} from "../src/doceditor/pycompat";

// Expected values are what CPython 3 prints for the same calls.
describe("Python compatibility helpers", () => {
  test("round(x) and round(x, 2)", () => {
    expect([0.5, 1.5, 2.5, -1.5, -2.5, 2.4, 2.6].map(pyRound)).toEqual([0, 2, 2, -2, -2, 2, 3]);
    const cases: [number, number][] = [
      [0.125, 0.12],
      [0.375, 0.38],
      [2.675, 2.67],
      [1.005, 1.0],
      [-0.125, -0.12],
      [12.345, 12.35],
      [1.115, 1.11],
      [0.045, 0.04],
      [15.5, 15.5],
      [1e-9, 0],
    ];
    for (const [x, want] of cases) expect(pyRoundTo(x, 2)).toBe(want);
  });

  test('f"{x:g}"', () => {
    const cases: [number, string][] = [
      [0, "0"],
      [10.5, "10.5"],
      [12, "12"],
      [15.25, "15.25"],
      [1234567, "1.23457e+06"],
      [0.00001, "1e-05"],
      [123456.7, "123457"],
      [-3.5, "-3.5"],
    ];
    for (const [x, want] of cases) expect(pyG(x)).toBe(want);
  });

  test("int(s)", () => {
    expect([" 12 ", "+3", "1_0", "-7"].map(pyInt)).toEqual([12, 3, 10, -7]);
    expect(["1__0", "12a", "", " "].map(pyInt)).toEqual([null, null, null, null]);
  });

  test("strip, split, html.escape", () => {
    expect(pyStrip(" \x1c a \t\n")).toBe("a");
    expect(pyStrip("﻿a")).toBe("﻿a"); // not white space in Python
    expect(firstWord(" 　 PAGE \\* MERGEFORMAT")).toBe("PAGE");
    expect(firstWord("   ")).toBe("");
    expect(escText(`a&<>"'`)).toBe(`a&amp;&lt;&gt;"'`);
    expect(escAttr(`a&<>"'`)).toBe("a&amp;&lt;&gt;&quot;&#x27;");
  });
});
