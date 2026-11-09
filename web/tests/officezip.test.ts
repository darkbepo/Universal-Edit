import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import { describe, expect, it } from "vitest";
import { relativeOfficeRels } from "../src/engines/officezip";

const rels = (target: string) =>
  `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
  `<Relationship Id="rId1" Type="…/worksheet" Target="${target}"/></Relationships>`;

describe("relativeOfficeRels (Pandoc can't follow absolute part paths)", () => {
  it("makes openpyxl-style absolute targets relative to their folder", () => {
    const xlsx = zipSync({
      "_rels/.rels": strToU8(rels("/xl/workbook.xml")), // package level: stays absolute
      "xl/_rels/workbook.xml.rels": strToU8(rels("/xl/worksheets/sheet1.xml")),
      "xl/worksheets/sheet1.xml": strToU8("<worksheet/>"),
    });
    const out = unzipSync(relativeOfficeRels(xlsx));
    expect(strFromU8(out["xl/_rels/workbook.xml.rels"])).toContain('Target="worksheets/sheet1.xml"');
    expect(strFromU8(out["_rels/.rels"])).toContain('Target="/xl/workbook.xml"');
    expect(strFromU8(out["xl/worksheets/sheet1.xml"])).toBe("<worksheet/>");
  });

  it("leaves Excel-style (already relative) files and non-zips untouched", () => {
    const excel = zipSync({ "xl/_rels/workbook.xml.rels": strToU8(rels("worksheets/sheet1.xml")) });
    expect(relativeOfficeRels(excel)).toBe(excel);
    const junk = new Uint8Array([1, 2, 3]);
    expect(relativeOfficeRels(junk)).toBe(junk);
  });
});
