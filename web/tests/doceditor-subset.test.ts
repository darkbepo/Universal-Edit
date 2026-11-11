// @vitest-environment jsdom
import vectorsJson from "../../tests/fixtures/doceditor-vectors.json";
import { describe, expect, test } from "vitest";
import { W_WEBPICS } from "../src/doceditor/types";
import { htmlTitle, normaliseHtml, normaliseHtmlReport, textLength } from "../src/doceditor/subset";

interface NormaliseVector {
  in: string;
  out: string;
  warnings: string[];
  images?: Record<string, string>;
}
const vectors = vectorsJson as unknown as { normalise: NormaliseVector[]; cases: string[] };

function b64(data: string): Uint8Array {
  return Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
}

const PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC";
const PNG = b64(PNG_B64);

describe("shared vectors", () => {
  test.each(vectors.normalise.map((v, i) => [i, v] as const))("normalise #%i", (_i, v) => {
    const got = normaliseHtmlReport(v.in);
    expect(got.html).toBe(v.out);
    expect(got.warnings).toEqual(v.warnings);
    const want = new Map(Object.entries(v.images ?? {}).map(([k, d]) => [k, b64(d)]));
    expect(got.images).toEqual(want);
  });

  test("idempotent", () => {
    for (const html of [...vectors.normalise.map((v) => v.out), ...vectors.cases]) {
      expect(normaliseHtml(html).html).toBe(html);
    }
  });
});

// Outputs produced by the desktop normaliser (html_subset.py) for inputs outside the vectors.
const DESKTOP: [string, string][] = [
  ["<pre>a\r\nb</pre>", "<pre>a\r\nb</pre>"],
  ["<pre>\r\nx</pre>", "<pre>\r\nx</pre>"],
  ["<pre>\n\nx</pre>", "<pre>\n\nx</pre>"],
  ["<pre>a\x0cb\x00c\x01d</pre>", "<pre>ab cd</pre>"],
  ["<p>a\x00b</p>", "<p>a b</p>"],
  ["<p>a\x01b\x0bc\x1fd</p>", "<p>abcd</p>"],
  ["<p>a\x7fb\x85c</p>", "<p>a\x7fb\x85c</p>"],
  ["<p>a\r\nb</p>", "<p>a b</p>"],
  ["<p>a&nbsp;&amp;&lt;</p>", "<p>a\u00a0&amp;&lt;</p>"],
  ["<br>", "<p><br></p>"],
  ["text", "<p>text</p>"],
  ["<!-- c -->", ""],
  ["<PRE>\n\na</PRE><pre>b\r</pre>", "<pre>\n\na</pre><pre>b\r</pre>"],
  ["<noscript><p>n</p></noscript><p>y</p>", "<p>y</p>"],
  ["<title>T</title><p>x</p>", "<p>x</p>"],
  ["<p>a<!-- c -->b</p>", "<p>ab</p>"],
  ["<textarea>q</textarea><p>z</p>", "<p>z</p>"],
  ["<p>x<p>y", "<p>x</p><p>y</p>"],
  ["<ul>t<li>a</ul>", "<ul><li>a</li></ul>"],
  ['<p><a href="a\x01b">x</a></p>', '<p><a href="ab">x</a></p>'],
  ['<span style="font-size:10.5px">x</span>', '<p><span style="font-size:7.88pt">x</span></p>'],
  ['<span style="font-size:0.125pt">x</span>', '<p><span style="font-size:0.12pt">x</span></p>'],
  ['<p style="margin-left:18pt">x</p>', "<p>x</p>"],
  ['<p style="margin-left:54pt">x</p>', '<p style="margin-left:72pt">x</p>'],
  ['<span style="color:rgb(300,0,0)">x</span>', '<p><span style="color:#12c0000">x</span></p>'],
  ['<span style="color:hsl(120,50%,50%)">x</span>', '<p><span style="color:#40bf40">x</span></p>'],
  ['<span style="color:#abcd">x</span>', '<p><span style="color:#aabbcc">x</span></p>'],
];

test.each(DESKTOP)("same as the desktop: %j", (input, out) => {
  expect(normaliseHtml(input).html).toBe(out);
});

test("deep nesting keeps text", () => {
  const { html } = normaliseHtml("<div>".repeat(600) + "x" + "</div>".repeat(600));
  expect(html).toContain("x");
});

test("unreadable input throws a plain error", () => {
  expect(() => normaliseHtml("<!doctype html>")).toThrow(
    new Error("This file couldn't be read as HTML."),
  );
  expect(normaliseHtml("  \n")).toEqual({ html: "", images: new Map() });
});

// Slow in jsdom (its parser takes quadratic time on deep nesting); the desktop fails here too.
test("too deeply nested input throws a plain error", { timeout: 60000 }, () => {
  expect(() => normaliseHtml("<div>".repeat(11000) + "x")).toThrow(
    new Error("This file couldn't be read as HTML."),
  );
});

test("images: data: kept, remote dropped, resolveImage used for relative paths, never for \\\\host or //host or C: or /abs", () => {
  const data = normaliseHtmlReport(`<p><img src="data:image/jpeg;base64,${PNG_B64}"></p>`);
  expect(data.html).toBe('<p><img src="img/1.jpg"></p>');
  expect([...data.images.keys()]).toEqual(["img/1.jpg"]);

  const remote = normaliseHtmlReport('<p>a<img src="https://example.com/x.png"></p>');
  expect(remote.html).toBe("<p>a</p>");
  expect(remote.warnings).toEqual([W_WEBPICS]);

  const asked: string[] = [];
  const resolveImage = (src: string) => {
    asked.push(src);
    return src === "sub/p.png" ? PNG : null;
  };
  const rel = normaliseHtml('<p><img src="sub/p.png" width="5px"></p>', { resolveImage });
  expect(rel.html).toBe('<p><img src="img/1.png" width="5"></p>');
  expect(rel.images.get("img/1.png")).toEqual(PNG);
  expect(asked).toEqual(["sub/p.png"]);

  asked.length = 0;
  for (const src of [
    "\\\\host\\share\\x.png",
    "//host/x.png",
    "%2F%2Fhost/x.png",
    "%5C%5Chost/x.png",
    "C:/x.png",
    "C:x.png",
    "c:\\x.png",
    "/abs/x.png",
    "\\abs\\x.png",
    "../x.png",
    "a/../../x.png",
    "file:///x.png",
    "http://host/x.png",
  ]) {
    expect(normaliseHtml(`<p>a<img src="${src}"></p>`, { resolveImage }).html).toBe("<p>a</p>");
  }
  expect(asked).toEqual([]);

  // Canonical names (an earlier pass's output) are kept and never reused for new pictures.
  const canon = normaliseHtml(
    `<p><img src="img/1.png"><img src="data:image/png;base64,${PNG_B64}"></p>`,
  );
  expect(canon.html).toBe('<p><img src="img/1.png"><img src="img/2.png"></p>');
});

test("warnings look inside <template> like the desktop", () => {
  const got = normaliseHtmlReport(
    '<template><p class=k>t</p><img src="http://x/a.png"></template><p>z</p>',
  );
  expect(got.html).toBe("<p>z</p>");
  expect(got.warnings).toEqual(["page styles, classes and scripts", "pictures from the web"]);
});

test("textLength counts characters like the desktop", () => {
  expect(textLength("<p>ab <b>c</b></p><p>d</p>")).toBe(5);
  expect(textLength("<p>😀<script>x</script>a</p>")).toBe(2);
  expect(textLength("")).toBe(0);
});

test("htmlTitle", () => {
  expect(htmlTitle("<html><head><title> a \n b </title></head><body>x</body></html>")).toBe("a b");
  expect(htmlTitle("<p>x</p>")).toBe("");
});
