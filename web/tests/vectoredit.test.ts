// The vector editor model against the shared cases (tests/test_vectoredit.py checks the same
// file; made by scripts/make_vectoredit_cases.py).
import { describe, expect, it } from "vitest";
import raw from "../../tests/fixtures/vectoredit_cases.json?raw";
import { Box, Matrix, formatTransform, num, parseTransform, rotate, scale } from "../src/vectoredit/geom";
import * as pathdata from "../src/vectoredit/pathdata";
import * as xmlsrc from "../src/vectoredit/xmlsrc";

/* eslint-disable @typescript-eslint/no-explicit-any */
const cases = JSON.parse(raw) as any;

function close(a: number[] | null, b: number[] | null): boolean {
  if (a === null || b === null) return a === b;
  return a.length === b.length && a.every((x, i) => Math.abs(x - b[i]) <= 1e-9 + 1e-9 * Math.abs(b[i]));
}

function setting(el: xmlsrc.Element, path: number[], name: string, value: string | null): xmlsrc.Element {
  if (!path.length) return el.set(name, value);
  const kids = [...el.children];
  const target = el.elements[path[0]];
  kids[kids.indexOf(target)] = setting(target, path.slice(1), name, value);
  return el.withChildren(kids);
}

describe("numbers", () => {
  it.each(cases.numbers as [number, string][])("%s", (x, text) => expect(num(x)).toBe(text));
});

describe("transforms", () => {
  it.each(cases.transforms as [string, number[] | null, string | null][])("%s", (text, m, written) => {
    if (m === null) {
      expect(() => parseTransform(text)).toThrow();
      return;
    }
    const got = parseTransform(text);
    expect(close([got.a, got.b, got.c, got.d, got.e, got.f], m)).toBe(true);
    expect(formatTransform(got)).toBe(written);
  });
});

describe("paths", () => {
  it.each(cases.paths as any[])("$d", (c) => {
    const segs = pathdata.parse(c.d);
    expect(segs.map((s) => s[0])).toEqual(c.segs.map((s: any[]) => s[0]));
    segs.forEach((s, i) => expect(close(s.slice(1) as number[], c.segs[i].slice(1))).toBe(true));
    expect(pathdata.write(segs)).toBe(c.written);
    const b = pathdata.bounds(segs);
    expect(close(b === null ? null : [b.x0, b.y0, b.x1, b.y1], c.bounds)).toBe(true);
    const moved = (cases.matrices as number[][]).map((m) =>
      pathdata.write(pathdata.transform(segs, new Matrix(...(m as [number, number, number, number, number, number])))),
    );
    expect(moved).toEqual(c.moved);
  });
});

describe("xml", () => {
  it.each((cases.xml_ok as any[]).map((c, i) => [i, c]))("round trip %i", (_i, c) => {
    const doc = xmlsrc.parse(c.src);
    expect(xmlsrc.write(doc)).toBe(c.src);
    expect(doc.root.name).toBe(c.root);
    expect(doc.root.text()).toBe(c.text);
    expect(doc.root.attrs.map((a) => [...a])).toEqual(c.attrs);
  });

  it.each((cases.xml_bad as string[]).map((s, i) => [i, s]))("refuses %i", (_i, src) => {
    expect(() => xmlsrc.parse(src)).toThrow(xmlsrc.XmlError);
  });

  it.each((cases.xml_edits as any[]).map((c, i) => [i, c]))("edit %i", (_i, c) => {
    const doc = xmlsrc.parse(c.src);
    let root = doc.root;
    for (const [path, name, value] of c.edits) root = setting(root, path, name, value);
    const out = xmlsrc.write(doc.withRoot(root));
    expect(out).toBe(c.out);
    expect(xmlsrc.write(xmlsrc.parse(out))).toBe(out);
  });

  it("writes new elements from their parts", () => {
    const el = xmlsrc.element("rect", [["x", "1"]]);
    const g = xmlsrc.element("g", [], [el, new xmlsrc.Text("\n")]);
    expect(xmlsrc.writeNode(g)).toBe('<g><rect x="1"/>\n</g>');
    expect(xmlsrc.writeNode(xmlsrc.parse("<svg/>").root.withChildren([el]))).toBe('<svg><rect x="1"/></svg>');
  });
});

describe("geometry", () => {
  it("inverts matrices and moves boxes", () => {
    const m = parseTransform("translate(5 6) rotate(30) scale(2 3)");
    const [x, y] = m.times(m.inverse()).apply(7, -4);
    expect(x).toBeCloseTo(7);
    expect(y).toBeCloseTo(-4);
    expect(() => scale(0).inverse()).toThrow();
    const b = new Box(0, 0, 10, 20).transformed(rotate(90));
    expect(close([b.x0, b.y0, b.x1, b.y1], [-20, 0, 0, 10])).toBe(true);
  });
});
