// Byte-exact text files for the code editor (same cases as tests/test_codeedit_textfile.py).
import { describe, expect, it } from "vitest";
import fixture from "../../tests/fixtures/codeedit_cases.json";
import { decode, encode, isBinary, looksLikeText } from "../src/codeedit/textfile";

type Case = { id: string; bytes_b64: string; looks_like_text: boolean; text?: string; encoding?: string; bom?: boolean; newline?: string; mixed?: boolean };
const cases = fixture.cases as Case[];
const byId = new Map(cases.map((c) => [c.id, c]));
const bytes = (b64: string) => Uint8Array.from(atob(b64), (ch) => ch.charCodeAt(0));
const withText = cases.filter((c) => c.text !== undefined);

describe("codeedit textfile", () => {
  it.each(cases)("looksLikeText $id", (c) => {
    expect(looksLikeText(bytes(c.bytes_b64).slice(0, 8192))).toBe(c.looks_like_text);
  });

  it.each(withText)("decode $id", (c) => {
    const f = decode(bytes(c.bytes_b64));
    expect([f.text, f.encoding, f.bom, f.newline, f.mixed]).toEqual([c.text, c.encoding, c.bom, c.newline, c.mixed]);
  });

  it.each(withText)("untouched round trip $id", (c) => {
    const data = bytes(c.bytes_b64);
    const f = decode(data);
    expect(encode(f, f.text)).toEqual(data);
  });

  it.each(fixture.resaves as { id: string; from: string; newline?: string; bom?: boolean; expect_b64: string }[])("resave $id", (r) => {
    const f = decode(bytes(byId.get(r.from)!.bytes_b64));
    const got = encode(f, f.text, { newline: r.newline as "\n" | undefined, bom: r.bom });
    expect(got).toEqual(bytes(r.expect_b64));
  });

  it("edited text uses the file's newline", () => {
    const f = decode(new TextEncoder().encode("one\r\ntwo\r\n"));
    expect(new TextDecoder().decode(encode(f, "one\ntwo\nthree\n"))).toBe("one\r\ntwo\r\nthree\r\n");
  });

  it("saves as Windows-1252", () => {
    const f = decode(new TextEncoder().encode("abc"));
    expect(encode(f, "€x", { encoding: "windows-1252" })).toEqual(new Uint8Array([0x80, 0x78]));
    expect(() => encode(f, "日本", { encoding: "windows-1252" })).toThrow("encoding");
    expect(() => encode(f, "\x80", { encoding: "windows-1252" })).toThrow("encoding"); // byte 0x80 means "€"
  });

  it("refuses binary", () => {
    expect(isBinary(new Uint8Array([0x61, 0, 0x62]))).toBe(true);
    expect(isBinary(new Uint8Array([0xff, 0xfe, 0x68, 0, 0x69, 0]))).toBe(false);
    expect(() => decode(new Uint8Array([0x61, 0, 0x62]))).toThrow("binary");
  });
});
