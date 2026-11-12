// @vitest-environment jsdom
import { strFromU8, unzipSync, zipSync } from "fflate";
import { describe, expect, test, vi } from "vitest";
import { writeDocx } from "../src/doceditor/docx-write";
import {
  hasDroppedFormats,
  hasMarkdownExtras,
  mdToHtml,
  splitFrontMatter,
} from "../src/doceditor/markdown";
import { toMarkdown } from "../src/doceditor/md-writer";
import { normaliseHtmlReport } from "../src/doceditor/subset";
import { CANT_EDIT, type Convert, decodeText, isEditableMarkdownZip, openDocument } from "../src/doceditor/open";
import { SAVE_FORMATS, saveDocument } from "../src/doceditor/save";
import {
  MAX_CHARS,
  MAX_FILE_BYTES,
  type OpenedDocument,
  type SaveContent,
  TOO_LARGE,
  W_MDEXTRA,
  W_PICTURES,
} from "../src/doceditor/types";

const PNG = Uint8Array.from(
  atob(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC",
  ),
  (c) => c.charCodeAt(0),
);
const NBSP = "\xa0";

const fileOf = (data: string | Uint8Array, name: string) =>
  new File([(typeof data === "string" ? new TextEncoder().encode(data) : data) as BlobPart], name);

const noConvert: Convert = async () => {
  throw new Error("convert should not be called");
};

/** Roughly what the editor page gives as plain text: one line per paragraph. */
function plainText(html: string): string {
  const body = new DOMParser().parseFromString(html, "text/html").body;
  const lines = Array.from(body.children).map((p) => (p.textContent ?? "").replaceAll(NBSP, " "));
  return lines.length ? lines.join("\n") + "\n" : "";
}

function contentOf(doc: OpenedDocument): SaveContent {
  return {
    html: doc.html,
    images: doc.images,
    plainText: plainText(doc.html),
    frontMatter: doc.frontMatter,
    title: doc.title,
    template: doc.template,
  };
}

async function blobBytes(blob: Blob): Promise<Uint8Array> {
  return new Uint8Array(await blob.arrayBuffer());
}

describe("openDocument", () => {
  test("relative pictures the website can't read are reported", async () => {
    const md = await openDocument(fileOf("![](pics/x.png)\n", "a.md"), noConvert);
    expect([md.html, md.warnings]).toEqual(["<p></p>", [W_PICTURES]]);
    const page = await openDocument(fileOf('<p>a<img src="x.png"></p>', "a.html"), noConvert);
    expect(page.warnings).toEqual([W_PICTURES]);
  });

  test("refuses large files before reading", async () => {
    const arrayBuffer = vi.fn(() => {
      throw new Error("read");
    });
    const big = { name: "a.txt", size: MAX_FILE_BYTES + 1, arrayBuffer } as unknown as File;
    await expect(openDocument(big, noConvert)).rejects.toThrow(TOO_LARGE);
    expect(arrayBuffer).not.toHaveBeenCalled();
  });

  test("refuses too many characters after reading", async () => {
    const text = "a".repeat(MAX_CHARS + 1);
    await expect(openDocument(fileOf(text, "a.txt"), noConvert)).rejects.toThrow(TOO_LARGE);
  });

  test("refuses other file types", async () => {
    await expect(openDocument(fileOf("x", "a.doc"), noConvert)).rejects.toThrow(
      "This file type can't be edited.",
    );
  });

  test("odt goes through DOCX and keeps its format", async () => {
    const docx = writeDocx("<h1>T</h1><p>x</p>", new Map());
    const convert = vi.fn<Convert>(async () => docx);
    const doc = await openDocument(fileOf(new Uint8Array([1, 2, 3]), "Report.odt"), convert);
    expect(convert).toHaveBeenCalledTimes(1);
    const [bytes, from, to, name] = convert.mock.calls[0];
    expect([Array.from(bytes), from, to, name]).toEqual([[1, 2, 3], "odt", "docx", "Report.odt"]);
    expect(doc.html).toBe("<h1>T</h1><p>x</p>");
    expect(doc.sourceFormat).toBe("odt");
    expect(doc.template).toBeNull();
  });

  test("rtf goes through DOCX too", async () => {
    const convert = vi.fn<Convert>(async () => writeDocx("<p>r</p>", new Map()));
    const doc = await openDocument(fileOf("{\\rtf1 r}", "a.rtf"), convert);
    expect(convert.mock.calls[0].slice(1, 3)).toEqual(["rtf", "docx"]);
    expect(doc.sourceFormat).toBe("rtf");
  });

  test("docx keeps the original as the template", async () => {
    const docx = writeDocx("<p>d</p>", new Map());
    const doc = await openDocument(fileOf(docx, "a.docx"), noConvert);
    expect(doc.html).toBe("<p>d</p>");
    expect(doc.template).toEqual(docx);
  });

  test("html keeps its title and reports what it drops", async () => {
    const page = "<html><head><title> My\n page </title><style>p{}</style></head><body><p>x</p>";
    const doc = await openDocument(fileOf(page, "a.htm"), noConvert);
    expect(doc.html).toBe("<p>x</p>");
    expect(doc.title).toBe("My page");
    expect(doc.sourceFormat).toBe("html");
    expect(doc.warnings).toEqual(["page styles, classes and scripts"]);
  });

  test("an empty document is one empty paragraph", async () => {
    for (const name of ["a.md", "a.html", "a.txt"]) {
      expect((await openDocument(fileOf("", name), noConvert)).html).toBe("<p></p>");
    }
  });

  test("txt keeps leading and repeated spaces as no-break spaces", async () => {
    const doc = await openDocument(fileOf("  a  b 'q' \"r\"\r\n", "a.txt"), noConvert);
    expect(doc.html).toBe(`<p>${NBSP}${NBSP}a ${NBSP}b &#x27;q&#x27; &quot;r&quot;</p>`);
  });
});

describe("decodeText", () => {
  test("UTF-8 without its byte order mark", () => {
    expect(decodeText(new Uint8Array([0xef, 0xbb, 0xbf, 0xc3, 0xa5]))).toBe("å");
  });
  test("Windows-1252 when it isn't UTF-8", () => {
    expect(decodeText(new Uint8Array([0x80, 0x93, 0xe5]))).toBe("€“å");
  });
  test("Latin-1 when Windows-1252 has no such byte", () => {
    expect(decodeText(new Uint8Array([0x80, 0x81, 0xe5]))).toBe("\x80\x81å");
  });
});

describe("splitFrontMatter", () => {
  test("a YAML block at the top, CRLF saved as LF", () => {
    expect(splitFrontMatter("---\r\ntitle: x\r\n...\r\nbody\r\n")).toEqual([
      "---\ntitle: x\n...\n",
      "body\r\n",
    ]);
  });
  test("a rule at the start is not front matter", () => {
    expect(splitFrontMatter("---\n\n# A\n---\n")).toEqual(["", "---\n\n# A\n---\n"]);
    expect(splitFrontMatter("---\ntitle: x\n")).toEqual(["", "---\ntitle: x\n"]);
    expect(splitFrontMatter("---\nnot a key\n---\n")).toEqual(["", "---\nnot a key\n---\n"]);
  });
});

describe("markdown warnings", () => {
  test("code in fences doesn't count", () => {
    expect(hasDroppedFormats("```\n`x` #### h\n```\n")).toBe(false);
    expect(hasDroppedFormats("````\n```\n`x`\n````\n")).toBe(false);
    expect(hasDroppedFormats("text `x`\n")).toBe(true);
    expect(hasMarkdownExtras("```\n<div>\n[^1]\n```\n")).toBe(false);
    expect(hasMarkdownExtras("`[^1]` and \\[^2]\n")).toBe(false);
    expect(hasMarkdownExtras("<div>x</div>\n")).toBe(true);
    expect(hasMarkdownExtras("<divx>\n")).toBe(false);
  });
  test("small headings, picture descriptions, comments and alignment", () => {
    expect(hasDroppedFormats("#### four\n")).toBe(true);
    expect(hasDroppedFormats("####### seven\n")).toBe(false);
    expect(hasDroppedFormats("![alt](x.png)\n")).toBe(true);
    expect(hasDroppedFormats("![](x.png)\n")).toBe(false);
    expect(hasDroppedFormats("- a\n\n<!-- -->\n\n- b\n")).toBe(false);
    expect(hasDroppedFormats("<!-- note -->\n")).toBe(true);
    expect(hasDroppedFormats("| a |\r\n|:--|\r\n")).toBe(true);
    expect(hasDroppedFormats("| a |\n|---|\n")).toBe(false);
  });
});

describe("mdToHtml reads like the desktop's markdown-it-py", () => {
  // expected values printed by the desktop's io.md_to_html
  test.each([
    ["[<!-- a --><!--", "<p>[<!-- a -->&lt;!--</p>\n"],
    ["a <!-- x ---> b", "<p>a &lt;!-- x ---&gt; b</p>\n"],
    ["x\\   \ny", "<p>x\\ <br />\ny</p>\n"],
    ["\x0ba\x0b", "<p>a</p>\n"],
    ["# \u3000T\u3000", "<h1>T</h1>\n"],
    ["a <b\x1cx=1> c", "<p>a <b\x1cx=1> c</p>\n"],
    // block HTML starts the way Python's \s reads it
    ["<div\x1cx>\n*a*\n", "<div\x1cx>\n*a*\n"],
    ["<div\x85>\n*b*\n", "<div\x85>\n*b*\n"],
    ["<div\ufeff>\n*c*\n", "<p>&lt;div\ufeff&gt;\n<em>c</em></p>\n"],
    ["<span a=1\x1c>\n*d*\n", "<span a=1\x1c>\n*d*\n"],
    ["<span\ufeff>\n*e*\n", "<p>&lt;span\ufeff&gt;\n<em>e</em></p>\n"],
    ["<pre\x1c>\n*f*\n", "<pre\x1c>\n*f*\n"],
  ])("%j", (md, html) => {
    expect(mdToHtml(md)).toBe(html);
  });
});

describe("toMarkdown", () => {
  test("rules the desktop writer follows", () => {
    // markdown-it would read [l](a b) as "a%20b": the exact address is kept as raw HTML
    expect(toMarkdown('<p><a href="a b(1)">l</a></p>')).toBe('<a href="a b(1)">l</a>\n');
    expect(toMarkdown('<p><a href="a(1)">l</a></p>')).toBe("[l](<a(1)>)\n");
    expect(toMarkdown("<p><b><i>x</i></b></p>")).toBe("**_x_**\n");
    expect(toMarkdown("<ul><li>[x] done</li><li>[ ]</li></ul>")).toBe("- [x] done\n- [ ]\n");
    expect(toMarkdown("<h2></h2>")).toBe("##\n");
    expect(toMarkdown("<p>a\tb</p>")).toBe("a&#9;b\n");
    expect(toMarkdown('<p><img src="img/1.png" width="20" height="10"></p>')).toBe(
      '<img src="img/1.png" width="20" height="10">\n',
    );
    expect(toMarkdown("<ul><li>a</li></ul><ul><li>b</li></ul>")).toBe("- a\n\n<!-- -->\n\n- b\n");
    expect(toMarkdown("<p>1. x # *y*</p><p>- z</p>")).toBe("1\\. x # \\*y\\*\n\n&#45; z\n");
    expect(toMarkdown("<h1>Title ##</h1>")).toBe("# Title \\#\\#\n");
    expect(toMarkdown('<p><span style="color:#ff0000">r</span> <u>u</u></p>')).toBe(
      '<span style="color:#ff0000">r</span> <u>u</u>\n',
    );
    expect(toMarkdown("<pre>a ``` b</pre><pre>c</pre>")).toBe("````\na ``` b\nc\n````\n");
    expect(toMarkdown("<table><tr><td><p>a|b</p></td></tr></table>")).toBe("| a\\|b |\n| --- |\n");
    // a table a pipe table can't hold, and an aligned paragraph: one line of raw HTML
    expect(toMarkdown("<table><tr><td><p>a|b</p><p>c</p></td></tr></table>")).toBe(
      "<table><tr><td><p>a|b</p><p>c</p></td></tr></table>\n",
    );
    expect(toMarkdown('<p style="text-align:center;margin-left:36pt">c</p>')).toBe(
      '<p align="center" style="margin-left:36pt">c</p>\n',
    );
    expect(toMarkdown("<p></p><p>x</p>")).toBe("<span></span>\n\nx\n");
    expect(toMarkdown("<p></p>")).toBe("");
    expect(toMarkdown("")).toBe("");
  });
});

describe("saveDocument", () => {
  const base: SaveContent = {
    html: "<h1>T</h1><p>x</p>",
    images: new Map(),
    plainText: "T\nx\n",
    frontMatter: "",
    title: "",
    template: null,
  };

  test("offers the website's formats", () => {
    expect(SAVE_FORMATS).toEqual([
      ["docx", "Word (DOCX)"],
      ["odt", "OpenDocument (ODT)"],
      ["rtf", "Rich Text (RTF)"],
      ["pdf", "PDF"],
      ["md", "Markdown"],
      ["txt", "Plain text (TXT)"],
      ["html", "Web page (HTML)"],
    ]);
  });

  test("md with a picture is a ZIP with the pictures folder", async () => {
    const content = { ...base, html: '<p><img src="img/1.png"></p>', images: new Map([["img/1.png", PNG]]) };
    const out = await saveDocument(content, "md", "a", noConvert);
    expect(out.name).toBe("a.zip");
    const files = unzipSync(await blobBytes(out.blob));
    expect(Object.keys(files).sort()).toEqual(["a.md", "a_files/1.png"]);
    expect(strFromU8(files["a.md"])).toBe("![](a_files/1.png)\n");
    expect(files["a_files/1.png"]).toEqual(PNG);
  });

  test("the picture folder link is URL-quoted, sized pictures too", async () => {
    const html = '<p><img src="img/1.png" width="5"><img src="img/2.png"></p>';
    const images = new Map([["img/1.png", PNG]]);
    const out = await saveDocument({ ...base, html, images }, "md", "my doc", noConvert);
    const files = unzipSync(await blobBytes(out.blob));
    expect(strFromU8(files["my doc.md"])).toBe(
      '<img src="my%20doc_files/1.png" width="5">![](img/2.png)\n',
    );
  });

  test("md without pictures is plain Markdown with its front matter", async () => {
    const out = await saveDocument({ ...base, frontMatter: "---\na: 1\n---\n" }, "md", "a", noConvert);
    expect(out.name).toBe("a.md");
    expect(await out.blob.text()).toBe("---\na: 1\n---\n\n# T\n\nx\n");
  });

  test("pdf goes html -> pdf through convert", async () => {
    const convert = vi.fn<Convert>(async () => new Uint8Array([37, 80, 68, 70]));
    const content = { ...base, images: new Map([["img/1.png", PNG]]), html: '<p><img src="img/1.png"></p>' };
    const out = await saveDocument(content, "pdf", "a", convert);
    expect(out.name).toBe("a.pdf");
    const [bytes, from, to] = convert.mock.calls[0];
    expect([from, to]).toEqual(["html", "pdf"]);
    expect(new TextDecoder().decode(bytes)).toContain('src="data:image/png;base64,');
    expect(await blobBytes(out.blob)).toEqual(new Uint8Array([37, 80, 68, 70]));
  });

  test("pdf is laid out like the desktop's: A4 portrait, 56 pt margins, its page styles", async () => {
    const convert = vi.fn<Convert>(async () => new Uint8Array([1]));
    await saveDocument(base, "pdf", "a", convert);
    const [bytes, , , name, options] = convert.mock.calls[0];
    expect(name).toBe("a.html");
    expect(options).toEqual({ paper: "a4", orientation: "portrait", margin_mm: (56 * 25.4) / 72, page_styles: true });
    const page = new TextDecoder().decode(bytes);
    expect(page).toContain("<style>");
    expect(page).toContain("body { font-family: sans-serif; font-size: 11pt; line-height: 1.4; }");
  });

  test("odt / rtf convert the DOCX", async () => {
    const convert = vi.fn<Convert>(async () => new Uint8Array([9]));
    const out = await saveDocument(base, "rtf", "a", convert);
    expect(out.name).toBe("a.rtf");
    const [bytes, from, to] = convert.mock.calls[0];
    expect([from, to]).toEqual(["docx", "rtf"]);
    expect((await openDocument(fileOf(bytes, "x.docx"), noConvert)).html).toBe(base.html);
  });

  test("html is a standalone page with data: pictures and the title", async () => {
    const content = { ...base, html: '<p><img src="img/1.png"></p>', images: new Map([["img/1.png", PNG]]) };
    const page = await (await saveDocument({ ...content, title: "A & B" }, "html", "a", noConvert)).blob.text();
    expect(page.startsWith('<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>A &amp; B</title>')).toBe(true);
    expect(page).toContain('<img src="data:image/png;base64,iVBORw0KGgo');
    const untitled = await (await saveDocument(base, "html", "stem", noConvert)).blob.text();
    expect(untitled).toContain("<title>stem</title>");
  });

  test("txt is the plain text", async () => {
    const out = await saveDocument(base, "txt", "a", noConvert);
    expect([out.name, await out.blob.text()]).toEqual(["a.txt", "T\nx\n"]);
  });

  test("an unknown format is refused", async () => {
    await expect(saveDocument(base, "doc", "a", noConvert)).rejects.toThrow();
  });
});

describe("round trips", () => {
  const sources: [string, string | Uint8Array][] = [
    ["a.txt", "Name     Qty\n  indented\n\nx <y> & z\n"],
    ["a.md", "---\ntitle: T\n---\n\n# Head\n\nSome **bold** and *it* [l](u%20v).\n\n- a\n    - b\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"],
    ["a.html", "<html><head><title>Page</title></head><body><h2>H</h2><p>a <u>u</u></p><ol><li>x</li></ol></body></html>"],
    ["a.docx", writeDocx("<h1>T</h1><p>x <b>y</b></p><ul><li>i</li></ul>", new Map())],
  ];
  test.each(sources)("%s stays the same over three save cycles", async (name, data) => {
    const ext = name.split(".")[1];
    let doc = await openDocument(fileOf(data, name), noConvert);
    const first = doc;
    for (let n = 0; n < 3; n++) {
      const out = await saveDocument(contentOf(doc), ext, "a", noConvert);
      expect(out.name).toBe(name);
      doc = await openDocument(new File([out.blob], out.name), noConvert);
      expect(doc.html).toBe(first.html);
      expect(doc.frontMatter).toBe(first.frontMatter);
      expect(doc.title).toBe(first.title);
    }
  });
});

describe("Markdown ZIP saved by the website", () => {
  const enc = (t: string) => new TextEncoder().encode(t);
  const base = { frontMatter: "", title: "", template: null, plainText: "" } as unknown as SaveContent;

  async function savedZip(stem: string): Promise<{ bytes: Uint8Array; content: SaveContent }> {
    const content = {
      ...base,
      html: '<p>Hi</p><p><img src="img/1.png"></p><p><img src="img/2.png" width="5"></p>',
      images: new Map([
        ["img/1.png", PNG],
        ["img/2.png", PNG.slice().reverse()],
      ]),
    } as SaveContent;
    const out = await saveDocument(content, "md", stem, noConvert);
    expect(out.name).toBe(`${stem}.zip`);
    return { bytes: new Uint8Array(await out.blob.arrayBuffer()), content };
  }

  test("reopens with its pictures", async () => {
    for (const stem of ["a", "my doc", "Æbler å"]) {
      const { bytes, content } = await savedZip(stem);
      expect(isEditableMarkdownZip(bytes)).toBe(true);
      const doc = await openDocument(fileOf(bytes, `${stem}.zip`), noConvert);
      expect(doc.sourceFormat).toBe("md");
      expect(doc.warnings).not.toContain(W_PICTURES);
      expect([...doc.images.keys()]).toEqual(["img/1.png", "img/2.png"]);
      expect(doc.images.get("img/1.png")).toEqual(PNG);
      expect(doc.images.get("img/2.png")).toEqual(PNG.slice().reverse());
      expect(doc.html).toBe(normaliseHtmlReport(content.html).html);
    }
  });

  test("a ZIP with two .md files or other files is not editable", async () => {
    const png = PNG;
    const ok = { "a.md": enc("x"), "a_files/1.png": png };
    expect(isEditableMarkdownZip(zipSync(ok))).toBe(true);
    expect(isEditableMarkdownZip(zipSync({ ...ok, "b.md": enc("y") }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ ...ok, "notes.txt": enc("y") }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ ...ok, "other_files/1.png": png }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ "d/a.md": enc("x") }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ "a_files/1.png": png }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ "a.md": enc("x"), "a_files/../x.png": png }))).toBe(false);
    expect(isEditableMarkdownZip(enc("not a zip"))).toBe(false);
    await expect(openDocument(fileOf(zipSync({ "a.md": enc("x"), "b.md": enc("y") }), "a.zip"), noConvert)).rejects.toThrow(
      CANT_EDIT,
    );
  });

  test("our own raw HTML gives no extras warning; raw HTML the editor drops still does", async () => {
    for (const html of [
      '<p style="text-align:center">a</p><h2 style="margin-left:36pt">h</h2>',
      "<table><tr><td><ol><li>x</li></ol></td><td><pre>a\nb</pre></td></tr></table>",
      '<ul><li style="text-align:right"><b>r</b> <a href="a b">l</a></li></ul>',
    ]) {
      const out = await saveDocument({ ...base, html, images: new Map() }, "md", "a", noConvert);
      const doc = await openDocument(fileOf(await blobBytes(out.blob), out.name), noConvert);
      expect(doc.html).toBe(html);
      expect(doc.warnings).toEqual([]);
    }
    for (const md of ["<details><summary>s</summary>\nx\n</details>\n\n<p>ok</p>\n", '<p align="center">ok</p>\n\n<div>d</div>\n']) {
      expect((await openDocument(fileOf(md, "a.md"), noConvert)).warnings).toContain(W_MDEXTRA);
    }
  });

  test("a picture used twice keeps one name from the first save on (md, docx)", async () => {
    const content = { ...base, html: '<p><img src="img/1.png"><img src="img/1.png"></p>', images: new Map([["img/1.png", PNG]]) };
    for (const fmt of ["md", "docx"]) {
      const out = await saveDocument(content, fmt, "a", noConvert);
      const doc = await openDocument(fileOf(await blobBytes(out.blob), out.name), noConvert);
      expect([...doc.images.keys()]).toEqual(["img/1.png"]);
      expect(doc.html.split('src="img/1.png"').length).toBe(3);
    }
  });

  test("a root .md with a backslash in its name is not editable", () => {
    expect(isEditableMarkdownZip(zipSync({ "a\\b.md": enc("x") }))).toBe(false);
    expect(isEditableMarkdownZip(zipSync({ "..\\a.md": enc("x"), "..\\a_files/1.png": PNG }))).toBe(false);
  });

  test("a %2e%2e link never leaves the pictures folder", async () => {
    const zip = zipSync({ "a.md": enc("![](a_files/%2e%2e/x.png)\n\n![](a_files/%2E%2E/x.png)\n"), "a_files/1.png": PNG, "x.png": PNG });
    expect(isEditableMarkdownZip(zip)).toBe(false); // x.png is outside the folder
    const ok = zipSync({ "a.md": enc("![](a_files/%2e%2e/a_files/1.png)\n"), "a_files/1.png": PNG });
    const doc = await openDocument(fileOf(ok, "a.zip"), noConvert);
    expect([...doc.images.keys()]).toEqual([]);
    expect(doc.warnings).toContain(W_PICTURES);
  });

  test("a folder entry inside the pictures folder is allowed but never a picture", async () => {
    const zip = zipSync({ "a.md": enc("![](a_files/sub/)\n\n![](a_files/sub/1.png)\n"), "a_files/sub/": new Uint8Array(), "a_files/sub/1.png": PNG });
    expect(isEditableMarkdownZip(zip)).toBe(true);
    const doc = await openDocument(fileOf(zip, "a.zip"), noConvert);
    expect([...doc.images.keys()]).toEqual(["img/1.png"]);
    expect(doc.warnings).toContain(W_PICTURES);
  });

  test("an entry outside the pictures folder is never read", async () => {
    const zip = zipSync({
      "a.md": enc("![](../x.png)\n\n![](a_files/../x.png)\n\n![](a_files/1.png)\n"),
      "a_files/1.png": PNG,
    });
    expect(isEditableMarkdownZip(zip)).toBe(true);
    const doc = await openDocument(fileOf(zip, "a.zip"), noConvert);
    expect([...doc.images.keys()]).toEqual(["img/1.png"]);
    expect(doc.warnings).toContain(W_PICTURES);
  });

  test("saving a reopened ZIP without pictures writes plain Markdown", async () => {
    const { bytes } = await savedZip("a");
    const doc = await openDocument(fileOf(bytes, "a.zip"), noConvert);
    const out = await saveDocument({ ...doc, html: "<p>x</p>", images: new Map() } as unknown as SaveContent, "md", "a", noConvert);
    expect(out.name).toBe("a.md");
    const again = await saveDocument(doc as unknown as SaveContent, "md", "a", noConvert);
    expect(again.name).toBe("a.zip");
  });
});
