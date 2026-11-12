// Properties: the web port gives the same text as the desktop (shared case files).
import { describe, expect, it } from "vitest";
import formatCases from "../../tests/fixtures/properties_format_cases.json";
import cases from "../../tests/fixtures/properties_cases.json";
import * as fmt from "../src/properties/format";
import { build } from "../src/properties/info";

const FN: Record<string, (...args: number[]) => string> = {
  size: fmt.size, size_exact: fmt.sizeExact, duration: fmt.duration, exposure: fmt.exposure,
  aperture: fmt.aperture, focal: fmt.focal, location: fmt.location, pixels: fmt.pixels,
  megapixels: fmt.megapixels, fps: fmt.fps, bitrate: fmt.bitrate, sample_rate: fmt.sampleRate,
  channels: fmt.channels, paper: fmt.paper,
};

describe("properties format", () => {
  for (const [name, items] of Object.entries(formatCases as Record<string, (number | string)[][]>)) {
    it(name, () => {
      expect(FN[name], `format.${name} is ported`).toBeTypeOf("function");
      for (const item of items) {
        const args = item.slice(0, -1) as number[];
        expect(FN[name](...args), `${name}(${args})`).toBe(item[item.length - 1]);
      }
    });
  }
  it("dates in local time", () => {
    expect(fmt.date(new Date(2026, 10, 12, 14, 3, 27))).toBe("2026-11-12 14:03");
  });
});

describe("properties sections", () => {
  for (const c of cases as { name: string; facts: Record<string, unknown>; sections: unknown }[]) {
    it(c.name, () => expect(build(c.facts)).toEqual(c.sections));
  }
});

describe("properties readers (no engine)", async () => {
  const r = await import("../src/properties/read");
  it("dates", () => {
    expect(r.pdfDate("D:20261112140300")).toBe("2026-11-12 14:03");
    expect(r.pdfDate("nonsense")).toBeUndefined();
    expect(r.pdfDate("D:20260230120000")).toBeUndefined(); // no 30 February (the desktop agrees)
    expect(r.pdfDate("D:20261112140300Z")).toBe(fmt.date(new Date(Date.UTC(2026, 10, 12, 14, 3))));
    expect(r.pdfDate("D:20261112140300+01'00'")).toBe(fmt.date(new Date(Date.UTC(2026, 10, 12, 13, 3))));
    expect(r.exifDate("2026:11:12 14:03:27")).toBe("2026-11-12 14:03");
    expect(r.isoLocal("2026-11-01T09:00:00Z")).toBe(fmt.date(new Date(Date.UTC(2026, 10, 1, 9))));
    expect(r.iso6709("+55.6761+012.5683/")).toEqual([55.6761, 12.5683]);
    expect(r.iso6709("-33.86+151.21+010.000/")).toEqual([-33.86, 151.21]);
  });
  it("EXIF numbers", () => {
    expect(r.rational("1/120")).toBeCloseTo(1 / 120);
    expect(r.rational("200")).toBe(200);
    expect(r.rational("1/0")).toBeUndefined();
    expect(r.dms("55/1, 40/1, 3396/100")).toBeCloseTo(55.6761, 5);
  });
  it("Word and ODF details (the desktop's test files)", () => {
    const core = '<cp:coreProperties xmlns:cp="x" xmlns:dc="y" xmlns:dcterms="z"><dc:title>Letter</dc:title><dc:creator>Ada</dc:creator><cp:lastModifiedBy>Bob</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-11-01T09:00:00Z</dcterms:created></cp:coreProperties>';
    const app = "<Properties><Pages>3</Pages><Words>1234</Words><Company>Acme</Company><Application>Microsoft Office Word</Application><TitlesOfParts><vt:lpstr>x</vt:lpstr></TitlesOfParts></Properties>";
    expect(r.officeFacts(core, app)).toEqual({ title: "Letter", author: "Ada", last_saved_by: "Bob", created: r.isoLocal("2026-11-01T09:00:00Z"), company: "Acme", application: "Microsoft Office Word", pages: 3, words: 1234 });
    const meta = '<office:document-meta><office:meta><dc:title>Notes</dc:title><meta:initial-creator>Ada</meta:initial-creator><meta:generator>LibreOffice/7.6</meta:generator><meta:document-statistic meta:page-count="2" meta:word-count="40"/></office:meta></office:document-meta>';
    expect(r.odfFacts(meta)).toEqual({ title: "Notes", author: "Ada", application: "LibreOffice", pages: 2, words: 40 });
  });
  it("font names", async () => {
    // no Node types in this project: load fs untyped (tests run in Node)
    const { readFileSync } = (await import(/* @vite-ignore */ `node:${"fs"}`)) as { readFileSync(p: string): Uint8Array };
    const f = r.fontFacts(new Uint8Array(readFileSync("node_modules/dejavu-fonts-ttf/ttf/DejaVuSans.ttf")));
    expect(f).toMatchObject({ family: "DejaVu Sans", style: "Book", outlines: "ttf" });
    expect(f.glyphs).toBeGreaterThan(1000);
    expect(r.bestName([[1, 0, 0, 1, "Mac"], [3, 1, 0x406, 1, "Dansk"], [3, 1, 0x409, 1, "English"]], 1)).toBe("English");
  });
});
