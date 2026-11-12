// @vitest-environment jsdom
// The document editor's ProseMirror schema and the subset ↔ document conversion: everything the
// normaliser can write must come back byte-identical (load → export, three times over).
import vectorsJson from "../../tests/fixtures/doceditor-vectors.json";
import appSource from "../src/ui/app.ts?raw";
import { DOMParser as PMParser, DOMSerializer } from "prosemirror-model";
import { EditorState, TextSelection } from "prosemirror-state";
import { describe, expect, test } from "vitest";
import { normaliseHtml } from "../src/doceditor/subset";
import { docToHtml, htmlToDoc } from "../src/ui/doceditor/convert";
import { docSchema } from "../src/ui/doceditor/schema";

interface Vectors {
  cases: string[];
  normalise: { out: string }[];
  markdown: { html: string }[];
  txt: { html: string }[];
  docx: { name: string; html: string }[];
}
const vectors = vectorsJson as unknown as Vectors;

const roundTrip = (html: string) => docToHtml(htmlToDoc(html));
/** Load → export three times; every pass must give `html` back. */
function expectIdentity(html: string): void {
  let out = html;
  for (let n = 0; n < 3; n++) {
    out = roundTrip(out);
    expect(out).toBe(html);
  }
}

describe("shared vectors", () => {
  test.each(vectors.cases.map((c, i) => [i, c] as const))("case #%i", (_i, c) => {
    expect(roundTrip(c)).toBe(normaliseHtml(c).html);
    expectIdentity(normaliseHtml(c).html);
  });
  test.each(vectors.normalise.map((v, i) => [i, v.out] as const))("normalise #%i out", (_i, html) =>
    expectIdentity(html),
  );
  test.each(vectors.markdown.map((v, i) => [i, v.html] as const))("markdown #%i html", (_i, html) =>
    expectIdentity(html),
  );
  test.each(vectors.txt.map((v, i) => [i, v.html] as const))("txt #%i html", (_i, html) =>
    expectIdentity(html),
  );
  test.each(vectors.docx.map((v) => [v.name, v.html] as const))("docx %s html", (_name, html) =>
    expectIdentity(html),
  );
});

describe("constructs of the subset", () => {
  test.each([
    // blocks and their styles
    "<h1>T</h1><h2>U</h2><h3>V</h3>",
    '<h2 style="text-align:center;margin-left:36pt;page-break-before:always">h</h2>',
    '<p style="text-align:left">left is kept</p>',
    '<p style="text-align:right;margin-left:720pt">deepest indent</p>',
    '<p style="page-break-before:always">n</p>',
    "<p></p><p>a</p><p></p>",
    "<p><br></p>",
    "<p>a<br></p>",
    "<p>a<br>b<br><br>c</p>",
    "<hr><p>a</p><hr><hr>",
    "<blockquote></blockquote>",
    "<blockquote><p>a</p><blockquote><p>b</p></blockquote><p>c</p></blockquote>",
    "<blockquote><h2>h</h2><pre>x</pre><ul><li>l</li></ul><hr><table><tr><td><p>t</p></td></tr></table></blockquote>",
    "<blockquote><p>a</p></blockquote><blockquote><p>b</p></blockquote>",
    // lists
    "<ul></ul>",
    "<ol><li>a</li><li></li><li>c</li></ol>",
    "<ul><li><ul><li>x</li></ul></li></ul>",
    "<ol><li>a<ol><li>b<ul><li>c</li></ul></li></ol></li><li>d</li></ol>",
    '<ul><li style="text-align:center;margin-left:72pt;page-break-before:always">x</li></ul>',
    "<ul><li>a<br>b</li></ul>",
    "<ul><li><b>a</b> <a href=\"u\">b</a></li></ul><ol><li>c</li></ol>",
    // tables
    "<table><tr><td><p></p></td></tr></table>",
    '<table style="border:none"><tr><td><p>x</p></td><td><p>y</p></td></tr></table>',
    '<table><tr><td colspan="2" rowspan="3"><p>a</p></td></tr><tr><td><p>b</p></td></tr></table>',
    "<table><tr><td><p>a</p></td></tr><tr><td><p>b</p></td><td><p>c</p></td></tr></table>",
    "<table><tr><td><p>a</p><p>b</p><ul><li>c</li></ul></td></tr></table>",
    "<table><tr><td><table><tr><td><p>inner</p></td></tr></table></td></tr></table>",
    "<table><tr><td><blockquote><p>q</p></blockquote><pre>c</pre><hr></td></tr></table>",
    // code blocks
    "<pre>a</pre><pre>b\nc\n</pre>",
    "<pre>\na</pre>",
    "<pre>\n\na\n</pre>",
    "<pre></pre>",
    "<pre>a\r\nb</pre>",
    "<pre>  x  y\t z  </pre>",
    "<pre><b>a\nb</b> <i>c</i></pre>",
    "<pre>a<br>b</pre>",
    '<pre><a href="u">x</a><img src="img/1.png"></pre>',
    '<pre><span style="color:#ff0000">r</span></pre>',
    "<pre>a</pre><pre>b</pre><pre>c</pre>",
    "<pre>&lt;tag&gt; &amp; \"q\"</pre>",
    // text
    "<p>a\tb\t\tc</p>",
    "<p>\ta</p>",
    "<p>  a  b  c</p>",
    "<p>&lt;&amp;&gt;\"'</p>",
    "<p> ​﻿ emoji 😀</p>",
    // inline
    '<p style="text-align:justify">a <b>b</b> <i>c</i> <u>d</u> <s>e</s></p>',
    '<p><span style="font-family:Arial;font-size:14pt;color:#ff0000;background-color:#ffcc00">f</span></p>',
    '<p><span style="font-size:10.5pt">half</span></p>',
    '<p><span style="font-family:Times New Roman">t</span></p>',
    '<p><a href="https://x.org/?a=1&amp;b=&quot;2&quot;">x</a></p>',
    '<p><img src="img/1.png" width="20" height="10"><img src="img/2.jpg"></p>',
    '<p><a href="u"><img src="img/1.png"></a></p>',
    "<h1><b>bold heading</b> <span style=\"font-size:30pt\">big</span></h1>",
    "<p><b>a</b><br><b>b</b></p>",
    "<p><b>a<br>b</b></p>",
  ])("%s", (html) => {
    expect(normaliseHtml(html).html).toBe(html); // a canonical input
    expectIdentity(html);
  });
});

describe("empty inline tags", () => {
  test.each([
    '<p>a <a href="u"></a> b</p>',
    '<p><a href="u"></a> b</p>',
    '<p><a href="u"></a><a href="u"></a></p>',
    '<p><a href="u"></a><a href="u">x</a></p>',
    '<p><a href="u"></a><a href="v"></a> <a href="u">y</a></p>',
    '<h2><a href="u"></a> title</h2>',
    '<ul><li><a href="u"></a> item</li></ul>',
    '<p><a href="u"><b></b></a></p>',
    '<p><b><a href="u"></a></b></p>',
    '<p><b><a href="u"></a>x</b></p>',
    "<p><b></b></p>",
    "<p>a <b></b>c</p>",
    "<p><b><i></i></b>x</p>",
    "<p><b>x<i></i></b></p>",
    "<p><b>x<i></i>y</b></p>",
    '<p><span style="color:#ff0000"></span></p>',
    '<pre><a href="u"></a>\ncode</pre>',
  ])("%s", (html) => {
    expect(normaliseHtml(html).html).toBe(html);
    expectIdentity(html);
  });

  test("an empty link is an atom carrying the link mark", () => {
    const doc = htmlToDoc('<p>a <a href="u"></a> b</p>');
    const para = doc.child(0);
    expect(para.childCount).toBe(3);
    expect(para.child(1).type.name).toBe("empty_inline");
    expect(para.child(1).marks.map((m) => m.type.name)).toEqual(["link"]);
    expect(para.textContent).toBe("a  b");
  });
});

describe("mark nesting order", () => {
  test.each([
    '<p><b><a href="u">x</a></b></p>',
    '<p><a href="u"><b>x</b></a></p>',
    '<p><b>x</b><a href="u"><b>y</b></a></p>',
    '<p><b>x<a href="u">y</a></b></p>',
    '<p><b><a href="u">y</a>x</b></p>',
    "<p><i><b>x</b></i></p>",
    "<p><b><i>x</i></b></p>",
    "<p><b><i>x</i></b> <i><b>y</b></i></p>",
    "<p><i>zero or <b>one</b></i></p>",
    '<p><b>Apply now at <a href="u">x</a></b></p>',
    '<p><i>a <a href="u">b</a></i></p>',
    "<p><s><u><i><b>all</b></i></u></s></p>",
    '<p><b><span style="color:#ff0000">r</span></b></p>',
    '<p><span style="color:#ff0000"><b>r</b></span></p>',
    '<p><span style="color:#ff0000"><span style="font-size:12pt">x</span></span></p>',
    '<p><span style="color:#ff0000">a<span style="font-size:12pt">b</span>c</span></p>',
    '<p><span style="font-size:12pt"><a href="u"><span style="color:#00ff00">l</span></a></span></p>',
    '<p><b><a href="u"><img src="img/1.png"></a></b></p>',
    '<p><a href="u"><b><img src="img/1.png"></b></a></p>',
    '<p><b>a</b><i><b>b</b></i></p>',
    "<p><b><b>x</b></b></p>",
  ])("%s", (html) => {
    expect(normaliseHtml(html).html).toBe(html);
    expectIdentity(html);
  });

  test("text without a record keeps the tags already open", () => {
    let state = EditorState.create({ doc: htmlToDoc("<p><b>xyz</b></p>") });
    state = state.apply(state.tr.addMark(2, 3, docSchema.marks.link.create({ href: "u" })));
    expect(docToHtml(state.doc)).toBe('<p><b>x<a href="u">y</a>z</b></p>');
  });

  test("typing inside a nested run keeps its order", () => {
    let state = EditorState.create({ doc: htmlToDoc('<p><b><a href="u">xy</a></b></p>') });
    state = state.apply(state.tr.insertText("Q", 2));
    expect(docToHtml(state.doc)).toBe('<p><b><a href="u">xQy</a></b></p>');
  });

  test("a mark added to recorded text goes inside", () => {
    let state = EditorState.create({ doc: htmlToDoc('<p><b><a href="u">xy</a></b></p>') });
    state = state.apply(state.tr.addMark(1, 3, docSchema.marks.italic.create()));
    expect(docToHtml(state.doc)).toBe('<p><b><a href="u"><i>xy</i></a></b></p>');
  });
});

describe("typed text", () => {
  const typed = (text: string, html = "<p></p>", at = 1) => {
    const state = EditorState.create({ doc: htmlToDoc(html) });
    return docToHtml(state.apply(state.tr.insertText(text, at)).doc);
  };

  test("typed spaces", () => {
    expect(typed("a    b")).toBe("<p>a    b</p>");
    expect(typed("  a")).toBe("<p>  a</p>");
    expect(typed("a  b", "<h1></h1>")).toBe("<h1>a  b</h1>");
    expect(typed("a  b", "<ul><li></li></ul>", 3)).toBe("<ul><li>a  b</li></ul>");
  });

  test("a space after a line break or at a cell's start is kept", () => {
    const state = EditorState.create({ doc: htmlToDoc("<p>a<br>b</p>") });
    expect(docToHtml(state.apply(state.tr.insertText(" ", 3)).doc)).toBe("<p>a<br> b</p>");
    expect(typed(" x", "<table><tr><td><p></p></td></tr></table>", 4)).toBe(
      "<table><tr><td><p> x</p></td></tr></table>",
    );
  });

  test("a space after a picture or an empty link stays plain", () => {
    expect(typed(" x", '<p><img src="img/1.png"></p>', 2)).toBe('<p><img src="img/1.png"> x</p>');
    expect(typed(" x", '<p><a href="u"></a></p>', 2)).toBe('<p><a href="u"></a> x</p>');
  });

  test("code keeps typed spaces as they are", () => {
    expect(typed("  a  b  ", "<pre></pre>")).toBe("<pre>  a  b  </pre>");
  });

  test("tabs kept", () => {
    expect(typed("a\tb\t", "<p></p>")).toBe("<p>a\tb\t</p>");
    expectIdentity("<p>\t\tindented</p>");
  });
});

describe("structure", () => {
  test("empty cell stays one paragraph over 3 cycles", () => {
    const html = "<table><tr><td><p></p></td><td><p>x</p></td></tr></table>";
    expectIdentity(html);
    const cell = htmlToDoc(html).child(0).child(0).child(0);
    expect(cell.childCount).toBe(1);
    expect(cell.child(0).type.name).toBe("paragraph");
  });

  test("empty li kept", () => {
    expectIdentity("<ol><li>a</li><li></li><li>c</li></ol>");
    const item = htmlToDoc("<ul><li></li></ul>").child(0).child(0);
    expect(item.childCount).toBe(1);
    expect(item.child(0).content.size).toBe(0);
  });

  test("border:none kept", () => {
    const html = '<table style="border:none"><tr><td><p>x</p></td></tr></table>';
    expect(htmlToDoc(html).child(0).attrs.borderless).toBe(true);
    expectIdentity(html);
    expect(htmlToDoc("<table><tr><td><p>x</p></td></tr></table>").child(0).attrs.borderless).toBe(false);
  });

  test("page break attr round-trips", () => {
    const html = '<p style="page-break-before:always">n</p><h1 style="page-break-before:always">h</h1>';
    const doc = htmlToDoc(html);
    expect(doc.child(0).attrs.pageBreak).toBe(true);
    expect(doc.child(1).attrs.pageBreak).toBe(true);
    expectIdentity(html);
    const state = EditorState.create({ doc: htmlToDoc("<p>x</p>") });
    const tr = state.tr.setNodeMarkup(0, undefined, { ...state.doc.child(0).attrs, pageBreak: true, indent: 2 });
    expect(docToHtml(tr.doc)).toBe('<p style="margin-left:72pt;page-break-before:always">x</p>');
  });

  test("li style lives on its first paragraph", () => {
    const doc = htmlToDoc('<ul><li style="text-align:center">x<ul><li>y</li></ul></li></ul>');
    const item = doc.child(0).child(0);
    expect(item.child(0).attrs.align).toBe("center");
    expect(item.child(1).type.name).toBe("bullet_list");
  });

  test("adjacent code blocks stay apart", () => {
    const doc = htmlToDoc("<pre>a</pre><pre>b\nc\n</pre>");
    expect(doc.childCount).toBe(2);
    expect(doc.child(1).textContent).toBe("b\nc\n");
    expect(htmlToDoc("<pre>\na</pre>").child(0).textContent).toBe("\na");
  });

  test("an empty document", () => {
    expect(roundTrip("")).toBe("");
    expect(htmlToDoc("").childCount).toBe(1);
    expectIdentity("<p></p>");
    // an emptied document is written as nothing, like the desktop
    let state = EditorState.create({ doc: htmlToDoc("<p>x</p>") });
    state = state.apply(state.tr.delete(1, 2));
    expect(docToHtml(state.doc)).toBe("");
    // but an empty paragraph with a style, or in a list, is content
    expectIdentity('<p style="text-align:center"></p>');
    expectIdentity("<ul><li></li></ul>");
  });

  test("an edit that breaks the subset's shape is normalised", () => {
    // a second paragraph inside a list item becomes a line of it
    const state = EditorState.create({ doc: htmlToDoc("<ul><li>a</li></ul>") });
    const p = docSchema.nodes.paragraph.create(null, docSchema.text("b"));
    const tr = state.tr.insert(4, p);
    expect(docToHtml(tr.doc)).toBe("<ul><li>a<br>b</li></ul>");
  });
});

describe("size", () => {
  test("a 300-paragraph document is quick", () => {
    const html = Array.from(
      { length: 300 },
      (_, i) => `<p>Paragraph ${i} with <b>bold</b>, <i>italic</i> and <a href="u${i}">a link</a>.</p>`,
    ).join("");
    const t = performance.now();
    expectIdentity(html);
    expect(performance.now() - t).toBeLessThan(3000);
  });

  test("deep nesting doesn't overflow the stack", () => {
    // as deep as the normaliser reliably goes (its limit is the engine's call stack)
    for (const html of [
      "<blockquote>".repeat(1000) + "<p>x</p>",
      "<ul><li>".repeat(800) + "x",
      "<table><tr><td>".repeat(300) + "<p>x</p>",
    ]) {
      const canonical = normaliseHtml(html).html;
      const t = performance.now();
      expectIdentity(canonical);
      expect(performance.now() - t).toBeLessThan(5000);
    }
  }, 30000);

  test("reading is iterative (ProseMirror's own parser overflows at ~3000 levels)", () => {
    const n = 6000;
    const doc = htmlToDoc("<blockquote>".repeat(n) + "<p>x</p>" + "</blockquote>".repeat(n));
    let depth = 0;
    for (let node = doc.child(0); node.type.name === "blockquote"; node = node.child(0)) depth++;
    expect(depth).toBe(n);
  });

  test("deep inline nesting", () => {
    const html = "<p>" + '<span style="color:#ff0000">'.repeat(800) + "x" + "</span>".repeat(800) + "</p>";
    const canonical = normaliseHtml(html).html;
    expect(() => roundTrip(canonical)).not.toThrow();
  });
});

describe("pasted HTML (the schema's parse rules)", () => {
  const parse = (html: string) => {
    const body = new DOMParser().parseFromString(html, "text/html").body;
    return PMParser.fromSchema(docSchema).parse(body, { preserveWhitespace: "full" });
  };

  test("tags and CSS become marks", () => {
    const doc = parse(
      '<p><strong>a</strong><em>b</em><span style="font-weight:700;text-decoration:underline line-through;color:red;font-size:12px;font-family:\'Arial\', sans-serif;background-color:#ff0">c</span></p>',
    );
    const para = doc.child(0);
    expect(para.child(0).marks.map((m) => m.type.name)).toEqual(["bold"]);
    expect(para.child(1).marks.map((m) => m.type.name)).toEqual(["italic"]);
    const c = Object.fromEntries(para.child(2).marks.map((m) => [m.type.name, m.attrs]));
    expect(Object.keys(c).sort()).toEqual(["bold", "color", "font", "highlight", "size", "strike", "underline"]);
    expect(c.color.rgb).toBe("#ff0000");
    expect(c.size.pt).toBe(9);
    expect(c.font.family).toBe("Arial");
    expect(c.highlight.rgb).toBe("#ffff00");
  });

  test("blocks keep their attributes", () => {
    const doc = parse(
      '<h5 style="text-align:right">h</h5><p style="margin-left:1in;page-break-before:always">p</p><table border="0"><tr><td>x</td></tr></table><p><img src="img/1.png" width="5"></p>',
    );
    expect(doc.child(0).attrs).toMatchObject({ level: 3, align: "right" });
    expect(doc.child(1).attrs).toMatchObject({ indent: 0, pageBreak: true }); // inches aren't read
    expect(doc.child(2).attrs.borderless).toBe(true);
    expect(doc.child(3).child(0).attrs).toEqual({ src: "img/1.png", width: 5, height: null });
  });

  test("the editor's own copies keep empty links and nesting", () => {
    const doc = htmlToDoc('<p><b><a href="u">x</a></b> <a href="v"></a></p>');
    const div = document.createElement("div");
    div.appendChild(DOMSerializer.fromSchema(docSchema).serializeFragment(doc.content));
    const back = PMParser.fromSchema(docSchema).parse(div, { preserveWhitespace: "full" });
    expect(docToHtml(back)).toBe('<p><b><a href="u">x</a></b> <a href="v"></a></p>');
  });
});

test("the page bundle doesn't import the editor", () => {
  expect(appSource).not.toMatch(/from\s+["'][^"']*doceditor\/(schema|convert)["']/);
});

test("selection helpers work on the schema", () => {
  const state = EditorState.create({ doc: htmlToDoc("<p>ab</p>") });
  expect(TextSelection.create(state.doc, 2).$from.parent.type.name).toBe("paragraph");
});
