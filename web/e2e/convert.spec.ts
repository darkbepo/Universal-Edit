import { existsSync, readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;
const hasVideo = () => existsSync(fx("clip.mp4"));

async function load(page: Page, files: string[]) {
  await page.goto("/");
  await page.locator('input[type="file"]').first().setInputFiles(files);
}

async function convert(page: Page, targetId: string, timeout = 150_000) {
  await page.locator(`.pill[data-target="${targetId}"]`).click();
  await page.getByRole("button", { name: "Convert" }).click();
  await expect(page.locator(".status")).toHaveClass(/success|error/, { timeout });
  await expect(page.locator(".status")).toHaveClass(/success/);
}

async function downloadSingle(page: Page, targetId: string): Promise<Buffer> {
  const download = page.waitForEvent("download", { timeout: 150_000 });
  await convert(page, targetId);
  return readFileSync(await (await download).path());
}

test("page is cross-origin isolated (multi-threaded engines available)", async ({ page }) => {
  await page.goto("/");
  // On hosts without COOP/COEP headers (GitHub Pages) coi-serviceworker reloads the page once.
  await expect
    .poll(() => page.evaluate(() => crossOriginIsolated).catch(() => false), { timeout: 15_000 })
    .toBe(true);
  await expect(page.getByText("Files never leave your device")).toBeVisible();
});

test("png → jpg", async ({ page }) => {
  await load(page, [fx("img0.png")]);
  const data = await downloadSingle(page, "jpg");
  expect([...data.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);
});

test("png → avif with resize", async ({ page }) => {
  await load(page, [fx("img0.png")]);
  await page.locator(`.pill[data-target="avif"]`).click();
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.locator("#opt-resize").selectOption({ label: "Scale by percentage" });
  const data = await downloadSingle(page, "avif");
  expect(data.subarray(4, 12).toString("latin1")).toContain("ftypavif");
});

test("images → PDF", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "images:pdf");
  expect(data.subarray(0, 4).toString()).toBe("%PDF");
  expect((data.toString("latin1").match(/\/Type\s*\/Page\b/g) ?? []).length).toBe(3);
});

test("images → animated GIF slideshow", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "slideshow:gif");
  expect(data.subarray(0, 6).toString()).toBe("GIF89a");
});

test("images → MP4 slideshow", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "slideshow:mp4");
  expect(data.subarray(4, 8).toString()).toBe("ftyp");
});

test("PDF split into three documents", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await convert(page, "split:pdf");
  await expect(page.locator(".results li")).toHaveCount(3);
  await expect(page.locator(".results a").first()).toHaveText("three_split/three_page_1.pdf");
});

test("PDF pages → PNG images", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await convert(page, "pages:png");
  await expect(page.locator(".results li")).toHaveCount(3);
});

test("PDF → A4 with page range", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await page.locator(`.pill[data-target="doc:pdf"]`).click();
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.locator("#opt-page_range").fill("2-3");
  const data = await downloadSingle(page, "doc:pdf");
  expect((data.toString("latin1").match(/\/Type\s*\/Page\b/g) ?? []).length).toBe(2);
});

test("Word files explain they need the desktop app", async ({ page }) => {
  await load(page, [fx("letter.docx")]);
  await expect(page.locator(".note")).toContainText("desktop app");
  await expect(page.locator(".pill")).toHaveCount(0);
});

test.describe("video", () => {
  test.skip(!hasVideo(), "needs ffmpeg to create the sample video");

  test("mp4 → webm", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "webm");
    expect([...data.subarray(0, 4)]).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
  });

  test("mp4 → mkv (H.264)", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "mkv");
    expect([...data.subarray(0, 4)]).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
  });

  test("mp4 → frames keeps 10 of every 30", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    await convert(page, "frames:jpg");
    await expect(page.locator(".results li")).toHaveCount(20); // 2 s × 30 fps × 10/30
  });

  test("mp4 → gif", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "video:gif");
    expect(data.subarray(0, 6).toString()).toBe("GIF89a");
  });

  test("mp4 → mp3", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "audio:mp3");
    expect(data.length).toBeGreaterThan(1000);
  });

  test("cancel stops a conversion", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    await page.locator(`.pill[data-target="mkv"]`).click();
    await page.getByRole("button", { name: "Convert" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator(".status")).toHaveText("Cancelled.");
    await expect(page.getByRole("button", { name: "Convert" })).toBeEnabled();
  });
});
