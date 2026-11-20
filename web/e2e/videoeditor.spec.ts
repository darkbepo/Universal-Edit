// The website's video editor (behind ?e2e / ?video-editor): the gate, the Edit switch, the tile,
// opening sources (straight, or through the FFmpeg copy), the media list, the timeline and the still preview.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
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

test("leaving mid-drag clears the timeline and lands no edit", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [a, b] = await mainIds(page);
  const s = await spans(page);
  const from = await at(page, (s[a].start + s[a].end) / 2);
  const past = await at(page, (s[b].start + s[b].end) / 2 + 0.5);
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 20, from.y);
  await page.mouse.move(past.x, past.y); // a reorder is under way
  await page.evaluate(() => (document.querySelector(".video-editor .editor-top .link") as HTMLButtonElement).click()); // leave
  await expect(page.locator(".video-editor")).toBeHidden();
  expect(await ve(page, "painted()")).toEqual([]);
  expect(await ve(page, "rows()")).toEqual([]);
  await page.mouse.move(past.x + 10, past.y);
  await page.mouse.up(); // the end of the old drag
  expect(await ve(page, "project()")).toBeNull();
  await page.getByRole("button", { name: "Clear" }).click();
  await openEditor(page, [fx("red.mp4")]);
  const ids = await mainIds(page);
  expect(ids).toHaveLength(1);
  expect(await ve(page, "painted()")).toEqual(ids); // only the new project's clip
  expect(await ve(page, "history.canUndo")).toBe(false);
  expect(await ve(page, "selected")).toBeNull();
  // a click where the old picture was selects nothing stale and edits nothing
  const old = await at(page, (s[b].start + s[b].end) / 2);
  await page.mouse.click(old.x, old.y);
  expect(await ve(page, "history.canUndo")).toBe(false);
  expect(await ve(page, "selected")).toBeNull();
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

// ---------------------------------------------------------------- the still preview
type RGBA = [number, number, number, number];
const RED: [number, number, number] = [208, 32, 32];
/** red.mp4 is untagged BT.601 (FFmpeg's default); Chromium decodes it as BT.709, which comes
 *  out ~(223, 49, 30): the red checks allow for that. */
const VIDEO_TOL = 24;
const BLUE: [number, number, number] = [32, 64, 208];
/** The preview's pixel at fractions of the frame, once every still asked for is drawn. */
const pixel = async (page: Page, fx = 0.5, fy = 0.5) => {
  await ve(page, "still()");
  return ve<RGBA | null>(page, `pixel(${fx}, ${fy})`);
};
const near = (got: RGBA | null, want: [number, number, number], tol = 12) =>
  got !== null && got.slice(0, 3).every((v, i) => Math.abs(v - want[i]) <= tol);
/** Every pixel of the shown still (to tell two stills apart). */
const stillPixels = async (page: Page) => {
  await ve(page, "still()");
  return page.locator("canvas.ve-still").evaluate((c: HTMLCanvasElement) => {
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    const out: number[] = []; // every 4th pixel each way is plenty to see the text
    for (let y = 0; y < c.height; y += 4) for (let x = 0; x < c.width; x += 4) out.push(d[(y * c.width + x) * 4]);
    return out;
  });
};
const differs = (a: number[], b: number[]) => a.length !== b.length || a.some((v, i) => Math.abs(v - b[i]) > 40);
const RED_BLUE = () => [fx("red.mp4"), fx("blue.png")];
const needsRed = () => test.skip(!existsSync(fx("red.mp4")), "needs ffmpeg");

test("the preview shows the frame under the playhead", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await expect(page.locator("canvas.ve-still")).toBeVisible();
  await ve(page, "seek(0.5)");
  expect(near(await pixel(page), RED, VIDEO_TOL)).toBe(true);
  await ve(page, "seek(1.5)");
  expect(near(await pixel(page), BLUE)).toBe(true);
  await expect(page.locator(".ve-time")).toHaveText("0:01.50 / 0:04.00");
  // the screen is SCREEN_BG in both themes
  expect(await page.locator(".ve-screen").evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(11, 12, 15)");
  await expect(page.getByRole("button", { name: "▶" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "▶" })).toHaveAttribute("title", "Playback comes soon");
});

test("scrubbing", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const from = await at(page, 0);
  const to = await at(page, 1.5);
  const y = (await timeline(page).boundingBox())!.y + 12; // the ruler
  await page.mouse.move(from.x, y);
  await page.mouse.down();
  for (let i = 1; i <= 10; i++) await page.mouse.move(from.x + ((to.x - from.x) * i) / 10, y);
  await page.mouse.up();
  expect(await ve<number>(page, "playhead")).toBeCloseTo(1.5, 1);
  expect(near(await pixel(page), BLUE)).toBe(true);
  await expect(page.locator(".ve-time")).toHaveText("0:01.50 / 0:04.00");
});

test("text shows in the preview", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await ve(page, "seek(0.5)");
  const plain = await stillPixels(page);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  const withText = await stillPixels(page);
  expect(differs(plain, withText)).toBe(true);
  expect(near(await pixel(page), RED, VIDEO_TOL)).toBe(true); // the text sits at the bottom, not the centre
});

test("a crossfade's middle frame mixes both clips", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const [, b] = await mainIds(page);
  const s = await spans(page);
  const tab = await at(page, s[b].start, "main", 0, 10);
  await page.mouse.click(tab.x, tab.y);
  await page.locator("#ve-kind").selectOption({ label: "Crossfade" });
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Change transition");
  const s2 = await spans(page);
  await ve(page, `seek(${(s2[b].start + s2[b].start + 0.5) / 2})`);
  const got = (await pixel(page))!;
  expect(got[0]).toBeGreaterThan(70);
  expect(got[0]).toBeLessThan(170);
  expect(got[2]).toBeGreaterThan(70);
  expect(got[2]).toBeLessThan(170);
});

test("a picture's grade shows in the preview", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await ve(page, "seek(2)");
  const before = (await pixel(page))!;
  const pic = (await mainIds(page))[1];
  await ve(page, `select("${pic}")`);
  await page.locator("#ve-brightness").evaluate((el: HTMLInputElement) => {
    el.value = "100";
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Change brightness");
  const after = (await pixel(page))!;
  expect(after[0] + after[1] + after[2]).toBeGreaterThan(before[0] + before[1] + before[2] + 60);
});

test("past the end shows the background", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await ve(page, "seek(0.5)");
  expect(near(await pixel(page), RED, VIDEO_TOL)).toBe(true);
  await timeline(page).focus();
  await page.keyboard.press("End");
  expect(await ve<number>(page, "playhead")).toBeCloseTo(4, 5);
  expect(near(await pixel(page), [0, 0, 0], 4)).toBe(true);
});

test("leaving blanks the preview", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  expect(near(await pixel(page), RED, VIDEO_TOL)).toBe(true);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  expect(await pixel(page)).toBeNull();
});

// SourceBank.picture (the still preview is its first user)
interface Pic { width: number; height: number; samples: number[] }
const pic = (page: Page, id: string, t: number, h: number) => ve<Pic | null>(page, `picture("${id}", ${t}, ${h})`);

test("source pictures: video frames at the asked height and time, pictures whole", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("blue.png")]);
  const small = (await pic(page, "s1", 0.5, 120))!;
  expect([small.width, small.height]).toEqual([160, 120]); // aspect kept
  const big = (await pic(page, "s1", 0.5, 2000))!;
  expect([big.width, big.height]).toEqual([320, 240]); // never above the source's own size
  const later = (await pic(page, "s1", 1.5, 120))!;
  expect(later.samples).not.toEqual(small.samples); // testsrc moves: another frame
  expect((await pic(page, "s1", 0.5, 120))!.samples).toEqual(small.samples); // same time, same frame
  expect(await pic(page, "s1", -1, 120)).not.toBeNull(); // before the first frame: the first frame
  const png = (await pic(page, "s2", 0, 50))!;
  expect([png.width, png.height]).toEqual([64, 36]); // a picture is its whole bitmap
  expect(png.samples.slice(0, 3)).toEqual([0x20, 0x40, 0xd0]);
  expect(await pic(page, "s9", 0, 100)).toBeNull(); // unknown source
});

test("source pictures of a turned video come upright", async ({ page }) => {
  test.skip(!existsSync(fx("turned.mp4")), "needs ffmpeg with -display_rotation");
  await page.goto("./?e2e");
  await openEditor(page, [fx("turned.mp4")]);
  const got = (await pic(page, "s1", 0.5, 200))!;
  expect([got.width, got.height]).toEqual([150, 200]); // 320 x 240 shown turned: 240 x 320
});

// ---------------------------------------------------------------- saving and opening projects
interface Saved {
  format: string; version: number; settings: unknown;
  sources: (Src & { relpath: string | null })[]; main: unknown[]; text_rows: unknown[][]; music_rows: unknown[][];
}
/** Chromium has the save window; these tests take the download path the other browsers get. */
const noSavePicker = (page: Page) =>
  page.addInitScript(() => { delete (window as unknown as { showSaveFilePicker?: unknown }).showSaveFilePicker; });
/** "Save project as…" → the downloaded file's path and its project. */
const saveAs = async (page: Page, where: string): Promise<{ path: string; name: string; json: Saved }> => {
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Save project as…" }).click()]);
  const path = `${where}/${download.suggestedFilename()}`;
  await download.saveAs(path);
  return { path, name: download.suggestedFilename(), json: JSON.parse(readFileSync(path, "utf-8")) as Saved };
};
const locate = (page: Page) => page.locator("dialog.ve-locate");
const statuses = (page: Page) => locate(page).locator(".ve-locate-status");
const pickSeveral = async (page: Page, files: string[]) => {
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), locate(page).getByRole("button", { name: "Pick several files…" }).click()]);
  await chooser.setFiles(files);
};
/** A small project as the website writes it (bare names, the fixtures' sizes); `change` edits it first. */
const handProject = (where: string, change: (p: Saved) => void = () => undefined) => {
  const size = (n: string) => statSync(fx(n)).size;
  const json = {
    format: "umcvideo", version: 1, settings: {},
    sources: [
      { id: "s1", path: "clip.mp4", kind: "video", duration: 2, width: 320, height: 240, fps: 30, has_audio: true, size: size("clip.mp4"), relpath: null },
      { id: "s2", path: "img0.png", kind: "image", duration: 0, width: 321, height: 241, fps: 0, has_audio: false, size: size("img0.png"), relpath: null },
      { id: "s3", path: "tone.wav", kind: "audio", duration: 3, width: 0, height: 0, fps: 0, has_audio: true, size: size("tone.wav"), relpath: null },
    ],
    main: [{ type: "video", id: "c1", source: "s1", in_: 0, out: 2 }, { type: "picture", id: "c2", source: "s2" }],
    text_rows: [[]],
    music_rows: [[{ type: "audio", id: "a1", source: "s3", start: 0, in_: 0, out: 3 }]],
  };
  change(json as Saved);
  mkdirSync(where, { recursive: true });
  const path = `${where}/hand.umcvideo`;
  writeFileSync(path, JSON.stringify(json));
  return path;
};
const needsTone = () => test.skip(!existsSync(fx("tone.wav")), "needs ffmpeg");
const openProjectFile = async (page: Page, path: string) => {
  await addFiles(page, [path]);
  await edit(page).click();
  await expect(locate(page)).toBeVisible();
};

test("save, reopen, locate, same timeline", async ({ page }, info) => {
  needsTone();
  await noSavePicker(page);
  await page.goto("./?e2e");
  await openEditor(page, MIXED());
  await ve(page, "seek(1)");
  await page.keyboard.press("s");
  await expect.poll(() => ve(page, "project().main.length")).toBe(3);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  expect(await ve(page, "history.dirty")).toBe(true);
  const saved = await saveAs(page, info.outputPath());
  expect(saved.name).toBe("clip.umcvideo");
  await expect(statusMsg(page)).toHaveText("Saved “clip.umcvideo”.");
  expect(await ve(page, "history.dirty")).toBe(false);
  expect(saved.json.format).toBe("umcvideo");
  expect(saved.json.version).toBe(1);
  expect(saved.json.sources.map((s) => s.path)).toEqual(["clip.mp4", "img0.png", "tone.wav"]);
  expect(saved.json.sources.map((s) => s.size)).toEqual(MIXED().map((f) => statSync(f).size));

  await page.goto("./?e2e");
  await openProjectFile(page, saved.path);
  await expect(locate(page)).toContainText("Some files of this project can't be found.");
  await expect(locate(page).locator(".ve-locate-row")).toHaveCount(3);
  await expect(statuses(page)).toHaveText(["Will be removed", "Will be removed", "Will be removed"]);
  await pickSeveral(page, MIXED());
  await expect(statuses(page)).toHaveText(["Use “clip.mp4”", "Use “img0.png”", "Use “tone.wav”"]);
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect(locate(page)).toHaveCount(0);
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  const got = await ve<Saved>(page, "project()");
  expect(got.sources).toEqual(saved.json.sources);
  expect(got.main).toEqual(saved.json.main);
  expect(got.text_rows).toEqual(saved.json.text_rows);
  expect(got.music_rows).toEqual(saved.json.music_rows);
  expect(got.settings).toEqual(saved.json.settings);
  await expect(statusMsg(page)).toHaveText("");
  await ve(page, "seek(0.5)");
  const px = await pixel(page);
  expect(px).not.toBeNull();
  expect(near(px, [11, 12, 15], 6)).toBe(false); // a frame, not the empty screen
});

test("remove a missing file", async ({ page }, info) => {
  needsTone();
  await page.goto("./?e2e");
  await openProjectFile(page, handProject(info.outputPath()));
  await pickSeveral(page, [fx("clip.mp4")]);
  await expect(statuses(page)).toHaveText(["Use “clip.mp4”", "Will be removed", "Will be removed"]);
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s1"]);
  expect(await ve(page, "project().main.map((m) => m.id)")).toEqual(["c1"]);
  expect(await ve(page, "project().music_rows.flat().length")).toBe(0);
  await expect(statusMsg(page)).toHaveText("Removed “img0.png” from the project. Removed “tone.wav” from the project.");
  await expect(statusMsg(page)).toHaveClass(/error/);
  // undo brings back the file's own project, with all three
  await page.keyboard.press("Control+z");
  await expect.poll(() => ve(page, "project().sources.length")).toBe(3);
});

test("Find file…, and a name-only match of another length is refused", async ({ page }, info) => {
  needsTone();
  // the project knows no sizes, so a name match must agree on the duration: clip.mp4 is 2 s
  const path = handProject(info.outputPath(), (p) => {
    for (const s of p.sources) s.size = 0;
    p.sources[0] = { ...p.sources[0], duration: 30 };
  });
  await page.goto("./?e2e");
  await openProjectFile(page, path);
  await pickSeveral(page, [fx("clip.mp4"), fx("tone.wav")]);
  await expect(statuses(page)).toHaveText(["Use “clip.mp4”", "Will be removed", "Use “tone.wav”"]);
  const row = locate(page).locator(".ve-locate-row").nth(1);
  const [chooser] = await Promise.all([page.waitForEvent("filechooser"), row.getByRole("button", { name: "Find file…" }).click()]);
  await chooser.setFiles([fx("img0.png")]);
  await expect(row.locator(".ve-locate-status")).toHaveText("Use “img0.png”");
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s2", "s3"]);
  await expect(statusMsg(page)).toHaveText("Couldn't use “clip.mp4” for “clip.mp4”.");
});

test("cancel", async ({ page }, info) => {
  needsTone();
  await page.goto("./?e2e");
  await openProjectFile(page, handProject(info.outputPath()));
  await locate(page).getByRole("button", { name: "Cancel" }).click();
  await expect(locate(page)).toHaveCount(0);
  await expect(page.locator(".video-editor")).toBeHidden();
  await expect(page.locator(".note")).toHaveText("Opening the project was cancelled.");
  await expect(page.locator(".queue li")).toHaveCount(1);
  await expect(edit(page)).toBeEnabled();
});

test("unsaved changes are asked about", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  const back = page.getByRole("button", { name: "← Back to Convert" });
  await back.click(); // nothing changed: no question
  await expect(page.locator(".video-editor")).toBeHidden();
  await edit(page).click();
  await expect.poll(() => ve(page, "project() !== null")).toBe(true);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  let asked = "";
  page.once("dialog", (d) => { asked = d.message(); void d.dismiss(); });
  await back.click();
  await expect.poll(() => asked).toBe("Discard unsaved changes to this video project?");
  await expect(page.locator(".video-editor")).toBeVisible();
  expect(await ve(page, "history.undoLabel")).toBe("Add text");
  page.once("dialog", (d) => void d.accept());
  await back.click();
  await expect(page.locator(".video-editor")).toBeHidden();
});

test("a desktop-saved project opens", async ({ page }, info) => {
  test.skip(!existsSync(fx("desktop.umcvideo")), "needs Python and ffmpeg");
  const desktop = JSON.parse(readFileSync(fx("desktop.umcvideo"), "utf-8")) as Saved;
  expect(desktop.sources[0].path).toMatch(/[\\/]clip\.mp4$/);
  expect(desktop.sources[0].relpath).toBe("clip.mp4");
  await noSavePicker(page);
  await page.goto("./?e2e");
  await openProjectFile(page, fx("desktop.umcvideo"));
  await pickSeveral(page, MIXED());
  await expect(statuses(page)).toHaveText(["Use “clip.mp4”", "Use “img0.png”", "Use “tone.wav”"]);
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  const got = await ve<Saved>(page, "project()");
  const where = (p: Saved) => p.sources.map((s) => [s.id, s.path, s.relpath]);
  expect(where(got)).toEqual(where(desktop));
  const ids = (p: Saved) => p.main.map((m) => (m as { id: string }).id);
  expect(ids(got)).toEqual(ids(desktop));
  expect(got.text_rows.flat().length).toBe(1);
  const again = await saveAs(page, info.outputPath());
  expect(again.name).toBe("clip.umcvideo");
  expect(where(again.json)).toEqual(where(desktop)); // re-saving keeps the desktop's path / relpath
});
