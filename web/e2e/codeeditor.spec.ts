// The website code editor: opening, byte-exact saving, tabs, find & replace, big files, phones.
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

declare global {
  interface Window {
    __codeeditor?: { text(): string; setText(t: string): void; tabs(): string[]; dirty: boolean };
    __writes?: number[][];
  }
}

const file = (name: string, data: Buffer | string) => ({ name, mimeType: "text/plain", buffer: Buffer.isBuffer(data) ? data : Buffer.from(data) });

async function openInEditor(page: Page, files: ReturnType<typeof file>[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".code-editor")).toBeVisible();
  await expect(page.locator(".code-tab")).toHaveCount(files.length);
}

async function saveBytes(page: Page): Promise<Buffer> {
  const download = page.waitForEvent("download");
  await page.locator(".code-editor").getByRole("button", { name: "Save", exact: true }).click();
  return readFileSync(await (await download).path());
}

test("a CSS file opens in the code editor and saves byte for byte", async ({ page }) => {
  const data = Buffer.from("﻿a {\r\n  color: red;\r\n}\r\n", "utf-8");
  await openInEditor(page, [file("style.css", data)]);
  expect(await saveBytes(page)).toEqual(data);
  await page.evaluate(() => window.__codeeditor!.setText(window.__codeeditor!.text() + "b {}"));
  expect(await saveBytes(page)).toEqual(Buffer.concat([data, Buffer.from("b {}")]));
});

test("Windows-1252 and no-break spaces survive an untouched save", async ({ page }) => {
  const cp1252 = Buffer.from([0x63, 0x61, 0x66, 0xe9, 0x0a]); // "café\n"
  const nbsp = Buffer.from("a b\n", "utf-8");
  await openInEditor(page, [file("old.txt", cp1252), file("space.md", nbsp)]);
  await page.locator(".code-tab").first().locator(".code-tab-name").click();
  expect(await saveBytes(page)).toEqual(cp1252);
  await page.locator(".code-tab").nth(1).locator(".code-tab-name").click();
  expect(await saveBytes(page)).toEqual(nbsp);
});

test("tabs keep the file order; Back asks about a hidden unsaved tab", async ({ page }) => {
  await openInEditor(page, [file("a.css", "a {}"), file("b.js", "let b = 1;")]);
  expect(await page.evaluate(() => window.__codeeditor!.tabs())).toEqual(["a.css", "b.js"]);
  await page.locator(".code-tab").first().locator(".code-tab-name").click();
  await page.evaluate(() => window.__codeeditor!.setText("changed"));
  await page.locator(".code-tab").nth(1).locator(".code-tab-name").click();
  page.once("dialog", (d) => void d.dismiss());
  await page.getByRole("button", { name: /Back/ }).click();
  await expect(page.locator(".code-editor")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: /Back/ }).click();
  await expect(page.locator(".code-editor")).toBeHidden();
});

test("closing another tab keeps what you typed in this one", async ({ page }) => {
  await openInEditor(page, [file("a.css", "a {}"), file("b.js", "let b = 1;")]);
  await page.locator(".code-tab").nth(1).locator(".code-tab-name").click();
  await page.evaluate(() => window.__codeeditor!.setText("typed in b"));
  await page.locator(".code-tab").first().locator(".code-tab-close").click();
  expect(await page.evaluate(() => window.__codeeditor!.tabs())).toEqual(["b.js"]);
  expect(await page.evaluate(() => window.__codeeditor!.text())).toBe("typed in b");
});

test("mixed line endings survive an untouched save", async ({ page }) => {
  const data = Buffer.from("a\r\nb\r\nc\nd", "utf-8");
  await openInEditor(page, [file("mixed.txt.js", data)]);
  expect(await saveBytes(page)).toEqual(data);
});

test("word wrap follows you to the other tabs", async ({ page }) => {
  await openInEditor(page, [file("a.css", "a {}"), file("b.css", "b {}")]);
  await page.locator(".cm-content").click();
  await page.keyboard.press("Alt+z");
  await expect(page.locator(".cm-content.cm-lineWrapping")).toHaveCount(1);
  await page.locator(".code-tab").nth(1).locator(".code-tab-name").click();
  await expect(page.locator(".cm-content.cm-lineWrapping")).toHaveCount(1);
});

test("two files with the same name both open", async ({ page }) => {
  await openInEditor(page, [file("index.js", "let one = 1;")]);
  await page.locator(".code-editor input[type=file]").setInputFiles([file("index.js", "let two = 2; // another folder")]);
  await expect(page.locator(".code-tab")).toHaveCount(2);
  expect(await page.evaluate(() => window.__codeeditor!.text())).toBe("let two = 2; // another folder");
});

test("find counts matches and Replace all is one undo step", async ({ page }) => {
  await openInEditor(page, [file("a.js", "a-b-c")]);
  await page.locator(".cm-content").click();
  await page.keyboard.press("Control+h");
  await page.getByRole("searchbox", { name: "Find" }).fill("-");
  await expect(page.locator(".code-find-count")).toHaveText("2 matches");
  await page.getByRole("textbox", { name: "Replace with" }).fill("+");
  await page.getByRole("button", { name: "Replace every match" }).click();
  expect(await page.evaluate(() => window.__codeeditor!.text())).toBe("a+b+c");
  await expect(page.locator(".code-find-count")).toHaveText("2 replaced");
  await page.locator(".cm-content").click();
  await page.keyboard.press("Control+z");
  expect(await page.evaluate(() => window.__codeeditor!.text())).toBe("a-b-c");
});

test("Save to a file… then Ctrl+S writes to that file again", async ({ page }) => {
  await page.addInitScript(() => {
    window.__writes = [];
    (window as unknown as { showSaveFilePicker: unknown }).showSaveFilePicker = async () => ({
      name: "picked.css",
      createWritable: async () => ({
        write: async (b: Blob) => void window.__writes!.push(Array.from(new Uint8Array(await b.arrayBuffer()))),
        close: async () => undefined,
      }),
    });
  });
  await openInEditor(page, [file("a.css", "a {}")]);
  await page.getByRole("button", { name: "Save as…" }).click();
  await page.getByRole("button", { name: "Save to a file…" }).click();
  await expect.poll(() => page.evaluate(() => window.__writes!.length)).toBe(1);
  await page.evaluate(() => window.__codeeditor!.setText("b {}"));
  await page.locator(".cm-content").click();
  await page.keyboard.press("Control+s");
  await expect.poll(() => page.evaluate(() => window.__writes!.length)).toBe(2);
  expect(await page.evaluate(() => String.fromCharCode(...window.__writes![1]))).toBe("b {}");
  await expect(page.locator(".code-tab").first()).toContainText("picked.css");
});

test("big files open without freezing", async ({ page }) => {
  const log = Array.from({ length: 120_000 }, (_, i) => `2026-11-14 12:00:${String(i % 60).padStart(2, "0")} INFO line ${i}`).join("\n");
  const minified = "var a=1;".repeat(190_000);
  await openInEditor(page, [file("big.log", log), file("app.min.js", minified)]);
  const start = Date.now();
  await page.locator(".cm-content").click();
  await page.keyboard.type("x");
  expect(Date.now() - start).toBeLessThan(3000);
  expect(await page.evaluate(() => window.__codeeditor!.text().length)).toBe(minified.length + 1);
});

test("a huge one-line file stays quick with word wrap on", async ({ page }) => {
  await openInEditor(page, [file("a.css", "a {}")]);
  await page.locator(".cm-content").click();
  await page.keyboard.press("Alt+z"); // wrap on
  const start = Date.now();
  await page.locator(".code-editor input[type=file]").setInputFiles([file("app.min.js", "var a=1;".repeat(190_000))]);
  await expect(page.locator(".code-tab")).toHaveCount(2);
  await page.locator(".cm-content").click();
  await page.keyboard.type("x");
  expect(Date.now() - start).toBeLessThan(4000);
});

test("a .txt offers Edit as code next to Edit", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles([file("notes.txt", "hello")]);
  await page.getByRole("button", { name: "Other editors" }).click();
  await page.getByRole("menuitem", { name: "Edit as code" }).click();
  await expect(page.locator(".code-editor")).toBeVisible();
  expect(await page.evaluate(() => window.__codeeditor!.text())).toBe("hello");
});

test("the code editor tile opens an Untitled file", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=code]").click();
  await expect(page.locator(".code-editor")).toBeVisible();
  await expect(page.locator(".code-tab")).toHaveText(["Untitled×"]);
});

test("phones: no sideways scroll, Save reachable", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await openInEditor(page, [file("a.css", "a { color: red; }"), file("b.css", "b {}"), file("c.css", "c {}")]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await expect(page.locator(".code-editor").getByRole("button", { name: "Save", exact: true })).toBeInViewport();
});
