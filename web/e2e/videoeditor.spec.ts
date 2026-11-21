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
  await expect(rows(page).nth(0)).toContainText(/0:02\.0\d/); // an FFmpeg build may make the sound a few ms longer
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

// ---------------------------------------------------------------- phones
/** Touch pointer events on the timeline canvas (headless Chromium has no real fingers). */
const touch = (page: Page, type: string, id: number, x: number, y: number) =>
  timeline(page).evaluate((c, a) => {
    c.dispatchEvent(new PointerEvent(a.type, {
      pointerId: a.id, pointerType: "touch", isPrimary: a.id === 11, clientX: a.x, clientY: a.y,
      button: a.type === "pointermove" ? -1 : 0, buttons: a.type === "pointerup" ? 0 : 1, bubbles: true, cancelable: true,
    }));
  }, { type, id, x, y });

test("375 px: preview, transport, timeline; Media and Settings sheets", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator(".editor-top .top-tools").getByRole("button", { name: "Add files", exact: true })).toBeVisible();
  const screen = (await page.locator(".ve-screen").boundingBox())!;
  const transport = (await page.locator(".ve-transport").boundingBox())!;
  const tl = (await page.locator(".video-timeline").boundingBox())!;
  expect(Math.abs(screen.width - 375)).toBeLessThanOrEqual(2);
  expect(Math.abs(screen.height - (screen.width * 9) / 16)).toBeLessThanOrEqual(2);
  expect(transport.y).toBeGreaterThanOrEqual(screen.y + screen.height - 1);
  expect(tl.y).toBeGreaterThanOrEqual(transport.y + transport.height - 1);
  expect(tl.y + tl.height).toBeGreaterThan(600); // it takes the rest of the window
  expect(await ve(page, "view().m.mainH")).toBeGreaterThanOrEqual(44);
  const media = page.locator(".video-media");
  const inspector = page.locator(".video-inspector");
  const mediaBtn = page.locator(".ve-phone-bar").getByRole("button", { name: "Media", exact: true });
  const settingsBtn = page.locator(".ve-phone-bar").getByRole("button", { name: "Settings", exact: true });
  await expect(media).toBeHidden();
  await expect(inspector).toBeHidden();
  await mediaBtn.click();
  await expect(page.locator(".video-media.sheet")).toBeVisible();
  await expect(inspector).toBeHidden();
  await expect(mediaBtn).toHaveAttribute("aria-pressed", "true");
  await settingsBtn.click();
  await expect(page.locator(".video-inspector.sheet")).toBeVisible();
  await expect(media).toBeHidden();
  await expect(mediaBtn).toHaveAttribute("aria-pressed", "false");
  await expect(settingsBtn).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(inspector).toBeHidden();
  await expect(settingsBtn).toHaveAttribute("aria-pressed", "false");
  // selecting an item brings its settings up; the sheet's × closes it
  const [a] = await mainIds(page);
  await ve(page, `select("${a}")`);
  await expect(inspector).toBeVisible();
  await page.getByRole("button", { name: "Close Settings" }).click();
  await expect(inspector).toBeHidden();
  await mediaBtn.click();
  await expect(media).toBeVisible();
  // wider than a phone: the three columns, no sheet; narrow again: nothing left open
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.locator(".video-media.sheet, .video-inspector.sheet")).toHaveCount(0);
  await expect(page.locator(".ve-phone-bar")).toBeHidden();
  await expect(media).toBeVisible();
  await expect(inspector).toBeVisible();
  const [m, p, i] = await Promise.all([media, page.locator(".video-preview"), inspector].map((l) => l.boundingBox()));
  expect(m!.x + m!.width).toBeLessThanOrEqual(p!.x + 1);
  expect(p!.x + p!.width).toBeLessThanOrEqual(i!.x + 1);
  expect(Math.abs(m!.y - i!.y)).toBeLessThanOrEqual(1);
  expect(await ve(page, "view().m.mainH")).toBe(64);
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(media).toBeHidden();
  await expect(inspector).toBeHidden();
});

test("375 px: a pinch zooms and never moves the clip under the finger", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [a] = await mainIds(page);
  const s = await spans(page);
  const before = await steps(page);
  const pps = await ve<number>(page, "view().pps");
  const from = await at(page, (s[a].start + s[a].end) / 2);
  await touch(page, "pointerdown", 11, from.x, from.y);
  await touch(page, "pointermove", 11, from.x + 20, from.y); // a clip drag has started
  await touch(page, "pointerdown", 12, from.x + 60, from.y);
  for (let k = 1; k <= 4; k++) {
    await touch(page, "pointermove", 11, from.x + 20 - k * 10, from.y);
    await touch(page, "pointermove", 12, from.x + 60 + k * 10, from.y);
  }
  await touch(page, "pointerup", 12, from.x + 100, from.y);
  await touch(page, "pointermove", 11, from.x + 120, from.y); // the finger left behind does nothing
  await touch(page, "pointerup", 11, from.x + 120, from.y);
  expect(await ve<number>(page, "view().pps")).toBeGreaterThan(pps * 1.5);
  expect(await steps(page)).toBe(before);
  expect(await spans(page)).toEqual(s);
  expect(await ve(page, "selected")).toBeNull(); // half of a pinch selects nothing
  await expect(page.locator(".video-inspector")).toBeHidden();
});

test("375 px: one finger still drags a clip and a tap selects it", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [a, b] = await mainIds(page);
  const s = await spans(page);
  const from = await at(page, (s[a].start + s[a].end) / 2);
  const past = await at(page, (s[b].start + s[b].end) / 2 + 0.5);
  await touch(page, "pointerdown", 11, from.x, from.y);
  for (let k = 1; k <= 5; k++) await touch(page, "pointermove", 11, from.x + ((past.x - from.x) * k) / 5, from.y);
  await touch(page, "pointerup", 11, past.x, from.y);
  await expect.poll(() => mainIds(page)).toEqual([b, a]);
  expect(await ve(page, "history.undoLabel")).toBe("Move clip");
  await page.keyboard.press("Escape"); // the drag selected the clip: its sheet came up
  await expect(page.locator(".video-inspector")).toBeHidden();
  const s2 = await spans(page);
  const tap = await at(page, (s2[b].start + s2[b].end) / 2);
  await touch(page, "pointerdown", 11, tap.x, tap.y);
  await touch(page, "pointerup", 11, tap.x, tap.y);
  expect(await ve(page, "selected")).toBe(b);
  await expect(page.locator(".video-inspector")).toBeVisible();
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
  await expect(page.locator(".ve-time")).toHaveText(/^0:01\.50 \/ 0:04\.0\d$/);
  // the screen is SCREEN_BG in both themes
  expect(await page.locator(".ve-screen").evaluate((el) => getComputedStyle(el).backgroundColor)).toBe("rgb(11, 12, 15)");
  await expect(playButton(page)).toBeEnabled();
  await expect(playButton(page)).toHaveAttribute("title", "Play (Space)");
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
  await expect(page.locator(".ve-time")).toHaveText(/^0:01\.50 \/ 0:04\.0\d$/);
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
  const end = await ve<number>(page, "playhead"); // the clip's real end (some FFmpeg builds add a few ms of sound)
  expect(end).toBeGreaterThanOrEqual(4);
  expect(end).toBeLessThan(4.1);
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

// ---------------------------------------------------------------- playback
const NO_SOUND = "No sound device — preview is silent";
const playing = (page: Page) => ve<boolean>(page, "playing");
const total = (page: Page) => ve<number>(page, "length()");
interface Stats { chunks: number; loudChunks: number; lateChunks: number; frames: number }
const stats = (page: Page) => ve<Stats>(page, "stats()");
const playButton = (page: Page) => page.locator(".ve-transport .ve-play");

test("Space plays and the playhead moves", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await expect(playButton(page)).toBeEnabled();
  await expect(playButton(page)).toHaveText("▶");
  await timeline(page).focus();
  await page.keyboard.press("Space");
  await expect(playButton(page)).toHaveText("⏸");
  await expect(playButton(page)).toHaveAttribute("title", "Pause (Space)");
  expect(await playing(page)).toBe(true);
  await page.waitForTimeout(800);
  expect(await ve<number>(page, "playhead")).toBeGreaterThan(0.3);
  await expect.poll(() => stats(page).then((s) => s.frames)).toBeGreaterThan(0);
  await page.keyboard.press("Space");
  await expect(playButton(page)).toHaveText("▶");
  expect(await playing(page)).toBe(false);
});

test("playing to the end stops at the end", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const end = await total(page);
  await ve(page, `seek(${end - 0.5})`);
  await ve(page, "play()");
  expect(await playing(page)).toBe(true);
  await expect.poll(() => playing(page), { timeout: 5000 }).toBe(false);
  expect(Math.abs((await ve<number>(page, "playhead")) - end)).toBeLessThan(0.05);
  await expect(playButton(page)).toHaveText("▶");
  expect(near(await pixel(page), [0, 0, 0], 4)).toBe(true); // the still past the end: the background
  // Play at the end starts from the beginning
  await ve(page, "play()");
  await expect.poll(() => ve<number>(page, "playhead")).toBeLessThan(1);
  await ve(page, "stop()");
});

test("Loop starts again", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  const loop = page.locator(".ve-transport .ve-loop");
  await expect(loop).toHaveAttribute("aria-pressed", "false");
  await timeline(page).focus();
  await page.keyboard.press("l");
  await expect(loop).toHaveAttribute("aria-pressed", "true");
  const end = await total(page);
  await ve(page, `seek(${end - 0.4})`);
  await ve(page, "play()");
  await expect.poll(() => ve<number>(page, "playhead"), { timeout: 5000 }).toBeLessThan(1);
  expect(await playing(page)).toBe(true);
  await ve(page, "stop()");
  await loop.click();
  await expect(loop).toHaveAttribute("aria-pressed", "false");
});

test("seek while playing stops and shows the still at the seek", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await ve(page, "play()");
  await expect.poll(() => ve<number>(page, "playhead")).toBeGreaterThan(0.2);
  await ve(page, "seek(2)");
  expect(await playing(page)).toBe(false);
  expect(await ve<number>(page, "playhead")).toBeCloseTo(2, 5);
  expect(near(await pixel(page), BLUE)).toBe(true);
  await expect(playButton(page)).toHaveText("▶");
});

test("an edit while playing stops", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  await ve(page, "play()");
  expect(await playing(page)).toBe(true);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  expect(await playing(page)).toBe(false);
  await expect(playButton(page)).toHaveText("▶");
  await ve(page, "play()");
  expect(await playing(page)).toBe(true);
  await timeline(page).focus();
  await page.keyboard.press("Control+z");
  await expect.poll(() => playing(page)).toBe(false);
});

test("sound plays", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "play()");
  await expect.poll(() => stats(page).then((s) => s.chunks), { timeout: 5000 }).toBeGreaterThanOrEqual(2);
  expect((await stats(page)).loudChunks).toBeGreaterThan(0);
  await ve(page, "stop()");
  const [clip] = await mainIds(page);
  await ve(page, `select("${clip}")`);
  await page.locator("#ve-mute").check();
  await expect.poll(() => ve(page, "project().main[0].mute")).toBe(true);
  await ve(page, "seek(0)");
  await ve(page, "play()");
  await expect.poll(() => stats(page).then((s) => s.chunks), { timeout: 5000 }).toBeGreaterThanOrEqual(2);
  expect((await stats(page)).loudChunks).toBe(0);
  await ve(page, "stop()");
});

test("a cold play keeps the opening sound", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "setArmMs(5000)"); // a slow runner's first block may take longer than the 300 ms cap
  await ve(page, "play()"); // nothing decoded yet: the clock waits for the first chunk
  await expect.poll(() => stats(page).then((s) => s.chunks + s.lateChunks), { timeout: 5000 }).toBeGreaterThanOrEqual(3);
  expect((await stats(page)).lateChunks).toBe(0);
  await ve(page, "stop()");
});

test("a broken frame stops playback where it was", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "play()");
  await expect.poll(() => ve<number>(page, "playhead")).toBeGreaterThan(0.3);
  await ve(page, "breakNextDraw()");
  await expect.poll(() => playing(page)).toBe(false);
  const at = await ve<number>(page, "playhead");
  expect(at).toBeGreaterThan(0.3);
  expect(at).toBeLessThan((await total(page)) - 0.1);
  await expect(statusMsg(page)).toHaveText("Playback stopped.");
  await expect(playButton(page)).toHaveText("▶");
  await ve(page, "still()");
  expect(await pixel(page)).not.toBeNull();
});

test("leaving while playing stops cleanly", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "play()");
  await expect.poll(() => stats(page).then((s) => s.frames)).toBeGreaterThan(0);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  expect(await playing(page)).toBe(false);
  const before = (await stats(page)).frames;
  await page.waitForTimeout(1000);
  expect((await stats(page)).frames).toBe(before);
  expect(errors).toEqual([]);
});

test("plays across a crossfade", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("img0.png")]);
  const [, b] = await mainIds(page);
  const s = await spans(page);
  const tab = await at(page, s[b].start, "main", 0, 10);
  await page.mouse.click(tab.x, tab.y);
  await page.locator("#ve-kind").selectOption({ label: "Crossfade" });
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Change transition");
  const s2 = await spans(page);
  await ve(page, `seek(${s2[b].start - 0.3})`);
  await ve(page, "play()");
  await expect.poll(() => ve<number>(page, "playhead"), { timeout: 5000 }).toBeGreaterThan(s2[b].start + 0.6);
  expect((await stats(page)).frames).toBeGreaterThan(0);
  expect(await playing(page)).toBe(true);
  await ve(page, "stop()");
  expect(errors).toEqual([]);
  await expect(statusMsg(page)).not.toHaveClass(/error/);
});

test("no sound device", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await ve(page, "forceWallClock()");
  await ve(page, "play()");
  await expect(statusMsg(page)).toHaveText(NO_SOUND);
  await expect.poll(() => ve<number>(page, "playhead")).toBeGreaterThan(0.3);
  expect((await stats(page)).chunks).toBe(0);
  await ve(page, "stop()");
});

test("the volume slider keeps its value", async ({ page }) => {
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  const slider = () => page.locator(".video-editor").getByRole("slider", { name: "Volume" });
  await expect(slider()).toBeVisible();
  await slider().fill("35");
  expect(await page.evaluate(() => localStorage.getItem("umc-audio-volume"))).toBe("35");
  await page.reload();
  await openEditor(page, [fx("clip.mp4")]);
  await expect(slider()).toHaveValue("35");
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

// The decoders are keyed by source id across the whole undo history: an id freed by "Fix missing
// files" (or by an undo) must never be given to a later file, or older states show its frames.
test("an id freed by Fix missing files isn't reused", async ({ page }, info) => {
  needsTone();
  await page.goto("./?e2e");
  await openProjectFile(page, handProject(info.outputPath()));
  await pickSeveral(page, [fx("clip.mp4"), fx("tone.wav")]); // img0.png (s2) is removed
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s1", "s3"]);
  await page.locator(".ve-media input[type=file]").setInputFiles([fx("blue.png")]);
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add files");
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect.poll(() => ve(page, "project().sources.length")).toBe(3);
  // the restored picture (s2, img0.png, never picked) has no decoder: not the new file's frames
  const s = await spans(page);
  await ve(page, `seek(${(s.c2.start + s.c2.end) / 2})`);
  const px = await pixel(page);
  expect(px).not.toBeNull();
  expect(near(px, BLUE)).toBe(false);
  expect(await ve(page, `decodes("s2")`)).toBe(false);
  // redo brings the new file back under its own id
  await page.keyboard.press("Control+y");
  await page.keyboard.press("Control+y");
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add files");
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s1", "s3", "s4"]);
  expect(await ve(page, `decodes("s4")`)).toBe(true);
});

test("a picked file that can't be used says why", async ({ page }, info) => {
  needsTone();
  await page.goto("./?e2e");
  await openProjectFile(page, handProject(info.outputPath()));
  await ve(page, "setLimits({ pictureSide: 100 })");
  await pickSeveral(page, MIXED());
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  await ve(page, "setLimits(null)");
  await expect(statusMsg(page)).toContainText(/^Couldn't use “img0\.png” for “img0\.png”\. \S/);
});

test("a file added while Redo is pressed doesn't take the redone id", async ({ page }) => {
  test.skip(!existsSync(fx("clip.avi")), "needs ffmpeg");
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await page.locator(".ve-media input[type=file]").setInputFiles([fx("blue.png")]);
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add files");
  await rows(page).nth(1).getByRole("button", { name: /^Add/ }).click(); // blue.png (s2) at the playhead
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add clip");
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect.poll(() => ve(page, "project().sources.length")).toBe(1);
  // the AVI goes through the copy (slow): redo both steps while it is being opened
  await page.locator(".ve-media input[type=file]").setInputFiles([fx("clip.avi")]);
  await expect(statusMsg(page)).toContainText(/Opening|Loading video engine|Converting/);
  await page.keyboard.press("Control+y");
  await page.keyboard.press("Control+y");
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add clip");
  await expect.poll(() => ve(page, "history.undoLabel"), { timeout: 120_000 }).toBe("Add files");
  expect(await ve(page, "project().sources.map((s) => s.id)")).toEqual(["s1", "s2", "s3"]);
  expect(await ve(page, "project().sources.map((s) => s.path)")).toEqual(["clip.mp4", "blue.png", "clip.avi"]);
  // the redone picture still shows blue.png
  const clip = (await ve<{ id: string; source: string }[]>(page, "project().main")).find((m) => m.source === "s2")!;
  const s = await spans(page);
  await ve(page, `seek(${(s[clip.id].start + s[clip.id].end) / 2})`);
  expect(near(await pixel(page), BLUE)).toBe(true);
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

test("unsaved changes are asked about", async ({ page }, info) => {
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
  // a project file dropped on the editor asks too; declining keeps the editor and its project
  let again = "";
  page.once("dialog", (d) => { again = d.message(); void d.dismiss(); });
  await addFiles(page, [handProject(info.outputPath())]);
  await expect.poll(() => again).toBe("Discard unsaved changes to this video project?");
  await expect(locate(page)).toHaveCount(0);
  await expect(page.locator(".video-editor")).toBeVisible();
  expect(await ve(page, "project().text_rows.flat().length")).toBe(1);
  expect(await ve(page, "history.undoLabel")).toBe("Add text");
  page.once("dialog", (d) => void d.accept());
  await back.click();
  await expect(page.locator(".video-editor")).toBeHidden();
});

test("beforeunload only while there are unsaved changes", async ({ page }, info) => {
  await noSavePicker(page);
  await page.goto("./?e2e");
  const blocked = () => page.evaluate(() => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  });
  await openEditor(page, [fx("clip.mp4")]);
  expect(await blocked()).toBe(false);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  expect(await blocked()).toBe(true);
  await saveAs(page, info.outputPath());
  expect(await blocked()).toBe(false);
  // typing in the inspector isn't a history step yet, but it is unsaved
  await page.locator("textarea#ve-text").fill("Not saved");
  await expect.poll(() => ve(page, "shown().text_rows[0][0].text")).toBe("Not saved");
  expect(await ve(page, "history.dirty")).toBe(false);
  expect(await blocked()).toBe(true);
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  expect(await blocked()).toBe(false); // left: the guard is gone
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

// ---------------------------------------------------------------- thumbnails and waveforms
interface Scanned { thumbs: Record<string, number>; waves: Record<string, number>; scans: Record<string, number> }
const scanned = (page: Page) => ve<Scanned>(page, "scanned()");
/** Pixels of the timeline canvas inside a clip: the top-left area where its thumbnails go. */
const clipPixels = (page: Page, id: string) =>
  page.evaluate((cid) => {
    const w = window as unknown as { __videoeditor: { spans(): Record<string, { start: number; row_kind: string; row: number }>; view(): { pps: number; scroll: number; m: { header: number } }; rows(): { kind: string; row: number; y: number }[] } };
    const hook = w.__videoeditor;
    const s = hook.spans()[cid];
    const v = hook.view();
    const r = hook.rows().find((q) => q.kind === s.row_kind && q.row === s.row)!;
    const c = document.querySelector("canvas.ve-timeline") as HTMLCanvasElement;
    const dpr = c.width / c.clientWidth;
    const x0 = Math.round((s.start - v.scroll) * v.pps + v.m.header) + 3;
    const out: number[][] = [];
    const d = c.getContext("2d")!;
    for (let k = 0; k < 8; k++) {
      const px = d.getImageData(Math.round((x0 + k * 4) * dpr), Math.round((r.y + 30) * dpr), 1, 1).data;
      out.push([px[0], px[1], px[2]]);
    }
    const probe = document.createElement("span");
    probe.style.color = "var(--clip-video)";
    document.body.append(probe);
    const m = /(\d+), (\d+), (\d+)/.exec(getComputedStyle(probe).color)!;
    probe.remove();
    return { pixels: out, fill: [Number(m[1]), Number(m[2]), Number(m[3])] };
  }, id);

test("clips show thumbnails and a waveform", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("blue.png")]);
  await expect.poll(async () => Object.keys((await scanned(page)).waves).length).toBe(1);
  await expect.poll(async () => Object.keys((await scanned(page)).thumbs).length).toBe(2);
  const got = await scanned(page);
  const src = await ve<Src[]>(page, "project().sources");
  const video = src.find((s) => s.kind === "video")!;
  expect(got.thumbs[video.id]).toBe(Math.min(40, Math.max(1, Math.ceil((video.duration * 80) / 120))));
  expect(got.thumbs[src.find((s) => s.kind === "image")!.id]).toBe(1);
  expect(got.waves[video.id]).toBe(Math.max(50, Math.min(4000, Math.round(video.duration * 50))));
  expect(Object.keys(got.waves)).toEqual([video.id]);
  const cid = (await ve<string[]>(page, "project().main.map((m) => m.id)"))[0];
  await expect.poll(async () => {
    const { pixels, fill } = await clipPixels(page, cid);
    return pixels.some((p) => p.some((v, i) => Math.abs(v - fill[i]) > 12));
  }).toBe(true);
});

test("a relinked source is scanned again", async ({ page }, info) => {
  needsTone();
  await noSavePicker(page);
  await page.goto("./?e2e");
  page.on("dialog", (d) => void d.accept());
  const path = handProject(info.outputPath());
  for (const n of [1, 2]) {
    if (n === 1) await openProjectFile(page, path);
    else {
      await expect.poll(async () => (await scanned(page)).scans.s3).toBe(1); // let the first scans finish
      await page.locator(".ve-media input[type=file]").setInputFiles([path]); // the same project again
      await expect(locate(page)).toBeVisible();
    }
    await pickSeveral(page, MIXED());
    await locate(page).getByRole("button", { name: "Open project" }).click();
    await expect.poll(async () => (await scanned(page)).scans.s1).toBe(n);
  }
  const got = await scanned(page);
  expect(got.thumbs.s1).toBeGreaterThan(0);
  expect(got.waves.s1).toBeGreaterThan(0);
  expect(got.waves.s3).toBeGreaterThan(0);
});

test("a source given another file drops its old scan and is scanned again", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await expect.poll(async () => (await scanned(page)).scans.s1).toBe(1);
  expect(await ve(page, `holds("s1")`)).toEqual({ thumbs: true, wave: true });
  // both the pictures and the waveform go at once (the new scan lands later)
  expect(await ve(page, `rescan("s1")`)).toEqual({ thumbs: false, wave: false });
  await expect.poll(async () => (await scanned(page)).scans.s1).toBe(2);
  const got = await scanned(page);
  expect(got.thumbs.s1).toBeGreaterThan(0);
  expect(got.waves.s1).toBeGreaterThan(0);
});

test("leaving stops the scan", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto("./?e2e");
  await openEditor(page, MIXED());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".video-editor")).toBeHidden();
  const after = await scanned(page);
  await page.waitForTimeout(1500);
  expect(await scanned(page)).toEqual(after);
  expect(errors).toEqual([]);
});

// ---------------------------------------------------------------- export
const exportButton = (page: Page) => page.getByRole("button", { name: "Export…" });
const exportDialog = (page: Page) => page.locator("dialog.video-export");
const dialogExport = (page: Page) => exportDialog(page).getByRole("button", { name: "Export", exact: true });
const exportStatus = (page: Page) => page.locator(".video-editor .ve-status .msg");
const EXPORTED = /^Exported “.+”\.$/;
interface Inspected { duration: number; audioDuration: number; width: number; height: number; hasAudio: boolean; codec: string | null; colour: number[] | null }
type ExportHook = { inspectExport(): Promise<Inspected & { colourAt(t: number): Promise<number[]> }>; exportNow(v: unknown): Promise<void> };
/** The last export as Mediabunny reads it on the page (`at` ≥ 0: also its colour in the middle at that time). */
const inspectExport = (page: Page, at = -1) => page.evaluate(async (t) => {
  const i = await (window as unknown as { __videoeditor: ExportHook }).__videoeditor.inspectExport();
  return { duration: i.duration, audioDuration: i.audioDuration, width: i.width, height: i.height, hasAudio: i.hasAudio, codec: i.codec, colour: t >= 0 ? await i.colourAt(t) : null };
}, at) as Promise<Inspected>;
/** Export with these values (the rest as the window would have them), skipping the window and the picker. */
const exportNow = (page: Page, values: Record<string, unknown> = {}) =>
  page.evaluate((v) => (window as unknown as { __videoeditor: ExportHook }).__videoeditor.exportNow(v), values);
const opfsSize = (page: Page, name: string) => page.evaluate(async (n) => {
  try {
    const root = await navigator.storage.getDirectory();
    return (await (await root.getFileHandle(n)).getFile()).size;
  } catch {
    return -1; // gone
  }
}, name);
const needsNoisy = () => test.skip(!existsSync(fx("noisy.mp4")), "needs ffmpeg");

test("export MP4 keeps length, size and sound", async ({ page }) => {
  test.setTimeout(120_000);
  needsTone();
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4"), fx("tone.wav")]);
  const len = await total(page);
  await exportNow(page, { container: "mp4" });
  await expect(exportStatus(page)).toHaveText("Exported “clip edit.mp4”.");
  const got = await inspectExport(page);
  expect(Math.abs(got.duration - len)).toBeLessThanOrEqual(1 / 30 + 1e-3);
  expect([got.width, got.height]).toEqual([320, 240]);
  expect(got.hasAudio).toBe(true);
  expect(Math.abs(got.audioDuration - got.duration)).toBeLessThan(0.05); // AAC / Opus priming allowed
});

test("export WebM at 480p", async ({ page }) => {
  test.setTimeout(120_000);
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, [fx("clip.mp4")]);
  await exportNow(page, { container: "webm", resolution: "480p" });
  await expect(exportStatus(page)).toHaveText("Exported “clip edit.webm”.");
  const got = await inspectExport(page);
  expect(got.height).toBe(480);
  expect(got.width % 2).toBe(0);
  expect(got.width).toBe(640);
  expect(["vp9", "av1"]).toContain(got.codec);
});

test("an odd project size exports at the even size below it", async ({ page }) => {
  test.setTimeout(120_000);
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, [fx("red.mp4")]);
  await ve(page, "select(null)"); // the inspector shows the project's settings
  await page.locator("#ve-resolution").selectOption({ label: "Custom" });
  await page.locator("#ve-width").fill("321");
  await page.locator("#ve-width").press("Tab");
  await page.locator("#ve-height").fill("181");
  await page.locator("#ve-height").press("Tab");
  await expect.poll(() => ve(page, "project().settings")).toMatchObject({ resolution: "custom", width: 321, height: 181 });
  await exportNow(page, {});
  await expect(exportStatus(page)).toHaveText("Exported “red edit.mp4”.");
  const got = await inspectExport(page);
  expect([got.width, got.height]).toEqual([320, 180]);
});

test("a crossfade's middle frame mixes both clips in the export", async ({ page }) => {
  test.setTimeout(120_000);
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
  const fade = await ve<number>(page, "project().main[1].transition_in.duration");
  await exportNow(page, {});
  await expect(exportStatus(page)).toHaveText(EXPORTED);
  const got = (await inspectExport(page, s2[b].start + fade / 2)).colour!;
  const red = [223, 49, 30]; // red.mp4 as Chromium decodes it (see RED)
  for (let c = 0; c < 3; c++) {
    expect(got[c]).toBeGreaterThanOrEqual(Math.min(red[c], BLUE[c]) - 20);
    expect(got[c]).toBeLessThanOrEqual(Math.max(red[c], BLUE[c]) + 20);
  }
  // neither clip alone
  expect(got[0]).toBeGreaterThan(70);
  expect(got[0]).toBeLessThan(190);
  expect(got[2]).toBeGreaterThan(60);
  expect(got[2]).toBeLessThan(180);
});

test("pictures and text only export without sound", async ({ page }) => {
  test.setTimeout(120_000);
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const [clip] = await mainIds(page);
  await ve(page, `select("${clip}")`);
  await page.keyboard.press("Delete"); // only the picture is left (red.mp4 stays a source)
  await expect.poll(() => mainIds(page)).toHaveLength(1);
  await page.getByRole("button", { name: "+ Text" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Add text");
  await exportNow(page, {});
  await expect(exportStatus(page)).toHaveText("Exported “red edit.mp4”.");
  const got = await inspectExport(page);
  expect(got.hasAudio).toBe(false);
  expect(got.duration).toBeGreaterThan(2.9);
});

test("export GIF opens", async ({ page }, info) => {
  test.setTimeout(180_000);
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, [fx("red.mp4")]);
  const [download] = await Promise.all([page.waitForEvent("download", { timeout: 170_000 }), exportNow(page, { container: "gif", gif_width: 160 })]);
  expect(download.suggestedFilename()).toBe("red edit.gif");
  const path = `${info.outputPath()}/out.gif`;
  await download.saveAs(path);
  const bytes = readFileSync(path);
  expect(bytes.subarray(0, 6).toString("latin1")).toBe("GIF89a");
  const width = await page.evaluate(async (b64) => {
    const img = new Image();
    img.src = `data:image/gif;base64,${b64}`;
    await img.decode();
    return img.naturalWidth;
  }, bytes.toString("base64"));
  expect(width).toBe(160);
  await expect(exportStatus(page)).toHaveText("Exported “red edit.gif”.");
});

test("export straight to a file", async ({ page }) => {
  test.setTimeout(120_000);
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const len = await total(page);
  await ve(page, `useOpfsHandle("straight.mp4")`);
  await exportButton(page).click();
  await expect(exportDialog(page)).toBeVisible();
  await expect(exportDialog(page).locator("h2")).toHaveText("Export video");
  await expect(exportDialog(page).locator(".ve-export-estimate")).toHaveText(/^About [\d.]+ (MB|GB)$/);
  await expect(exportDialog(page).locator(".ve-export-limits")).toContainText("Browser limits:");
  await expect(exportDialog(page).locator(".ve-export-slow")).toContainText("desktop app");
  await expect(dialogExport(page)).toBeEnabled();
  await dialogExport(page).click();
  await expect(exportDialog(page)).toHaveCount(0);
  await expect(exportStatus(page)).toHaveText("Exported “straight.mp4”.", { timeout: 90_000 });
  expect(await opfsSize(page, "straight.mp4")).toBeGreaterThan(1000);
  const got = await inspectExport(page);
  expect(Math.abs(got.duration - len)).toBeLessThanOrEqual(1 / 30 + 1e-3);
  expect([got.width, got.height]).toEqual([320, 180]);
});

test("cancel stops the export and leaves no file", async ({ page }) => {
  test.setTimeout(240_000);
  needsNoisy();
  await page.goto("./?e2e");
  await openEditor(page, [fx("noisy.mp4")]);
  const [clip] = await mainIds(page);
  await ve(page, `select("${clip}")`); // so Delete would remove it
  await ve(page, `useOpfsHandle("long.mp4")`);
  await page.evaluate("void window.__videoeditor.exportNow({ resolution: '1080p' })");
  await expect(exportStatus(page)).toContainText(/Exporting… 0:0[1-9]/, { timeout: 90_000 });
  // the project is read-only meanwhile
  expect(await ve(page, "selected")).toBe(clip);
  await page.keyboard.press("Delete");
  await page.keyboard.press("s");
  expect(await mainIds(page)).toEqual([clip]);
  expect(await ve(page, "history.steps")).toBe(0);
  // leaving asks first; No keeps exporting
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.dismiss();
  });
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  expect(asked).toBe("Cancel the export and leave?");
  await expect(page.locator(".video-editor")).toBeVisible();
  expect(await ve(page, "exporting")).toBe(true);
  await page.locator(".video-editor .ve-status").getByRole("button", { name: "Cancel" }).click();
  await expect(exportStatus(page)).toHaveText("Export cancelled.");
  await expect.poll(() => opfsSize(page, "long.mp4")).toBeLessThanOrEqual(0);
  await exportNow(page, {});
  await expect(exportStatus(page)).toHaveText("Exported “noisy edit.mp4”.", { timeout: 150_000 });
  expect((await inspectExport(page)).duration).toBeGreaterThan(9.9);
});

test("too big for a download shows the limit", async ({ page }) => {
  needsRed();
  await noSavePicker(page);
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  await ve(page, "setExportLimits({ downloadBytes: 1000 })");
  await exportButton(page).click();
  await expect(exportDialog(page).locator(".ve-export-problem")).toContainText("too big for this browser");
  await expect(dialogExport(page)).toBeDisabled();
  await exportDialog(page).getByRole("button", { name: "Cancel" }).click();
  await expect(exportDialog(page)).toHaveCount(0);
});

test("missing files block export", async ({ page }, info) => {
  needsTone();
  await page.goto("./?e2e");
  await openProjectFile(page, handProject(info.outputPath()));
  await pickSeveral(page, [fx("clip.mp4")]);
  await locate(page).getByRole("button", { name: "Open project" }).click();
  await expect.poll(() => ve(page, "history.undoLabel")).toBe("Fix missing files");
  await page.keyboard.press("Control+z"); // the project's own sources: two aren't found
  await expect.poll(() => ve(page, "project().sources.length")).toBe(3);
  await exportButton(page).click();
  await expect(exportDialog(page).locator(".ve-export-problem")).toHaveText("Find the missing files first — Export needs every file.");
  await expect(dialogExport(page)).toBeDisabled();
});

test("the dialog hides codecs the browser can't encode", async ({ page }) => {
  needsRed();
  await page.goto("./?e2e");
  await openEditor(page, RED_BLUE());
  const encodable = await page.evaluate(async () => {
    const probe: Record<string, string> = { h264: "avc1.42001f", vp9: "vp09.00.10.08", av1: "av01.0.04M.08", h265: "hev1.1.6.L93.B0" };
    const out: string[] = [];
    for (const [k, codec] of Object.entries(probe)) {
      try {
        if ((await VideoEncoder.isConfigSupported({ codec, width: 1280, height: 720, bitrate: 1e6 })).supported) out.push(k);
      } catch { /* not this one */ }
    }
    return out;
  });
  await exportButton(page).click();
  const labels = () => exportDialog(page).locator("#vex-video_codec option").allTextContents();
  const LABELS: Record<string, string> = { "H.264": "h264", VP9: "vp9", AV1: "av1" };
  const ids = (ls: string[]) => ls.filter((l) => l !== "Automatic").map((l) => LABELS[l] ?? (l.startsWith("H.265") ? "h265" : l));
  await expect.poll(async () => (await labels()).length).toBeGreaterThan(0);
  expect((await labels())[0]).toBe("Automatic");
  for (const id of ids(await labels())) expect(encodable).toContain(id);
  await exportDialog(page).locator("#vex-container").selectOption({ label: "WebM" });
  await expect.poll(async () => ids(await labels()).filter((i) => i === "h264" || i === "h265")).toEqual([]);
});
