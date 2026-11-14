// The website code editor's colours use the same kinds and CSS variables as the desktop app.
// (tests/test_theme.py checks styles.css has every --code-* colour)
import { describe, expect, it } from "vitest";
import { KINDS, KIND_TAGS, codeHighlight } from "../src/ui/codeeditor/setup";

describe("code editor colours (website)", () => {
  it("has the desktop's kinds", () => {
    // src/umc/codeedit/lexing.py KINDS
    expect(KINDS).toEqual(["keyword", "string", "comment", "number", "function", "type", "tag", "attribute", "operator", "builtin"]);
  });

  it("styles every kind with its CSS variable", () => {
    for (const kind of KINDS) {
      expect(KIND_TAGS[kind].length).toBeGreaterThan(0);
      const spec = codeHighlight.specs.find((s) => s.color === `var(--code-${kind})`);
      expect(spec, kind).toBeTruthy();
    }
  });
});
