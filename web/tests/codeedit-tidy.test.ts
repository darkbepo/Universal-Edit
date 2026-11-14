// Tidy for the code editor — same cases as tests/test_codeedit_tidy.py.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/codeedit_tidy_cases.json";
import { TIDY, TidyError, tidy } from "../src/codeedit/tidy";

type Case = { id: string; language: string; text: string; result?: string; error?: boolean };

describe("code editor tidy (website)", () => {
  it.each(cases as Case[])("$id", (c) => {
    if (c.error) expect(() => tidy(c.language, c.text)).toThrow(TidyError);
    else expect(tidy(c.language, c.text)).toBe(c.result);
  });

  it("tidying twice changes nothing", () => {
    for (const c of cases as Case[]) {
      if (c.error) continue;
      const once = tidy(c.language, c.text);
      expect(tidy(c.language, once), c.id).toBe(once);
    }
  });

  it("tidies the same languages as the desktop", () => {
    expect([...TIDY].sort()).toEqual(["json", "svg", "xml"]);
  });
});
