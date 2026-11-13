// The editor tiles on the empty start screen: open an editor without a file.
import { expect, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

declare global {
  interface Window {
    __editor?: { doc: { width: number; height: number; layers: { data: Uint8ClampedArray }[] } | null };
    __viewer3d?: { infoRows(): Record<string, string> };
  }
}

test("tiles: image, 3D, audio and document editors open, the video editor says Coming soon; hidden once files are added", async ({ page }) => {
  await page.goto("./?e2e");
  const tiles = page.locator(".editor-tile");
  await expect(tiles).toHaveCount(5);
  for (const id of ["image", "model", "audio", "document"]) {
    await expect(page.locator(`.editor-tile[data-editor=${id}]`)).toBeEnabled();
  }
  await expect(page.locator(".editor-tile[data-editor=document]")).not.toContainText("Coming soon");
  const video = page.locator(".editor-tile[data-editor=video]");
  await expect(video).toBeDisabled();
  await expect(video).toContainText("Coming soon");
  await page.locator('input[type="file"]').first().setInputFiles([`${FIXTURES}img0.png`]);
  await expect(page.locator(".editor-hub")).toBeHidden();
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.locator(".editor-hub")).toBeVisible();
});

test("image editor tile: New picture window makes an empty picture of that size and colour", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=image]").click();
  const dlg = page.locator("dialog.new-picture");
  await expect(dlg).toBeVisible();
  await expect(page.locator("#np-width")).toHaveValue("1920");
  await page.locator("#np-size").selectOption({ label: "Square (1080 × 1080)" });
  await expect(page.locator("#np-height")).toHaveValue("1080");
  await page.locator("#np-width").fill("300");
  await expect(page.locator("#np-size")).toHaveValue("custom");
  await page.locator("#np-height").fill("200");
  await dlg.getByText("Transparent").click();
  await dlg.getByRole("button", { name: "Create" }).click();
  await expect(page.locator(".editor:not(.viewer3d)")).toBeVisible();
  const doc = await page.evaluate(() => {
    const d = window.__editor!.doc!;
    return { w: d.width, h: d.height, n: d.layers.length, alpha: d.layers[0].data[3] };
  });
  expect(doc).toEqual({ w: 300, h: 200, n: 1, alpha: 0 });
  // Cancel leaves the start screen as it was
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await page.locator(".editor-tile[data-editor=image]").click();
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect(dlg).toHaveCount(0);
  await expect(page.locator(".editor-hub")).toBeVisible();
});

test("3D viewer tile: opens empty, Open… shows a model", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=model]").dblclick(); // a second click while it loads
  const viewer = page.locator(".viewer3d");
  await expect(viewer).toBeVisible();
  await expect(viewer).toHaveCount(1);
  await expect(page.locator(".viewer3d-placeholder")).toContainText("Open a 3D model");
  await viewer.locator('input[type="file"]').setInputFiles([`${FIXTURES}cube-10mm.stp`]);
  await expect(page.locator(".viewer3d-info")).toContainText("Width (X)");
  const rows = await page.evaluate(() => window.__viewer3d!.infoRows());
  expect(rows["Width (X)"]).toBe("10.0 mm");
});

test("a short, narrow window scrolls to the tiles instead of cutting them off", async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 620 });
  await page.goto("./?e2e");
  const last = page.locator(".editor-tile[data-editor=document]");
  await last.scrollIntoViewIfNeeded();
  await expect(last).toBeInViewport({ ratio: 1 });
  const tile = (await last.boundingBox())!;
  const bar = (await page.locator(".bar").boundingBox())!;
  expect(tile.y + tile.height).toBeLessThanOrEqual(bar.y); // not hidden under the bottom bar
  await expect(page.locator(".drop")).toBeVisible();
});

test("pressing Convert while a tile's editor is still loading doesn't let a second tile open too", async ({ page }) => {
  // the live site's service worker fetches the chunks, and page.route never sees those requests
  test.skip(!!process.env.E2E_BASE_URL, "page.route can't hold requests made by the live site's service worker");
  await page.goto("./?e2e");
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  await page.route(/\.js(\?|$)/, async (route) => {
    await gate; // hold every lazily loaded chunk: the 3D viewer is still loading
    await route.continue();
  });
  await page.locator(".editor-tile[data-editor=model]").click();
  await page.getByRole("button", { name: "Convert mode" }).click();
  await page.locator(".editor-tile[data-editor=audio]").click();
  release();
  await expect(page.locator(".viewer3d")).toBeVisible();
  await page.waitForTimeout(500); // the audio editor would load by now
  await expect(page.locator(".audio-canvas")).toHaveCount(0); // never even mounted
});
