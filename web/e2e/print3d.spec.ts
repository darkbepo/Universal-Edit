// 3D viewer print tools: Arrange (turn, size, units, bed, lay flat), Check & Repair, overhangs,
// filament rows, Save as — in real Chromium (WebGL) with real clicks.
import { expect, type Page, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { FIXTURES } from "./global-setup"; // box.stl (20 × 10 × 5 mm)

type V = [number, number, number];

interface ViewerHook {
  infoRows(): Record<string, string>;
  screenOf(p: V): [number, number];
  setView(name: string): void;
  checked: number;
  repaired: number;
  panel: string;
  layingFlat: boolean;
}
declare global {
  interface Window {
    __viewer3d?: ViewerHook;
  }
}

/** 12 outward-facing triangles of a box. */
function box(x0: number, y0: number, z0: number, x1: number, y1: number, z1: number): V[][] {
  const c: V[] = [];
  for (const z of [z0, z1]) for (const y of [y0, y1]) for (const x of [x0, x1]) c.push([x, y, z]);
  const quads = [[0, 2, 3, 1], [4, 5, 7, 6], [0, 1, 5, 4], [2, 6, 7, 3], [0, 4, 6, 2], [1, 3, 7, 5]];
  return quads.flatMap(([a, b, cc, d]) => [[c[a], c[b], c[cc]], [c[a], c[cc], c[d]]]);
}

function stl(tris: V[][]): Buffer {
  const out = Buffer.alloc(84 + tris.length * 50);
  out.writeUInt32LE(tris.length, 80);
  tris.forEach((t, i) => t.forEach((p, k) => p.forEach((v, a) => out.writeFloatLE(v, 84 + i * 50 + 12 + k * 12 + a * 4))));
  return out;
}

const rows = (page: Page) => page.evaluate(() => window.__viewer3d!.infoRows());
const screen = (page: Page, p: V) => page.evaluate((q) => window.__viewer3d!.screenOf(q), p);

async function open(page: Page, files: string | { name: string; mimeType: string; buffer: Buffer }) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".viewer3d-info")).toContainText("Width (X)");
}

async function setNumber(page: Page, id: string, value: number) {
  await page.locator(id).fill(String(value));
  await page.locator(id).dispatchEvent("change");
}

test("arrange: turn, size, units, bed, undo, save", async ({ page }) => {
  await open(page, `${FIXTURES}box.stl`);
  expect(await rows(page)).toMatchObject({ "Printer bed": "Fits the printer's bed.", "Printed solid": "≈ 1 g" });
  await page.getByRole("button", { name: "Arrange", exact: true }).click();
  await expect(page.locator(".arrange-panel")).toBeVisible();
  await expect(page.locator("#arr-size-x")).toHaveValue("20.0");
  await page.getByRole("button", { name: "Turn +90° about X" }).click();
  expect(await rows(page)).toMatchObject({ "Width (X)": "20.0 mm", "Depth (Y)": "5.0 mm", "Height (Z)": "10.0 mm" });
  await setNumber(page, "#arr-size-x", 40); // proportions kept
  expect(await rows(page)).toMatchObject({ "Width (X)": "40.0 mm", "Height (Z)": "20.0 mm" });
  await page.getByRole("button", { name: "Made in inches" }).click();
  expect((await rows(page))["Printer bed"]).toContain("too wide");
  await page.locator("#arr-bed").selectOption("custom");
  await setNumber(page, "#arr-bed-w", 1200);
  await setNumber(page, "#arr-bed-d", 1200);
  await setNumber(page, "#arr-bed-h", 1200);
  expect((await rows(page))["Printer bed"]).toBe("Fits the printer's bed.");
  // (Ctrl+Z inside a number box is the box's own undo: use the button)
  await page.getByRole("button", { name: "Undo", exact: true }).click(); // inches undone
  await page.getByRole("button", { name: "Undo", exact: true }).click(); // size undone
  expect(await rows(page)).toMatchObject({ "Width (X)": "20.0 mm", "Height (Z)": "10.0 mm" });
  await page.getByRole("button", { name: "Mirror along Y" }).click();
  expect((await rows(page)).Watertight).toBe("Yes");
  await setNumber(page, "#arr-pos-x", 50);
  await page.getByRole("button", { name: "Centre", exact: true }).click();
  await expect(page.locator("#arr-pos-x")).toHaveValue("0.0");
  await page.locator(".arrange-panel").getByRole("button", { name: "Done" }).click();
  await expect(page.locator(".arrange-panel")).toBeHidden();

  await page.getByRole("button", { name: "Save as…" }).click();
  await page.locator("#logo-save-format").selectOption("stl");
  const download = page.waitForEvent("download");
  await page.locator(".logo-save-dialog").getByRole("button", { name: "Save" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("box edited.stl");
  const data = readFileSync((await file.path())!);
  let top = -Infinity;
  for (let t = 0; t < data.readUInt32LE(80); t++) for (let k = 0; k < 3; k++) top = Math.max(top, data.readFloatLE(84 + t * 50 + 12 + k * 12 + 8));
  expect(top).toBeCloseTo(10, 3);
  // the bed choice is kept for next time
  await page.reload();
  expect(await page.evaluate(() => localStorage.getItem("umc-bed"))).toBe("Custom size|1200|1200|1200");
});

test("lay flat with a real click on a slanted face", async ({ page }) => {
  const t = (30 * Math.PI) / 180;
  const turn = ([x, y, z]: V): V => [x, y * Math.cos(t) - z * Math.sin(t), y * Math.sin(t) + z * Math.cos(t) + 20];
  await open(page, { name: "tilted.stl", mimeType: "model/stl", buffer: stl(box(-10, -10, -10, 10, 10, 10).map((tri) => tri.map(turn))) });
  await page.getByRole("button", { name: "Arrange", exact: true }).click();
  await page.getByRole("button", { name: "Top", exact: true }).click();
  await page.getByRole("button", { name: "Lay flat on a face…" }).click();
  expect(await page.evaluate(() => window.__viewer3d!.layingFlat)).toBe(true);
  const z = Number((await rows(page))["Height (Z)"].split(" ")[0]);
  const [cx, cy] = await screen(page, [0, 0, z]); // the top of the tilted cube is one of its faces
  await page.mouse.click(cx, cy);
  await expect.poll(async () => (await rows(page))["Height (Z)"]).toBe("20.0 mm");
  expect(await page.evaluate(() => window.__viewer3d!.layingFlat)).toBe(false);
});

test("check and repair a cube with a hole; overhangs", async ({ page }) => {
  await open(page, { name: "holed.stl", mimeType: "model/stl", buffer: stl(box(-10, -10, 0, 10, 10, 20).slice(2)) });
  expect((await rows(page)).Watertight).toContain("No");
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await expect(page.locator(".check-result")).toContainText("1 hole in the surface");
  await page.getByRole("button", { name: "Repair" }).click();
  await expect(page.locator(".check-repaired")).toHaveText("Repaired: closed 1 hole.");
  await expect(page.locator(".check-result")).toContainText("No problems found");
  await expect(page.getByRole("button", { name: "Repair" })).toBeDisabled();
  expect((await rows(page)).Watertight).toBe("Yes");
  await page.keyboard.press("Control+z");
  await expect(page.locator(".check-result")).toContainText("hole");

  await open(page, { name: "t.stl", mimeType: "model/stl", buffer: stl([...box(-5, -5, 0, 5, 5, 10), ...box(-5, -5, 10, 25, 5, 20)]) });
  await page.getByRole("button", { name: "Check", exact: true }).click();
  await expect(page.locator(".check-overhang")).toContainText("3.0 cm² of overhang") // the whole underside counts, also where it rests on the cube;
  await page.locator("#check-show").uncheck();
  await expect(page.locator(".check-result")).toContainText("No problems found");
});

test("filament rows follow the material and infill", async ({ page }) => {
  await open(page, `${FIXTURES}box.stl`);
  await page.locator("#filament-material").selectOption("PETG");
  await setNumber(page, "#filament-infill", 100);
  expect(await rows(page)).toHaveProperty(["Filament (PETG, 100 % infill)"]);
  await page.reload();
  expect(await page.evaluate(() => localStorage.getItem("umc-filament"))).toBe("PETG|1");
});

test("phone width: the Arrange panel fits", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await open(page, `${FIXTURES}box.stl`);
  await page.getByRole("button", { name: "Arrange", exact: true }).click();
  const panel = await page.locator(".arrange-panel").boundingBox();
  expect(panel!.width).toBeLessThanOrEqual(375);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
});
