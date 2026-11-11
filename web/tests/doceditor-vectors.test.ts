// @vitest-environment jsdom
// The shared Markdown and TXT vectors (tests/fixtures/doceditor-vectors.json, made from the
// desktop code) must come out byte-identical on the website.
import vectorsJson from "../../tests/fixtures/doceditor-vectors.json";
import { describe, expect, test } from "vitest";
import { mdToHtml, splitFrontMatter } from "../src/doceditor/markdown";
import { toMarkdown } from "../src/doceditor/md-writer";
import { openDocument, txtToHtml } from "../src/doceditor/open";
import { normaliseHtml } from "../src/doceditor/subset";

interface MarkdownVector {
  md: string;
  html: string;
  front_matter: string;
  warnings: string[];
  md_out: string;
}
interface TxtVector {
  txt: string;
  html: string;
}
const vectors = vectorsJson as unknown as { markdown: MarkdownVector[]; txt: TxtVector[] };

const noConvert = async (): Promise<Uint8Array> => {
  throw new Error("not used");
};
const file = (text: string, name: string) => new File([new TextEncoder().encode(text)], name);

describe("shared markdown vectors", () => {
  test.each(vectors.markdown.map((v, i) => [i, v] as const))("markdown #%i reads", (_i, v) => {
    const [front, body] = splitFrontMatter(v.md);
    expect(front).toBe(v.front_matter);
    expect(normaliseHtml(mdToHtml(body)).html).toBe(v.html);
  });

  test.each(vectors.markdown.map((v, i) => [i, v] as const))("markdown #%i writes", (_i, v) => {
    expect(toMarkdown(v.html)).toBe(v.md_out);
  });

  test.each(vectors.markdown.map((v, i) => [i, v] as const))("markdown #%i opens", async (_i, v) => {
    const doc = await openDocument(file(v.md, "a.md"), noConvert);
    expect(doc.html).toBe(v.html);
    expect(doc.frontMatter).toBe(v.front_matter);
    expect(doc.warnings).toEqual(v.warnings);
    expect(doc.sourceFormat).toBe("md");
  });
});

describe("shared txt vectors", () => {
  test.each(vectors.txt.map((v, i) => [i, v] as const))("txt #%i", async (_i, v) => {
    expect(txtToHtml(v.txt)).toBe(v.html);
    const doc = await openDocument(file(v.txt, "a.txt"), noConvert);
    expect(doc.html).toBe(v.html);
    expect(doc.sourceFormat).toBe("txt");
  });
});
