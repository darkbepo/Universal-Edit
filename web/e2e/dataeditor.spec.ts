// The website data editor: opening, typing, rows / columns, sort, copy / paste, find, saving
// (same format and another one), workbooks with sheets, the hub tile, phones.
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { strToU8, zipSync } from "fflate";
import { findPython } from "./global-setup";

declare global {
  interface Window {
    __dataeditor?: {
      columns(): string[];
      rows(): string[][];
      format: string;
      dirty: boolean;
      sheet: number;
      current: number[];
      select(r: number, c: number, r2?: number, c2?: number): void;
    };
  }
}

const CSV = "name;age\r\nAnn;3\r\nBob;10\r\n";
const file = (name: string, data: string | Uint8Array) => ({ name, mimeType: "application/octet-stream", buffer: Buffer.from(data) });

async function openInEditor(page: Page, f: ReturnType<typeof file>, choice?: string) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles([f]);
  if (choice) {
    await page.locator(".edit-more").click();
    await page.getByRole("menuitem", { name: choice }).click();
  } else await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".data-editor")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__dataeditor?.rows().length ?? -1)).toBeGreaterThan(-1);
}

const rows = (page: Page) => page.evaluate(() => window.__dataeditor!.rows());
const cell = (page: Page, r: number, c: number) => page.locator(`.data-row[data-row="${r}"] .data-cell[data-col="${c}"]`);

/** Click Save (or the Save as window's Save) and read what was downloaded. */
async function saveBytes(page: Page, scope = ".data-editor .editor-top"): Promise<[string, Buffer]> {
  const download = page.waitForEvent("download");
  await page.locator(scope).getByRole("button", { name: "Save", exact: true }).click();
  const d = await download;
  return [d.suggestedFilename(), readFileSync(await d.path())];
}

test("a CSV opens in the table; typing, rows, sort and saving keep its separator and line endings", async ({ page }) => {
  await openInEditor(page, file("people.csv", CSV));
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["name", "age"]);
  expect(await rows(page)).toEqual([["Ann", "3"], ["Bob", "10"]]);
  await expect(page.locator(".data-header-switch")).toBeVisible();
  // untouched: the bytes as read
  let [name, data] = await saveBytes(page);
  expect(name).toBe("people.csv");
  expect(data.toString()).toBe(CSV);
  // typing into a cell (typing starts editing; Enter keeps it and moves down)
  await cell(page, 0, 1).click();
  await page.keyboard.type("4");
  await page.keyboard.press("Enter");
  expect((await rows(page))[0][1]).toBe("4");
  expect(await page.evaluate(() => window.__dataeditor!.current)).toEqual([1, 1]);
  // a new row below, filled with Tab between cells
  await page.getByRole("button", { name: "+ Row" }).click();
  await page.keyboard.press("Home");
  await page.keyboard.type("Cy");
  await page.keyboard.press("Tab");
  await page.keyboard.type("2");
  await page.keyboard.press("Enter");
  expect(await rows(page)).toEqual([["Ann", "4"], ["Bob", "10"], ["Cy", "2"]]);
  // sort by age: numbers by value
  await cell(page, 0, 1).click();
  await page.getByRole("button", { name: "Sort A→Z" }).click();
  expect((await rows(page)).map((r) => r[0])).toEqual(["Cy", "Ann", "Bob"]);
  [, data] = await saveBytes(page);
  expect(data.toString()).toBe("name;age\r\nCy;2\r\nAnn;4\r\nBob;10\r\n");
  expect(await page.evaluate(() => window.__dataeditor!.dirty)).toBe(false);
  // undo / redo with the keyboard
  await page.keyboard.press("Control+z");
  expect((await rows(page)).map((r) => r[0])).toEqual(["Ann", "Bob", "Cy"]);
  await page.keyboard.press("Control+y");
  expect((await rows(page)).map((r) => r[0])).toEqual(["Cy", "Ann", "Bob"]);
});

test("copy and paste a block, Delete empties cells, columns are renamed and added", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openInEditor(page, file("t.csv", "a,b\n1,2\n3,4\n"));
  await cell(page, 0, 0).click();
  await cell(page, 1, 1).click({ modifiers: ["Shift"] });
  await page.keyboard.press("Control+c");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe("1\t2\n3\t4");
  // paste past the edges: rows and columns are added, one undo step
  await cell(page, 1, 1).click();
  await page.keyboard.press("Control+v");
  expect(await rows(page)).toEqual([["1", "2", ""], ["3", "1", "2"], ["", "3", "4"]]);
  await page.keyboard.press("Control+z");
  expect(await rows(page)).toEqual([["1", "2"], ["3", "4"]]);
  await cell(page, 0, 0).click();
  await page.keyboard.press("Delete");
  expect((await rows(page))[0][0]).toBe("");
  // double-click edits in place
  await cell(page, 1, 0).dblclick();
  await expect(page.locator(".data-input")).toBeVisible();
  await page.keyboard.press("Control+a");
  await page.keyboard.type("x");
  await page.keyboard.press("Enter");
  expect((await rows(page))[1][0]).toBe("x");
  // rename by double-clicking the name (the browser's prompt)
  page.once("dialog", (d) => void d.accept("letters"));
  await page.locator('.data-col[data-col="0"]').dblclick();
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["letters", "b"]);
  // the column menu
  await page.locator('.data-col[data-col="1"]').click({ button: "right" });
  await page.getByRole("menuitem", { name: "Add a column to the right" }).click();
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["letters", "b", "column3"]);
  // the header switch: the names become the first row
  await page.locator(".data-header-switch input").uncheck();
  expect((await rows(page))[0]).toEqual(["letters", "b", "column3"]);
  await expect(page.locator('.data-col[data-col="0"]')).toHaveText("A");
});

test("JSON: Edit opens the code editor, ▾ the table; nested keys, find & replace, numbers kept", async ({ page }) => {
  const json = '[\n  {"pet": "cat", "n": 12, "a": {"b": 1.0}},\n  {"pet": "Cat", "n": 2}\n]\n';
  await openInEditor(page, file("t.json", json), "Edit as table");
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["pet", "n", "a.b"]);
  await expect(page.locator(".data-header-switch")).toBeHidden();
  await page.keyboard.press("Control+h");
  await page.getByLabel("Find", { exact: true }).fill("2");
  await page.getByLabel("Replace with").fill("7");
  await page.getByRole("button", { name: "Replace all" }).click();
  await expect(page.locator(".sub-find-count")).toHaveText("Replaced 2");
  const [, data] = await saveBytes(page);
  expect(data.toString()).toBe('[\n  {\n    "pet": "cat",\n    "n": 17,\n    "a": {\n      "b": 1.0\n    }\n  },\n  {\n    "pet": "Cat",\n    "n": 7\n  }\n]\n');
});

test("Save as another format, then the new file is edited", async ({ page }) => {
  await openInEditor(page, file("t.csv", "name,a.b\nAnn,1\n"));
  await page.locator(".data-editor .editor-top").getByRole("button", { name: "Save as…" }).click();
  const dialog = page.locator(".data-save-dialog");
  await dialog.getByLabel("Format").selectOption("json");
  const [name, data] = await saveBytes(page, ".data-save-dialog");
  expect(name).toBe("t.json");
  expect(JSON.parse(data.toString())).toEqual([{ name: "Ann", a: { b: 1 } }]);
  expect(await page.evaluate(() => window.__dataeditor!.format)).toBe("json");
  await expect(page.locator(".data-title")).toHaveText("t.json");
});

test("a workbook: one tab per sheet; an edited save keeps the other cells' formulas", async ({ page }) => {
  const sheet = (rows: string) => `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${rows}</sheetData></worksheet>`;
  const parts: Record<string, string> = {
    "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
    "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    "xl/workbook.xml": '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="One" sheetId="1" r:id="rId1"/><sheet name="Two" sheetId="2" r:id="rId2"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
    "xl/worksheets/sheet1.xml": sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>n</t></is></c><c r="B1" t="inlineStr"><is><t>twice</t></is></c></row><row r="2"><c r="A2"><v>2</v></c><c r="B2"><f>A2*2</f><v>4</v></c></row>'),
    "xl/worksheets/sheet2.xml": sheet('<row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row>'),
  };
  const xlsx = zipSync(Object.fromEntries(Object.entries(parts).map(([k, v]) => [k, strToU8(v)])));
  await openInEditor(page, file("book.xlsx", xlsx));
  await expect(page.locator(".data-tab")).toHaveText(["One", "Two"]);
  expect(await rows(page)).toEqual([["2", "4"]]);
  await cell(page, 0, 0).click();
  await page.keyboard.type("5");
  await page.keyboard.press("Enter");
  await page.locator(".data-tab", { hasText: "Two" }).click();
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["x"]);
  const [, data] = await saveBytes(page);
  // the desktop's reader (and openpyxl, when there is one) sees the change and the formula
  const python = findPython("import openpyxl");
  test.skip(!python, "needs a Python with openpyxl");
  const path = join(tmpdir(), `umc-web-book-${Date.now()}.xlsx`);
  writeFileSync(path, data);
  const out = execFileSync(python!, ["-c", "import sys, openpyxl; ws = openpyxl.load_workbook(sys.argv[1])['One']; print(ws['A2'].value, ws['B2'].value)", path]).toString().trim();
  expect(out).toBe("5 =A2*2");
});

test("the hub tile opens a new table; Back asks about unsaved changes", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=data]").click();
  await expect(page.locator(".data-editor")).toBeVisible();
  expect(await page.evaluate(() => window.__dataeditor!.columns())).toEqual(["column1", "column2", "column3"]);
  await page.keyboard.type("hello");
  await page.keyboard.press("Enter");
  page.once("dialog", (d) => void d.dismiss());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".data-editor")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".data-editor")).toBeHidden();
});

test("phones: the top bar, tools and table fit 375 px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 740 });
  await openInEditor(page, file("people.csv", CSV));
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(375);
  await expect(page.getByRole("button", { name: "+ Row" })).toBeInViewport();
  await expect(cell(page, 0, 0)).toBeInViewport();
  // choosing a cell far right scrolls the table, never the page (the Back button stays)
  await page.evaluate(() => window.__dataeditor!.select(0, 1));
  await cell(page, 0, 1).click();
  await page.keyboard.press("End");
  expect(await page.evaluate(() => [...document.querySelectorAll<HTMLElement>(".data-editor, .data-editor *")].filter((e) => !e.classList.contains("data-grid") && e.scrollLeft > 0).length)).toBe(0);
  await expect(page.getByRole("button", { name: /Back/ })).toBeInViewport();
});
