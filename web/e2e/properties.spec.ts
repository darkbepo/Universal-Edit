// Properties mode: Convert | Edit | Properties shows "About this file" for the added files.
import { existsSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

async function add(page: Page, names: string[]) {
  await page.locator('input[type="file"]').first().setInputFiles(names.map((n) => `${FIXTURES}${n}`));
  await expect(page.locator(".queue li")).toHaveCount(names.length);
}

/** {label: value} of one card, once the reading is done. */
async function card(page: Page, title: string): Promise<Record<string, string>> {
  const c = page.locator(`.prop-card[data-title="${title}"]`);
  await expect(c).toBeVisible({ timeout: 60_000 });
  const labels = await c.locator("dt").allTextContents();
  const values = await c.locator("dd").allTextContents();
  return Object.fromEntries(labels.map((l, i) => [l, values[i]]));
}

const propertiesButton = (page: Page) => page.getByRole("button", { name: "Properties", exact: true });

test("a photo: camera, settings and location; Convert brings the formats back", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}camera.jpg`), "python3 missing: no sample photo");
  await page.goto("./");
  await expect(propertiesButton(page)).toBeDisabled();
  await add(page, ["camera.jpg"]);
  await propertiesButton(page).click();
  await expect(propertiesButton(page)).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".properties-pane")).toBeVisible();
  await expect(page.locator(".convert-pane")).toBeHidden();
  await expect(page.getByRole("button", { name: "Convert", exact: true })).toBeHidden();
  const file = await card(page, "File");
  expect(file.Name).toBe("camera.jpg");
  expect(file.Type).toBe("JPG image");
  expect(await card(page, "Picture")).toMatchObject({ Dimensions: "64 × 48 px", Resolution: "300 dpi", Orientation: "Turned 90° right" });
  expect(await card(page, "Camera")).toEqual({
    Camera: "Canon EOS R6", Lens: "RF 50mm F1.8 STM", Taken: "2026-11-12 14:03", Exposure: "1/120 s",
    Aperture: "f/1.8", ISO: "200", "Focal length": "50 mm", Flash: "Didn't fire",
  });
  expect(await card(page, "Details")).toEqual({ Description: "Harbour at dusk", Author: "Ada Lovelace", Copyright: "CC BY 4.0" });
  expect(await card(page, "Location")).toEqual({ Coordinates: "55.67610, 12.56997", Altitude: "12 m" });
  await page.getByRole("button", { name: "Convert mode" }).click();
  await expect(page.locator(".properties-pane")).toBeHidden();
  await expect(page.locator(".convert-pane")).toBeVisible();
  await expect(page.getByRole("button", { name: "Convert", exact: true })).toBeVisible();
});

test("several files: the picker chooses which one; clearing goes back to Convert", async ({ page }) => {
  await page.goto("./");
  await add(page, ["three.pdf", "img0.png"]);
  await propertiesButton(page).click();
  expect(await card(page, "Document")).toMatchObject({ Pages: "3", "Page size": "A4 (210 × 297 mm)" });
  await expect(page.locator(".status")).toContainText("2 files");
  await page.locator(".prop-file").selectOption({ label: "img0.png" });
  await expect(page.locator('.prop-card[data-title="Document"]')).toBeHidden();
  expect(await card(page, "Picture")).toMatchObject({ Dimensions: "321 × 241 px", Colour: "Colour" });
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByRole("button", { name: "Convert mode" })).toHaveAttribute("aria-pressed", "true");
  await expect(propertiesButton(page)).toBeDisabled();
  await expect(page.locator(".editor-hub")).toBeVisible();
});

test("video and music: codecs, size and tags", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}clip.mp4`) || !existsSync(`${FIXTURES}song.mp3`), "ffmpeg missing: no samples");
  await page.goto("./");
  await add(page, ["clip.mp4", "song.mp3"]);
  await propertiesButton(page).click();
  expect(await card(page, "Video")).toMatchObject({ Dimensions: "320 × 240 px", "Frame rate": "30 fps", Codec: "H.264" });
  expect(await card(page, "Audio")).toMatchObject({ Codec: "AAC" });
  await page.locator(".prop-file").selectOption({ label: "song.mp3" });
  await expect(page.locator('.prop-card[data-title="Video"]')).toBeHidden();
  expect(await card(page, "Audio")).toMatchObject({ Codec: "MP3", Channels: "Stereo", "Sample rate": "44.1 kHz" });
  expect(await card(page, "Tags")).toEqual({ Title: "Song", Artist: "Band", Year: "2019" });
});

test("fonts, Word documents and 3D models", async ({ page }) => {
  await page.goto("./");
  await add(page, ["DejaVuSans.woff2", "report.docx", "box.stl"]);
  await propertiesButton(page).click();
  expect(await card(page, "Font")).toMatchObject({ Family: "DejaVu Sans", Style: "Book", Outlines: "TrueType" });
  await page.locator(".prop-file").selectOption({ label: "report.docx" });
  await expect(page.locator('.prop-card[data-title="Font"]')).toBeHidden();
  await expect(page.locator('.prop-card[data-title="File"]')).toContainText("DOCX document");
  await page.locator(".prop-file").selectOption({ label: "box.stl" });
  expect(await card(page, "3D model")).toMatchObject({ Watertight: "Yes" });
});
