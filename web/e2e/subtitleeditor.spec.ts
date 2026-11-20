// The website subtitle editor: opening, editing, undo, find, shift, saving, the video preview, phones.
import { existsSync, readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

declare global {
  interface Window {
    __subeditor?: {
      cues(): { start: number; end: number; text: string }[];
      format: string;
      dirty: boolean;
      row: number;
      caption: string;
      select(i: number): void;
      showAt(ms: number): void;
    };
  }
}

const SRT = "1\r\n00:00:01,000 --> 00:00:02,000\r\nHello there\r\n\r\n2\r\n00:00:03,000 --> 00:00:04,000\r\nSecond line\r\n";
const file = (name: string, data: string | Buffer) => ({ name, mimeType: "text/plain", buffer: Buffer.isBuffer(data) ? data : Buffer.from(data) });

async function openInEditor(page: Page, files: ReturnType<typeof file>[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".subtitle-editor")).toBeVisible();
}

/** Click the editor's Save (or the Save as window's Save) and read what was downloaded. */
async function saveBytes(page: Page, scope = ".subtitle-editor .editor-top"): Promise<[string, Buffer]> {
  const download = page.waitForEvent("download");
  await page.locator(scope).getByRole("button", { name: "Save", exact: true }).click();
  const d = await download;
  return [d.suggestedFilename(), readFileSync(await d.path())];
}

const cues = (page: Page) => page.evaluate(() => window.__subeditor!.cues());

test("a subtitle file opens in the table and an untouched save keeps its bytes", async ({ page }) => {
  await openInEditor(page, [file("film.srt", SRT)]);
  await expect(page.locator(".sub-rows .sub-row")).toHaveCount(2);
  await expect(page.locator('.sub-row[data-index="1"]')).toContainText("0:00:03.000");
  await expect(page.locator(".sub-text")).toHaveValue("Hello there");
  const [name, data] = await saveBytes(page);
  expect(name).toBe("film.srt");
  expect(data.toString()).toBe(SRT);
});

test("typing, times, add / split / join, undo, and saving with CRLF", async ({ page }) => {
  await openInEditor(page, [file("film.srt", SRT)]);
  const box = page.locator(".sub-text");
  await box.click();
  await box.press("End");
  await box.pressSequentially("!!");
  expect((await cues(page))[0].text).toBe("Hello there!!");
  await expect(page.locator(".sub-title")).toContainText("●");
  await page.keyboard.press("Control+z");
  expect((await cues(page))[0].text).toBe("Hello there"); // the typing was one step
  await page.keyboard.press("Control+y");
  await page.locator('.sub-row[data-index="1"]').click();
  await page.getByLabel("Start", { exact: true }).fill("2.5");
  await page.getByLabel("Start", { exact: true }).press("Enter");
  expect((await cues(page))[1].start).toBe(2500);
  await page.getByRole("button", { name: "+ Add" }).click();
  expect((await cues(page)).length).toBe(3);
  await page.keyboard.press("Control+z");
  await page.locator('.sub-row[data-index="0"]').click();
  await page.getByRole("button", { name: "Split", exact: true }).click();
  expect((await cues(page)).map((c) => c.text)).toEqual(["Hello", "there!!", "Second line"]);
  await page.getByRole("button", { name: "Join", exact: true }).click();
  expect((await cues(page))[0].text).toBe("Hello\nthere!!");
  const [, data] = await saveBytes(page);
  expect(data.toString()).toBe("1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\nthere!!\r\n\r\n2\r\n00:00:02,500 --> 00:00:04,000\r\nSecond line\r\n");
  expect(await page.evaluate(() => window.__subeditor!.dirty)).toBe(false);
});

test("in the text box Ctrl+Delete deletes a word, Alt+Delete in the list deletes the subtitle", async ({ page }) => {
  await openInEditor(page, [file("film.srt", SRT)]);
  const box = page.locator(".sub-text");
  await box.click();
  await box.press("Control+Home");
  await box.press("Control+Delete");
  expect((await cues(page)).length).toBe(2);
  await page.locator('.sub-row[data-index="1"]').click();
  await page.keyboard.press("Alt+Delete");
  expect((await cues(page)).map((c) => c.text)).toEqual([expect.not.stringContaining("Hello") as unknown as string]);
});

test("find & replace, shift times and problem marks", async ({ page }) => {
  const text = SRT + "\r\n3\r\n00:00:03,500 --> 00:00:03,700\r\nOverlap and flash\r\n";
  await openInEditor(page, [file("film.srt", text)]);
  await expect(page.locator(".sub-row.problem")).toHaveCount(1);
  await expect(page.locator(".sub-counts")).toHaveText("3 subtitles");
  await page.getByRole("button", { name: "1 with problems" }).click();
  await expect(page.locator(".sub-note")).toContainText("Starts before line 2 ends");
  await page.keyboard.press("Control+h");
  await page.getByLabel("Find", { exact: true }).fill("line");
  await expect(page.locator(".sub-find-count")).toHaveText("1 found");
  await page.getByLabel("Replace with").fill("row");
  await page.getByRole("button", { name: "Replace all" }).click();
  expect((await cues(page))[1].text).toBe("Second row");
  await page.getByRole("button", { name: "Shift times…" }).click();
  await page.getByLabel("Seconds").fill("-0.5");
  await page.locator(".sub-shift-dialog").getByRole("button", { name: "Shift" }).click();
  expect((await cues(page)).map((c) => c.start)).toEqual([500, 2500, 3000]);
});

test("Save as another format names what it leaves out, then edits the new file", async ({ page }) => {
  const ass = "[Script Info]\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" +
    "Dialogue: 0,0:00:01.00,0:00:02.00,Top,,0,0,0,,{\\i1}Hi{\\i0}\\Nthere\n";
  await openInEditor(page, [file("styled.ass", ass)]);
  let asked = "";
  page.on("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await page.locator(".subtitle-editor").getByRole("button", { name: "Save as…" }).click();
  await page.getByLabel("Format").selectOption("srt");
  const [name, data] = await saveBytes(page, ".sub-save-dialog");
  expect(asked).toContain("styles, positions and effects");
  expect(name).toBe("styled.srt");
  expect(data.toString()).toBe("1\n00:00:01,000 --> 00:00:02,000\n<i>Hi</i>\nthere\n");
  expect(await page.evaluate(() => window.__subeditor!.format)).toBe("srt");
});

test("a subtitle with its video: the subtitle shows over the picture", async ({ page }) => {
  test.skip(!existsSync(`${FIXTURES}clip.mp4`), "clip.mp4 needs ffmpeg in global-setup");
  const clip = { name: "clip.mp4", mimeType: "video/mp4", buffer: readFileSync(`${FIXTURES}clip.mp4`) };
  await openInEditor(page, [clip, file("clip.srt", SRT)]);
  await expect(page.locator(".sub-video-pane")).toBeVisible();
  await page.evaluate(() => window.__subeditor!.showAt(1500));
  expect(await page.evaluate(() => window.__subeditor!.caption)).toBe("Hello there");
  await expect(page.locator('.sub-row[data-index="0"]')).toHaveClass(/playing/);
  await page.evaluate(() => window.__subeditor!.showAt(2500));
  expect(await page.evaluate(() => window.__subeditor!.caption)).toBe("");
  await page.getByRole("button", { name: "Close video" }).click();
  await expect(page.locator(".sub-video-pane")).toBeHidden();
});

test("the hub tile opens an empty editor; Back asks about unsaved changes", async ({ page }) => {
  await page.goto("./?e2e");
  await page.locator(".editor-tile[data-editor=subtitle]").click();
  await expect(page.locator(".subtitle-editor")).toBeVisible();
  await page.getByRole("button", { name: "+ Add" }).click();
  await page.locator(".sub-text").fill("first");
  expect(await cues(page)).toEqual([{ start: 0, end: 2000, text: "first" }]);
  let asked = 0;
  page.once("dialog", (d) => {
    asked++;
    void d.dismiss();
  });
  await page.locator(".subtitle-editor").getByRole("button", { name: /Back/ }).click();
  expect(asked).toBe(1);
  await expect(page.locator(".subtitle-editor")).toBeVisible();
});

test("phones: the table, edit box and top bar fit 375 px", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 740 });
  await openInEditor(page, [file("film.srt", SRT)]);
  const overflow = await page.evaluate(() => document.querySelector(".subtitle-editor")!.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  for (const target of [page.getByLabel("Start", { exact: true }), page.locator(".sub-text")]) {
    await target.scrollIntoViewIfNeeded(); // the editor's middle scrolls as a whole on phones
    await expect(target).toBeInViewport();
  }
});
