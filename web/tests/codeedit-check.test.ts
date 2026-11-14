// JSON / YAML / XML checks for the code editor — same cases as tests/test_codeedit_check.py.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/codeedit_check_cases.json";
import { CHECKED, check } from "../src/codeedit/check";

type Case = { id: string; language: string; text: string; ok?: boolean; line?: number; column?: number };

describe("code editor checks (website)", () => {
  it.each(cases as Case[])("$id", (c) => {
    const problems = check(c.language, c.text);
    if (c.ok) {
      expect(problems).toEqual([]);
      return;
    }
    expect(problems.length).toBeGreaterThan(0);
    expect(problems[0].line).toBe(c.line);
    if (c.column !== undefined) expect(problems[0].column).toBe(c.column);
    expect(problems[0].message).not.toMatch(/\n|Error:/);
  });

  it("very deep JSON is a problem, not a crash", () => {
    const problems = check("json", "[".repeat(5000) + "]".repeat(5000));
    expect(problems.length).toBe(1);
    expect(problems[0].message).toMatch(/deep/);
    expect(check("json", "[".repeat(200) + "]".repeat(200))).toEqual([]);
  });

  it("checks the same languages as the desktop", () => {
    expect([...CHECKED].sort()).toEqual(["json", "svg", "xml", "yaml"]);
  });
});
