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

test("an odd-sized copy keeps the size it decodes at", async ({ page }) => {
  test.skip(!existsSync(fx("odd.avi")), "needs ffmpeg");
  await page.goto("./?e2e");
  await openEditor(page, [fx("odd.avi")]);
  const src = await ve<Src & { width: number; height: number }>(page, "project().sources[0]");
  expect([src.width, src.height]).toEqual([320, 240]); // the copy's real size (yuv420p needs even sizes)
  expect(src.path).toBe("odd.avi");
});

test("leaving stops an opening batch and the next open doesn't wait for it", async ({ page }) => {
  test.skip(!existsSync(fx("clip.avi")), "needs ffmpeg");
  await page.goto("./?e2e");
  await addFiles(page, [fx("clip.avi"), fx("clip.mp4"), fx("img0.png")]);
  await edit(page).click();
  await expect(statusMsg(page)).toContainText(/Loading video engine|Converting/); // the copy runs
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  await page.getByRole("button", { name: "Clear" }).click();
  const t0 = Date.now();
  await openEditor(page, [fx("red.mp4")]);
  await expect(rows(page)).toHaveCount(1);
  expect(Date.now() - t0).toBeLessThan(15_000);
  await page.waitForTimeout(3000); // the old batch would have finished by now
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("red.mp4");
  expect(await ve(page, "project().sources.map((s) => s.path)")).toEqual(["red.mp4"]);
  expect(await ve(page, `decodes("s1")`)).toBe(true);
  expect(await ve(page, `decodes("s2")`)).toBe(false);
  await expect(statusMsg(page)).toHaveText("");
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

// ---------------------------------------------------------------- timeline, inspector, editing
interface Span { start: number; end: number; row_kind: string; row: number }
interface TlRow { kind: string; row: number; y: number; h: number }
const timeline = (page: Page) => page.locator("canvas.ve-timeline");
const spans = (page: Page) => ve<Record<string, Span>>(page, "spans()");
const mainIds = (page: Page) => ve<string[]>(page, "project().main.map((m) => m.id)");
const inspectorTitle = (page: Page) => page.locator(".ve-inspector-title");
/** Page coordinates of time `t` on the row `kind` / `row` (`dy` px below the row's top, else its middle). */
const at = async (page: Page, t: number, kind = "main", row = 0, dy?: number) => {
  const box = (await timeline(page).boundingBox())!;
  const v = await ve<{ pps: number; scroll: number; m: { header: number } }>(page, "view()");
  const r = (await ve<TlRow[]>(page, "rows()")).find((x) => x.kind === kind && x.row === row)!;
  return { x: box.x + Math.round((t - v.scroll) * v.pps + v.m.header), y: box.y + r.y + (dy ?? r.h / 2) };
};
const drag = async (page: Page, from: { x: number; y: number }, dx: number) => {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let i = 1; i <= 5; i++) await page.mouse.move(from.x + (dx * i) / 5, from.y);
  await page.mouse.up();
};
const steps = (page: Page) => ve<number>(page, "history.steps");

test("clips land on the right rows", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, MIXED());
  await expect(timeline(page)).toBeVisible();
  const s = await spans(page);
  const main = await mainIds(page);
  expect(main.map((id) => s[id].row_kind)).toEqual(["main", "main"]);
  const music = await ve<string>(page, "project().music_rows[0][0].id");
  expect(s[music].row_kind).toBe("music");
  // a source dropped on the timeline: music on the music row it's dropped on, pictures on the main row
  const drop = async (id: string, kind: string) => {
    const p = await at(page, 6, kind);
    await timeline(page).evaluate((el, a) => {
      const dt = new DataTransfer();
      dt.setData("application/x-umc-source", a.id);
      for (const type of ["dragover", "drop"]) el.dispatchEvent(new DragEvent(type, { dataTransfer: dt, clientX: a.x, clientY: a.y, bubbles: true, cancelable: true }));
    }, { id, x: p.x, y: p.y });
  };
  await drop("s3", "music");
  await expect.poll(() => ve(page, "project().music_rows[0].length")).toBe(2);
  expect(await ve(page, "history.undoLabel")).toBe("Add music");
  await drop("s2", "music");
  await expect.poll(() => ve(page, "project().main.length")).toBe(3);
  expect(await ve(page, "history.undoLabel")).toBe("Add clip");
  // "+" in the header adds a text row
  const box = (await timeline(page).boundingBox())!;
  const r = (await ve<TlRow[]>(page, "rows()")).find((x) => x.kind === "text")!;
  await page.mouse.click(box.x + 80 - 13, box.y + r.y + 13);
  await expect.poll(() => ve(page, "project().text_rows.length")).toBe(2);
  expect(await ve(page, "history.undoLabel")).toBe("Add row");
});

test("split, trim, undo, redo", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  await ve(page, "seek(1)");
  await page.keyboard.press("s");
  await expect.poll(() => ve(page, "project().main.length")).toBe(3);
  expect(await ve(page, "history.undoLabel")).toBe("Split clip");
  await expect(page.getByRole("button", { name: "Undo", exact: true })).toHaveAttribute("title", "Undo Split clip (Ctrl+Z)");
  const pic = (await mainIds(page))[2];
  const before = (await spans(page))[pic];
  await drag(page, await at(page, before.end), 40);
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Trim clip");
  const after = (await spans(page))[pic];
  expect(after.end - after.start).toBeCloseTo(before.end - before.start + 0.5, 5);
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect.poll(() => ve(page, "project().main.length")).toBe(2);
  await page.keyboard.press("Control+y");
  await expect.poll(() => ve(page, "project().main.length")).toBe(3);
  expect(await ve(page, "history.undoLabel")).toBe("Split clip");
});

test("reorder by dragging", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [a, b] = await mainIds(page);
  const s = await spans(page);
  const from = await at(page, (s[a].start + s[a].end) / 2);
  const past = await at(page, (s[b].start + s[b].end) / 2 + 0.5);
  await drag(page, from, past.x - from.x);
  await expect.poll(() => mainIds(page)).toEqual([b, a]);
  expect(await ve(page, "history.undoLabel")).toBe("Move clip");
});

test("inspector is one undo step", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  await expect(inspectorTitle(page)).toHaveText("PROJECT");
  const pic = (await mainIds(page))[1];
  const s = (await spans(page))[pic];
  const p = await at(page, (s.start + s.end) / 2);
  await page.mouse.click(p.x, p.y);
  await expect(inspectorTitle(page)).toHaveText("PICTURE");
  expect(await ve(page, "selected")).toBe(pic);
  const n = await steps(page);
  const slider = page.locator("#ve-brightness");
  await slider.scrollIntoViewIfNeeded();
  const box = (await slider.boundingBox())!;
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  for (const f of [0.6, 0.7, 0.8, 0.9]) await page.mouse.move(box.x + box.width * f, y);
  // previewed live, not pushed yet
  expect(await ve<number>(page, "shown().main[1].brightness")).toBeGreaterThan(0);
  expect(await steps(page)).toBe(n);
  await page.mouse.up();
  await expect.poll(() => steps(page)).toBe(n + 1);
  expect(await ve(page, "history.undoLabel")).toBe("Change brightness");
  expect(await ve<number>(page, "project().main[1].brightness")).toBeGreaterThan(0);
});

test("transition tab", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [a, b] = await mainIds(page);
  const s = await spans(page);
  const tab = await at(page, s[b].start, "main", 0, 10);
  await page.mouse.click(tab.x, tab.y);
  await expect(inspectorTitle(page)).toHaveText("TRANSITION");
  expect(await ve(page, "selected")).toBe(`tr:${b}`);
  await page.locator("#ve-kind").selectOption({ label: "Crossfade" });
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Change transition");
  const s2 = await spans(page);
  expect(s2[a].end - s2[b].start).toBeCloseTo(0.5, 5);
  await timeline(page).focus();
  await page.keyboard.press("Delete");
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Remove transition");
  const s3 = await spans(page);
  expect(s3[b].start).toBeCloseTo(s3[a].end, 5);
});

test("text", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "seek(0.5)");
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  const item = await ve<{ id: string; start: number }>(page, "project().text_rows[0][0]");
  expect(item.start).toBeCloseTo(0.5, 5);
  expect(await ve(page, "selected")).toBe(item.id);
  await expect(inspectorTitle(page)).toHaveText("TEXT");
  const area = page.locator("textarea#ve-text");
  await expect(area).toBeVisible();
  await area.fill("Hello there");
  await expect.poll(() => ve(page, `label("${item.id}")`)).toBe("Hello there");
  await area.press("Enter"); // Enter makes a new line, it doesn't commit
  await area.pressSequentially("again");
  await expect.poll(() => ve(page, `label("${item.id}")`)).toBe("Hello there again");
  expect(await ve(page, "history.undoLabel")).toBe("Add text");
  await timeline(page).focus(); // leaving the box finishes the edit
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Change text");
  expect(await ve(page, "project().text_rows[0][0].text")).toBe("Hello there\nagain");
});

test("refused edits are shown", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "loadBig(500)");
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect(statusMsg(page)).toHaveText("This project is too big to edit (max 4 hours and 500 items).");
  await expect(statusMsg(page)).toHaveClass(/error/);
  expect(await ve(page, "history.canUndo")).toBe(false);
});
