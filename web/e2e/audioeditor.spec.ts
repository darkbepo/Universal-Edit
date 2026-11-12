// The audio editor: Convert | Edit for audio files, selecting with the mouse, editing, saving —
// real decoding (FFmpeg in the worker) and encoding in headless Chromium.
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;

interface AudioHook {
  doc: { rate: number; channels: number; frames: number; duration: number; dirty: boolean; selection: [number, number]; setSelection(a: number, b?: number): void } | null;
  fpp: number;
  playing: boolean;
  draw(): void;
}
declare global {
  interface Window {
    __audio?: AudioHook;
  }
}

async function openEditor(page: Page, files: string[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".audio-editor")).toBeVisible();
  await expect(page.locator(".audio-info")).toContainText("kHz", { timeout: 120_000 });
}

const info = (page: Page) => page.evaluate(() => {
  const d = window.__audio!.doc!;
  return { rate: d.rate, channels: d.channels, duration: d.duration, dirty: d.dirty, selection: d.selection };
});

test("Edit opens audio files (joined) in the editor and draws the waveform", async ({ page }) => {
  await openEditor(page, [fx("tone.wav"), fx("beep.mp3")]);
  const d = await info(page);
  expect(d.channels).toBe(2); // the mono MP3 is fitted to the first file
  expect(d.duration).toBeGreaterThan(3.9);
  expect(d.duration).toBeLessThan(4.1);
  await expect(page.locator(".audio-info")).toHaveText(/44\.1 kHz · Stereo · 0:04\.\d{3}/);
  await expect(page.getByText("tone.wav + 1 more")).toBeVisible();
  // the waveform is drawn: the middle of the first lane isn't the background
  const drawn = await page.evaluate(() => {
    window.__audio!.draw();
    const c = document.querySelector(".audio-canvas") as HTMLCanvasElement;
    const g = c.getContext("2d")!;
    const mid = g.getImageData(c.width >> 1, Math.round(c.height * 0.3), 1, 1).data;
    const corner = g.getImageData(c.width - 2, c.height - 2, 1, 1).data;
    return [Array.from(mid), Array.from(corner)];
  });
  expect(drawn[0]).not.toEqual(drawn[1]);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".audio-editor")).toBeHidden();
  await expect(page.locator(".pill[data-target]").first()).toBeVisible();
});

test("drag to select, cut, undo, effects and save as MP3", async ({ page }) => {
  await openEditor(page, [fx("tone.wav")]);
  const box = (await page.locator(".audio-canvas").boundingBox())!;
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 3, y);
  await page.mouse.down();
  await page.mouse.move(box.x + (box.width * 2) / 3, y, { steps: 5 });
  await page.mouse.up();
  let d = await info(page);
  expect((d.selection[1] - d.selection[0]) / d.rate).toBeGreaterThan(0.9);
  await expect(page.locator(".audio-selection")).toContainText("Length 0:01");
  await page.getByRole("button", { name: "Cut", exact: true }).click();
  d = await info(page);
  expect(d.duration).toBeLessThan(2.1);
  expect(d.dirty).toBe(true);
  await expect(page.locator(".audio-info")).toContainText("Unsaved changes");
  await page.keyboard.press("Control+z");
  expect((await info(page)).duration).toBeGreaterThan(2.9);
  // select the first second with the keyboard-free hook, fade it and trim to it
  await page.evaluate(() => window.__audio!.doc!.setSelection(0, 44100));
  for (const name of ["Fade in", "Trim"]) await page.getByRole("button", { name, exact: true }).click();
  expect((await info(page)).duration).toBeCloseTo(1, 2);
  await page.getByRole("button", { name: "Amplify…" }).click();
  await page.getByLabel("Change the volume by (dB)").fill("-6");
  await page.getByRole("button", { name: "OK", exact: true }).click();
  await page.getByRole("button", { name: "Zoom in" }).click();
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  // save
  await page.getByRole("button", { name: "Save", exact: true }).click(); // first Save asks
  await page.getByLabel("Format").selectOption("mp3");
  await expect(page.getByLabel("Quality (bitrate)")).toBeVisible();
  await page.getByLabel("Quality (bitrate)").selectOption("128");
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await page.locator("dialog.save").getByRole("button", { name: "Save", exact: true }).click();
  const file = await download;
  expect(file.suggestedFilename()).toBe("tone edited.mp3");
  const bytes = readFileSync(await file.path());
  expect(bytes.length).toBeGreaterThan(5000); // ~1 s at 128 kbps ≈ 16 KB
  expect([bytes[0], bytes[1], bytes[2]]).toEqual([0x49, 0x44, 0x33]); // "ID3"
  await expect(page.locator(".audio-editor .msg")).toHaveText("Saved “tone edited.mp3”.");
  expect((await info(page)).dirty).toBe(false);
});

test("the audio tile opens an empty editor; Edit is off for audio mixed with images", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=audio]").click();
  await expect(page.locator(".audio-editor")).toBeVisible();
  await expect(page.locator(".audio-placeholder")).toContainText("Open a sound");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await page.locator('input[type="file"]').first().setInputFiles([fx("tone.wav"), fx("img0.png")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
});
