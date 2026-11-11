// The 3D viewer: Convert | Edit for 3D models, measurements, views — in real Chromium (WebGL).
import { writeFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;

interface ViewerHook {
  style: string;
  showGrid: boolean;
  camera: { yaw: number; pitch: number; distance: number };
  infoRows(): Record<string, string>;
  render(): void;
}
declare global {
  interface Window {
    __viewer3d?: ViewerHook;
  }
}

/** A 20 × 10 × 5 mm box as binary STL (12 triangles), away from the origin. */
function boxStl(path: string) {
  const [w, d, h] = [20, 10, 5];
  const c = (x: number, y: number, z: number) => [x * w + 40, y * d + 40, z * h + 40];
  const quads = [
    [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]], [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], [[0, 1, 0], [0, 1, 1], [1, 1, 1], [1, 1, 0]],
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]], [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]],
  ];
  const buf = Buffer.alloc(84 + 12 * 50);
  buf.writeUInt32LE(12, 80);
  let o = 84;
  for (const q of quads) for (const tri of [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]) {
    o += 12; // normal (slicers recompute it)
    for (const v of tri) for (const x of c(v[0], v[1], v[2])) { buf.writeFloatLE(x, o); o += 4; }
    o += 2;
  }
  writeFileSync(path, buf);
}

async function openViewer(page: Page, files: string[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".viewer3d")).toBeVisible();
  await expect(page.locator(".viewer3d-info")).toContainText("Width (X)");
}

test.beforeAll(() => boxStl(fx("box.stl")));

test("Edit opens 3D models in the viewer and shows their size", async ({ page }) => {
  await openViewer(page, [fx("box.stl")]);
  const rows = await page.evaluate(() => window.__viewer3d!.infoRows());
  expect(rows).toMatchObject({ "Width (X)": "20.0 mm", "Depth (Y)": "10.0 mm", "Height (Z)": "5.0 mm", Triangles: "12", Watertight: "Yes", Volume: "1.00 cm³" });
  // the model is drawn: the middle of the picture isn't the background
  const differs = await page.evaluate(() => {
    window.__viewer3d!.render();
    const c = document.querySelector(".viewer3d canvas") as HTMLCanvasElement;
    const g = document.createElement("canvas");
    g.width = c.width; g.height = c.height;
    const ctx = g.getContext("2d")!;
    ctx.drawImage(c, 0, 0);
    const mid = ctx.getImageData(c.width >> 1, c.height >> 1, 1, 1).data;
    const corner = ctx.getImageData(2, 2, 1, 1).data;
    return mid.some((v, i) => Math.abs(v - corner[i]) > 20);
  });
  expect(differs).toBe(true);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".viewer3d")).toBeHidden();
  await expect(page.locator(".pill[data-target]").first()).toBeVisible();
});

test("views, display styles, grid and switching models (STEP too)", async ({ page }) => {
  await openViewer(page, [fx("box.stl"), fx("cube-10mm.stp")]);
  await page.getByRole("button", { name: "Top", exact: true }).click();
  expect(await page.evaluate(() => window.__viewer3d!.camera.pitch)).toBe(90);
  await page.getByRole("button", { name: "Front", exact: true }).click();
  expect(await page.evaluate(() => [window.__viewer3d!.camera.yaw, window.__viewer3d!.camera.pitch])).toEqual([-90, 0]);
  await page.getByRole("button", { name: "Wireframe" }).click();
  expect(await page.evaluate(() => window.__viewer3d!.style)).toBe("wireframe");
  await page.getByLabel("Bed grid").uncheck();
  expect(await page.evaluate(() => window.__viewer3d!.showGrid)).toBe(false);
  // drag to rotate
  const box = (await page.locator(".viewer3d-canvas").boundingBox())!;
  const yaw = await page.evaluate(() => window.__viewer3d!.camera.yaw);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 100, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  expect(await page.evaluate(() => window.__viewer3d!.camera.yaw)).not.toBe(yaw);
  // the second file: a 10 mm STEP cube
  await page.getByLabel("Model").selectOption("cube-10mm.stp");
  await expect(page.locator(".viewer3d-title")).toHaveText("cube-10mm.stp");
  await expect(page.locator(".viewer3d-info")).toContainText("10.0 mm");
});

test("Edit is off for a mix of models and other files", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles([fx("box.stl"), fx("img0.png")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
});
