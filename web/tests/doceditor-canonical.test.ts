// @vitest-environment jsdom
// The normaliser's output is canonical: a second pass changes nothing, at any nesting depth
// (the desktop runs the same checks in tests/test_doceditor_canonical.py).
import { describe, expect, test } from "vitest";
import { MAX_NESTING, normaliseHtml } from "../src/doceditor/subset";
import { Rand, randomHtml } from "./doceditor-gen";

const norm = (html: string) => normaliseHtml(html).html;

test.each([
  // a trailing space beside an empty tag at the end of a block (trimmed after merging)
  ["<p><b>z </b><b></b> </p>", "<p><b>z</b></p>"],
  ['<p><a href="u">z </a><a href="u"></a></p>', '<p><a href="u">z</a></p>'],
  [
    "<blockquote><p><strong>z </strong><strong></strong>  </p></blockquote>",
    "<blockquote><p><b>z</b></p></blockquote>",
  ],
  // ... and a space that merging puts after a space
  ['<p><a href="u">z </a><a href="u"></a> x</p>', '<p><a href="u">z </a>x</p>'],
  // white space at the start of a list item is kept like in a paragraph (a tab stays)
  ["<ol><li><sub>\t</sub><b>x</b></li></ol>", "<ol><li>\t<b>x</b></li></ol>"],
  ["<ol><li>\t<b>x</b></li></ol>", "<ol><li>\t<b>x</b></li></ol>"],
  ["<div><ol><li><code>\t</code></li></ol></div>", "<ol><li>\t</li></ol>"],
  ["<ul><li><p>a</p>\t</li></ul>", "<ul><li>a<br>\t</li></ul>"],
  // loose lists still drop their source formatting
  ["<ul>\n<li>\n<p>a</p>\n</li>\n</ul>", "<ul><li>a</li></ul>"],
])("a second pass changes nothing: %j", (html, expected) => {
  expect(norm(html)).toBe(expected);
  expect(norm(expected)).toBe(expected);
});

describe("nesting cap", () => {
  test("nesting below the cap is kept", () => {
    const out = norm("<blockquote>".repeat(MAX_NESTING - 1) + "<p>a <b>b</b></p>");
    expect(out.split("<blockquote>").length - 1).toBe(MAX_NESTING - 1);
    expect(out).toContain("<p>a <b>b</b></p>");
    expect(norm(out)).toBe(out);
  });

  test.each([
    ["<blockquote>".repeat(150) + "<p>a <b>b</b></p>", "a b"],
    ["<ul><li>".repeat(150) + "a<p>b</p>c", "a b c"],
    ["<ul>".repeat(150) + "<li>a</li>", "a"],
    ["<table><tr><td>".repeat(120) + "<p>a</p><p>b</p>", "a b"],
    ["<div>".repeat(300) + "a<div>b</div>c", "a b c"],
    ["<p>" + '<span style="color:red">'.repeat(200) + "a<br>b" + "</span>".repeat(200) + "</p>", "a b"],
    ["<ol><li>".repeat(60) + "<table><tr><td>".repeat(60) + "x", "x"],
    ["<blockquote>".repeat(98) + "<ul><li>a<ul><li>b<ul><li>c</li></ul></li></ul></li></ul>", "a b c"],
    [
      "<blockquote>".repeat(99) + '<table><tr><td>a</td><td><img src="img/1.png"> b</td></tr></table>',
      "a b",
    ],
  ])("nesting above the cap is flattened (%#)", (html, words) => {
    const out = norm(html);
    expect(norm(out)).toBe(out);
    let depth = 0;
    let deepest = 0;
    for (const m of out.matchAll(/<(\/?)(blockquote|ul|ol|li|table|tr|td|span|b|i|u|s|a)[ >]/g)) {
      depth += m[1] ? -1 : 1;
      deepest = Math.max(deepest, depth);
    }
    expect(deepest).toBeLessThanOrEqual(MAX_NESTING + 2);
    const text = out.replace(/<[^>]*>/g, "");
    for (const word of words.split(" ")) expect(text).toContain(word);
  });
});

// jsdom keeps about 0.18 MB per parse, so the loops stay small.
test.each([1, 2, 3])("normalising twice changes nothing (seed %i)", (seed) => {
  const r = new Rand(seed * 7919);
  for (let n = 0; n < 300; n++) {
    const raw = randomHtml(r);
    const once = norm(raw);
    expect(norm(once), raw).toBe(once);
  }
});
