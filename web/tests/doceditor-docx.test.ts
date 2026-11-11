// @vitest-environment jsdom
import { Deflate, strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import vectorsJson from "../../tests/fixtures/doceditor-vectors.json";
import { afterEach, describe, expect, test, vi } from "vitest";
import { crc32 } from "../src/doceditor/docx-package";
import { readDocx } from "../src/doceditor/docx-read";
import { writeDocx } from "../src/doceditor/docx-write";
import { normaliseHtml } from "../src/doceditor/subset";
import {
  MAX_UNPACKED_BYTES,
  TOO_LARGE,
  W_COLUMNS,
  W_COMMENTS,
  W_EQUATIONS,
  W_FIELDS,
  W_HEADERS,
  W_LISTSTYLE,
  W_NOTES,
  W_OBJECTS,
  W_PICTURES,
  W_TEXTBOX,
  W_TRACKED,
} from "../src/doceditor/types";

const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing";
const PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships";
const CT = "http://schemas.openxmlformats.org/package/2006/content-types";
const XML_NS = "http://www.w3.org/XML/1998/namespace";

const PNG = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
  ),
  (c) => c.charCodeAt(0),
);

type Files = Record<string, Uint8Array>;

function parse(xml: string): Document {
  const doc = new DOMParser().parseFromString(xml, "application/xml");
  expect(doc.getElementsByTagName("parsererror").length).toBe(0);
  return doc;
}

function unzip(bytes: Uint8Array): Files {
  return unzipSync(bytes);
}

function part(files: Files, name: string): Document {
  expect(Object.keys(files)).toContain(name);
  return parse(strFromU8(files[name]));
}

function write(html: string, images: Map<string, Uint8Array> = new Map(), template?: Uint8Array) {
  const files = unzip(writeDocx(html, images, template ?? null));
  return { files, doc: part(files, "word/document.xml") };
}

const kids = (el: Element, local: string): Element[] =>
  Array.from(el.children).filter((c) => c.namespaceURI === W && c.localName === local);
const kid = (el: Element | null | undefined, local: string): Element | null =>
  el ? (kids(el, local)[0] ?? null) : null;
const all = (root: Document | Element, local: string, ns = W): Element[] =>
  Array.from(root.getElementsByTagNameNS(ns, local));
const val = (el: Element | null): string | null => (el ? el.getAttributeNS(W, "val") : null);
const body = (doc: Document): Element => all(doc, "body")[0];
/** Paragraphs directly in the body (not in tables). */
const paras = (doc: Document): Element[] => kids(body(doc), "p");
const pStyle = (p: Element): string | null => val(kid(kid(p, "pPr"), "pStyle"));
const text = (p: Element): string =>
  all(p, "t")
    .map((t) => t.textContent)
    .join("");
const numPr = (p: Element) => {
  const np = kid(kid(p, "pPr"), "numPr");
  return { ilvl: val(kid(np, "ilvl")), numId: val(kid(np, "numId")) };
};

function rels(files: Files, name = "word/_rels/document.xml.rels") {
  return all(part(files, name), "Relationship", PKG_RELS).map((r) => ({
    id: r.getAttribute("Id") as string,
    type: r.getAttribute("Type") as string,
    target: r.getAttribute("Target") as string,
    mode: r.getAttribute("TargetMode"),
  }));
}

function numFmt(files: Files, p: Element): string | null {
  const numbering = part(files, "word/numbering.xml");
  const { ilvl, numId } = numPr(p);
  const num = all(numbering, "num").find((n) => n.getAttributeNS(W, "numId") === numId);
  const abstractId = val(kid(num, "abstractNumId"));
  const abstract = all(numbering, "abstractNum").find(
    (a) => a.getAttributeNS(W, "abstractNumId") === abstractId,
  );
  const lvl = kids(abstract as Element, "lvl").find((l) => l.getAttributeNS(W, "ilvl") === ilvl);
  return val(kid(lvl, "numFmt"));
}

afterEach(() => vi.restoreAllMocks());

describe("package", () => {
  test("has every part, the right content types and a final sectPr", () => {
    const { files, doc } = write("<p>a</p>");
    for (const name of [
      "[Content_Types].xml",
      "_rels/.rels",
      "word/document.xml",
      "word/styles.xml",
      "word/numbering.xml",
      "word/settings.xml",
      "word/_rels/document.xml.rels",
      "docProps/core.xml",
      "docProps/app.xml",
    ]) {
      expect(Object.keys(files)).toContain(name);
    }
    const types = part(files, "[Content_Types].xml");
    const overrides = new Map(
      all(types, "Override", CT).map((o) => [
        o.getAttribute("PartName"),
        o.getAttribute("ContentType"),
      ]),
    );
    expect(overrides.get("/word/document.xml")).toBe(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    );
    expect(overrides.get("/word/styles.xml")).toContain("styles+xml");
    expect(overrides.get("/word/numbering.xml")).toContain("numbering+xml");
    expect(overrides.get("/word/settings.xml")).toContain("settings+xml");
    expect(overrides.get("/docProps/core.xml")).toContain("core-properties+xml");
    const root = rels(files, "_rels/.rels");
    expect(root.find((r) => r.type.endsWith("/officeDocument"))?.target).toBe("word/document.xml");
    const ids = rels(files).map((r) => r.id);
    expect(new Set(ids).size).toBe(ids.length);
    const last = body(doc).lastElementChild as Element;
    expect(last.localName).toBe("sectPr");
    expect(text(paras(doc)[0])).toBe("a");
  });

  test("is deterministic", () => {
    const html = '<h1>T</h1><ol><li>a</li></ol><p><img src="img/1.png" width="4"></p>';
    const images = new Map([["img/1.png", PNG]]);
    expect(writeDocx(html, images)).toEqual(writeDocx(html, images));
  });

  test("an empty fragment is one empty paragraph", () => {
    const { doc } = write("");
    expect(paras(doc).length).toBe(1);
    expect(text(paras(doc)[0])).toBe("");
  });

  test("the styles the writer uses exist with python-docx's names", () => {
    const { files } = write("<p>a</p>");
    const styles = part(files, "word/styles.xml");
    const names = new Map(
      all(styles, "style").map((s) => [s.getAttributeNS(W, "styleId"), val(kid(s, "name"))]),
    );
    expect(names.get("Normal")).toBe("Normal");
    expect(names.get("Heading1")).toBe("heading 1");
    expect(names.get("Heading2")).toBe("heading 2");
    expect(names.get("Heading3")).toBe("heading 3");
    expect(names.get("Quote")).toBe("Quote");
    expect(names.get("Code")).toBe("Code");
    expect(names.get("ListBullet")).toBe("List Bullet");
    expect(names.get("ListNumber")).toBe("List Number");
    expect(names.get("TableGrid")).toBe("Table Grid");
    expect(names.get("Hyperlink")).toBe("Hyperlink");
  });

  test.each((vectorsJson as { cases: string[] }).cases)("shared case writes: %s", (html) => {
    const { doc } = write(html);
    expect(body(doc).lastElementChild?.localName).toBe("sectPr");
  });
});

describe("blocks and runs", () => {
  test("headings, alignment and run properties", () => {
    const { doc } = write(
      '<h1>Title</h1><h2>U</h2><h3>V</h3><p style="text-align:center">a <b>b</b> <i>c</i> <u>d</u> <s>e</s> ' +
        '<span style="font-family:Arial;font-size:14pt;color:#ff0000;' +
        'background-color:#ffcc00">f</span></p>',
    );
    const ps = paras(doc);
    expect(ps.slice(0, 3).map(pStyle)).toEqual(["Heading1", "Heading2", "Heading3"]);
    const p = ps[3];
    expect(val(kid(kid(p, "pPr"), "jc"))).toBe("center");
    const runs = new Map(
      kids(p, "r")
        .filter((r) => text(r).trim())
        .map((r) => [text(r).trim(), kid(r, "rPr") as Element]),
    );
    expect(kid(runs.get("b") as Element, "b")).not.toBeNull();
    expect(kid(runs.get("c") as Element, "i")).not.toBeNull();
    expect(val(kid(runs.get("d") as Element, "u"))).toBe("single");
    expect(kid(runs.get("e") as Element, "strike")).not.toBeNull();
    const f = runs.get("f") as Element;
    const fonts = kid(f, "rFonts") as Element;
    expect(fonts.getAttributeNS(W, "ascii")).toBe("Arial");
    expect(fonts.getAttributeNS(W, "hAnsi")).toBe("Arial");
    expect(fonts.getAttributeNS(W, "eastAsia")).toBe("Arial");
    expect(val(kid(f, "sz"))).toBe("28");
    expect(val(kid(f, "color"))).toBe("FF0000");
    expect((kid(f, "shd") as Element).getAttributeNS(W, "fill")).toBe("FFCC00");
  });

  test("font sizes are whole half-points", () => {
    const { doc } = write('<p><span style="font-size:10.25pt">x</span></p>');
    expect(val(kid(kid(kids(paras(doc)[0], "r")[0], "rPr"), "sz"))).toBe("20");
  });

  test("page break, rule, indent, code and quote", () => {
    const { doc } = write(
      '<p style="page-break-before:always">n</p><hr><p style="margin-left:72pt">i</p>' +
        '<p style="margin-left:36pt">j</p><pre>a\nb</pre><blockquote><p>q</p></blockquote>',
    );
    const ps = paras(doc);
    expect(kid(kid(ps[0], "pPr"), "pageBreakBefore")).not.toBeNull();
    const bottom = kid(kid(kid(ps[1], "pPr"), "pBdr"), "bottom") as Element;
    expect(text(ps[1])).toBe("");
    expect(val(bottom)).toBe("single");
    expect(bottom.getAttributeNS(W, "sz")).toBe("6");
    expect(kid(kid(ps[2], "pPr"), "ind")?.getAttributeNS(W, "left")).toBe("1440");
    expect(kid(kid(ps[3], "pPr"), "ind")?.getAttributeNS(W, "left")).toBe("720");
    expect(pStyle(ps[4])).toBe("Code");
    const code = kids(ps[4], "r");
    expect(code.length).toBe(1);
    expect(Array.from(code[0].children).map((c) => c.localName)).toEqual(["t", "br", "t"]);
    expect(text(ps[4])).toBe("ab");
    expect(pStyle(ps[5])).toBe("Quote");
  });

  test("a code block that starts with a blank line keeps it", () => {
    const { doc } = write("<pre>\nx</pre>");
    const r = kids(paras(doc)[0], "r")[0];
    expect(Array.from(r.children).map((c) => c.localName)).toEqual(["br", "t"]);
  });

  test("tabs become w:tab and no-break spaces plain preserved spaces", () => {
    const { doc } = write("<p>a\tb  c</p>");
    const r = kids(paras(doc)[0], "r")[0];
    expect(Array.from(r.children).map((c) => c.localName)).toEqual(["t", "tab", "t"]);
    const ts = all(r, "t");
    expect(ts.map((t) => t.textContent)).toEqual(["a", "b  c"]);
    for (const t of ts) expect(t.getAttributeNS(XML_NS, "space")).toBe("preserve");
  });

  test("line breaks are their own runs", () => {
    const { doc } = write("<p>a<br>b</p>");
    const runs = kids(paras(doc)[0], "r");
    expect(runs.length).toBe(3);
    expect(kid(runs[1], "br")).not.toBeNull();
  });
});

describe("lists", () => {
  test("each top-level list restarts its numbering", () => {
    const { doc } = write("<ol><li>a</li></ol><p>x</p><ol><li>b</li></ol>");
    const ps = paras(doc).filter((p) => kid(kid(p, "pPr"), "numPr"));
    expect(ps.length).toBe(2);
    expect(new Set(ps.map((p) => numPr(p).numId)).size).toBe(2);
    expect(pStyle(ps[0])).toBe("ListNumber");
  });

  test("nested bullets share the numbering one level deeper", () => {
    const { doc } = write("<ul><li>a<ul><li>b</li></ul></li></ul>");
    const [p0, p1] = paras(doc);
    expect(pStyle(p0)).toBe("ListBullet");
    expect(text(p0)).toBe("a");
    expect(numPr(p0).ilvl).toBe("0");
    expect(numPr(p1).ilvl).toBe("1");
    expect(numPr(p0).numId).toBe(numPr(p1).numId);
  });

  test("mixed nested lists keep their kind", () => {
    let { files, doc } = write("<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul>");
    expect(paras(doc).map((p) => numFmt(files, p))).toEqual(["bullet", "bullet", "decimal"]);
    ({ files, doc } = write("<ol><li>x<ul><li>y</li></ul></li></ol>"));
    expect(paras(doc).map((p) => numFmt(files, p))).toEqual(["decimal", "bullet"]);
    expect(paras(doc).map((p) => numPr(p).ilvl)).toEqual(["0", "1"]);
    expect(new Set(paras(doc).map((p) => numPr(p).numId)).size).toBe(2);
  });

  test("numbering: 9-level definitions before the nums, ordered lists restart", () => {
    const { files } = write("<ol><li>a</li></ol><ul><li>b</li></ul>");
    const numbering = part(files, "word/numbering.xml").documentElement;
    const order = Array.from(numbering.children).map((c) => c.localName);
    expect(order).toEqual(["abstractNum", "abstractNum", "num", "num"]);
    for (const a of kids(numbering, "abstractNum")) {
      const lvls = kids(a, "lvl");
      expect(lvls.length).toBe(9);
      expect(lvls.map((l) => kid(kid(l, "pPr"), "ind")?.getAttributeNS(W, "left"))).toEqual([
        "720",
        "1440",
        "2160",
        "2880",
        "3600",
        "4320",
        "5040",
        "5760",
        "6480",
      ]);
    }
    const [ordered, bullets] = kids(numbering, "num");
    expect(kid(kid(ordered, "lvlOverride"), "startOverride")).not.toBeNull();
    expect(kid(bullets, "lvlOverride")).toBeNull();
  });

  test("an empty item is kept", () => {
    const { doc } = write("<ul><li>a</li><li></li><li>c</li></ul>");
    const ps = paras(doc);
    expect(ps.length).toBe(3);
    expect(ps.map(text)).toEqual(["a", "", "c"]);
    expect(ps.every((p) => numPr(p).numId !== null)).toBe(true);
  });
});

describe("tables", () => {
  const tcs = (tr: Element) => kids(tr, "tc");
  const tcPr = (tc: Element) => kid(tc, "tcPr");

  test("column span", () => {
    const { doc } = write(
      '<table><tr><td colspan="2"><p>a</p></td></tr><tr><td><p>b</p></td><td><p>c</p></td></tr></table>',
    );
    const tbl = kids(body(doc), "tbl")[0];
    expect(val(kid(kid(tbl, "tblPr"), "tblStyle"))).toBe("TableGrid");
    const [r0, r1] = kids(tbl, "tr");
    expect(tcs(r0).length).toBe(1);
    expect(val(kid(tcPr(tcs(r0)[0]), "gridSpan"))).toBe("2");
    expect(text(tcs(r1)[1])).toBe("c");
    expect(kids(kid(tbl, "tblGrid") as Element, "gridCol").length).toBe(2);
  });

  test("row span", () => {
    const { doc } = write(
      '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr><tr><td><p>c</p></td></tr></table>',
    );
    const [r0, r1] = kids(kids(body(doc), "tbl")[0], "tr");
    expect(val(kid(tcPr(tcs(r0)[0]), "vMerge"))).toBe("restart");
    const cont = kid(tcPr(tcs(r1)[0]), "vMerge") as Element;
    expect(cont).not.toBeNull();
    expect(cont.hasAttributeNS(W, "val")).toBe(false);
    expect(text(tcs(r1)[0])).toBe("");
    expect(text(tcs(r1)[1])).toBe("c");
  });

  test("borders: explicit single, or all none for border:none", () => {
    let { doc } = write("<table><tr><td><p>x</p></td></tr></table>");
    let borders = kid(kid(kids(body(doc), "tbl")[0], "tblPr"), "tblBorders") as Element;
    expect(Array.from(borders.children).map((b) => [b.localName, val(b)])).toEqual(
      ["top", "left", "bottom", "right", "insideH", "insideV"].map((e) => [e, "single"]),
    );
    ({ doc } = write(
      '<table style="border:none"><tr><td><p>x</p></td><td><p>y</p></td></tr></table>',
    ));
    const tblPr = kid(kids(body(doc), "tbl")[0], "tblPr") as Element;
    expect(kid(tblPr, "tblStyle")).toBeNull();
    borders = kid(tblPr, "tblBorders") as Element;
    expect(borders.children.length).toBe(6);
    expect(Array.from(borders.children).every((b) => val(b) === "none")).toBe(true);
  });

  test("a nested table keeps the outer table intact and the cell ends with a paragraph", () => {
    const { doc } = write(
      "<table><tr><td><table><tr><td><p>a</p></td></tr><tr><td><p>b</p></td></tr></table>" +
        "</td></tr></table>",
    );
    const outer = kids(body(doc), "tbl");
    expect(outer.length).toBe(1);
    expect(kids(outer[0], "tr").length).toBe(1);
    const cell = tcs(kids(outer[0], "tr")[0])[0];
    const inner = kids(cell, "tbl")[0];
    expect(kids(inner, "tr").map((tr) => text(tr))).toEqual(["a", "b"]);
    expect(cell.lastElementChild?.localName).toBe("p");
    // nothing before the nested table
    expect(Array.from(cell.children).map((c) => c.localName)).toEqual(["tcPr", "tbl", "p"]);
  });

  test("text after a nested table stays after it", () => {
    const { doc } = write(
      "<table><tr><td><table><tr><td><p>a</p></td></tr></table><p>x</p></td></tr></table>",
    );
    const cell = tcs(kids(kids(body(doc), "tbl")[0], "tr")[0])[0];
    expect(Array.from(cell.children).map((c) => c.localName)).toEqual(["tcPr", "tbl", "p"]);
    expect(text(cell.lastElementChild as Element)).toBe("x");
  });
});

describe("pictures and links", () => {
  test("a picture's extent is px × 9525", () => {
    const { files, doc } = write(
      '<p><img src="img/1.png" width="96"></p>',
      new Map([["img/1.png", PNG]]),
    );
    const extent = all(doc, "extent", WP);
    expect(extent.length).toBe(1);
    expect(extent[0].getAttribute("cx")).toBe("914400");
    expect(extent[0].getAttribute("cy")).toBe("914400");
    const image = rels(files).find((r) => r.type.endsWith("/image"));
    expect(image?.target).toBe("media/image1.png");
    expect(files["word/media/image1.png"]).toEqual(PNG);
    const blip = all(doc, "blip", "http://schemas.openxmlformats.org/drawingml/2006/main")[0];
    expect(blip.getAttributeNS(R, "embed")).toBe(image?.id);
    const defaults = all(part(files, "[Content_Types].xml"), "Default", CT).map((d) => [
      d.getAttribute("Extension"),
      d.getAttribute("ContentType"),
    ]);
    expect(defaults).toContainEqual(["png", "image/png"]);
  });

  test("width and height are both kept", () => {
    const { doc } = write(
      '<p><img src="img/1.png" width="10" height="20"></p>',
      new Map([["img/1.png", PNG]]),
    );
    const extent = all(doc, "extent", WP)[0];
    expect([extent.getAttribute("cx"), extent.getAttribute("cy")]).toEqual(["95250", "190500"]);
  });

  test("the same picture twice is one part", () => {
    const { files } = write(
      '<p><img src="img/1.png" width="4"><img src="img/1.png" width="4"></p>',
      new Map([["img/1.png", PNG]]),
    );
    expect(rels(files).filter((r) => r.type.endsWith("/image")).length).toBe(1);
  });

  // Native sizes as python-docx computes them: pixels / dpi (72 unless the file says).
  const bytes = (...parts: (number[] | string)[]) =>
    Uint8Array.from(
      parts.flatMap((p) => (typeof p === "string" ? Array.from(p, (c) => c.charCodeAt(0)) : p)),
    );
  const le16 = (n: number) => [n & 255, n >> 8];
  const le32 = (n: number) => [n & 255, (n >> 8) & 255, (n >> 16) & 255, n >>> 24];
  const be16 = (n: number) => [n >> 8, n & 255];
  const GIF = bytes("GIF89a", le16(2), le16(3), [0, 0, 0, 0, 0]);
  const BMP = bytes("BM", new Array(16).fill(0), le32(4), le32(2), new Array(12).fill(0), [
    ...le32(3780),
    ...le32(3780),
    ...new Array(8).fill(0),
  ]);
  const JPEG = bytes(
    [0xff, 0xd8, 0xff, 0xe0],
    be16(16),
    "JFIF\0",
    [1, 1, 1],
    be16(300),
    be16(300),
    [0, 0, 0xff, 0xc0],
    be16(17),
    [8],
    be16(10),
    be16(20),
    new Array(10).fill(0),
    [0xff, 0xd9],
  );
  test.each([
    ["png", PNG, "png", ["12700", "12700"]],
    ["gif", GIF, "gif", ["25400", "38100"]],
    ["bmp", BMP, "bmp", ["38100", "19050"]],
    ["jpg", JPEG, "jpg", ["60960", "30480"]],
  ])("native size of a %s picture", (_name, data, ext, size) => {
    const { files, doc } = write('<p><img src="img/1.png"></p>', new Map([["img/1.png", data]]));
    const e = all(doc, "extent", WP)[0];
    expect([e.getAttribute("cx"), e.getAttribute("cy")]).toEqual(size);
    expect(rels(files).find((r) => r.type.endsWith("/image"))?.target).toBe(`media/image1.${ext}`);
  });

  test("height only scales the width", () => {
    const { doc } = write('<p><img src="img/1.png" height="6"></p>', new Map([["img/1.png", GIF]]));
    const e = all(doc, "extent", WP)[0];
    expect([e.getAttribute("cx"), e.getAttribute("cy")]).toEqual(["38100", "57150"]);
  });

  test("WebP is left out, as python-docx can't read it", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const webp = bytes("RIFF", le32(30), "WEBPVP8 ", new Array(22).fill(0));
    const { doc } = write(
      '<p>x<img src="img/1.webp" width="3"></p>',
      new Map([["img/1.webp", webp]]),
    );
    expect(all(doc, "drawing").length).toBe(0);
    expect(warn).toHaveBeenCalled();
  });

  test("bad and missing pictures are skipped with a console warning", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    let { doc } = write(
      '<p>x<img src="img/1.png" width="10"></p>',
      new Map([["img/1.png", strToU8("not an image")]]),
    );
    expect(all(doc, "drawing").length).toBe(0);
    expect(text(paras(doc)[0])).toBe("x");
    ({ doc } = write('<p>x<img src="img/9.png"></p>'));
    expect(all(doc, "drawing").length).toBe(0);
    expect(warn.mock.calls.map((c) => c.join(" ")).join("\n")).toContain("img/9.png");
  });

  test("a link is a w:hyperlink with an external relationship", () => {
    const { files, doc } = write(
      '<p><a href="https://x.org">x</a> and <a href="https://x.org">again</a></p>',
    );
    const links = all(doc, "hyperlink");
    expect(links.length).toBe(2);
    const id = links[0].getAttributeNS(R, "id");
    const rel = rels(files).find((r) => r.id === id);
    expect(rel?.target).toBe("https://x.org");
    expect(rel?.mode).toBe("External");
    expect(rel?.type.endsWith("/hyperlink")).toBe(true);
    expect(links[1].getAttributeNS(R, "id")).toBe(id);
    expect(val(kid(kid(kid(links[0], "r"), "rPr"), "rStyle"))).toBe("Hyperlink");
  });
});

// ----- template ---------------------------------------------------------------------------

const A4_LANDSCAPE =
  `<w:sectPr xmlns:w="${W}" xmlns:r="${R}"><w:headerReference w:type="default" r:id="rIdHead"/>` +
  '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>' +
  '<w:pgMar w:top="1134" w:right="851" w:bottom="1134" w:left="851" w:header="709" w:footer="709" w:gutter="0"/>' +
  "</w:sectPr>";
const HEADER = `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="${W}"><w:p><w:r><w:t>Head</w:t></w:r></w:p></w:hdr>`;

/** A DOCX like one saved by Word: A4 landscape, Arial Normal, a header, old body content. */
function makeTemplate(edit?: (styles: Document) => void): Uint8Array {
  const files = unzip(
    writeDocx(
      '<p>old <a href="https://old.example/">link</a><img src="img/1.png" width="2"></p>',
      new Map([["img/1.png", PNG]]),
    ),
  );
  const ser = (d: Document) => new XMLSerializer().serializeToString(d);
  const doc = part(files, "word/document.xml");
  const b = body(doc);
  b.replaceChild(parse(A4_LANDSCAPE).documentElement, kid(b, "sectPr") as Element);
  files["word/document.xml"] = strToU8(ser(doc));
  const styles = part(files, "word/styles.xml");
  const normal = all(styles, "style").find(
    (s) => s.getAttributeNS(W, "styleId") === "Normal",
  ) as Element;
  const rPr = styles.createElementNS(W, "w:rPr");
  const fonts = styles.createElementNS(W, "w:rFonts");
  fonts.setAttributeNS(W, "w:ascii", "Arial");
  fonts.setAttributeNS(W, "w:hAnsi", "Arial");
  rPr.appendChild(fonts);
  normal.appendChild(rPr);
  edit?.(styles);
  files["word/styles.xml"] = strToU8(ser(styles));
  files["word/header1.xml"] = strToU8(HEADER);
  const docRels = strFromU8(files["word/_rels/document.xml.rels"]).replace(
    "</Relationships>",
    '<Relationship Id="rIdHead" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>',
  );
  files["word/_rels/document.xml.rels"] = strToU8(docRels);
  files["[Content_Types].xml"] = strToU8(
    strFromU8(files["[Content_Types].xml"]).replace(
      "</Types>",
      '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>',
    ),
  );
  return zipSync(files);
}

describe("template", () => {
  test("keeps the page setup, styles and header; the body holds only the new content", () => {
    const template = makeTemplate();
    const before = unzip(template);
    const { files, doc } = write("<p>new</p><ol><li>n</li></ol>", new Map(), template);
    const b = body(doc);
    expect(Array.from(b.children).map((c) => c.localName)).toEqual(["p", "p", "sectPr"]);
    expect(paras(doc).map(text)).toEqual(["new", "n"]);
    const sect = kid(b, "sectPr") as Element;
    expect(kid(sect, "pgSz")?.getAttributeNS(W, "orient")).toBe("landscape");
    expect(kid(sect, "pgSz")?.getAttributeNS(W, "w")).toBe("16838");
    expect(kid(sect, "headerReference")?.getAttributeNS(R, "id")).toBe("rIdHead");
    expect(files["word/styles.xml"]).toEqual(before["word/styles.xml"]);
    expect(files["word/header1.xml"]).toEqual(before["word/header1.xml"]);
    const r = rels(files);
    expect(r.find((x) => x.id === "rIdHead")?.target).toBe("header1.xml");
    // what the old body pointed at is gone
    expect(r.some((x) => x.target === "https://old.example/")).toBe(false);
    expect(r.some((x) => x.type.endsWith("/image"))).toBe(false);
    expect(Object.keys(files).some((n) => n.startsWith("word/media/"))).toBe(false);
    // the new list is numbered with its own definition
    expect(numFmt(files, paras(doc)[1])).toBe("decimal");
  });

  test("tables use the template's text width", () => {
    const { doc } = write(
      "<table><tr><td><p>a</p></td><td><p>b</p></td></tr></table>",
      new Map(),
      makeTemplate(),
    );
    const cols = kids(kid(kids(body(doc), "tbl")[0], "tblGrid") as Element, "gridCol");
    // (16838 - 851 - 851) / 2
    expect(cols.map((c) => c.getAttributeNS(W, "w"))).toEqual(["7568", "7568"]);
  });

  test("new pictures and links in a template", () => {
    const { files, doc } = write(
      '<p><a href="https://new.example/">n</a><img src="img/1.png" width="3"></p>',
      new Map([["img/1.png", PNG]]),
      makeTemplate(),
    );
    const r = rels(files);
    const ids = r.map((x) => x.id);
    expect(new Set(ids).size).toBe(ids.length);
    const link = all(doc, "hyperlink")[0].getAttributeNS(R, "id");
    expect(r.find((x) => x.id === link)?.target).toBe("https://new.example/");
    const img = r.find((x) => x.type.endsWith("/image"));
    expect(files[`word/${img?.target}`]).toEqual(PNG);
  });

  test("styles the template lacks are added", () => {
    const missing = ["heading 1", "quote", "list bullet", "table grid", "hyperlink"];
    const template = makeTemplate((styles) => {
      for (const s of all(styles, "style")) {
        if (missing.includes((val(kid(s, "name")) ?? "").toLowerCase())) s.remove();
      }
    });
    const { files, doc } = write(
      "<h1>T</h1><blockquote><p>q</p></blockquote><ul><li>a</li></ul>" +
        '<table><tr><td><p>c</p></td></tr></table><p><a href="https://x.org/">x</a></p>',
      new Map(),
      template,
    );
    const styles = part(files, "word/styles.xml");
    const names = all(styles, "style").map((s) => val(kid(s, "name"))?.toLowerCase());
    for (const name of missing) expect(names).toContain(name);
    expect(pStyle(paras(doc)[0])).toBe("Heading1");
    const added = all(styles, "style").find((s) => s.getAttributeNS(W, "styleId") === "ListBullet");
    expect(all(added as Element, "numPr").length).toBe(0);
  });

  test("an unreadable template falls back to a new document", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { doc } = write("<p>a</p>", new Map(), strToU8("not a zip"));
    expect(paras(doc).map(text)).toEqual(["a"]);
  });
});

// ----- broken or unusual templates (review round 1) -----------------------------------------

/** A template made by the writer, with `edit` applied to its files. */
function editedTemplate(edit: (files: Files) => void): Uint8Array {
  const files = unzip(writeDocx("<p>seed</p>", new Map()));
  edit(files);
  return zipSync(files);
}

describe("template edge cases", () => {
  test("parts named by the relationships but missing get no second relationship", () => {
    const template = editedTemplate((f) => {
      delete f["word/styles.xml"];
      delete f["word/numbering.xml"];
    });
    const { files, doc } = write("<h1>T</h1><ul><li>a</li></ul>", new Map(), template);
    const r = rels(files);
    for (const kind of ["/styles", "/numbering"]) {
      const of = r.filter((x) => x.type.endsWith(kind));
      expect(of.length).toBe(1);
      expect(Object.keys(files)).toContain(`word/${of[0].target}`);
    }
    expect(pStyle(paras(doc)[0])).toBe("Heading1");
    const overrides = all(part(files, "[Content_Types].xml"), "Override", CT).map((o) =>
      o.getAttribute("PartName"),
    );
    expect(overrides.filter((n) => n === "/word/styles.xml").length).toBe(1);
    expect(overrides.filter((n) => n === "/word/numbering.xml").length).toBe(1);
  });

  test("new numbering goes before w:numIdMacAtCleanup", () => {
    const template = editedTemplate((f) => {
      f["word/numbering.xml"] = strToU8(
        `<w:numbering xmlns:w="${W}"><w:abstractNum w:abstractNumId="4"><w:lvl w:ilvl="0"/>` +
          '</w:abstractNum><w:numIdMacAtCleanup w:val="3"/></w:numbering>',
      );
    });
    const { files } = write("<ul><li>a</li></ul>", new Map(), template);
    const root = part(files, "word/numbering.xml").documentElement;
    expect(Array.from(root.children).map((c) => c.localName)).toEqual([
      "abstractNum",
      "abstractNum",
      "num",
      "numIdMacAtCleanup",
    ]);
  });

  test("Hyperlink and Code get free ids when theirs are taken by other styles", () => {
    const template = makeTemplate((styles) => {
      for (const s of all(styles, "style")) {
        const id = s.getAttributeNS(W, "styleId");
        if (id === "Hyperlink") kid(s, "name")?.setAttributeNS(W, "w:val", "Fancy");
        if (id === "Code") kid(s, "name")?.setAttributeNS(W, "w:val", "Mono");
      }
    });
    const { files, doc } = write(
      '<p><a href="https://x.org/">x</a></p><pre>c</pre>',
      new Map(),
      template,
    );
    const styles = all(part(files, "word/styles.xml"), "style");
    const idOf = (name: string) =>
      styles.find((s) => val(kid(s, "name"))?.toLowerCase() === name)?.getAttributeNS(W, "styleId");
    const ids = styles.map((s) => s.getAttributeNS(W, "styleId"));
    expect(new Set(ids).size).toBe(ids.length);
    expect(idOf("hyperlink")).toBe("Hyperlink1");
    expect(idOf("code")).toBe("Code1");
    const link = all(doc, "hyperlink")[0];
    expect(val(kid(kid(kid(link, "r"), "rPr"), "rStyle"))).toBe("Hyperlink1");
    expect(pStyle(paras(doc)[1])).toBe("Code1");
  });

  test("a prefix the template binds to another namespace is left alone", () => {
    const DML = "http://schemas.openxmlformats.org/drawingml/2006/main";
    const template = editedTemplate((f) => {
      const xml = strFromU8(f["word/document.xml"]);
      f["word/document.xml"] = strToU8(xml.replace(`xmlns:a="${DML}"`, 'xmlns:a="urn:other"'));
    });
    const { doc } = write(
      '<p><img src="img/1.png" width="2"></p>',
      new Map([["img/1.png", PNG]]),
      template,
    );
    expect(doc.documentElement.lookupNamespaceURI("a")).toBe("urn:other");
    expect(all(doc, "blip", DML).length).toBe(1);
    expect(all(doc, "graphic", DML).length).toBe(1);
  });

  test("part names are matched case-insensitively and percent-decoded", () => {
    const template = editedTemplate((f) => {
      f["word/My Header.xml"] = strToU8(HEADER);
      f["word/_rels/document.xml.rels"] = strToU8(
        strFromU8(f["word/_rels/document.xml.rels"]).replace(
          "</Relationships>",
          '<Relationship Id="rIdHead" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="MY%20HEADER.XML"/></Relationships>',
        ),
      );
      f["[Content_Types].xml"] = strToU8(
        strFromU8(f["[Content_Types].xml"]).replace(
          "</Types>",
          '<Override PartName="/word/my%20header.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>',
        ),
      );
    });
    const { files } = write("<p>new</p>", new Map(), template);
    expect(strFromU8(files["word/My Header.xml"])).toBe(HEADER);
    const overrides = all(part(files, "[Content_Types].xml"), "Override", CT).map((o) =>
      o.getAttribute("PartName"),
    );
    expect(overrides).toContain("/word/my%20header.xml");
  });
});

// ----- writer leftovers (task 4) ------------------------------------------------------------

describe("writer: names and ids that are already taken", () => {
  test("a new part never collides case-insensitively with an existing one", () => {
    const template = editedTemplate((f) => {
      f["word/media/IMAGE1.PNG"] = PNG;
      f["word/_rels/document.xml.rels"] = strToU8(
        strFromU8(f["word/_rels/document.xml.rels"]).replace(
          "</Relationships>",
          '<Relationship Id="rIdX" Type="http://example.com/other" Target="media/IMAGE1.PNG"/></Relationships>',
        ),
      );
    });
    const { files } = write(
      '<p><img src="img/1.png" width="2"></p>',
      new Map([["img/1.png", PNG]]),
      template,
    );
    const keys = Object.keys(files).map((n) => n.toLowerCase());
    expect(new Set(keys).size).toBe(keys.length);
    expect(Object.keys(files)).toContain("word/media/IMAGE1.PNG");
    const img = rels(files).find((x) => x.type.endsWith("/image"));
    expect(img?.target).toBe("media/image2.png");
  });

  test("a built-in style whose id is taken gets the first free id", () => {
    const template = makeTemplate((styles) => {
      for (const s of all(styles, "style")) {
        const name = (val(kid(s, "name")) ?? "").toLowerCase();
        if (name === "heading 1" || name === "quote") s.remove();
      }
      for (const s of all(styles, "style")) {
        const id = s.getAttributeNS(W, "styleId");
        if (id === "Heading2") s.setAttributeNS(W, "w:styleId", "Heading1"); // heading 2, Heading 1's id
        if (id === "ListBullet") s.setAttributeNS(W, "w:styleId", "Quote");
      }
      for (const p of all(styles, "basedOn")) {
        if (p.getAttributeNS(W, "val") === "Heading2") p.setAttributeNS(W, "w:val", "Heading1");
      }
    });
    const source = "<h1>T</h1><h2>U</h2><blockquote><p>q</p></blockquote>";
    const { files, doc } = write(source, new Map(), template);
    const styles = all(part(files, "word/styles.xml"), "style");
    const ids = styles.map((s) => s.getAttributeNS(W, "styleId"));
    expect(new Set(ids).size).toBe(ids.length);
    const idOf = (name: string) =>
      styles.find((s) => val(kid(s, "name"))?.toLowerCase() === name)?.getAttributeNS(W, "styleId");
    expect(idOf("heading 1")).toBe("Heading11");
    expect(idOf("quote")).toBe("Quote1");
    expect(paras(doc).map(pStyle)).toEqual(["Heading11", "Heading1", "Quote1"]);
    expect(readDocx(writeDocx(source, new Map(), template)).html).toBe(source);
  });
});

// ----- reader ---------------------------------------------------------------------------------

const NBSP = " ";
const DML = "http://schemas.openxmlformats.org/drawingml/2006/main";
const PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture";
const WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape";
const VML = "urn:schemas-microsoft-com:vml";
const RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const DOC_NS =
  `xmlns:w="${W}" xmlns:r="${R}" xmlns:wp="${WP}" xmlns:a="${DML}" xmlns:pic="${PIC}" ` +
  `xmlns:wps="${WPS}" xmlns:v="${VML}"`;
const HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';

interface Extra {
  /** w:style elements added to styles.xml. */
  styles?: string;
  /** The content of numbering.xml (replaces what is there). */
  numbering?: string;
  /** Relationship elements added to the document's relationships. */
  rels?: string;
  /** Default / Override elements added to [Content_Types].xml. */
  types?: string;
  /** The body's final w:sectPr. */
  sect?: string;
  files?: Record<string, string | Uint8Array>;
}

/** A DOCX whose body is `bodyXml` (w: elements), with the writer's styles and parts. */
function makeDocx(bodyXml: string, extra: Extra = {}): Uint8Array {
  const files = unzip(writeDocx("<p>seed</p>", new Map()));
  const sect = extra.sect ?? '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>';
  files["word/document.xml"] = strToU8(
    `${HEAD}<w:document ${DOC_NS}><w:body>${bodyXml}${sect}</w:body></w:document>`,
  );
  const add = (name: string, end: string, xml: string | undefined) => {
    if (xml) files[name] = strToU8(strFromU8(files[name]).replace(end, `${xml}${end}`));
  };
  add("word/styles.xml", "</w:styles>", extra.styles);
  add("word/_rels/document.xml.rels", "</Relationships>", extra.rels);
  add("[Content_Types].xml", "</Types>", extra.types);
  if (extra.numbering !== undefined) {
    files["word/numbering.xml"] = strToU8(
      `${HEAD}<w:numbering xmlns:w="${W}">${extra.numbering}</w:numbering>`,
    );
  }
  for (const [name, data] of Object.entries(extra.files ?? {})) {
    files[name] = typeof data === "string" ? strToU8(data) : data;
  }
  return zipSync(files);
}

const wt = (s: string) => `<w:t xml:space="preserve">${s}</w:t>`;
const run = (s: string, rPr = "") => `<w:r>${rPr ? `<w:rPr>${rPr}</w:rPr>` : ""}${wt(s)}</w:r>`;
const para = (inner: string, pPr = "") => `<w:p>${pPr ? `<w:pPr>${pPr}</w:pPr>` : ""}${inner}</w:p>`;
const read = (body: string, extra?: Extra) => readDocx(makeDocx(body, extra));
const html = (body: string, extra?: Extra) => read(body, extra).html;
const abstractNum = (id: number, fmt: string) =>
  `<w:abstractNum w:abstractNumId="${id}"><w:lvl w:ilvl="0"><w:numFmt w:val="${fmt}"/></w:lvl>` +
  `<w:lvl w:ilvl="1"><w:numFmt w:val="${fmt}"/></w:lvl></w:abstractNum>`;
const num = (id: number, abstract: number) =>
  `<w:num w:numId="${id}"><w:abstractNumId w:val="${abstract}"/></w:num>`;
const listPr = (numId: number, ilvl = 0) =>
  `<w:numPr><w:ilvl w:val="${ilvl}"/><w:numId w:val="${numId}"/></w:numPr>`;
const pStyleOf = (id: string) => `<w:pStyle w:val="${id}"/>`;
const pStyleXml = (id: string, name: string, inner = "") =>
  `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="${name}"/>${inner}</w:style>`;
const charStyle = (id: string, inner: string) =>
  `<w:style w:type="character" w:styleId="${id}"><w:name w:val="${id}"/>${inner}</w:style>`;
const cell = (inner: string, tcPr = "") => `<w:tc><w:tcPr>${tcPr}</w:tcPr>${inner}</w:tc>`;
const drawing = (rid: string, cx = 952500, cy = 476250) =>
  `<w:r><w:drawing><wp:inline><wp:extent cx="${cx}" cy="${cy}"/><wp:docPr id="1" name="P"/>` +
  `<a:graphic><a:graphicData uri="${PIC}"><pic:pic><pic:blipFill><a:blip r:embed="${rid}"/>` +
  "</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>";
const imageRel = (id: string, target: string) =>
  `<Relationship Id="${id}" Type="${RT}/image" Target="${target}"/>`;

describe("reader: round trips", () => {
  test.each((vectorsJson as { cases: string[] }).cases)("shared case reads back: %s", (c) => {
    expect(readDocx(writeDocx(c, new Map())).html).toBe(normaliseHtml(c).html);
  });

  test.each([
    "<ul><li>a<ul><li>b<ol><li>c</li></ol></li></ul></li></ul><ol><li>d</li></ol>",
    "<table><tr><td><ul><li>a</li><li>b</li></ul></td></tr></table>",
    '<table><tr><td rowspan="2"><p>a</p></td><td><p>b</p></td></tr><tr><td><p>c</p></td></tr></table>',
    "<table><tr><td><table><tr><td><p>a</p></td></tr></table></td></tr></table><p>after</p>",
    `<p>Name:\tJohn ${NBSP}${NBSP}${NBSP}Smith</p><p>${NBSP}${NBSP}indented</p>`,
    "<pre>a  b\n  c</pre>",
  ])("nested lists, tables, white space: %s", (c) => {
    expect(readDocx(writeDocx(c, new Map())).html).toBe(normaliseHtml(c).html);
  });

  test("a picture comes back with its bytes and size", () => {
    const got = readDocx(
      writeDocx('<p><img src="img/1.png" width="96"></p>', new Map([["img/1.png", PNG]])),
    );
    expect(got.images).toEqual(new Map([["img/1.png", PNG]]));
    expect(got.html).toContain('src="img/1.png"');
    expect(got.html).toContain('width="96"');
  });

  test("unicode", () => {
    const c = "<p>Blåbærgrød — Привет — 你好 — 😀</p>";
    expect(readDocx(writeDocx(c, new Map())).html).toBe(c);
  });

  test("the opened document keeps the original bytes as its template", () => {
    const bytes = writeDocx("<p>a</p>", new Map());
    const got = readDocx(bytes);
    expect(got.sourceFormat).toBe("docx");
    expect(got.template).toBe(bytes);
    expect(got.warnings).toEqual([]);
    expect(got.frontMatter).toBe("");
    expect(got.title).toBe("");
  });

  test("an unreadable file is a plain error", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const message = "This Word document could not be opened.";
    expect(() => readDocx(strToU8("nope"))).toThrow(message);
    const noDocument = unzip(writeDocx("<p>a</p>", new Map()));
    delete noDocument["word/document.xml"];
    expect(() => readDocx(zipSync(noDocument))).toThrow(message);
    const broken = unzip(writeDocx("<p>a</p>", new Map()));
    broken["word/document.xml"] = strToU8("<w:document");
    expect(() => readDocx(zipSync(broken))).toThrow(message);
  });
});

describe("reader: paragraphs and runs", () => {
  test("a page break run splits the paragraph", () => {
    const body = para(`${run("a")}<w:r><w:br w:type="page"/></w:r>${run("b")}`);
    expect(html(body)).toBe('<p>a</p><p style="page-break-before:always">b</p>');
  });

  test("Heading 4 is h3, Title is h1, Quote and Code", () => {
    const styles =
      pStyleXml("Heading4", "heading 4") +
      pStyleXml("Title", "Title") +
      pStyleXml("MyQuote", "Intense Quote") +
      pStyleXml("Src", "Source Code");
    const body =
      para(run("Top"), pStyleOf("Heading4")) +
      para(run("x"), pStyleOf("Title")) +
      para(run("q"), pStyleOf("MyQuote")) +
      para(run("a  b"), pStyleOf("Src"));
    expect(html(body, { styles })).toBe(
      "<h3>Top</h3><h1>x</h1><blockquote><p>q</p></blockquote><pre>a  b</pre>",
    );
  });

  test("a style based on a heading is a heading", () => {
    const styles = pStyleXml("Mine", "Mine", '<w:basedOn w:val="Heading2"/>');
    expect(html(para(run("h"), pStyleOf("Mine")), { styles })).toBe("<h2>h</h2>");
  });

  test("tabs, and spaces HTML would collapse, across runs and breaks", () => {
    const body =
      para(`${run("Name:")}<w:r><w:tab/></w:r>${run("John  ")}${run("  Smith")}`) +
      para(run("  indented")) +
      para(`${run("a")}<w:r><w:br/></w:r>${run(" b")}<w:r><w:noBreakHyphen/></w:r>${run("c")}`);
    expect(html(body)).toBe(
      `<p>Name:\tJohn ${NBSP}${NBSP}${NBSP}Smith</p><p>${NBSP}${NBSP}indented</p>` +
        `<p>a<br>${NBSP}b-c</p>`,
    );
  });

  test("highlight colour and shading", () => {
    expect(html(para(run("h", '<w:highlight w:val="yellow"/>')))).toBe(
      '<p><span style="background-color:#ffff00">h</span></p>',
    );
    expect(html(para(run("s", '<w:shd w:val="clear" w:fill="FFCC00"/>')))).toBe(
      '<p><span style="background-color:#ffcc00">s</span></p>',
    );
  });

  test("direct run properties", () => {
    const rPr =
      '<w:rFonts w:ascii="Arial"/><w:b/><w:i/><w:strike/><w:color w:val="FF0000"/>' +
      '<w:sz w:val="21"/><w:u w:val="single"/>';
    expect(html(para(run("f", rPr) + run("g", '<w:b w:val="0"/><w:u w:val="none"/>')))).toBe(
      '<p><b><i><u><s><span style="font-family:Arial;font-size:10.5pt;color:#ff0000">f</span>' +
        "</s></u></i></b>g</p>",
    );
  });

  test("a character style becomes formatting; paragraph styles add none", () => {
    const styles =
      charStyle("Strong", "<w:rPr><w:b/></w:rPr>") +
      pStyleXml("BoldPara", "Bold Para", "<w:rPr><w:b/><w:i/></w:rPr>");
    const body = para(
      `${run("Plain then ")}${run("strong words", '<w:rStyle w:val="Strong"/>')}`,
      pStyleOf("BoldPara"),
    );
    expect(html(body, { styles })).toBe("<p>Plain then <b>strong words</b></p>");
  });

  test("character style chain, and direct formatting wins", () => {
    const styles =
      charStyle("Loud", "<w:rPr><w:i/></w:rPr>") +
      charStyle(
        "Louder",
        '<w:basedOn w:val="Loud"/><w:rPr><w:b/><w:color w:val="FF0000"/></w:rPr>',
      );
    const style = '<w:rStyle w:val="Louder"/>';
    const body = para(run("a", style) + run("b", `${style}<w:b w:val="0"/>`));
    expect(html(body, { styles })).toBe(
      '<p><b><i><span style="color:#ff0000">a</span></i></b>' +
        '<i><span style="color:#ff0000">b</span></i></p>',
    );
  });

  test("a link keeps its href but not the look of its style", () => {
    const rels = `<Relationship Id="rIdL" Type="${RT}/hyperlink" Target="https://x.org/?a=1&amp;b=2" TargetMode="External"/>`;
    const style = '<w:rStyle w:val="Hyperlink"/>';
    const body = para(
      `${run("see ")}<w:hyperlink r:id="rIdL">${run("x", style)}${run("y", `${style}<w:b/>`)}</w:hyperlink>`,
    );
    expect(html(body, { rels })).toBe('<p>see <a href="https://x.org/?a=1&amp;b=2">x<b>y</b></a></p>');
  });

  test("a link to a bookmark (no relationship) is plain text", () => {
    expect(html(para(`<w:hyperlink w:anchor="top">${run("x")}</w:hyperlink>`))).toBe("<p>x</p>");
  });

  test("alignment, indent, page break before and a rule", () => {
    const body =
      para(run("c"), '<w:jc w:val="center"/>') +
      para(run("j"), '<w:ind w:left="1440"/><w:jc w:val="both"/>') +
      para(run("n"), "<w:pageBreakBefore/>") +
      para("", '<w:pBdr><w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/></w:pBdr>');
    expect(html(body)).toBe(
      '<p style="text-align:center">c</p><p style="text-align:justify;margin-left:72pt">j</p>' +
        '<p style="page-break-before:always">n</p><hr>',
    );
  });
});

describe("reader: wrappers", () => {
  test.each([
    ["w:customXml", false],
    ["w:smartTag", false],
    ["w:ins", true],
    ["w:moveTo", true],
  ])("block wrapper %s keeps its text", (wrapper, tracked) => {
    const attrs = tracked ? ' w:id="1" w:author="A"' : "";
    const got = read(`<${wrapper}${attrs}>${para(run("cx"))}</${wrapper}>`);
    expect(got.html).toBe("<p>cx</p>");
    expect(got.warnings.includes(W_TRACKED)).toBe(tracked);
  });

  test.each(["w:del", "w:moveFrom"])("block deletion %s is dropped", (wrapper) => {
    const body = `<${wrapper} w:id="1" w:author="A">${para(run("gone"))}</${wrapper}>${para(run("kept"))}`;
    expect(html(body)).toBe("<p>kept</p>");
  });

  test("content controls keep their text", () => {
    const body =
      `<w:sdt><w:sdtPr/><w:sdtContent>${para(run("block"))}</w:sdtContent></w:sdt>` +
      para(`<w:sdt><w:sdtContent>${run("inline")}</w:sdtContent></w:sdt>`);
    expect(html(body)).toBe("<p>block</p><p>inline</p>");
  });

  test("inline insertions are kept, deletions dropped, simple fields read through", () => {
    const body = para(
      `<w:ins w:id="1" w:author="A">${run("in ")}</w:ins>` +
        `<w:del w:id="2" w:author="A"><w:r><w:delText>out</w:delText></w:r></w:del>` +
        `<w:fldSimple w:instr=" DATE ">${run("today")}</w:fldSimple>`,
    );
    const got = read(body);
    expect(got.html).toBe("<p>in today</p>");
    expect(got.warnings).toEqual([W_TRACKED, W_FIELDS]);
  });
});

describe("reader: lists", () => {
  const numbering = abstractNum(0, "bullet") + abstractNum(1, "decimal") + num(1, 0) + num(2, 1);

  test("lists from numPr, nested and mixed", () => {
    const body =
      para(run("a"), listPr(1)) +
      para(run("b"), listPr(1, 1)) +
      para(run("c"), listPr(2, 1)) +
      para(run("d"), listPr(1)) +
      para(run("x")) +
      para(run("e"), listPr(2));
    expect(html(body, { numbering })).toBe(
      "<ul><li>a<ul><li>b</li></ul><ol><li>c</li></ol></li><li>d</li></ul><p>x</p><ol><li>e</li></ol>",
    );
  });

  test("a list from the paragraph style only", () => {
    const styles =
      pStyleXml("LB", "List Bullet X", `<w:pPr>${listPr(1)}</w:pPr>`) +
      pStyleXml("LN", "List Number X", '<w:pPr><w:numPr><w:numId w:val="2"/></w:numPr></w:pPr>');
    const body = para(run("a"), pStyleOf("LB")) + para(run("b"), pStyleOf("LN"));
    expect(html(body, { styles, numbering })).toBe("<ul><li>a</li></ul><ol><li>b</li></ol>");
  });

  test("numbering format none is not a list; numId 0 neither", () => {
    const none = abstractNum(0, "none") + num(1, 0);
    expect(html(para(run("a"), listPr(1)), { numbering: none })).toBe("<p>a</p>");
    expect(html(para(run("a"), listPr(0)), { numbering })).toBe("<p>a</p>");
  });

  test("lettered and Roman numbering warns and stays a numbered list", () => {
    const files = unzip(writeDocx("<ol><li>a</li><li>b</li></ol>", new Map()));
    files["word/numbering.xml"] = strToU8(
      strFromU8(files["word/numbering.xml"]).replaceAll('w:val="decimal"', 'w:val="lowerLetter"'),
    );
    const got = readDocx(zipSync(files));
    expect(got.warnings).toContain(W_LISTSTYLE);
    expect(got.html).toBe("<ol><li>a</li><li>b</li></ol>");
    expect(readDocx(writeDocx("<ol><li>a</li></ol>", new Map())).warnings).toEqual([]);
  });

  test("a list without its definition is a bullet list", () => {
    expect(html(para(run("a"), listPr(7)), { numbering })).toBe("<ul><li>a</li></ul>");
  });
});

describe("reader: tables", () => {
  test("vertical merge by hand; no borders and no style is borderless", () => {
    const tc = (vm: string | null, s: string) =>
      cell(para(s ? run(s) : ""), vm === null ? "" : `<w:vMerge${vm}/>`);
    const restart = ' w:val="restart"';
    const rows =
      `<w:tr>${tc(restart, "a")}${tc(null, "b")}</w:tr>` +
      `<w:tr>${tc("", "")}${tc(null, "c")}</w:tr>` +
      `<w:tr>${tc("", "")}${tc(null, "d")}</w:tr>`;
    expect(html(`<w:tbl>${rows}</w:tbl>${para(run("after"))}`)).toBe(
      normaliseHtml(
        '<table style="border:none"><tr><td rowspan="3"><p>a</p></td><td><p>b</p></td></tr>' +
          "<tr><td><p>c</p></td></tr><tr><td><p>d</p></td></tr></table><p>after</p>",
      ).html,
    );
  });

  test("cell borders alone make a bordered table; all-none borders don't", () => {
    const edges = (v: string) =>
      `<w:tcBorders>${["top", "left", "bottom", "right"].map((e) => `<w:${e} w:val="${v}" w:sz="4"/>`).join("")}</w:tcBorders>`;
    const table = (v: string) => `<w:tbl><w:tr>${cell(para(run("c")), edges(v))}</w:tr></w:tbl>`;
    expect(html(table("single"))).toBe("<table><tr><td><p>c</p></td></tr></table>");
    expect(html(table("nil"))).toBe('<table style="border:none"><tr><td><p>c</p></td></tr></table>');
  });

  test("borders from the table style chain", () => {
    const styles =
      '<w:style w:type="table" w:styleId="Base"><w:name w:val="Base"/><w:tblPr><w:tblBorders>' +
      '<w:top w:val="single"/></w:tblBorders></w:tblPr></w:style>' +
      '<w:style w:type="table" w:styleId="Mine"><w:name w:val="Mine"/><w:basedOn w:val="Base"/></w:style>';
    const body = `<w:tbl><w:tblPr><w:tblStyle w:val="Mine"/></w:tblPr><w:tr>${cell(para(run("c")))}</w:tr></w:tbl>`;
    expect(html(body, { styles })).toBe("<table><tr><td><p>c</p></td></tr></table>");
  });

  test("an empty cell is one empty paragraph; colspan; a cell's paragraph after a table", () => {
    const inner = `<w:tbl><w:tr>${cell(para(run("i")))}</w:tr></w:tbl>`;
    const body =
      `<w:tbl><w:tr>${cell("<w:p/>", '<w:gridSpan w:val="2"/>')}</w:tr>` +
      `<w:tr>${cell("")}${cell(`${inner}<w:p/>`)}</w:tr></w:tbl><w:p/>`;
    expect(html(body)).toBe(
      '<table style="border:none"><tr><td colspan="2"><p></p></td></tr><tr><td><p></p></td>' +
        '<td><table style="border:none"><tr><td><p>i</p></td></tr></table></td></tr></table>',
    );
  });
});

describe("reader: pictures", () => {
  const formats: [string, string, string][] = [
    ["png", "image/png", "png"],
    ["jpeg", "image/jpeg", "jpg"],
    ["gif", "image/gif", "gif"],
    ["bmp", "image/bmp", "bmp"],
    ["webp", "image/webp", "webp"],
  ];
  test.each(formats)("%s is kept", (ext, type, out) => {
    const data = Uint8Array.of(1, 2, 3, ext.length);
    const got = read(para(drawing("rIdP")), {
      rels: imageRel("rIdP", `media/p.${ext}`),
      types: `<Default Extension="${ext}" ContentType="${type}"/>`,
      files: { [`word/media/p.${ext}`]: data },
    });
    expect(got.images).toEqual(new Map([[`img/1.${out}`, data]]));
    expect(got.html).toBe(`<p><img src="img/1.${out}" width="100" height="50"></p>`);
    expect(got.warnings).toEqual([]);
  });

  test("unsupported_picture_skipped_on_web", () => {
    // The desktop converts TIFF / EMF to PNG with Pillow; the website has no such decoder.
    for (const [ext, type] of [
      ["tif", "image/tiff"],
      ["emf", "image/x-emf"],
    ]) {
      vi.spyOn(console, "warn").mockImplementation(() => {});
      const got = read(para(run("x") + drawing("rIdP")), {
        rels: imageRel("rIdP", `media/p.${ext}`),
        types: `<Default Extension="${ext}" ContentType="${type}"/>`,
        files: { [`word/media/p.${ext}`]: Uint8Array.of(1, 2, 3) },
      });
      expect(got.warnings).toEqual([W_PICTURES]);
      expect(got.html).toBe("<p>x</p>");
      expect(got.images.size).toBe(0);
    }
  });

  test("a picture whose part is missing warns", () => {
    const got = read(para(drawing("rIdNone")));
    expect(got.warnings).toEqual([W_PICTURES]);
    expect(got.html).toBe("<p></p>");
  });

  test("the same picture twice is two names", () => {
    const got = read(para(drawing("rIdP") + drawing("rIdP", 9525, 9525)), {
      rels: imageRel("rIdP", "media/p.png"),
      types: '<Default Extension="png" ContentType="image/png"/>',
      files: { "word/media/p.png": PNG },
    });
    expect(Array.from(got.images.keys())).toEqual(["img/1.png", "img/2.png"]);
  });
});

describe("reader: warnings", () => {
  const headerRel = `<Relationship Id="rIdH" Type="${RT}/header" Target="header1.xml"/>`;
  const headerSect = '<w:sectPr><w:headerReference w:type="default" r:id="rIdH"/></w:sectPr>';
  const header = (inner: string) => `${HEAD}<w:hdr xmlns:w="${W}"><w:p>${inner}</w:p></w:hdr>`;

  test("headers and footers with content; empty ones are fine", () => {
    const extra = (inner: string): Extra => ({
      sect: headerSect,
      rels: headerRel,
      files: { "word/header1.xml": header(inner) },
    });
    expect(read(para(run("x")), extra(run("Head"))).warnings).toEqual([W_HEADERS]);
    expect(read(para(run("x")), extra("")).warnings).toEqual([]);
  });

  test("comments", () => {
    const got = read(para(`${run("x")}<w:r><w:commentReference w:id="0"/></w:r>`), {
      rels: `<Relationship Id="rIdC" Type="${RT}/comments" Target="comments.xml"/>`,
      files: { "word/comments.xml": `${HEAD}<w:comments xmlns:w="${W}"/>` },
    });
    expect(got.warnings).toEqual([W_COMMENTS]);
  });

  test("text boxes, columns, notes, fields and objects", () => {
    const body =
      para(
        `<w:r><w:pict><v:shape><v:textbox><w:txbxContent>${para(run("tb"))}</w:txbxContent>` +
          "</v:textbox></v:shape></w:pict></w:r>",
      ) +
      para(`${run("a")}<w:r><w:footnoteReference w:id="1"/></w:r>`) +
      para('<w:r><w:instrText xml:space="preserve"> TOC \\o </w:instrText></w:r>') +
      para("<w:r><w:object/></w:r>");
    const got = read(body, { sect: '<w:sectPr><w:cols w:num="2"/></w:sectPr>' });
    expect(got.warnings).toEqual([W_TEXTBOX, W_COLUMNS, W_NOTES, W_FIELDS, W_OBJECTS]);
  });

  test("page number fields are fine", () => {
    const body =
      para('<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>') +
      para(`<w:fldSimple w:instr="NUMPAGES">${run("3")}</w:fldSimple>`);
    expect(read(body).warnings).toEqual([]);
  });

  test("order: found while reading, then headers, then the rest", () => {
    const numbering = abstractNum(0, "upperRoman") + num(1, 0);
    const got = read(
      para(`${run("a")}<w:r><w:commentReference w:id="0"/></w:r>`, listPr(1)) +
        para(drawing("rIdNone")),
      {
        numbering,
        sect: headerSect,
        rels: headerRel,
        files: { "word/header1.xml": header(run("Head")) },
      },
    );
    expect(got.warnings).toEqual([W_LISTSTYLE, W_PICTURES, W_HEADERS, W_COMMENTS]);
  });
});

describe("reader: large documents", () => {
  test("5 000 paragraphs and 5 000 lists read in linear time", () => {
    const n = 5000;
    const body: string[] = [];
    const numbering = [abstractNum(0, "decimal")];
    for (let i = 1; i <= n; i++) {
      body.push(para(run(`p${i}  x`)), para(run(`l${i}`), listPr(i)));
      numbering.push(num(i, 0));
    }
    const started = performance.now();
    const got = read(body.join(""), { numbering: numbering.join("") });
    const ms = performance.now() - started;
    expect(got.html.startsWith(`<p>p1 ${NBSP}x</p><ol><li>l1</li></ol><p>p2`)).toBe(true);
    expect(got.html.split("<ol>").length).toBe(n + 1);
    expect(ms).toBeLessThan(20000);
  });
});

// ----- fix round 1: encodings, template fallback, no styles part ---------------------------

/** `text` as single bytes (Latin-1, plus the windows-1252 characters used here). */
function singleByte(text: string): Uint8Array {
  const cp1252: Record<string, number> = { "€": 0x80, "–": 0x96, "“": 0x93, "”": 0x94 };
  return Uint8Array.from(Array.from(text), (c) => cp1252[c] ?? c.charCodeAt(0));
}

/** `text` as UTF-16LE with a byte order mark. */
function utf16(text: string): Uint8Array {
  const out = new Uint8Array(2 + text.length * 2);
  out[0] = 0xff;
  out[1] = 0xfe;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    out[2 + 2 * i] = code & 0xff;
    out[3 + 2 * i] = code >> 8;
  }
  return out;
}

const declared = (encoding: string, xml: string) =>
  `<?xml version="1.0" encoding="${encoding}" standalone="yes"?>\n${xml.replace(/^<\?xml[^>]*\?>\s*/, "")}`;

describe("XML encodings", () => {
  test.each([
    ["ISO-8859-1", "café naïve"],
    ["windows-1252", "café – “€”"],
  ])("a document.xml declared %s is decoded by its declaration", (encoding, words) => {
    const xml = declared(
      encoding,
      `<w:document xmlns:w="${W}"><w:body>${para(run(words))}<w:sectPr/></w:body></w:document>`,
    );
    const got = read("", { files: { "word/document.xml": singleByte(xml) } });
    expect(got.html).toBe(`<p>${words}</p>`);
  });

  test("a UTF-16 template is used, and the parts the writer changes become UTF-8", () => {
    const files = unzip(makeTemplate());
    for (const name of [
      "[Content_Types].xml",
      "_rels/.rels",
      "word/document.xml",
      "word/_rels/document.xml.rels",
      "word/styles.xml",
      "word/header1.xml",
    ]) {
      files[name] = utf16(declared("UTF-16", strFromU8(files[name])));
    }
    const template = zipSync(files);
    const onWarning = vi.fn();
    const bytes = writeDocx("<p>new</p>", new Map(), template, { onWarning });
    expect(onWarning).not.toHaveBeenCalled();
    const out = unzip(bytes);
    const xml = strFromU8(out["word/document.xml"]);
    expect(xml.startsWith('<?xml version="1.0" encoding="UTF-8"')).toBe(true);
    const sect = kid(body(parse(xml)), "sectPr") as Element;
    expect(kid(sect, "pgSz")?.getAttributeNS(W, "orient")).toBe("landscape");
    expect(kid(sect, "headerReference")?.getAttributeNS(R, "id")).toBe("rIdHead");
    expect(out["word/header1.xml"]).toEqual(files["word/header1.xml"]);
    const back = readDocx(bytes);
    expect(back.html).toBe("<p>new</p>");
    expect(back.warnings).toEqual([W_HEADERS]); // the UTF-16 header is read too
  });
});

describe("template fallback", () => {
  test("is reported through onWarning and console.error", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const onWarning = vi.fn();
    const files = unzip(writeDocx("<p>a</p>", new Map(), strToU8("not a zip"), { onWarning }));
    expect(paras(part(files, "word/document.xml")).map(text)).toEqual(["a"]);
    expect(onWarning).toHaveBeenCalledTimes(1);
    expect(onWarning).toHaveBeenCalledWith(
      "Couldn't keep the original file's layout; it was saved with default styles.",
    );
    expect(error).toHaveBeenCalled();
    onWarning.mockClear();
    writeDocx("<p>a</p>", new Map(), makeTemplate(), { onWarning });
    writeDocx("<p>a</p>", new Map(), null, { onWarning });
    expect(onWarning).not.toHaveBeenCalled();
  });
});

describe("reader: a package without styles.xml", () => {
  test("reads like the desktop (Word's built-in style ids are recognised)", () => {
    const source =
      "<h1>A</h1><h2>B</h2><blockquote><p>q</p></blockquote><pre>c</pre><p>x</p>" +
      "<table><tr><td><p>t</p></td></tr></table>";
    const files = unzip(writeDocx(source, new Map()));
    delete files["word/styles.xml"];
    const drop = (name: string, re: RegExp) => {
      files[name] = strToU8(strFromU8(files[name]).replace(re, ""));
    };
    drop("word/_rels/document.xml.rels", /<Relationship [^>]*\/styles"[^>]*\/>/);
    drop("[Content_Types].xml", /<Override PartName="\/word\/styles.xml"[^>]*\/>/);
    // the desktop's docx_reader.read() on the same file (scratchpad probe, Task 4f)
    expect(readDocx(zipSync(files)).html).toBe(
      "<h1>A</h1><h2>B</h2><blockquote><p>q</p></blockquote><pre>c</pre><p>x</p>" +
        "<table><tr><td><p>t</p></td></tr></table>",
    );
  });
});

// ----- content the readers used to drop (shared "docx" vectors) ---------------------------

interface DocxVector {
  name: string;
  body: string;
  rels?: string;
  types?: string;
  parts?: Record<string, string>;
  html: string;
  warnings: string[];
}
const docxVectors = (vectorsJson as unknown as { docx: DocxVector[] }).docx;

/** The same package as `docx_package()` in tests/doceditor_samples.py. */
function vectorDocx(v: { body: string; rels?: string; types?: string; parts?: Record<string, string> }) {
  const ns =
    `xmlns:w="${W}" xmlns:r="${R}" ` +
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" ' +
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"';
  const head = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>';
  const files: Record<string, string> = {
    "[Content_Types].xml":
      `${head}<Types xmlns="${CT}"><Default Extension="rels" ContentType="application/vnd.` +
      'openxmlformats-package.relationships+xml"/><Default Extension="xml" ' +
      'ContentType="application/xml"/><Override PartName="/word/document.xml" ' +
      'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.' +
      `document.main+xml"/>${v.types ?? ""}</Types>`,
    "_rels/.rels":
      `${head}<Relationships xmlns="${PKG_RELS}"><Relationship Id="rId1" ` +
      `Type="${RT}/officeDocument" Target="word/document.xml"/></Relationships>`,
    "word/document.xml": `${head}<w:document ${ns}><w:body>${v.body}<w:sectPr/></w:body></w:document>`,
    "word/_rels/document.xml.rels": `${head}<Relationships xmlns="${PKG_RELS}">${v.rels ?? ""}</Relationships>`,
    ...(v.parts ?? {}),
  };
  return zipSync(Object.fromEntries(Object.entries(files).map(([k, s]) => [k, strToU8(s)])));
}

describe("shared docx vectors", () => {
  test("cover every case", () => {
    expect(docxVectors.length).toBe(11);
    expect(docxVectors.filter((v) => v.warnings.includes(W_EQUATIONS)).length).toBe(2);
  });
  test.each(docxVectors.map((v) => [v.name, v] as const))("%s", (_name, v) => {
    const got = readDocx(vectorDocx(v));
    expect([got.html, got.warnings]).toEqual([v.html, v.warnings]);
  });
});

// ----- zip bombs ----------------------------------------------------------------------------

/** `data` (a zip) with entry `name`'s uncompressed size in the central directory set to `size`. */
function claimedSize(data: Uint8Array, name: string, size: number): Uint8Array {
  const out = data.slice();
  const view = new DataView(out.buffer);
  for (let at = 0; at + 46 <= out.length; at++) {
    if (view.getUint32(at, true) !== 0x02014b50) continue;
    const len = view.getUint16(at + 28, true);
    if (strFromU8(out.subarray(at + 46, at + 46 + len)) === name) view.setUint32(at + 24, size, true);
  }
  return out;
}

describe("zip bombs", () => {
  const good = () => vectorDocx({ body: para(run("x")) });

  test("a package that claims to unpack past the limit is refused", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const one = claimedSize(good(), "word/document.xml", MAX_UNPACKED_BYTES + 1);
    const half = MAX_UNPACKED_BYTES / 2 + 1;
    const two = claimedSize(claimedSize(good(), "word/document.xml", half), "_rels/.rels", half);
    for (const data of [one, two]) expect(() => readDocx(data)).toThrow(TOO_LARGE);
    error.mockRestore();
  });

  test("an entry that inflates past its declared size is not unpacked further", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const big = vectorDocx({ body: para(run("x".repeat(2_000_000))) });
    expect(() => readDocx(claimedSize(big, "word/document.xml", 100))).toThrow(
      "This Word document could not be opened.",
    );
    error.mockRestore();
  });

  test("a normal package opens", () => {
    expect(readDocx(good()).html).toBe("<p>x</p>");
  });

  test("claims that add up to exactly the limit open; one byte more is refused", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const zip = good();
    let others = 0;
    unzipSync(zip, {
      filter: (f) => {
        if (f.name !== "_rels/.rels") others += f.originalSize;
        return false;
      },
    });
    const at = (extra: number) =>
      readDocx(claimedSize(zip, "_rels/.rels", MAX_UNPACKED_BYTES - others + extra));
    expect(at(0).html).toBe("<p>x</p>");
    expect(() => at(1)).toThrow(TOO_LARGE);
    error.mockRestore();
  });

  test("an entry claiming 100 bytes that inflates to 200 MB is stopped quickly", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    // 200 MB of zeros, deflated in 1 MB pieces (never 200 MB in memory)
    const pieces: Uint8Array[] = [];
    const deflate = new Deflate({ level: 1 }, (chunk) => {
      pieces.push(chunk);
    });
    const zeros = new Uint8Array(1 << 20);
    for (let i = 0; i < 200; i++) deflate.push(zeros, i === 199);
    const files = unzip(good());
    const bomb = rawZip([
      ...Object.entries(files).filter(([name]) => name !== "word/document.xml"),
      ["word/document.xml", joined(pieces), 100],
    ]);
    const start = performance.now();
    expect(() => readDocx(bomb)).toThrow("This Word document could not be opened.");
    expect(performance.now() - start).toBeLessThan(1000);
    error.mockRestore();
  });
});

describe("zip checksums", () => {
  const MESSAGE = "This Word document could not be opened.";
  /** Where entry `name`'s central-directory record (and its data) sit in a zip. */
  function locate(zip: Uint8Array, name: string) {
    const view = new DataView(zip.buffer, zip.byteOffset, zip.byteLength);
    for (let at = 0; at + 46 <= zip.length; at++) {
      if (view.getUint32(at, true) !== 0x02014b50) continue;
      const len = view.getUint16(at + 28, true);
      if (strFromU8(zip.subarray(at + 46, at + 46 + len)) !== name) continue;
      const local = view.getUint32(at + 42, true);
      const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
      return { cd: at, start, size: view.getUint32(at + 20, true) };
    }
    throw new Error(`no ${name}`);
  }
  const good = () => vectorDocx({ body: para(run("x")) });
  const withPicture = () =>
    writeDocx('<p><img src="img/1.png" width="96"></p>', new Map([["img/1.png", PNG]]), null);

  test("a document the writer made opens again (its checksums are right)", () => {
    expect(readDocx(withPicture()).html).toContain("<img");
  });

  test("one flipped byte inside a stored picture is refused", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const zip = withPicture();
    const { start, size } = locate(zip, "word/media/image1.png");
    zip[start + Math.floor(size / 2)] ^= 0xff;
    expect(() => readDocx(zip)).toThrow(MESSAGE);
    error.mockRestore();
  });

  test("a wrong checksum on a deflated document.xml is refused", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const zip = good();
    const { cd } = locate(zip, "word/document.xml");
    new DataView(zip.buffer).setUint32(cd + 16, 0x12345678, true);
    expect(() => readDocx(zip)).toThrow(MESSAGE);
    error.mockRestore();
  });

  test("one flipped byte inside a deflated document.xml is refused", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    const zip = vectorDocx({ body: para(run("hello world ".repeat(200))) });
    const { start, size } = locate(zip, "word/document.xml");
    zip[start + Math.floor(size / 2)] ^= 0x01;
    expect(() => readDocx(zip)).toThrow(MESSAGE);
    error.mockRestore();
  });

  test("crc32 matches the standard check value", () => {
    expect(crc32(strToU8("123456789"))).toBe(0xcbf43926);
  });
});

function joined(parts: Uint8Array[]): Uint8Array {
  let n = 0;
  for (const p of parts) n += p.length;
  const out = new Uint8Array(n);
  n = 0;
  for (const p of parts) {
    out.set(p, n);
    n += p.length;
  }
  return out;
}

/** A zip of stored entries ([name, data]) and deflated ones ([name, deflated data, the
 * uncompressed size it claims]); stored entries get their real CRC, deflated ones 0. */
function rawZip(entries: ([string, Uint8Array] | [string, Uint8Array, number])[]): Uint8Array {
  const locals: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;
  for (const [name, data, claimed] of entries) {
    const fname = strToU8(name);
    const method = claimed === undefined ? 0 : 8;
    const size = claimed ?? data.length;
    const head = (sig: number, len: number) => {
      const h = new Uint8Array(len + fname.length);
      const v = new DataView(h.buffer);
      v.setUint32(0, sig, true);
      return { h, v };
    };
    const local = head(0x04034b50, 30);
    local.v.setUint16(4, 20, true);
    local.v.setUint16(8, method, true);
    local.v.setUint32(18, data.length, true);
    local.v.setUint32(22, size, true);
    local.v.setUint16(26, fname.length, true);
    local.h.set(fname, 30);
    const cd = head(0x02014b50, 46);
    cd.v.setUint16(4, 20, true);
    cd.v.setUint16(6, 20, true);
    cd.v.setUint16(10, method, true);
    const crc = claimed === undefined ? crc32(data) : 0;
    local.v.setUint32(14, crc, true);
    cd.v.setUint32(16, crc, true);
    cd.v.setUint32(20, data.length, true);
    cd.v.setUint32(24, size, true);
    cd.v.setUint16(28, fname.length, true);
    cd.v.setUint32(42, offset, true);
    cd.h.set(fname, 46);
    locals.push(local.h, data);
    central.push(cd.h);
    offset += local.h.length + data.length;
  }
  const dir = joined(central);
  const end = new Uint8Array(22);
  const v = new DataView(end.buffer);
  v.setUint32(0, 0x06054b50, true);
  v.setUint16(8, entries.length, true);
  v.setUint16(10, entries.length, true);
  v.setUint32(12, dir.length, true);
  v.setUint32(16, offset, true);
  return joined([...locals, dir, end]);
}

describe("XML declarations TextDecoder misreads", () => {
  test.each(["ucs-2", "unicode", "x-user-defined", "UTF-16"])(
    "a part declared %s but written as UTF-8 is read as UTF-8",
    (label) => {
      const xml = `<?xml version="1.0" encoding="${label}"?><w:document xmlns:w="${W}"><w:body>${para(run("café — 你好"))}<w:sectPr/></w:body></w:document>`;
      expect(read("", { files: { "word/document.xml": strToU8(xml) } }).html).toBe(
        "<p>café — 你好</p>",
      );
    },
  );
});
