import { describe, expect, it } from "vitest";
import { plan } from "../src/converters/docroute";

const engines = (src: string, dst: string) => plan(src, dst)?.map((e) => e.engine);

describe("document routes", () => {
  it("Word → PDF / HTML skip Pandoc (mammoth.js is ~50× smaller to download)", () => {
    expect(engines("docx", "pdf")).toEqual(["mammoth", "mupdf"]);
    expect(engines("docx", "html")).toEqual(["mammoth"]);
  });

  it("everything else Word does still goes through Pandoc", () => {
    expect(engines("docx", "md")).toEqual(["pandoc"]);
    expect(engines("docx", "odt")).toEqual(["pandoc"]);
    expect(engines("md", "pdf")).toEqual(["pandoc", "mupdf"]);
  });
});
