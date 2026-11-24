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

// ------------------------------------------------------------------ step 2: shapes, edits, io

import * as vdoc from "../src/vectoredit/doc";
import { History } from "../src/vectoredit/history";
import * as vio from "../src/vectoredit/io";
import * as shapes from "../src/vectoredit/shapes";
import { gunzipSync } from "fflate";

const DRAWING: string = cases.edits[0].src;
const latin1 = (s: string) => Uint8Array.from(s, (c) => c.charCodeAt(0));
const utf8 = (s: string) => new TextEncoder().encode(s);
const text = (b: Uint8Array) => new TextDecoder().decode(b);

function runOps(src: string, ops: any[]): string {
  let d = vdoc.parse(src);
  try {
    for (const [name, ...a] of ops) {
      if (name === "move") d = vdoc.move(d, a[0], a[1], a[2]);
      else if (name === "resize") d = vdoc.resize(d, a[0], new Box(a[1][0], a[1][1], a[1][2], a[1][3]));
      else if (name === "style") d = vdoc.setStyle(d, a[0], a[1], a[2]);
      else if (name === "hide") d = vdoc.setHidden(d, a[0], a[1]);
      else if (name === "rename") d = vdoc.rename(d, a[0], a[1]);
      else if (name === "delete") d = vdoc.remove(d, a[0]);
      else if (name === "duplicate") d = vdoc.duplicate(d, a[0])[0];
      else if (name === "reorder") d = vdoc.reorder(d, a[0], a[1])[0];
      else if (name === "group") d = vdoc.group(d, a[0])[0];
      else if (name === "ungroup") d = vdoc.ungroup(d, a[0])[0];
      else if (name === "text") d = vdoc.setText(d, a[0], a[1]);
      else if (name === "attr") d = vdoc.setAttr(d, a[0], a[1], a[2]);
      else if (name === "transform") d = vdoc.transform(d, a[0], new Matrix(...(a[1] as [number, number, number, number, number, number])));
      else if (name === "add") {
        const el = vdoc.newShape(a[0], new Box(a[1][0], a[1][1], a[1][2], a[1][3]), a[2], vdoc.ids(d));
        d = vdoc.add(d, el, a[3])[0];
      } else throw new Error(name);
    }
  } catch (e) {
    if (e instanceof vdoc.VectorError) return "error: " + e.message;
    throw e;
  }
  return vdoc.write(d);
}

const boxList = (b: Box | null) => (b === null ? null : [b.x0, b.y0, b.x1, b.y1]);

describe("vector document", () => {
  it("lists items with their bounds", () => {
    const d = vdoc.parse(DRAWING);
    const got = vdoc.items(d);
    expect(got.length).toBe(cases.items.length);
    got.forEach((item, i) => {
      const want = cases.items[i];
      expect([item.path, item.kind, item.name, item.depth, item.hidden, item.container]).toEqual([
        want.path, want.kind, want.name, want.depth, want.hidden, want.container,
      ]);
      expect(close(boxList(vdoc.bounds(d, item.path)), want.bounds)).toBe(true);
    });
  });

  it.each((cases.edits as any[]).map((c, i) => [i, c]))("edit %i", (_i, c) => {
    expect(runOps(c.src, c.ops)).toBe(c.out);
  });

  it("hits and rubber bands", () => {
    const d = vdoc.parse(DRAWING);
    for (const [x, y, inside, pad, want] of cases.hits) expect(vdoc.hit(d, x, y, inside, pad)).toEqual(want);
    for (const [b, inside, want] of cases.in_box) {
      expect(vdoc.inBox(d, new Box(b[0], b[1], b[2], b[3]), inside)).toEqual(want);
    }
  });

  it.each((cases.canvases as any[]).map((c, i) => [i, c]))("canvas %i", (_i, [src, w, h, view]) => {
    const [gw, gh, gv] = vdoc.canvas(vdoc.parse(src));
    expect(close([gw, gh, gv.x0, gv.y0, gv.x1, gv.y1], [w, h, ...view])).toBe(true);
  });

  it("reads and writes styles", () => {
    for (const [src, name, want] of cases.style_get) expect(shapes.styleGet(xmlsrc.parse(src).root, name)).toBe(want);
    for (const [src, name, value, want] of cases.style_set) {
      expect(xmlsrc.writeNode(shapes.styleSet(xmlsrc.parse(src).root, name, value))).toBe(want);
    }
  });

  it("opens and saves files", () => {
    for (const [w, h, unit, want] of cases.new_text) {
      expect(vio.newText(w, h, unit)).toBe(want);
      const opened = vio.newDocument(w, h, unit);
      expect(text(vio.toBytes(opened, opened.doc))).toBe(want);
    }
    for (const [data, message] of cases.open_bad) {
      expect(() => vio.openBytes(latin1(data))).toThrow(message);
    }
    const gz = latin1(cases.svgz.latin1);
    const opened = vio.openBytes(gz);
    expect(opened.gz).toBe(true);
    expect(vdoc.write(opened.doc)).toBe(cases.svgz.text);
    expect(vio.toBytes(opened, opened.doc)).toBe(opened.data);
    const moved = vdoc.move(opened.doc, [[0]], 1, 0);
    expect(text(gunzipSync(vio.toBytes(opened, moved)))).toContain('x="2"');
  });

  it("keeps encodings, BOMs and line endings", () => {
    const body = utf8(DRAWING.replaceAll("\n", "\r\n"));
    const data = new Uint8Array([0xef, 0xbb, 0xbf, ...body]);
    const opened = vio.openBytes(data);
    expect(vio.toBytes(opened, opened.doc)).toBe(data);
    const out = vio.toBytes(opened, vdoc.move(opened.doc, [[1]], 1, 0));
    expect([...out.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(text(out)).toContain("\r\n");
    expect(text(out)).toContain('x="11"');
    const cp = latin1('<svg xmlns="http://www.w3.org/2000/svg"><text>caf\xe9</text></svg>');
    const latin = vio.openBytes(cp);
    const snow = vdoc.setText(latin.doc, [0], "☃");
    expect(() => vio.toBytes(latin, snow)).toThrow("encoding");
    expect(text(vio.toBytes(latin, snow, true))).toContain("☃");
  });

  it("merges history steps and tracks saving", () => {
    const d0 = vdoc.parse(DRAWING);
    const h = new History(d0);
    expect(h.dirty).toBe(false);
    const d1 = vdoc.move(d0, [[1]], 1, 0);
    h.push(d1, "Move", "nudge");
    h.push(vdoc.move(d1, [[1]], 1, 0), "Move", "nudge");
    expect(h.dirty && h.canUndo).toBe(true);
    expect(h.undo()).toBe("Move");
    expect(h.doc).toBe(d0);
    expect(h.dirty).toBe(false);
    expect(h.redo()).toBe("Move");
    expect(vdoc.write(h.doc)).toContain('x="12"');
    h.markSaved();
    expect(h.dirty).toBe(false);
    h.push(h.doc, "Nothing");
    expect(h.version).toBe(4);
  });
});

describe("vector files", () => {
  it("stops unpacking an oversized .svgz", async () => {
    const { gzipSync } = await import("fflate");
    const bomb = gzipSync(new Uint8Array(vio.MAX_OPEN_BYTES + 10));
    expect(() => vio.openBytes(bomb)).toThrow("too big");
  });
});
