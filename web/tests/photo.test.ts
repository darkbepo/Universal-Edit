// Changing photo details: the web port writes the desktop's exact bytes (shared case file).
import { describe, expect, it } from "vitest";
import cases from "../../tests/fixtures/photo_edit_cases.json";
import * as photo from "../src/properties/photo";
import * as xmp from "../src/properties/xmp";

const hex = (b: Uint8Array) => [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
const bytes = (h: string) => Uint8Array.from(h.match(/../g) ?? [], (x) => parseInt(x, 16));

describe("photo details: same bytes as the desktop", () => {
  for (const c of cases as { name: string; input: string; edit?: photo.PhotoEdit; output?: string; details: photo.PhotoDetails }[]) {
    it(c.name, () => {
      let data: Uint8Array = bytes(c.input);
      if (c.edit) {
        const out = photo.apply(data, c.edit);
        expect(hex(out)).toBe(c.output);
        data = out;
      }
      expect(photo.readDetails(data)).toEqual(c.details);
    });
  }
});

describe("photo details: rules", () => {
  it("dates", () => {
    expect(photo.exifDate("2026-11-12 14:03")).toBe("2026:11:12 14:03:00");
    expect(photo.exifDate("2026-1-2")).toBe("2026:01:02 00:00:00");
    for (const bad of ["12/11/2026", "2026-02-30 10:00", "2026-13-01", "2026-11-12 25:00"]) expect(() => photo.exifDate(bad)).toThrow(photo.PhotoError);
  });
  it("tags", () => expect(photo.tagsText(" a, b;;c , ")).toBe("a; b; c"));
  it("xmp removal (the desktop's test)", () => {
    const packet = '<rdf:Description dc:x="1" tiff:Orientation="6" exif:GPSLatitude="1" exif:GPSLongitude="2"><dc:title><rdf:Alt><rdf:li>T &amp; U</rdf:li></rdf:Alt></dc:title><dc:creator/></rdf:Description>';
    expect(xmp.get(packet, "dc:title")).toBe("T & U");
    expect(xmp.remove(packet, ["dc:title", "dc:creator", "tiff:Orientation", "exif:GPS*"])).toBe('<rdf:Description dc:x="1"></rdf:Description>');
  });
  it("refuses what it can't change", () => {
    expect(() => photo.apply(new TextEncoder().encode("GIF89a...."), { values: { title: "x" } })).toThrow(photo.PhotoError);
    expect(photo.canChange("JPG")).toBe(true);
    expect(photo.canChange("heic")).toBe(false);
  });
});

describe("photo form lists", () => {
  it("match the photo code", async () => {
    const form = await import("../src/ui/photoform");
    expect(form.FIELDS).toEqual([...photo.FIELDS]);
    expect(form.ROTATIONS).toEqual(photo.ROTATIONS);
  });
});
