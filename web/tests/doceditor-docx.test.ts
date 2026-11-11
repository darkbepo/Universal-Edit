// @vitest-environment jsdom
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";
import vectorsJson from "../../tests/fixtures/doceditor-vectors.json";
import { afterEach, describe, expect, test, vi } from "vitest";
import { writeDocx } from "../src/doceditor/docx-write";

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
    vi.spyOn(console, "warn").mockImplementation(() => {});
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
