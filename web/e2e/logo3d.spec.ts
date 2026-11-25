// 3D viewer → Add logo: a picture raised on a face of the model, or as a badge; undo; Save as.
// Real Chromium (WebGL) with real mouse clicks / drags on the view.
import { expect, type Page, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { FIXTURES } from "./global-setup"; // box.stl (20 × 10 × 5 mm) and shapes.png are made there

const fx = (name: string) => `${FIXTURES}${name}`;

interface ViewerHook {
  editing: { point: [number, number, number] | null; logo: unknown | null } | null;
  infoRows(): Record<string, string>;
  screenOf(p: [number, number, number]): [number, number];
  setView(name: string): void;
  render(): void;
}
declare global {
  interface Window {
    __viewer3d?: ViewerHook;
  }
}

async function openViewer(page: Page) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles([fx("box.stl")]);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".viewer3d-info")).toContainText("Width (X)");
}

async function choosePicture(page: Page) {
  await page.locator(".logo-panel input[type=file]").setInputFiles(fx("shapes.png"));
  await expect(page.locator(".logo-picture-name")).toHaveText("shapes.png");
  await expect(page.locator("#logo-mode")).toHaveValue("dark"); // no transparency
}

async function setNumber(page: Page, id: string, value: number) {
  await page.locator(id).fill(String(value));
  await page.locator(id).press("Enter");
  await page.locator(id).dispatchEvent("change");
}

const screen = (page: Page, p: [number, number, number]) => page.evaluate((q) => window.__viewer3d!.screenOf(q), p);

/** Max Z of a binary STL. */
function stlTop(data: Buffer): number {
  const n = data.readUInt32LE(80);
  let top = -Infinity;
  for (let t = 0; t < n; t++) for (let k = 0; k < 3; k++) top = Math.max(top, data.readFloatLE(84 + t * 50 + 12 + k * 12 + 8));
  return top;
}

test("a logo on the top face: click to place, drag to move, apply, undo, save", async ({ page }) => {
  await openViewer(page);
  await page.getByRole("button", { name: "Top", exact: true }).click();
  await page.getByRole("button", { name: "Add logo…" }).click();
  await expect(page.locator(".logo-panel")).toBeVisible();
  await expect(page.getByRole("button", { name: "Undo" })).toBeDisabled();
  await choosePicture(page);
  await setNumber(page, "#logo-width", 8);
  await expect(page.locator(".logo-note")).toContainText("Click a flat face");

  // a real click on the middle of the top face
  const [cx, cy] = await screen(page, [0, 0, 5]);
  await page.mouse.click(cx, cy);
  await expect(page.locator(".logo-note")).toContainText("8.0 × 4.9 mm");
  const point = await page.evaluate(() => window.__viewer3d!.editing!.point);
  expect(point![2]).toBeCloseTo(5, 3);
  expect(Math.abs(point![0])).toBeLessThan(0.5);

  // drag the logo by its rectangle (left part) 7 mm to the right: it keeps the grabbed spot
  const [gx, gy] = await screen(page, [-2.5, 0, 5]);
  const [tx, ty] = await screen(page, [4.5, 0, 5]);
  await page.mouse.move(gx, gy);
  await page.mouse.down();
  await page.mouse.move((gx + tx) / 2, (gy + ty) / 2, { steps: 4 });
  await page.mouse.move(tx, ty, { steps: 4 });
  await page.mouse.up();
  const moved = await page.evaluate(() => window.__viewer3d!.editing!.point);
  expect(moved![0]).toBeCloseTo(7, 0);
  await expect(page.locator(".logo-note")).toContainText("hangs over the edge"); // 8 mm wide at x≈7 on a 20 mm box
  await page.mouse.move(tx, ty);
  await page.mouse.down();
  await page.mouse.move(gx, gy, { steps: 6 });
  await page.mouse.up();
  await expect(page.locator(".logo-note")).not.toContainText("hangs over the edge");

  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page.locator(".logo-panel")).toBeHidden();
  expect(await page.evaluate(() => window.__viewer3d!.infoRows())).toMatchObject({ "Height (Z)": "7.0 mm", Parts: "2" });
  await expect(page.locator(".logo-list li")).toHaveCount(1);

  await page.keyboard.press("Control+z");
  expect(await page.evaluate(() => window.__viewer3d!.infoRows()["Height (Z)"])).toBe("5.0 mm");
  await page.keyboard.press("Control+y");
  expect(await page.evaluate(() => window.__viewer3d!.infoRows()["Height (Z)"])).toBe("7.0 mm");

  // leaving asks while the logo isn't saved
  page.once("dialog", (d) => void d.dismiss());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".viewer3d")).toBeVisible();

  await page.getByRole("button", { name: "Save as…" }).click();
  await page.locator("#logo-save-format").selectOption("stl");
  const download = page.waitForEvent("download");
  await page.locator(".logo-save-dialog").getByRole("button", { name: "Save" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("box with logo.stl");
  const data = readFileSync((await file.path())!);
  expect(stlTop(data)).toBeCloseTo(7, 3);
  await expect(page.locator(".viewer3d .editor-status")).toContainText("Saved");
  await page.getByRole("button", { name: "← Back to Convert" }).click(); // saved: no question
  await expect(page.locator(".viewer3d")).toBeHidden();
});

test("change and remove a logo; 3MF save", async ({ page }) => {
  await openViewer(page);
  await page.getByRole("button", { name: "Top", exact: true }).click();
  await page.getByRole("button", { name: "Add logo…" }).click();
  await choosePicture(page);
  await setNumber(page, "#logo-width", 6);
  const [cx, cy] = await screen(page, [0, 0, 5]);
  await page.mouse.click(cx, cy);
  await expect(page.locator(".logo-note")).toContainText("6.0 ×");
  await page.getByRole("button", { name: "Apply" }).click();
  await page.locator(".logo-list li").first().dblclick();
  await expect(page.locator(".logo-panel h2")).toHaveText("Change the logo");
  await setNumber(page, "#logo-thickness", 3);
  await page.getByRole("button", { name: "Apply" }).click();
  expect(await page.evaluate(() => window.__viewer3d!.infoRows()["Height (Z)"])).toBe("8.0 mm");
  await expect(page.locator(".logo-list li")).toHaveCount(1);

  await page.getByRole("button", { name: "Save as…" }).click();
  const download = page.waitForEvent("download");
  await page.locator(".logo-save-dialog").getByRole("button", { name: "Save" }).click(); // 3MF first
  expect((await download).suggestedFilename()).toBe("box with logo.3mf");

  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.locator(".logo-list li")).toHaveCount(0);
  expect(await page.evaluate(() => window.__viewer3d!.infoRows()["Height (Z)"])).toBe("5.0 mm");
  page.once("dialog", (d) => void d.accept()); // removing it after saving is a change too
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".viewer3d")).toBeHidden();
});

test("a keychain badge from the start screen's 3D tile", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator('.editor-tile[data-editor="model"]').click();
  await expect(page.locator(".viewer3d")).toBeVisible();
  await page.getByRole("button", { name: "Add logo…" }).click();
  await expect(page.locator("#logo-where")).toBeHidden(); // no model: a badge
  await choosePicture(page);
  await page.locator("#logo-plate").selectOption("rounded");
  await page.locator("#logo-hole").check();
  await expect(page.locator(".logo-note")).toContainText("40.0 × 24.6 mm");
  await page.getByRole("button", { name: "Apply" }).click();
  expect(await page.evaluate(() => window.__viewer3d!.infoRows())).toMatchObject({ Parts: "2", Watertight: "Yes", "Height (Z)": "4.0 mm" });
  page.once("dialog", (d) => void d.accept()); // "Discard …?" — yes: the badge goes
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".viewer3d")).toBeHidden();
  await page.locator('.editor-tile[data-editor="model"]').click();
  await expect(page.locator(".viewer3d-placeholder")).toContainText("Open a 3D model");
  await expect(page.locator(".logo-list li")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Undo" })).toBeDisabled();
});

test("Apply right after a change waits for the new trace", async ({ page }) => {
  await openViewer(page);
  await page.getByRole("button", { name: "Top", exact: true }).click();
  await page.getByRole("button", { name: "Add logo…" }).click();
  await choosePicture(page);
  await setNumber(page, "#logo-width", 6);
  const [cx, cy] = await screen(page, [0, 0, 5]);
  await page.mouse.click(cx, cy);
  await expect(page.locator(".logo-note")).toContainText("6.0 ×");
  await page.locator("#logo-width").fill("9");
  await page.locator("#logo-width").dispatchEvent("change");
  await page.getByRole("button", { name: "Apply" }).click(); // before the 250 ms pause ends
  await expect(page.locator(".logo-panel")).toBeHidden();
  await expect(page.locator(".logo-list li")).toContainText("(9 mm, on the model)");
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
});

test("phone width: the panel fits under the view", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await openViewer(page);
  await page.getByRole("button", { name: "Add logo…" }).click();
  const panel = await page.locator(".logo-panel").boundingBox();
  expect(panel!.width).toBeLessThanOrEqual(375);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
