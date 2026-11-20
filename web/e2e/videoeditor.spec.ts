// The website's video editor (behind ?e2e / ?video-editor): the gate, the Edit switch, the tile.


import { expect, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;
const MIXED = () => [fx("clip.mp4"), fx("img0.png"), fx("tone.wav")];
const addFiles = (page: import("@playwright/test").Page, files: string[]) =>
  page.locator('input[type="file"]').first().setInputFiles(files);
const edit = (page: import("@playwright/test").Page) => page.getByRole("button", { name: "Edit", exact: true });

test("Edit opens a video + picture + music set", async ({ page }) => {
  await page.goto("./?e2e");
  await addFiles(page, MIXED());
  await expect(edit(page)).toBeEnabled();
  await expect(edit(page)).toHaveAttribute("title", "Open the files in the video editor");
  await edit(page).click();
  await expect(page.locator(".video-editor")).toBeVisible();
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  await expect(page.locator(".pill[data-target]").first()).toBeVisible();
});

test("without the gate nothing changes", async ({ page }) => {
  await page.goto("./");
  await addFiles(page, MIXED());
  await expect(page.locator(".queue li")).toHaveCount(3);
  await expect(edit(page)).toBeDisabled();
  await page.getByRole("button", { name: "Clear" }).click();
  await page.locator(`input[type="file"]`).first().setInputFiles({ name: "p.umcvideo", mimeType: "application/json", buffer: Buffer.from("{}") });
  await expect(page.locator(".queue li")).toHaveCount(1);
  await expect(edit(page)).toBeDisabled();
});

test("the tile opens an empty project with ?video-editor only", async ({ page }) => {
  await page.goto("./?e2e&video-editor");
  const tile = page.locator(".editor-tile[data-editor=video]");
  await expect(tile).toBeEnabled();
  await tile.click();
  await expect(page.locator(".video-editor")).toBeVisible();
  await expect(page.locator(".video-timeline")).toBeVisible();
  await page.goto("./?e2e");
  await expect(page.locator(".editor-tile[data-editor=video]")).toBeDisabled();
  await expect(page.locator(".editor-tile[data-editor=video]")).toContainText("Coming soon");
});

test("375 px: no sideways scroll, short Back, Save project on the second row", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("./?e2e&video-editor");
  await page.locator(".editor-tile[data-editor=video]").click();
  await expect(page.locator(".video-editor")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: /← Back/ })).toBeVisible();
  const back = await page.getByRole("button", { name: /← Back/ }).boundingBox();
  const save = await page.getByRole("button", { name: "Save project", exact: true }).boundingBox();
  expect(save!.y).toBeGreaterThan(back!.y + back!.height - 1);
});
