import { expect, test } from "vitest";
import { printCss } from "../src/engines/mupdf";

test("HTML → PDF restores tables for Pandoc's pages", () => {
  const css = printCss(0);
  expect(css).toContain("table{display:table!important");
  expect(css).toContain("padding:3px 8px");
  expect(css).not.toContain("body{margin");
});

test("a page with its own styles keeps its table look (the document editor's PDF save)", () => {
  const css = printCss(20, { pageStyles: true });
  expect(css).not.toContain("table{");
  expect(css).not.toContain("th,td{");
  expect(css).toContain("body{margin:20mm!important");
});
