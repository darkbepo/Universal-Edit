// Code files are accepted as the "code" family — same cases as tests/test_detect_code.py.
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/detect_code_cases.json";
import { detectBytes, familyOfExt } from "../src/core/detect";
import { targetsFor } from "../src/core/registry";
import type { InputFile } from "../src/core/types";

const bytes = (b64: string) => Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));

describe("code files", () => {
  it.each(cases as { id: string; name: string; bytes_b64: string; family: string | null; ext: string | null }[])(
    "shared case $id",
    (c) => {
      const want = c.family === null ? null : { family: c.family, ext: c.ext };
      expect(detectBytes(c.name, bytes(c.bytes_b64).slice(0, 8192))).toEqual(want);
    },
  );

  it("keeps other families", () => {
    expect(familyOfExt("css")).toBe("code");
    expect(familyOfExt("html")).toBe("document");
  });

  it("offers only packing", () => {
    const f = { file: new File(["a{}"], "a.css"), name: "a.css", ext: "css", family: "code" } as InputFile;
    expect(new Set(targetsFor([f]).targets.map((t) => t.id))).toEqual(new Set(["pack:zip", "pack:tar.gz"]));
  });
});
