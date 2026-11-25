// The website vector editor: opening an SVG, moving a shape with the mouse, drawing, the
// layers list, appearance, saving byte for byte, the hub tile, and phones.
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { expect, type Page, test } from "@playwright/test";

declare global {
  interface Window {
    __vectoreditor?: {
      text(): string;
      items(): { path: number[]; kind: string; name: string }[];
      selection: number[][];
      dirty: boolean;
      tool: string;
      select(paths: number[][]): void;
      client(x: number, y: number): [number, number];
      setZoom(s: number): void;
    };
  }
}

const SVG =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  "<!-- made by hand -->\n" +
  '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">\n' +
  '  <rect id="box" x="10" y="10" width="40" height="20" style="fill:#ff0000"/>\n' +
  '  <g id="grp" transform="translate(100 0)">\n' +
  '    <circle id="dot" cx="20" cy="20" r="10" fill="blue"/>\n' +
  "  </g>\n" +
  '  <text id="hi" x="10" y="80" font-size="20">Hello</text>\n' +
  "</svg>\n";
const file = (name: string, data: string) => ({ name, mimeType: "image/svg+xml", buffer: Buffer.from(data) });

async function openInEditor(page: Page, f: ReturnType<typeof file>) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles([f]);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".vector-editor")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__vectoreditor?.items().length ?? 0)).toBeGreaterThan(0);
  await page.evaluate(() => window.__vectoreditor!.setZoom(2)); // whole units under the mouse
}

const at = (page: Page, x: number, y: number) => page.evaluate(([a, b]) => window.__vectoreditor!.client(a, b), [x, y]);

async function drag(page: Page, from: [number, number], to: [number, number]) {
  const [ax, ay] = await at(page, ...from);
  const [bx, by] = await at(page, ...to);
  await page.mouse.move(ax, ay);
  await page.mouse.down();
  await page.mouse.move((ax + bx) / 2, (ay + by) / 2, { steps: 3 });
  await page.mouse.move(bx, by, { steps: 3 });
  await page.mouse.up();
}

async function saveBytes(page: Page): Promise<[string, Buffer]> {
  const download = page.waitForEvent("download");
  await page.locator(".vector-editor .editor-top").getByRole("button", { name: "Save", exact: true }).click();
  const d = await download;
  return [d.suggestedFilename(), readFileSync(await d.path())];
}

test("an SVG opens, saves byte for byte, and a dragged shape changes only its own line", async ({ page }) => {
  await openInEditor(page, file("drawing.svg", SVG));
  expect((await page.evaluate(() => window.__vectoreditor!.items())).map((i) => i.name)).toEqual(["box", "grp", "dot", "hi"]);
  await expect(page.locator(".vec-layers > li").first()).toContainText("hi"); // topmost first
  let [name, data] = await saveBytes(page);
  expect(name).toBe("drawing.svg");
  expect(data.toString()).toBe(SVG);
  await drag(page, [20, 20], [30, 25]);
  expect(await page.evaluate(() => window.__vectoreditor!.selection)).toEqual([[0]]);
  [, data] = await saveBytes(page);
  expect(data.toString()).toBe(SVG.replace('x="10" y="10"', 'x="20" y="15"'));
  expect(await page.evaluate(() => window.__vectoreditor!.dirty)).toBe(false);
});

test("tools draw shapes, the panel changes them, undo takes them back", async ({ page }) => {
  await openInEditor(page, file("drawing.svg", SVG));
  await page.getByRole("button", { name: "Rectangle" }).click();
  await drag(page, [60, 50], [90, 70]);
  await page.getByRole("button", { name: "Ellipse" }).click();
  await drag(page, [100, 50], [120, 60]);
  const kinds = (await page.evaluate(() => window.__vectoreditor!.items())).map((i) => i.kind);
  expect(kinds.slice(-2)).toEqual(["rect", "ellipse"]);
  // the new ellipse is chosen: change its outline width in the panel
  await page.getByLabel("Width", { exact: true }).first().fill("3");
  await page.getByLabel("Width", { exact: true }).first().press("Enter");
  expect(await page.evaluate(() => window.__vectoreditor!.text())).toContain("stroke-width:3");
  // keys: Delete removes it, Ctrl+Z brings it back
  await page.locator(".vec-stage").focus();
  await page.keyboard.press("Delete");
  expect((await page.evaluate(() => window.__vectoreditor!.items())).length).toBe(5);
  await page.keyboard.press("Control+z");
  expect((await page.evaluate(() => window.__vectoreditor!.items())).length).toBe(6);
  // the pen: three clicks and Enter
  await page.keyboard.press("p");
  for (const pt of [[10, 90], [40, 90], [40, 60]] as [number, number][]) {
    const [x, y] = await at(page, ...pt);
    await page.mouse.click(x, y);
  }
  await page.keyboard.press("Enter");
  expect(await page.evaluate(() => window.__vectoreditor!.text())).toContain('d="M10 90 L40 90 L40 60"');
  // hide a layer with its tick
  await page.getByLabel("Show box").uncheck();
  expect(await page.evaluate(() => window.__vectoreditor!.text())).toContain("display:none");
});

test("the hub tile makes a new drawing that saves as SVGZ", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator('.editor-tile[data-editor="vector"]').click();
  const dialog = page.locator("dialog.vec-new-dialog");
  await expect(dialog).toBeVisible();
  await dialog.locator("#vec-size").selectOption({ index: 3 }); // Full HD
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(page.locator(".vector-editor")).toBeVisible();
  await page.getByRole("button", { name: "Rectangle" }).click();
  await drag(page, [100, 100], [600, 400]);
  const saveDialog = page.locator("dialog.vec-save-dialog");
  await page.locator(".vector-editor .editor-top").getByRole("button", { name: "Save", exact: true }).click();
  await saveDialog.getByLabel("Format").selectOption("svgz");
  const download = page.waitForEvent("download");
  await saveDialog.getByRole("button", { name: "Save", exact: true }).click();
  const d = await download;
  expect(d.suggestedFilename()).toBe("Drawing.svgz");
  const text = gunzipSync(readFileSync(await d.path())).toString();
  expect(text).toContain('width="1920"');
  expect(text).toContain("<rect");
});

test("export makes a PNG through the worker", async ({ page }) => {
  await openInEditor(page, file("drawing.svg", SVG));
  await page.getByRole("button", { name: "Export…" }).click();
  const dialog = page.locator("dialog.vec-save-dialog");
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  const d = await download;
  expect(d.suggestedFilename()).toBe("drawing.png");
  expect(readFileSync(await d.path()).subarray(1, 4).toString()).toBe("PNG");
});

test("phones: the panel goes under the drawing and nothing scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 720 });
  await openInEditor(page, file("drawing.svg", SVG));
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(width).toBeLessThanOrEqual(375);
  const stage = await page.locator(".vec-stage").boundingBox();
  const side = await page.locator(".vec-side").boundingBox();
  expect(side!.y).toBeGreaterThan(stage!.y + stage!.height - 2);
  await expect(page.getByRole("button", { name: "Rectangle" })).toBeVisible();
});
