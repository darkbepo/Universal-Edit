// The website's video editor (behind ?e2e / ?video-editor): the gate, the Edit switch, the tile,
// opening sources (straight, or through the FFmpeg copy) and the media list.
import { execFileSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
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

// ---------------------------------------------------------------- sources and the media list
type Page = import("@playwright/test").Page;
/** An expression on the page's `window.__videoeditor` hook, e.g. `project().main.length`. */
const ve = <T>(page: Page, expr: string) => page.evaluate(`window.__videoeditor.${expr}`) as Promise<T>;
/** Engine downloads seen by the page and its workers (FFmpeg.wasm, ImageMagick). */
const engineRequests = (page: Page) => {
  const seen: string[] = [];
  page.on("request", (r) => { if (/ffmpeg-core|@ffmpeg|magick\.wasm/.test(r.url())) seen.push(r.url()); });
  return seen;
};
const openEditor = async (page: Page, files: string[]) => {
  await addFiles(page, files);
  await edit(page).click();
  await expect(page.locator(".video-editor")).toBeVisible();
  await expect.poll(() => ve(page, "project() !== null")).toBe(true);
};
const rows = (page: Page) => page.locator(".ve-media-row");
const statusMsg = (page: Page) => page.locator(".video-editor .editor-status .msg");
interface Src { id: string; path: string; kind: string; duration: number; size: number }

test("media list shows each source", async ({ page }) => {
  const engines = engineRequests(page);
  await page.goto("./?e2e");
  await openEditor(page, MIXED());
  await expect(rows(page)).toHaveCount(3);
  await expect(rows(page).nth(0)).toContainText("clip.mp4");
  await expect(rows(page).nth(0)).toContainText("0:02.00");
  await expect(rows(page).nth(1)).toContainText("Picture");
  await expect(rows(page).nth(2)).toContainText(/Music · 0:0\d\.\d\d/);
  expect(await ve(page, "project().main.length")).toBe(2);
  expect(await ve(page, "project().music_rows.flat().length")).toBe(1);
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s1", "s2", "s3"]);
  for (const id of ["s1", "s2", "s3"]) expect(await ve(page, `decodes("${id}")`)).toBe(true);
  const loaded = await page.evaluate(() => performance.getEntriesByType("resource").map((e) => e.name).filter((n) => /ffmpeg-core|@ffmpeg|\.wasm/.test(n)));
  expect(loaded).toEqual([]);
  expect(engines).toEqual([]);
  await expect(statusMsg(page)).toHaveText("");
});

test("an AVI goes through the copy", async ({ page }) => {
  test.skip(!existsSync(fx("clip.avi")), "needs ffmpeg");
  // 1 s from global-setup, but convert.spec rewrites it (2 s): compare with what's on disk
  const seconds = Number(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", fx("clip.avi")]).toString());
  const engines = engineRequests(page);
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  // picked / dropped while the editor is open: into the project, not the hidden converter queue
  await addFiles(page, [fx("clip.avi")]);
  await expect(rows(page)).toHaveCount(2, { timeout: 120_000 });
  await expect(rows(page).nth(1)).toContainText("clip.avi");
  await expect(rows(page).nth(1).locator(".ve-media-note")).toHaveText("Converted for this browser — the desktop app reads it directly.");
  const src = await ve<Src>(page, "project().sources[1]");
  expect(src.id).toBe("s2");
  expect(src.path).toBe("clip.avi"); // the original name, not the copy's
  expect(src.kind).toBe("video");
  expect(Math.abs(src.duration - seconds)).toBeLessThan(0.15);
  expect(src.size).toBe(statSync(fx("clip.avi")).size); // the original size too
  expect(await ve(page, `decodes("s2")`)).toBe(true);
  expect(await ve(page, "history.undoLabel")).toBe("Add files");
  expect(engines.some((u) => /ffmpeg-core/.test(u))).toBe(true);
  await expect(page.locator(".queue li")).toHaveCount(1); // the converter's queue still has clip.mp4 only
});

test("limits are shown", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "setLimits({ copyBytes: 1000 })");
  await page.locator(".ve-media input[type=file]").setInputFiles([fx("clip.avi")]);
  await expect(statusMsg(page)).toContainText(/clip\.avi is too big for the browser .* use the desktop app/);
  await expect(rows(page)).toHaveCount(1);
  expect(await ve(page, "history.canUndo")).toBe(false);
  await ve(page, "setLimits(null)");
});

test("add at the playhead", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const before = await ve<number>(page, "project().main.length");
  await rows(page).nth(1).dblclick();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add clip");
  expect(await ve(page, "project().main.length")).toBe(before + 1);
  // the row's own button does the same (touch screens can't drag)
  await rows(page).nth(1).getByRole("button", { name: /^Add/ }).click();
  expect(await ve(page, "project().main.length")).toBe(before + 2);
});
