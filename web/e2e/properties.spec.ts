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
  expect(await card(page, "Details")).toEqual({ Title: "Harbour at dusk", Author: "Ada Lovelace", Copyright: "CC BY 4.0" });
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

test("a turned video says how far it's turned, clockwise like the desktop app", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}turned.mp4`), "ffmpeg missing: no sample");
  await page.goto("./");
  await add(page, ["turned.mp4"]);
  await propertiesButton(page).click();
  expect(await card(page, "Video")).toMatchObject({ Rotation: "270°", Dimensions: "320 × 240 px" });
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

test.describe("change photo details", () => {
  test.skip(!existsSync(`${FIXTURES}camera.jpg`), "python3 missing: no sample photo");

  test("change, download, and the queue keeps the changed picture", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg"]);
    await propertiesButton(page).click();
    const form = page.locator(".ph-form");
    await expect(form).toBeVisible();
    await expect(page.locator("#ph-title")).toHaveValue("Harbour at dusk");
    await expect(page.locator("#ph-taken")).toHaveValue("2026-11-12 14:03");
    await expect(page.locator("#ph-rotation")).toHaveValue("6");
    const save = form.getByRole("button", { name: "Download with changes" });
    await expect(save).toBeDisabled();
    await page.locator("#ph-title").fill("Pier");
    await page.locator("#ph-author").fill("Grace Hopper");
    await page.locator("#ph-rotation").selectOption("1");
    await page.locator("#ph-remove-location").check();
    const download = page.waitForEvent("download");
    await save.click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("camera.jpg");
    const saved = await file.path();
    const { readDetails } = await import("../src/properties/photo");
    const { readFileSync } = await import("node:fs");
    const d = readDetails(new Uint8Array(readFileSync(saved)));
    expect([d.title, d.author, d.orientation, d.has_location]).toEqual(["Pier", "Grace Hopper", 1, false]);
    await expect(form).toContainText("Downloaded “camera.jpg”");
    // "About this file" now shows the changed picture
    await expect(page.locator('.prop-card[data-title="Details"]')).toContainText("Pier");
    await expect(page.locator('.prop-card[data-title="Location"]')).toHaveCount(0);
    // the desktop app reads the same details from it
    const { execFileSync } = await import("node:child_process");
    const out = execFileSync("python3", ["-c",
      "import sys, json; sys.path.insert(0, sys.argv[1]); from dataclasses import asdict; " +
      "from umc.properties.photo import read_details; print(json.dumps(asdict(read_details(open(sys.argv[2], 'rb').read()))))",
      new URL("../../src", import.meta.url).pathname, saved]).toString();
    expect(JSON.parse(out)).toEqual(d);
  });

  test("several pictures: apply to all, several values stay, a ZIP download", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg", "img0.png"]);
    await propertiesButton(page).click();
    await expect(page.locator(".ph-form")).toBeVisible();
    await page.locator("#ph-apply-all").check();
    await expect(page.locator("#ph-title")).toHaveValue("");
    await expect(page.locator("#ph-title")).toHaveAttribute("placeholder", "Several values");
    await page.locator("#ph-copyright").fill("CC0");
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download changed pictures (.zip)" }).click();
    const file = await download;
    expect(file.suggestedFilename()).toBe("pictures (changed details).zip");
    const { unzipSync } = await import("fflate");
    const { readFileSync } = await import("node:fs");
    const { readDetails } = await import("../src/properties/photo");
    const zip = unzipSync(new Uint8Array(readFileSync(await file.path())));
    expect(Object.keys(zip).sort()).toEqual(["camera.jpg", "img0.png"]);
    expect(readDetails(zip["camera.jpg"])).toMatchObject({ copyright: "CC0", title: "Harbour at dusk" });
    expect(readDetails(zip["img0.png"])).toMatchObject({ copyright: "CC0", title: "" });
  });

  test("a bad date is refused in plain words", async ({ page }) => {
    await page.goto("./");
    await add(page, ["camera.jpg"]);
    await propertiesButton(page).click();
    await page.locator("#ph-taken").fill("12/11/2026");
    await expect(page.locator("#ph-taken")).toHaveClass(/invalid/);
    await page.getByRole("button", { name: "Download with changes" }).click();
    await expect(page.locator(".ph-note")).toContainText("year-month-day");
  });

  test("no form for pictures it can't change", async ({ page }) => {
    await page.goto("./");
    await add(page, ["design.psd"]);
    await propertiesButton(page).click();
    await expect(page.locator('.prop-card[data-title="File"]')).toBeVisible();
    await expect(page.locator(".ph-form")).toBeHidden();
  });
});
