// The Markdown-ZIP check the Edit switch runs on a lone archive: its own small module, so a
// dropped ZIP doesn't download the editor's readers (markdown-it, the DOCX reader…).
import mdzipSrc from "../src/doceditor/mdzip.ts?raw";
import appSrc from "../src/ui/app.ts?raw";
import pageSrc from "../src/ui/doceditor/page.ts?raw";
import { strToU8, zipSync } from "fflate";
import { expect, test } from "vitest";
import { isEditableMarkdownZip, markdownZipName } from "../src/doceditor/mdzip";

test("the check names the .md of a website Markdown ZIP", () => {
  const zip = zipSync({ "a.md": strToU8("x"), "a_files/1.png": new Uint8Array([1]) });
  expect(markdownZipName(zip)).toBe("a.md");
  expect(isEditableMarkdownZip(zip)).toBe(true);
  expect(isEditableMarkdownZip(zipSync({ "a.md": strToU8("x"), "b.md": strToU8("y") }))).toBe(false);
});

test("mdzip.ts imports only the ZIP package code", () => {
  const imports = [...mdzipSrc.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]);
  expect(imports).toEqual(["./docx-package"]);
});

test("app.ts and the page take the check from mdzip, not open.ts", () => {
  for (const [f, src] of [["app.ts", appSrc], ["page.ts", pageSrc]]) {
    expect(src, f).not.toMatch(/isEditableMarkdownZip[^\n]*doceditor\/open"/);
    expect(src, f).not.toMatch(/\{ isEditableMarkdownZip \}[^\n]*import\("\.\.\/doceditor\/open"\)/);
  }
});
