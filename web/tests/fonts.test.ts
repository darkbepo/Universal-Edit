import { describe, expect, it } from "vitest";
import { canonicalExt, familyOfExt } from "../src/core/detect";
import type { InputFile } from "../src/core/types";
import { fontConverter, LATIN, SUBSETS } from "../src/converters/fonts";
import { flavorOf, parseUnicodes, sfntToWoff, woffToSfnt } from "../src/engines/fonts";
import py from "../../src/umc/converters/fonts.py?raw";

/** A minimal SFNT with three fake tables (enough to test the container code). */
function fakeSfnt(): Uint8Array {
  const tables: [string, Uint8Array][] = [
    ["head", new Uint8Array(54).fill(7)],
    ["cmap", new TextEncoder().encode("x".repeat(1000))], // compressible
    ["name", new Uint8Array([1, 2, 3])], // tiny: stored, and needs padding
  ];
  const n = tables.length;
  const size = 12 + 16 * n + tables.reduce((s, [, d]) => s + ((d.length + 3) & ~3), 0);
  const out = new Uint8Array(size);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x00010000);
  dv.setUint16(4, n);
  dv.setUint16(6, 32); dv.setUint16(8, 1); dv.setUint16(10, 16);
  const sorted = [...tables].sort(([a], [b]) => (a < b ? -1 : 1));
  let off = 12 + 16 * n;
  sorted.forEach(([tag, data], i) => {
    for (let k = 0; k < 4; k++) dv.setUint8(12 + i * 16 + k, tag.charCodeAt(k));
    dv.setUint32(12 + i * 16 + 4, 0x12345678 + i);
    dv.setUint32(12 + i * 16 + 8, off);
    dv.setUint32(12 + i * 16 + 12, data.length);
    out.set(data, off);
    off += (data.length + 3) & ~3;
  });
  return out;
}

describe("fonts", () => {
  it("WOFF round trip is byte-identical", async () => {
    const sfnt = fakeSfnt();
    const woff = await sfntToWoff(sfnt);
    expect(flavorOf(woff)).toBe("woff");
    expect(woff.length).toBeLessThan(sfnt.length);
    expect(await woffToSfnt(woff)).toEqual(sfnt);
  });

  it("parses unicode ranges", () => {
    expect(parseUnicodes("U+0041-0043,U+20AC")).toEqual([0x41, 0x42, 0x43, 0x20ac]);
    expect(parseUnicodes(LATIN)).toContain(0xe6); // æ
  });

  it("uses the same character subsets as the desktop app", () => {
    // NAME = [LATIN + ] "…" (possibly in parentheses over several lines) — join the quoted pieces
    const value = (name: string) => {
      const m = new RegExp(`^${name} = ([\\s\\S]*?)(?=\\n[A-Z_]+ = |\\n\\n)`, "m").exec(py)!;
      const text = [...m[1].matchAll(/"([^"]*)"/g)].map((q) => q[1]).join("");
      return (m[1].startsWith("LATIN +") ? LATIN : "") + text;
    };
    expect(value("LATIN")).toBe(LATIN);
    expect(value("LATIN_EXT")).toBe(SUBSETS["latin-ext"]);
    expect(value("CYRILLIC")).toBe(SUBSETS.cyrillic);
    expect(value("GREEK")).toBe(SUBSETS.greek);
  });

  it("never offers TTF ↔ OTF outline changes in the browser", () => {
    const f = (name: string): InputFile => ({ file: new File([], name), name, ext: canonicalExt(name), family: familyOfExt(canonicalExt(name))! });
    expect(fontConverter.targets([f("a.ttf")]).map((t) => t.id)).toEqual(["woff", "woff2"]);
    expect(fontConverter.targets([f("a.otf")]).map((t) => t.id)).toEqual(["woff", "woff2"]);
    expect(fontConverter.targets([f("a.woff2")]).map((t) => t.id)).toEqual(["ttf", "otf", "woff"]);
  });
});
