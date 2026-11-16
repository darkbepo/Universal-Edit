// Changing document details: the same rules (and the same XML bytes) as the desktop's
// properties/document.py, via the shared case file; and the zip rewrite keeps every other entry.
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { describe, expect, it } from "vitest";
import casesText from "../../tests/fixtures/document_cases.json?raw";
import * as D from "../src/properties/document";

type Case<T> = T & { expect?: unknown; error?: string };
const cases = JSON.parse(casesText) as {
  clean: Case<{ values: D.Values }>[];
  keywords: Case<{ text: string }>[];
  unescape: Case<{ text: string }>[];
  core: Case<{ xml: string | null; values: D.Values }>[];
  odf: Case<{ xml: string | null; values: D.Values }>[];
  types: Case<{ xml: string }>[];
  rels: Case<{ xml: string }>[];
  manifest: Case<{ xml: string }>[];
  target: Case<{ xml: string | null }>[];
  details: Case<{ kind: string; parts: D.Parts }>[];
  changed: Case<{ kind: string; parts: D.Parts; values: D.Values }>[];
};

function check(c: { expect?: unknown; error?: string }, fn: () => unknown) {
  if (c.error !== undefined) expect(fn).toThrow(c.error);
  else expect(fn()).toEqual(c.expect);
}

describe("shared cases", () => {
  it("cleans values, splits keywords, unescapes", () => {
    for (const c of cases.clean) check(c, () => D.cleanValues(c.values));
    for (const c of cases.keywords) check(c, () => D.keywordList(c.text));
    for (const c of cases.unescape) check(c, () => D.unescape(c.text));
  });
  it("changes core.xml and meta.xml into the same bytes", () => {
    for (const c of cases.core) check(c, () => D.coreXml(c.xml, c.values));
    for (const c of cases.odf) check(c, () => D.odfMeta(c.xml, c.values));
  });
  it("adds the package parts a new details part needs", () => {
    for (const c of cases.types) check(c, () => D.contentTypes(c.xml));
    for (const c of cases.rels) check(c, () => D.packageRels(c.xml));
    for (const c of cases.manifest) check(c, () => D.manifest(c.xml));
    for (const c of cases.target) check(c, () => D.coreTarget(c.xml));
    for (const c of cases.details) check(c, () => D.detailsFromParts(c.kind, c.parts));
    for (const c of cases.changed) check(c, () => D.changedParts(c.kind, c.parts, c.values));
  });
});

const core = cases.core[0].xml!;
const odfMeta = cases.odf[0].xml!;

describe("zip rewrite", () => {
  it("keeps every other entry byte for byte and in order", () => {
    const src = zipSync({
      "[Content_Types].xml": strToU8('<Types xmlns="x"></Types>'),
      "_rels/.rels": strToU8('<Relationships xmlns="x"></Relationships>'),
      "word/document.xml": strToU8("<w:document/>".repeat(50)),
      "word/media/pic.png": [new Uint8Array([1, 2, 3, 4]), { level: 0 }],
      "docProps/core.xml": strToU8(core),
    });
    const out = D.saveZip(src, "office", { title: "Neu ☃", keywords: "a, b" }, "a.docx");
    const files = unzipSync(out);
    expect(Object.keys(files)).toEqual(Object.keys(unzipSync(src)));
    expect(strFromU8(files["word/document.xml"])).toBe("<w:document/>".repeat(50));
    expect([...files["word/media/pic.png"]]).toEqual([1, 2, 3, 4]);
    const d = D.readZipDetails(out, "office", "a.docx");
    expect([d.title, d.author, d.keywords]).toEqual(["Neu ☃", "Ann", "a, b"]);
    // the entries before the changed one are the very same bytes
    const before = strFromU8(src, true).indexOf("docProps/core.xml") - 30; // its local header
    expect(out.subarray(0, before)).toEqual(src.subarray(0, before));
  });

  it("adds core.xml with its relationship and content type when missing", () => {
    const src = zipSync({
      "[Content_Types].xml": strToU8('<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>'),
      "_rels/.rels": strToU8('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'),
    });
    const out = D.saveZip(src, "office", { title: "Made" }, "a.xlsx");
    const files = unzipSync(out);
    expect(Object.keys(files)).toEqual(["[Content_Types].xml", "_rels/.rels", "docProps/core.xml"]);
    expect(strFromU8(files["_rels/.rels"])).toContain('Target="docProps/core.xml"');
    expect(D.readZipDetails(out, "office", "a.xlsx").title).toBe("Made");
  });

  it("keeps ODF's mimetype first and stored", () => {
    const src = zipSync({
      mimetype: [strToU8("application/vnd.oasis.opendocument.text"), { level: 0 }],
      "content.xml": strToU8("<x/>"),
      "meta.xml": strToU8(odfMeta),
    });
    const out = D.saveZip(src, "odf", { keywords: "x; y" }, "a.odt");
    expect(strFromU8(out.subarray(30, 38))).toBe("mimetype");
    expect(new DataView(out.buffer).getUint16(8, true)).toBe(0); // stored
    expect(D.readZipDetails(out, "odf", "a.odt").keywords).toBe("x, y");
  });

  it("refuses what isn't a zip in plain words", () => {
    expect(() => D.readZipDetails(new Uint8Array(100), "office", "b.docx")).toThrow("can't be read as a document");
    expect(() => D.cleanValues({ title: "x".repeat(2001) })).toThrow("2000");
  });
});
