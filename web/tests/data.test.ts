import { describe, expect, it } from "vitest";
import { dataConverter, parseCsv } from "../src/converters/data";
import { defaults, type Values } from "../src/core/options";
import type { InputFile } from "../src/core/types";

const PEOPLE = [
  { name: "Ada", age: 36, address: { city: "London", zip: "N1" }, admin: true },
  { name: "Lin", age: 7, address: { city: "Oslo", zip: "0150" }, admin: false },
];

async function convert(name: string, data: Uint8Array | string, to: string, opts: Values = {}) {
  const ext = name.split(".").pop()!;
  const file = new File([data as BlobPart], name);
  const input: InputFile = { file, name, ext, family: "document" };
  const target = dataConverter.targets([input]).find((t) => t.id === `data:${to}`)!;
  const [out] = await dataConverter.run([input], target, { ...defaults(dataConverter.options), ...opts }, { progress() {} });
  return out;
}
const text = (d: Uint8Array) => new TextDecoder().decode(d);

describe("data formats", () => {
  it("parses quoted CSV", () => {
    expect(parseCsv('a,b\n"x, y","say ""hi"""\n')).toEqual([["a", "b"], ["x, y", 'say "hi"']]);
    expect(parseCsv("a;b\n1;2")).toEqual([["a", "b"], ["1", "2"]]);
  });

  it("JSON → CSV → JSON round trip (numbers, booleans, nesting)", async () => {
    const csv = await convert("people.json", JSON.stringify({ people: PEOPLE }), "csv");
    expect(text(csv.data).split("\r\n")[0]).toBe("name,age,address.city,address.zip,admin");
    expect(text(csv.data)).toContain("Ada,36,London,N1,true");
    const back = await convert("people.csv", csv.data, "json");
    expect(JSON.parse(text(back.data))).toEqual(PEOPLE);
  });

  it("JSON → XLSX → JSON keeps text zip codes and numbers", async () => {
    const xlsx = await convert("people.json", JSON.stringify(PEOPLE), "xlsx");
    expect(text(xlsx.data.slice(0, 2))).toBe("PK");
    const back = await convert("people.xlsx", xlsx.data, "json");
    expect(JSON.parse(text(back.data))).toEqual(PEOPLE);
  });

  it("JSON → YAML → XML → JSON", async () => {
    const yml = await convert("people.json", JSON.stringify({ people: PEOPLE }), "yaml");
    expect(text(yml.data)).toContain("city: London");
    const xml = await convert("people.yaml", yml.data, "xml");
    expect(text(xml.data)).toMatch(/^<\?xml/);
    expect(text(xml.data)).toContain("<city>London</city>");
    const back = await convert("people.xml", xml.data, "json");
    expect(JSON.parse(text(back.data)).root.people[0].address.city).toBe("London");
  });

  it("XML attributes → Excel-friendly semicolon CSV", async () => {
    const xml = '<library><book id="1"><title>Æble</title></book><book id="2"><title>Bog</title></book></library>';
    const csv = await convert("books.xml", xml, "csv", { excel_bom: true, delimiter: ";" });
    expect([...csv.data.slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]); // UTF-8 BOM
    expect(text(csv.data).trim().split("\r\n")).toEqual(["@id;title", "1;Æble", "2;Bog"]);
  });

  it("explains invalid JSON", async () => {
    await expect(convert("bad.json", "{nope", "csv")).rejects.toThrow(/isn't valid JSON/);
  });
});
