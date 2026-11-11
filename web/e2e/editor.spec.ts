// The image editor: Convert | Edit switch, drawing, layers, text, crop, saving — in real Chromium.
import { existsSync, readFileSync } from "node:fs";
import { unzipSync } from "fflate";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;

interface EditorHook {
  doc: { width: number; height: number; active: number; layers: { name: string; width: number; height: number; data: Uint8ClampedArray; x: number; y: number }[] };
  history: { canUndo: boolean };
  toolId: string;
  imageToPage(x: number, y: number): { x: number; y: number };
  pixel(x: number, y: number, layer?: number): number[];
}
declare global {
  interface Window {
    __editor?: EditorHook;
  }
}

async function load(page: Page, files: string[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
}

async function openEditor(page: Page, files = [fx("img0.png"), fx("img1.png")]) {
  await load(page, files);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".editor")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__editor?.doc.layers.length ?? 0)).toBe(files.length);
  // the tool options float over the picture: tuck them away so strokes reach it
  await page.evaluate(() => (window.__editor as unknown as { floating: { minimise(k: string): void } }).floating.minimise("tool"));
}

const showToolOptions = (page: Page) => page.evaluate(() => (window.__editor as unknown as { floating: { restore(k: string): void } }).floating.restore("tool"));

async function at(page: Page, x: number, y: number) {
  return page.evaluate(([ix, iy]) => (window.__editor as EditorHook).imageToPage(ix, iy), [x, y]);
}

async function drag(page: Page, from: [number, number], to: [number, number]) {
  const a = await at(page, ...from);
  const b = await at(page, ...to);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2, { steps: 4 });
  await page.mouse.move(b.x, b.y, { steps: 4 });
  await page.mouse.up();
}

const pixel = (page: Page, x: number, y: number, layer?: number) => page.evaluate(([ix, iy, l]) => (window.__editor as EditorHook).pixel(ix, iy, l ?? undefined), [x, y, layer ?? null] as const);

async function saveAs(page: Page, name: string, format: string): Promise<Buffer> {
  await page.getByRole("button", { name: "Save as…" }).click();
  const dlg = page.locator("dialog.save[open]");
  await dlg.getByLabel("File name").fill(name);
  await dlg.getByLabel("Format").selectOption(format);
  const download = page.waitForEvent("download", { timeout: 120_000 });
  await dlg.getByRole("button", { name: "Save", exact: true }).click();
  return readFileSync(await (await download).path());
}

test("Edit disabled for non-images, enabled for images", async ({ page }) => {
  await load(page, [fx("doc.md")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Clear" }).click();
  await page.locator('input[type="file"]').first().setInputFiles([fx("img0.png"), fx("img1.png")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeEnabled();
});

test("Edit opens one layer per image; Back returns to the converter", async ({ page }) => {
  await openEditor(page);
  const names = await page.evaluate(() => (window.__editor as EditorHook).doc.layers.map((l) => l.name));
  expect(names).toEqual(["img0", "img1"]);
  await expect(page.locator(".layers li")).toHaveCount(2);
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".editor")).toBeHidden();
  await expect(page.locator('.pill[data-target="jpg"]')).toBeVisible();
});

test("drawing a stroke paints and undo restores", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).click(); // empty layer on top
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).doc.layers.length)).toBe(3);
  expect((await pixel(page, 100, 100))[3]).toBe(0);
  await drag(page, [80, 100], [140, 100]);
  expect(await pixel(page, 110, 100)).toEqual([0, 0, 0, 255]);
  await expect(page.getByRole("button", { name: "Undo" })).toBeEnabled();
  await page.keyboard.press("Control+z");
  expect((await pixel(page, 110, 100))[3]).toBe(0);
});

test("shortcuts select tools; typing in a field doesn't", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("e");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("eraser");
  await page.keyboard.press("u");
  await page.keyboard.press("u");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("ellipse");
  await page.keyboard.press("Control+s"); // opens our Save dialog, not the browser's
  const dlg = page.locator("dialog.save[open]");
  await expect(dlg).toBeVisible();
  await dlg.getByLabel("File name").fill("brush");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("ellipse");
  await dlg.getByRole("button", { name: "Cancel" }).click();
});

test("layers: add, delete and undo", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).click();
  await expect(page.locator(".layers li")).toHaveCount(3);
  await page.getByRole("button", { name: "Delete" }).click();
  await expect(page.locator(".layers li")).toHaveCount(2);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect(page.locator(".layers li")).toHaveCount(3);
});

test("rectangle and text", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).click();
  await page.keyboard.press("u");
  await drag(page, [20, 20], [120, 80]);
  expect((await pixel(page, 21, 50))[3]).toBeGreaterThan(200); // left edge of the outline
  expect((await pixel(page, 70, 50))[3]).toBe(0); // inside stays empty
  await page.keyboard.press("t");
  const p = await at(page, 30, 150);
  await page.mouse.click(p.x, p.y);
  await page.keyboard.type("Hi");
  await page.keyboard.press("Control+Enter");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).doc.layers.at(-1)?.name)).toBe("Hi");
});

test("crop with Enter", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("c");
  await drag(page, [0, 0], [100, 60]);
  await page.keyboard.press("Enter");
  await expect.poll(() => page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height])).toEqual([100, 60]);
});

test("save as PNG and .ora", async ({ page }) => {
  await openEditor(page);
  const png = await saveAs(page, "edited", "png");
  expect([...png.subarray(1, 4)]).toEqual([0x50, 0x4e, 0x47]);
  expect(png.readUInt32BE(16)).toBe(321);
  const ora = await saveAs(page, "edited", "ora");
  const files = unzipSync(new Uint8Array(ora));
  expect(new TextDecoder().decode(files.mimetype)).toBe("image/openraster");
  expect(new TextDecoder().decode(files["stack.xml"]).match(/<layer /g)?.length).toBe(2);
});

test("an .ora reopens with its layers", async ({ page }) => {
  await openEditor(page);
  const ora = await saveAs(page, "again", "ora");
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await page.getByRole("button", { name: "Clear" }).click();
  await page.locator('input[type="file"]').first().setInputFiles({ name: "again.ora", mimeType: "image/openraster", buffer: ora });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__editor?.doc.layers.map((l) => l.name))).toEqual(["img0", "img1"]);
});

test("psd opens via the worker", async ({ page }) => {
  test.skip(!existsSync(fx("design.psd")), "design.psd is made by python3 in global-setup (not available here)");
  await openEditor(page, [fx("design.psd")]);
  const d = await page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height]);
  expect(d[0]).toBeGreaterThan(0);
});

test("drop in edit mode adds a layer", async ({ page }) => {
  await openEditor(page, [fx("img0.png")]);
  await page.locator('input[type="file"]').first().setInputFiles([fx("img2.png")]);
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).doc.layers.length)).toBe(2);
});

test("Back with unsaved changes asks; beforeunload only while dirty", async ({ page }) => {
  await openEditor(page);
  const blocked = () => page.evaluate(() => {
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    return e.defaultPrevented;
  });
  expect(await blocked()).toBe(false);
  await page.getByRole("button", { name: "Add" }).click();
  expect(await blocked()).toBe(true);
  page.once("dialog", (d) => d.dismiss());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".editor")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".editor")).toBeHidden();
  expect(await blocked()).toBe(false);
});

test("touch: two fingers don't paint", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).click();
  const a = await at(page, 100, 100);
  const b = await at(page, 200, 100);
  await page.locator(".editor canvas.view").evaluate((el, [p, q]) => {
    const fire = (type: string, id: number, x: number, y: number) =>
      el.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: "touch", clientX: x, clientY: y, bubbles: true, isPrimary: id === 1, buttons: type === "pointerup" ? 0 : 1 }));
    fire("pointerdown", 1, p.x, p.y);
    fire("pointerdown", 2, q.x, q.y);
    for (let i = 1; i <= 5; i++) {
      fire("pointermove", 1, p.x + i * 5, p.y);
      fire("pointermove", 2, q.x - i * 5, q.y);
    }
    fire("pointerup", 1, p.x + 25, p.y);
    fire("pointerup", 2, q.x - 25, q.y);
  }, [a, b]);
  expect((await pixel(page, 105, 100))[3]).toBe(0);
  expect(await page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(true); // only the "Add layer"
});

// ---------------------------------------------------------------- review fixes
const size = (page: Page) => page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height]);

test("ctrl+s in a text field is ours, not the browser's", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("t");
  const p = await at(page, 30, 150);
  await page.mouse.click(p.x, p.y);
  await page.keyboard.type("Yo");
  const prevented = await page.locator("textarea.text-box").evaluate((el) => {
    const e = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    el.dispatchEvent(e);
    return e.defaultPrevented;
  });
  expect(prevented).toBe(true);
  await expect(page.locator("dialog.save[open]")).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).doc.layers.at(-1)?.name)).toBe("Yo");
});

test("Enter and Space press a focused button", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator(".layers li")).toHaveCount(3);
  await page.keyboard.press(" ");
  await expect(page.locator(".layers li")).toHaveCount(4);
});

test("double-click renames a layer; Esc cancels", async ({ page }) => {
  await openEditor(page);
  await page.locator(".layers li .lname").first().dblclick();
  await page.locator(".layers input.rename").fill("Sky");
  await page.keyboard.press("Enter");
  await expect(page.locator(".layers li .lname").first()).toHaveText("Sky");
  await page.locator(".layers li .lname").first().dblclick();
  await page.locator(".layers input.rename").fill("Nope");
  await page.keyboard.press("Escape");
  await expect(page.locator(".layers li .lname").first()).toHaveText("Sky");
});

test("crop: the bar appears next to the rectangle; Apply, Cancel and Esc", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("c");
  const bar = page.getByRole("toolbar", { name: "Crop" });
  await expect(bar).toBeHidden(); // nothing to apply before a rectangle is drawn
  await expect(page.locator(".editor-status")).toContainText("Drag a rectangle");
  await drag(page, [0, 0], [200, 100]);
  await page.keyboard.press("Escape");
  await expect(bar).toBeHidden();
  await page.keyboard.press("Enter");
  expect(await size(page)).toEqual([321, 241]);
  await drag(page, [20, 20], [200, 100]);
  await expect(bar).toBeVisible();
  await expect(bar).toContainText("180 × 80 px");
  // next to the corner where the drag ended: below it, right edges lined up
  const corner = await at(page, 200, 100);
  const box = await bar.boundingBox();
  if (!box) throw new Error("no layout");
  expect(box.y).toBeGreaterThanOrEqual(corner.y);
  expect(Math.abs(box.x + box.width - corner.x)).toBeLessThan(3);
  await bar.getByRole("button", { name: "Cancel" }).click();
  await expect(bar).toBeHidden();
  expect(await size(page)).toEqual([321, 241]);
  await drag(page, [0, 0], [200, 100]);
  await bar.getByRole("button", { name: "Apply crop" }).click();
  await expect.poll(() => size(page)).toEqual([200, 100]);
  await expect(bar).toBeHidden();
});

test("selection: the bar crops to it or deselects", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("m");
  const bar = page.getByRole("toolbar", { name: "Crop" });
  await drag(page, [150, 120], [10, 10]); // ends at the top-left corner
  await expect(bar).toBeVisible();
  await bar.getByRole("button", { name: "Deselect" }).click();
  await expect(bar).toBeHidden();
  await drag(page, [10, 10], [150, 120]);
  await bar.getByRole("button", { name: "Crop to selection" }).click();
  await expect.poll(() => size(page)).toEqual([140, 110]);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => size(page)).toEqual([321, 241]);
});

// ------------------------------------------------------------------ layers list

/** Layer names top first, as the list shows them. */
const rowNames = (page: Page) => page.locator(".layers li .lname").allTextContents();
const selected = (page: Page) =>
  page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { selected?: number[] };
    return [...new Set([...(d.selected ?? []), d.active])].filter((i) => i < d.layers.length).sort((a, b) => a - b);
  });

async function fourLayers(page: Page) {
  await openEditor(page);
  await page.getByRole("button", { name: "Add" }).click();
  await page.getByRole("button", { name: "Add" }).click();
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4", "Layer 3", "img1", "img0"]);
}

const row = (page: Page, i: number) => page.locator(".layers li").nth(i);

test("layers: Ctrl+click and Shift+click select several", async ({ page }) => {
  await fourLayers(page);
  await row(page, 0).locator(".lname").click();
  await row(page, 2).locator(".lname").click({ modifiers: ["Control"] });
  await expect.poll(() => selected(page)).toEqual([1, 3]);
  await expect(page.getByRole("button", { name: "Merge layers" })).toBeVisible();
  await row(page, 1).locator(".lname").click();
  await row(page, 3).locator(".lname").click({ modifiers: ["Shift"] });
  await expect.poll(() => selected(page)).toEqual([0, 1, 2]);
  await expect(page.locator(".layers li.selected")).toHaveCount(3);
  await page.getByRole("button", { name: "Delete" }).click();
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4"]);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4", "Layer 3", "img1", "img0"]);
  await expect.poll(() => selected(page)).toEqual([0, 1, 2]);
});

test("layers: arrows on a row move it", async ({ page }) => {
  await fourLayers(page);
  await expect(page.getByRole("button", { name: "Move img0 down" })).toBeDisabled();
  await page.getByRole("button", { name: "Move img0 up" }).click();
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4", "Layer 3", "img0", "img1"]);
  await expect(page.locator(".layers li.active .lname")).toHaveText("img0");
});

test("layers: drag rows to reorder", async ({ page }) => {
  await fourLayers(page);
  await row(page, 2).locator(".lname").click();
  await row(page, 3).locator(".lname").click({ modifiers: ["Control"] });
  const from = await row(page, 3).locator(".lname").boundingBox();
  const to = await row(page, 0).boundingBox();
  if (!from || !to) throw new Error("no layout");
  await page.mouse.move(from.x + 10, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 10, to.y + 4, { steps: 8 });
  await expect(row(page, 0)).toHaveClass(/drop-above/);
  await page.mouse.up();
  await expect.poll(() => rowNames(page)).toEqual(["img1", "img0", "Layer 4", "Layer 3"]);
  await expect(page.locator(".layers li.selected")).toHaveCount(2);
  await page.keyboard.press("Control+z");
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4", "Layer 3", "img1", "img0"]);
});

test("layers: a press released outside the list doesn't turn into a drag later", async ({ page }) => {
  await fourLayers(page);
  const from = await row(page, 3).locator(".lname").boundingBox();
  const list = await page.locator(".layers").boundingBox();
  if (!from || !list) throw new Error("no layout");
  await page.mouse.move(from.x + 10, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(list.x - 40, from.y + from.height / 2, { steps: 4 }); // sideways, out of the list
  await page.mouse.up();
  await page.mouse.move(from.x + 10, from.y + from.height / 2, { steps: 2 }); // hover back…
  await page.mouse.move(from.x + 10, list.y + 6, { steps: 6 }); // …and up the list, no button held
  await expect(page.locator(".layers.dragging")).toHaveCount(0);
  await page.mouse.click(from.x + 10, list.y + 6);
  await expect.poll(() => rowNames(page)).toEqual(["Layer 4", "Layer 3", "img1", "img0"]);
});

test("layers: right-click menu", async ({ page }) => {
  await fourLayers(page);
  await row(page, 0).locator(".lname").click();
  await row(page, 1).locator(".lname").click({ modifiers: ["Control"] });
  await row(page, 0).click({ button: "right" });
  const menu = page.getByRole("menu", { name: "Layer actions" });
  await expect(menu.getByRole("menuitem", { name: "Move up", exact: true })).toBeDisabled();
  await expect(menu.getByRole("menuitem", { name: "Rename…" })).toHaveCount(0);
  await menu.getByRole("menuitem", { name: "Hide 2 layers" }).click();
  await expect(menu).toHaveCount(0);
  await expect(row(page, 0).locator("input[type=checkbox]")).not.toBeChecked();
  await row(page, 1).click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Move to bottom" }).click();
  await expect.poll(() => rowNames(page)).toEqual(["img1", "img0", "Layer 4", "Layer 3"]);
  // right-clicking a row outside the selection selects it; Esc closes the menu
  await row(page, 0).click({ button: "right" });
  await expect.poll(() => selected(page)).toEqual([3]);
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await row(page, 0).click({ button: "right" });
  await menu.getByRole("menuitem", { name: "Rename…" }).click();
  await page.locator(".layers input.rename").fill("Top");
  await page.keyboard.press("Enter");
  await expect(row(page, 0).locator(".lname")).toHaveText("Top");
});

// ------------------------------------------------------------------ clipboard, Move tool, shortcuts

type Hook = EditorHook & {
  missingActions(): string[];
  viewCenter(): [number, number];
  doc: EditorHook["doc"] & { selected?: number[] };
};
const hook = (page: Page) => page.evaluate(() => {
  const e = window.__editor as unknown as Hook;
  return { names: e.doc.layers.map((l) => l.name), pos: e.doc.layers.map((l) => [l.x, l.y]), sizes: e.doc.layers.map((l) => [l.width, l.height]), active: e.doc.active };
});

test("Ctrl+C / Ctrl+V paste in place through the system clipboard; Ctrl+X cuts", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openEditor(page);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("Control+c");
  await expect(page.locator(".editor-status")).toContainText("Copied");
  // the picture really is on the system clipboard (other programs can paste it)
  await expect.poll(() => page.evaluate(async () => (await navigator.clipboard.read()).some((it) => it.types.includes("image/png")))).toBe(true);
  const before = await hook(page);
  await page.keyboard.press("Control+v");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1", "Pasted 3"]);
  const after = await hook(page);
  expect(after.pos[2]).toEqual(before.pos[1]); // back in the same place
  expect(after.sizes[2]).toEqual(before.sizes[1]);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1"]);
  await page.keyboard.press("Control+x");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).pixel(1, 1, 1)[3])).toBe(0);
});

test("pasting a picture from another program centres it in view", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openEditor(page);
  await page.evaluate(async () => {
    const c = new OffscreenCanvas(10, 6);
    const g = c.getContext("2d") as OffscreenCanvasRenderingContext2D;
    g.fillStyle = "#00ff00";
    g.fillRect(0, 0, 10, 6);
    await navigator.clipboard.write([new ClipboardItem({ "image/png": await c.convertToBlob({ type: "image/png" }) })]);
  });
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("Control+v");
  await expect.poll(async () => (await hook(page)).names.length).toBe(3);
  const [cx, cy] = await page.evaluate(() => (window.__editor as unknown as Hook).viewCenter());
  const h = await hook(page);
  expect(h.sizes[2]).toEqual([10, 6]);
  expect(h.pos[2]).toEqual([Math.round(cx - 5), Math.round(cy - 3)]);
  expect(await page.evaluate(([x, y]) => (window.__editor as EditorHook).pixel(x, y, 2), [Math.round(cx), Math.round(cy)])).toEqual([0, 255, 0, 255]);
  // text on the clipboard: nothing to paste
  await page.evaluate(() => navigator.clipboard.writeText("hello"));
  await page.keyboard.press("Control+v");
  await expect(page.locator(".editor-status")).toContainText("no picture");
});

test("Move tool: drag, Shift+arrow and arrow nudge, undo", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("v");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("move");
  const start = (await hook(page)).pos[1];
  await drag(page, [5, 5], [25, 15]);
  await expect.poll(async () => (await hook(page)).pos[1]).toEqual([start[0] + 20, start[1] + 10]);
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("Shift+ArrowDown");
  await expect.poll(async () => (await hook(page)).pos[1]).toEqual([start[0] + 19, start[1] + 20]);
  for (let i = 0; i < 3; i++) await page.keyboard.press("Control+z");
  await expect.poll(async () => (await hook(page)).pos[1]).toEqual(start);
});

test("layer shortcuts, Delete in the layers list, and the ? list", async ({ page }) => {
  await openEditor(page);
  expect(await page.evaluate(() => (window.__editor as unknown as Hook).missingActions())).toEqual([]);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("Control+Alt+n");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1", "Layer 3"]);
  await page.keyboard.press("Control+j");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1", "Layer 3", "Layer 3 copy"]);
  await page.keyboard.press("Control+[");
  await expect.poll(async () => (await hook(page)).names[2]).toBe("Layer 3 copy");
  await page.keyboard.press("Control+Shift+]");
  await expect.poll(async () => (await hook(page)).names[3]).toBe("Layer 3 copy");
  await page.keyboard.press("Control+e");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1", "Layer 3"]);
  await page.keyboard.press("Control+Alt+a");
  await expect(page.locator(".layers li.selected")).toHaveCount(3);
  await page.locator(".layers li .lname").first().click(); // select just the top layer (focuses the list)
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await hook(page)).names).toEqual(["img0", "img1"]);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("?");
  const dlg = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(dlg).toBeVisible();
  await expect(dlg).toContainText("Ctrl+Alt+N");
  await expect(dlg).toContainText("Paste as a new layer");
  await page.keyboard.press("Escape");
  await expect(dlg).toHaveCount(0);
});

test("Ctrl+Plus / Ctrl+Minus zoom the picture, not the page", async ({ page }) => {
  await openEditor(page);
  await page.getByRole("button", { name: "100 %" }).click();
  await page.locator(".canvas-host canvas").focus();
  const zoom = () => page.evaluate(() => {
    const a = (window.__editor as EditorHook).imageToPage(0, 0);
    const b = (window.__editor as EditorHook).imageToPage(100, 0);
    return b.x - a.x;
  });
  const z0 = await zoom();
  await page.keyboard.press("Control+=");
  await expect.poll(zoom).toBeGreaterThan(z0);
  await page.keyboard.press("Control+-");
  await page.keyboard.press("Control+-");
  await expect.poll(zoom).toBeLessThan(z0);
});

// ------------------------------------------------------------------ Adjust menu

/** Click an item in the tool rail by (the start of) its name. */
const rail = async (page: Page, item: string) => {
  await page.locator(".tool-rail").getByRole("button", { name: new RegExp(`^${item}`) }).click();
};
const adjustMenu = rail;
const layerPx = (page: Page, x: number, y: number) => page.evaluate(([a, b]) => (window.__editor as EditorHook).pixel(a, b), [x, y]);

test("Quick fix: live preview, hold to see the original, cancel and apply", async ({ page }) => {
  await openEditor(page);
  const before = await layerPx(page, 5, 5);
  await adjustMenu(page, "Quick fix");
  const dlg = page.locator("dialog.quick-fix");
  await dlg.getByLabel("Saturation").fill("-100");
  const previewing = () => page.evaluate(() => (window.__editor as unknown as { previewing(): boolean }).previewing());
  await expect.poll(previewing).toBe(true);
  await dlg.getByRole("button", { name: "Hold to see the original" }).hover();
  await page.mouse.down();
  await expect.poll(previewing).toBe(false);
  await page.mouse.up();
  await expect.poll(previewing).toBe(true);
  await dlg.getByRole("button", { name: "Cancel" }).click();
  await expect.poll(previewing).toBe(false);
  expect(await layerPx(page, 5, 5)).toEqual(before);
  await adjustMenu(page, "Quick fix");
  await dlg.getByLabel("Saturation").fill("-100");
  await dlg.getByRole("button", { name: "Apply" }).click();
  await expect.poll(async () => {
    const [r, g, b] = await layerPx(page, 5, 5);
    return r === g && g === b;
  }).toBe(true);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => layerPx(page, 5, 5)).toEqual(before);
});

test("Levels and Curves windows change the picture", async ({ page }) => {
  await openEditor(page);
  const before = await layerPx(page, 5, 5);
  await adjustMenu(page, "Levels");
  const lv = page.locator("dialog.levels");
  await expect(lv.locator("canvas.histogram")).toBeVisible();
  await lv.getByLabel("Output black").fill("120");
  await lv.getByRole("button", { name: "Apply" }).click();
  await expect.poll(async () => (await layerPx(page, 5, 5)).slice(0, 3).every((v) => v >= 120)).toBe(true);
  await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => layerPx(page, 5, 5)).toEqual(before);
  // curves: drag the middle of the line up → brighter
  await adjustMenu(page, "Curves");
  const cv = page.locator("dialog.curves");
  const box = await cv.locator("canvas.curve-editor").boundingBox();
  if (!box) throw new Error("no layout");
  const PAD = 8;
  const side = box.width - 2 * PAD;
  const at = (x: number, y: number) => [box.x + PAD + (side * x) / 255, box.y + PAD + side * (1 - y / 255)] as const;
  await page.mouse.move(...at(128, 128));
  await page.mouse.down();
  await page.mouse.move(...at(128, 220), { steps: 5 });
  await page.mouse.up();
  await cv.getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(true);
  const after = await layerPx(page, 5, 5);
  expect(after.slice(0, 3).reduce((a, b) => a + b, 0)).toBeGreaterThan(before.slice(0, 3).reduce((a, b) => a + b, 0));
});

test("Curves keeps its end points; Levels treats an empty Output white as 255", async ({ page }) => {
  await openEditor(page);
  const before = await layerPx(page, 5, 5);
  await adjustMenu(page, "Curves");
  const cv = page.locator("dialog.curves");
  const box = await cv.locator("canvas.curve-editor").boundingBox();
  if (!box) throw new Error("no layout");
  const PAD = 8;
  const side = box.width - 2 * PAD;
  const at = (x: number, y: number) => [box.x + PAD + (side * x) / 255, box.y + PAD + side * (1 - y / 255)] as const;
  await page.mouse.click(...at(128, 128)); // a third point
  await page.mouse.move(...at(255, 255));
  await page.mouse.down();
  await page.mouse.move(box.x + box.width + 120, box.y - 120, { steps: 5 }); // drag the white end point off
  await page.mouse.up();
  await page.mouse.click(box.x + box.width / 2, box.y + 2, { button: "right" }); // right-click: no point
  await cv.getByRole("button", { name: "Apply" }).click();
  // the white end point is still there: bright colours stay bright (without it they'd drop to ~128)
  expect(Math.max(...before.slice(0, 3))).toBeGreaterThan(140);
  const curved = await layerPx(page, 5, 5);
  expect(Math.max(...curved.slice(0, 3).map((v, i) => Math.abs(v - before[i])))).toBeLessThanOrEqual(3);
  if (await page.evaluate(() => (window.__editor as EditorHook).history.canUndo)) await page.getByRole("button", { name: "Undo" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(false);
  await adjustMenu(page, "Levels");
  const lv = page.locator("dialog.levels");
  await lv.getByLabel("Output white").fill("");
  await lv.getByLabel("Output black").fill("10");
  await lv.getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(true);
  const after = await layerPx(page, 5, 5);
  expect(Math.max(...after.slice(0, 3))).toBeGreaterThan(100); // not turned black (that would be ≤ 10)
  expect(after.slice(0, 3).some((v, i) => v !== before[i])).toBe(true);
});

// ------------------------------------------------------------------ Filters menu

const filterMenu = rail; // "^Blur" — not "Background blur"

test("Filters: blur and sharpen apply as one undo step each", async ({ page }) => {
  await openEditor(page);
  await filterMenu(page, "Blur");
  // the starting radius (2 px) is previewed before any slider is touched
  await expect.poll(() => page.evaluate(() => (window.__editor as unknown as { previewing(): boolean }).previewing())).toBe(true);
  await page.locator("dialog.blur").getByLabel("Radius").fill("80");
  await page.locator("dialog.blur").getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(true);
  await page.getByRole("button", { name: "Undo" }).click();
  await filterMenu(page, "Sharpen");
  await page.locator("dialog.sharpen").getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).history.canUndo)).toBe(true);
});

test("Looks: tiles preview each look; choosing one and applying it", async ({ page }) => {
  await openEditor(page);
  await filterMenu(page, "Looks");
  const dlg = page.locator("dialog.looks");
  await expect(dlg.getByRole("radio")).toHaveCount(11);
  await dlg.getByRole("radio", { name: "Black & white" }).click();
  await expect(dlg.getByRole("radio", { name: "Black & white" })).toHaveAttribute("aria-checked", "true");
  await dlg.getByRole("button", { name: "Apply" }).click();
  await expect.poll(async () => {
    const [r, g, b] = await layerPx(page, 5, 5);
    return r === g && g === b;
  }).toBe(true);
});

// ------------------------------------------------------------------ selection tools

const selState = (page: Page) =>
  page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { selection: { x0: number; y0: number; x1: number; y1: number } | null; selectionMask?: Uint8Array | null };
    return d.selection ? { box: [d.selection.x0, d.selection.y0, d.selection.x1, d.selection.y1], masked: !!d.selectionMask } : null;
  });

test("selection: letters cycle tools; Shift adds, Alt subtracts", async ({ page }) => {
  await openEditor(page);
  const tool = () => page.evaluate(() => (window.__editor as EditorHook).toolId);
  for (const [key, ids] of [["m", ["select", "ellipse_select", "select"]], ["q", ["lasso", "polygon_lasso"]], ["w", ["wand"]]] as const) {
    for (const want of ids) {
      await page.keyboard.press(key);
      await expect.poll(tool).toBe(want);
    }
  }
  await page.keyboard.press("m");
  await drag(page, [10, 10], [60, 60]);
  await page.keyboard.down("Shift");
  await drag(page, [100, 10], [150, 60]);
  await page.keyboard.up("Shift");
  await expect.poll(() => selState(page)).toEqual({ box: [10, 10, 150, 60], masked: true });
  await page.keyboard.down("Alt");
  await drag(page, [100, 10], [150, 60]);
  await page.keyboard.up("Alt");
  await expect.poll(() => selState(page)).toEqual({ box: [10, 10, 60, 60], masked: false });
  await expect(page.getByRole("toolbar", { name: "Crop" })).toContainText("Crop to selection");
});

test("selection: polygon lasso finishes on double-click and Enter; Select menu inverts and feathers", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("q");
  await page.keyboard.press("q");
  for (const [x, y] of [[20, 20], [120, 20], [70, 100]] as const) {
    const p = await at(page, x, y);
    await page.mouse.click(p.x, p.y);
  }
  expect(await selState(page)).toBeNull();
  const last = await at(page, 70, 100);
  await page.mouse.dblclick(last.x, last.y);
  await expect.poll(async () => (await selState(page))?.masked).toBe(true);
  await page.getByRole("button", { name: "Undo" }).click();
  for (const [x, y] of [[20, 20], [120, 20], [70, 100]] as const) {
    const p = await at(page, x, y);
    await page.mouse.click(p.x, p.y);
  }
  await page.keyboard.press("Enter");
  await expect.poll(async () => (await selState(page))?.masked).toBe(true);
  await rail(page, "Invert selection");
  await expect.poll(async () => (await selState(page))?.box[0]).toBe(0);
  await page.keyboard.press("Control+Alt+d");
  await page.locator("dialog.feather").getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel)).toBe("Feather selection");
});

test("selection: magic wand picks a colour", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("w");
  const p = await at(page, 5, 5);
  await page.mouse.click(p.x, p.y);
  await expect.poll(() => selState(page)).not.toBeNull();
});

// ------------------------------------------------------------------ masks, gradient, background blur

test("layer mask from the layer menu, mask brush (K), G cycles fill / gradient", async ({ page }) => {
  await openEditor(page);
  await page.locator(".layers li").first().click({ button: "right" });
  await page.getByRole("menu", { name: "Layer actions" }).getByRole("menuitem", { name: "Add layer mask" }).click();
  await expect(page.locator(".layers li").first().locator("canvas.mask-thumb")).toHaveCount(1);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("k");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("mask_brush");
  const p = await at(page, 5, 5);
  await page.mouse.click(p.x, p.y);
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel)).toBe("Mask brush");
  for (const want of ["fill", "gradient"]) {
    await page.keyboard.press("g");
    await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe(want);
  }
  await drag(page, [0, 0], [150, 0]);
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel)).toBe("Gradient");
});

test("background blur asks for a subject, then blurs around it", async ({ page }) => {
  await openEditor(page);
  await rail(page, "Background blur");
  await expect(page.locator(".editor-status")).toContainText("Select the subject first");
  await page.keyboard.press("m");
  await drag(page, [10, 10], [80, 80]);
  await rail(page, "Background blur");
  await page.locator("dialog.background-blur").getByRole("button", { name: "Apply" }).click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel)).toBe("Background blur");
});

// ------------------------------------------------------------------ text v2

test("text: box style, click the text again to edit it, layer menu Edit text…", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("t");
  await showToolOptions(page);
  await page.locator(".tool-settings").getByLabel("Box behind (back colour)").check();
  await page.locator(".tool-options").getByRole("button", { name: /^Minimise/ }).click(); // out of the way
  const p = await at(page, 20, 20);
  await page.mouse.click(p.x, p.y);
  await page.locator("textarea.text-box").fill("Hello");
  await page.keyboard.press("Control+Enter");
  const top = () => page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { layers: { text?: { text: string; box: boolean } | null }[] };
    const l = d.layers[d.layers.length - 1];
    return { n: d.layers.length, text: l.text?.text ?? null, box: l.text?.box ?? null };
  });
  await expect.poll(top).toEqual({ n: 3, text: "Hello", box: true });
  const q = await at(page, 30, 30);
  await page.mouse.click(q.x, q.y); // inside the text layer
  await expect(page.locator("textarea.text-box")).toHaveValue("Hello");
  await page.locator("textarea.text-box").fill("Hello there");
  await page.keyboard.press("Control+Enter");
  await expect.poll(top).toEqual({ n: 3, text: "Hello there", box: true });
  await page.keyboard.press("b");
  await page.locator(".layers li").first().click({ button: "right" });
  await page.getByRole("menu", { name: "Layer actions" }).getByRole("menuitem", { name: "Edit text…" }).click();
  await expect(page.locator("textarea.text-box")).toHaveValue("Hello there");
  await page.keyboard.press("Escape");
});

// ------------------------------------------------------------------ annotation

test("annotation: L cycles line / arrow, highlighter (H), stickers window adds a layer", async ({ page }) => {
  await openEditor(page);
  const undoLabel = () => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel);
  const toolId = () => page.evaluate(() => (window.__editor as EditorHook).toolId);
  await page.locator(".canvas-host canvas").focus();
  for (const want of ["line", "arrow"]) {
    await page.keyboard.press("l");
    await expect.poll(toolId).toBe(want);
  }
  await drag(page, [10, 10], [120, 60]);
  await expect.poll(undoLabel).toBe("Arrow");
  await page.keyboard.press("h");
  await expect.poll(toolId).toBe("highlighter");
  await drag(page, [10, 80], [120, 80]);
  await expect.poll(undoLabel).toBe("Highlighter");
  const layers = await page.locator(".layers li").count();
  await rail(page, "Stickers");
  const dlg = page.locator("dialog.stickers");
  await expect(dlg.getByRole("radio")).toHaveCount(11);
  await dlg.getByRole("radio", { name: "Star" }).click();
  await dlg.getByRole("button", { name: "Add" }).click();
  await expect(page.locator(".layers li")).toHaveCount(layers + 1);
  await expect.poll(undoLabel).toBe("Sticker");
  // drawn by the browser canvas: the star's middle is its yellow fill
  const mid = await page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as unknown as { layers: { width: number; height: number; data: Uint8ClampedArray }[]; active: number };
    const l = d.layers[d.active];
    const i = (Math.floor(l.height * 0.53) * l.width + Math.floor(l.width / 2)) * 4;
    return Array.from(l.data.slice(i, i + 4));
  });
  expect(mid).toEqual([255, 200, 61, 255]);
});

test("pasteboard: a layer dragged past the edge stays visible around the picture, isn't saved", async ({ page }) => {
  await openEditor(page);
  const size = await page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height]);
  const viewPx = async (ix: number, iy: number) => {
    const p = await at(page, ix, iy);
    return page.evaluate(([x, y]) => {
      const c = document.querySelector(".canvas-host canvas.view") as HTMLCanvasElement;
      const r = c.getBoundingClientRect();
      const k = c.width / r.width;
      return Array.from((c.getContext("2d") as CanvasRenderingContext2D).getImageData(Math.round((x - r.left) * k), Math.round((y - r.top) * k), 1, 1).data);
    }, [p.x, p.y]);
  };
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("v");
  // just past the right edge: the picture is fitted to the view (≈ 2.4× zoom), so a point much
  // further out would fall outside the visible canvas element itself
  const outside = [size[0] + 3, 10] as [number, number];
  const before = await viewPx(...outside);
  await drag(page, [5, 5], [Math.round(size[0] * 0.4) + 5, 5]);
  await expect.poll(() => viewPx(...outside)).not.toEqual(before);
  const png = await saveAs(page, "pasteboard", "png");
  expect(png.readUInt32BE(16)).toBe(size[0]); // the saved picture is the canvas only
});

test("clone stamp asks for a source, then copies; content-aware fill from the Select menu", async ({ page }) => {
  await openEditor(page);
  const undoLabel = () => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("s");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("clone");
  const a = await at(page, 30, 30);
  await page.mouse.click(a.x, a.y);
  await expect(page.locator(".editor")).toContainText("Alt+click the place to copy from first");
  const src = await at(page, 10, 10);
  await page.keyboard.down("Alt");
  await page.mouse.click(src.x, src.y);
  await page.keyboard.up("Alt");
  await drag(page, [40, 40], [60, 40]);
  await expect.poll(undoLabel).toBe("Clone stamp");
  await page.keyboard.press("m");
  await drag(page, [20, 20], [40, 40]);
  await rail(page, "Content-aware fill");
  await expect.poll(undoLabel).toBe("Content-aware fill");
});

test("Remove background hides the background with a layer mask (model faked)", async ({ page }) => {
  await openEditor(page);
  await page.evaluate(() => (window.__editor as EditorHook & { fakeSegmenter(): void }).fakeSegmenter());
  await rail(page, "Remove background");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel)).toBe("Remove background");
  await expect(page.locator(".layers li.active canvas.mask-thumb, .layers li canvas.mask-thumb").first()).toBeVisible();
  const corner = await pixel(page, 0, 0);
  expect(corner[3]).toBeGreaterThan(0); // the pixels themselves are kept
});

test("polygon lasso: Esc drops unfinished corners, so Enter then selects nothing", async ({ page }) => {
  await openEditor(page);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("q");
  await page.keyboard.press("q");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("polygon_lasso");
  for (const [x, y] of [[5, 5], [60, 5], [30, 40]] as [number, number][]) {
    const p = await at(page, x, y);
    await page.mouse.click(p.x, p.y);
  }
  await page.keyboard.press("Escape");
  await page.keyboard.press("Enter");
  expect(await selState(page)).toBeNull();
});

test("quick fixes: the rail's menu closes on an outside click; dragging layers selects no text; Alt+click eye isolates; mask brush adds a mask; picker loupe", async ({ page }) => {
  await openEditor(page);
  const undoLabel = () => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel);
  // the rail's "Rotate & flip" list closes on a click elsewhere
  await rail(page, "Rotate & flip");
  await expect(page.getByRole("menu", { name: "Rotate & flip" })).toBeVisible();
  await page.locator(".editor-status").click();
  await expect(page.getByRole("menu", { name: "Rotate & flip" })).toHaveCount(0);
  // dragging a layer row doesn't highlight text
  const rows = page.locator(".layers li");
  const a = await rows.nth(0).boundingBox();
  const b = await rows.nth(1).boundingBox();
  if (!a || !b) throw new Error("no rows");
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2, b.y + b.height + 40, { steps: 8 });
  expect(await page.evaluate(() => String(window.getSelection() ?? ""))).toBe("");
  await page.mouse.up();
  // Alt+click an eye: only that layer stays visible; again: all
  const eye = rows.nth(0).locator('input[type="checkbox"]');
  const visible = () => page.evaluate(() => (window.__editor as EditorHook).doc.layers.map((l) => (l as unknown as { visible: boolean }).visible));
  await eye.click({ modifiers: ["Alt"] });
  await expect.poll(undoLabel).toBe("Show only this layer");
  expect((await visible()).filter(Boolean).length).toBe(1);
  await eye.click({ modifiers: ["Alt"] });
  await expect.poll(visible).toEqual([true, true]);
  // mask brush on a layer without a mask
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("k");
  const p = await at(page, 10, 10);
  await page.mouse.click(p.x, p.y);
  await expect.poll(undoLabel).toBe("Mask brush");
  await expect(page.locator(".layers li canvas.mask-thumb")).toHaveCount(1);
  // the picker's loupe appears while held
  await page.keyboard.press("i");
  const q = await at(page, 60, 60);
  const shot = async () => (await page.locator(".canvas-host canvas.view").screenshot()).length;
  const plain = await shot();
  await page.mouse.move(q.x, q.y);
  await page.mouse.down();
  await page.mouse.move(q.x + 1, q.y + 1);
  expect(await shot()).not.toBe(plain);
  await page.mouse.up();
});

test("selection: drag inside to move it (move cursor), arrows nudge, undo", async ({ page }) => {
  await openEditor(page);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("m");
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("select");
  await drag(page, [5, 5], [25, 20]);
  await expect.poll(() => selState(page)).toEqual({ box: [5, 5, 25, 20], masked: false });
  const inside = await at(page, 10, 10);
  await page.mouse.move(inside.x, inside.y);
  expect(await page.locator(".canvas-host canvas.view").evaluate((c) => (c as HTMLElement).style.cursor)).toBe("move");
  await drag(page, [10, 10], [20, 15]);
  await expect.poll(() => selState(page)).toEqual({ box: [15, 10, 35, 25], masked: false });
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Shift+ArrowDown");
  await expect.poll(() => selState(page)).toEqual({ box: [16, 20, 36, 35], masked: false });
  await page.keyboard.press("Control+z"); // both arrow presses are one step
  await expect.poll(() => selState(page)).toEqual({ box: [15, 10, 35, 25], masked: false });
  await page.keyboard.press("Control+z");
  await expect.poll(() => selState(page)).toEqual({ box: [5, 5, 25, 20], masked: false });
});

test("tool rail: groups with names, narrows to icons (remembered), tools and actions, rotate list", async ({ page }) => {
  await openEditor(page);
  const r = page.locator(".tool-rail");
  await expect(r.locator(".rail-group").first()).toHaveText("Select");
  const lasso = r.getByRole("button", { name: "Lasso", exact: true });
  await expect(lasso.locator(".rail-label")).toBeVisible();
  await lasso.click();
  await expect.poll(() => page.evaluate(() => (window.__editor as EditorHook).toolId)).toBe("lasso");
  await expect(lasso).toHaveAttribute("aria-pressed", "true");
  await r.getByRole("button", { name: "Icons only" }).click();
  await expect(r).toHaveClass(/narrow/);
  await expect(lasso.locator(".rail-label")).toBeHidden();
  await page.reload();
  await page.locator('input[type="file"]').first().setInputFiles([fx("img0.png"), fx("img1.png")]);
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.locator(".tool-rail")).toHaveClass(/narrow/);
  await page.locator(".tool-rail").getByRole("button", { name: "Show tool names" }).click();
  await expect(page.locator(".tool-rail")).not.toHaveClass(/narrow/);
  const size = () => page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height]);
  const before = await size();
  await rail(page, "Rotate & flip");
  await page.getByRole("menu", { name: "Rotate & flip" }).getByRole("menuitem", { name: "Rotate 90° right", exact: true }).click();
  await expect.poll(size).toEqual([before[1], before[0]]);
  await rail(page, "Select all");
  await expect.poll(() => selState(page)).toEqual({ box: [0, 0, before[1], before[0]], masked: false });
  await expect(page.locator("details.menu")).toHaveCount(0); // the top-bar menus are gone
});

test("tool rail: the rotate list toggles on its button, Esc closes only the list, focus goes back to the canvas", async ({ page }) => {
  await openEditor(page);
  const list = page.getByRole("menu", { name: "Rotate & flip" });
  await rail(page, "Rotate & flip");
  await expect(list).toBeVisible();
  await rail(page, "Rotate & flip"); // a second click closes it
  await expect(list).toHaveCount(0);
  await page.locator(".canvas-host canvas").focus();
  await page.keyboard.press("c");
  await drag(page, [5, 5], [40, 30]); // a pending crop
  await expect(page.locator(".crop-bar")).toBeVisible();
  await rail(page, "Rotate & flip");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Escape");
  await expect(list).toHaveCount(0);
  await expect(page.locator(".crop-bar")).toBeVisible(); // the crop is still there
  await page.locator(".tool-rail").getByRole("button", { name: "Move", exact: true }).click();
  expect(await page.evaluate(() => document.activeElement?.classList.contains("view"))).toBe(true);
});

// ------------------------------------------------------------------ floating windows

test("windows: several open at once, the icon minimises and restores, dragged and remembered, × cancels", async ({ page }) => {
  await openEditor(page);
  const r = page.locator(".tool-rail");
  const quick = page.locator("dialog.quick-fix");
  const blur = page.locator("dialog.blur");
  await rail(page, "Quick fix");
  await rail(page, "Blur");
  await expect(quick).toBeVisible();
  await expect(blur).toBeVisible(); // both at once, and the picture stays usable behind them
  await expect(r.getByRole("button", { name: /^Blur/ })).toHaveAttribute("data-panel", "open");
  await rail(page, "Blur"); // again: minimised into its icon
  await expect(blur).toBeHidden();
  await expect(r.getByRole("button", { name: /^Blur/ })).toHaveAttribute("data-panel", "minimised");
  await rail(page, "Blur"); // and back
  await expect(blur).toBeVisible();
  // drag the Quick fix window by its title bar
  const bar = quick.locator(".panel-title");
  const b = (await bar.boundingBox())!;
  await page.mouse.move(b.x + 40, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + 140, b.y + b.height / 2 + 60, { steps: 5 });
  await page.mouse.up();
  const moved = (await quick.boundingBox())!;
  expect(Math.round(moved.x - (b.x - 1))).toBeGreaterThan(80);
  // × closes it like Cancel: no preview left, nothing applied
  await quick.getByRole("button", { name: "Close" }).click();
  await expect(quick).toHaveCount(0);
  await blur.getByRole("button", { name: "Close" }).click(); // its own preview goes too
  expect(await page.evaluate(() => (window.__editor as EditorHook & { previewing(): boolean }).previewing())).toBe(false);
  await rail(page, "Quick fix"); // opens where it was left
  const again = (await page.locator("dialog.quick-fix").boundingBox())!;
  expect(Math.abs(again.x - moved.x)).toBeLessThan(3);
  expect(Math.abs(again.y - moved.y)).toBeLessThan(3);
  await page.locator("dialog.quick-fix").press("Escape");
  await expect(page.locator("dialog.quick-fix")).toHaveCount(0);
});

test("windows: the tool options float, follow the tool, and the chosen tool's icon minimises them", async ({ page }) => {
  await openEditor(page);
  const tool = page.locator(".tool-options");
  await expect(tool).toBeHidden(); // openEditor tucked it away
  await page.locator(".tool-rail").getByRole("button", { name: "Eraser", exact: true }).click(); // another tool: back
  await expect(tool).toBeVisible();
  await expect(tool.locator(".panel-title-text")).toHaveText("Eraser");
  await expect(page.locator(".editor-side .tool-settings")).toHaveCount(0); // the side holds only colours and layers
  await page.locator(".tool-rail").getByRole("button", { name: "Eraser", exact: true }).click(); // the chosen tool again
  await expect(tool).toBeHidden();
  await expect(tool.getByRole("button", { name: "Close" })).toHaveCount(0); // only minimises
});

test("windows: Enter in a field means OK; Enter on a focused Close button just closes; size windows close when the size changes", async ({ page }) => {
  await openEditor(page);
  const size = () => page.evaluate(() => [(window.__editor as EditorHook).doc.width, (window.__editor as EditorHook).doc.height]);
  const [w, h] = await size();
  await rail(page, "Quick fix");
  const quick = page.locator("dialog.quick-fix");
  await quick.getByRole("button", { name: "Close" }).focus();
  await page.keyboard.press("Enter");
  await expect(quick).toHaveCount(0);
  expect(await page.evaluate(() => (window.__editor as EditorHook & { history: { canUndo: boolean } }).history.canUndo)).toBe(false);
  await rail(page, "Resize image");
  const resize = page.locator("dialog.resize");
  await resize.getByLabel("Width").fill(String(Math.round(w / 2)));
  await resize.getByLabel("Width").press("Enter");
  await expect(resize).toHaveCount(0);
  await expect.poll(size).not.toEqual([w, h]);
  const smaller = await size();
  await rail(page, "Canvas size");
  await expect(page.locator("dialog.canvas-size")).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click(); // the size changes back underneath it
  await expect.poll(size).toEqual([w, h]);
  expect(smaller[0]).toBe(Math.round(w / 2));
  await expect(page.locator("dialog.canvas-size")).toHaveCount(0);
});

// ------------------------------------------------------------------ live text layers

test("text: a layer from the first click, shown live while typing, movable by its grip, one undo step", async ({ page }) => {
  await openEditor(page);
  const layers = () => page.evaluate(() => (window.__editor as EditorHook).doc.layers.length);
  const top = () => page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { layers: { x: number; width: number; text?: { text: string } | null }[] };
    const l = d.layers[d.layers.length - 1];
    return { text: l.text?.text ?? null, x: l.x, width: l.width };
  });
  const n = await layers();
  await page.keyboard.press("t");
  const p = await at(page, 20, 20);
  await page.mouse.click(p.x, p.y);
  await expect.poll(layers).toBe(n + 1); // before anything is typed
  await expect(page.locator(".layers li")).toHaveCount(n + 1);
  await page.locator("textarea.text-box").pressSequentially("Hi");
  await expect.poll(async () => (await top()).text).toBe("Hi"); // drawn while typing
  expect((await top()).width).toBeGreaterThan(1);
  const x0 = (await top()).x;
  const g = (await page.locator(".text-grip").boundingBox())!;
  await page.mouse.move(g.x + g.width / 2, g.y + g.height / 2);
  await page.mouse.down();
  await page.mouse.move(g.x + g.width / 2 + 30, g.y + g.height / 2, { steps: 4 });
  await page.mouse.up();
  await expect.poll(async () => (await top()).x).toBeGreaterThan(x0); // moved while typing
  await expect(page.locator("textarea.text-box")).toBeFocused(); // and still typing
  await page.keyboard.press("Control+Enter");
  await expect(page.locator("textarea.text-box")).toHaveCount(0);
  expect((await top()).text).toBe("Hi");
  await page.keyboard.press("Control+z"); // the whole typing is one step
  await expect.poll(layers).toBe(n);
});

test("text: the style controls can be used while typing; a click elsewhere finishes", async ({ page }) => {
  await openEditor(page);
  const top = () => page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { layers: { text?: { text: string; bold: boolean } | null }[] };
    return d.layers[d.layers.length - 1].text ?? null;
  });
  await page.keyboard.press("t");
  const p = await at(page, 20, 20);
  await page.mouse.click(p.x, p.y);
  await page.locator("textarea.text-box").pressSequentially("Hi");
  await showToolOptions(page);
  await page.locator(".tool-settings").getByLabel("Bold").check(); // takes the focus…
  await expect(page.locator("textarea.text-box")).toHaveCount(1); // …but the typing goes on
  await expect.poll(async () => (await top())?.bold).toBe(true); // and the text follows
  await page.locator(".tool-rail").getByRole("button", { name: "Brush", exact: true }).click();
  await expect(page.locator("textarea.text-box")).toHaveCount(0);
  expect(await top()).toMatchObject({ text: "Hi", bold: true });
});

// ------------------------------------------------------------------ shape layers

test("shapes: each on its own layer, a corner handle reshapes it, Rasterize turns it into pixels", async ({ page }) => {
  await openEditor(page);
  const layers = () => page.evaluate(() => (window.__editor as EditorHook).doc.layers.length);
  const box = () => page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { active: number; layers: { x: number; y: number; shape?: { points: number[] } | null }[] };
    const l = d.layers[d.active];
    return l.shape ? l.shape.points.map((v, i) => Math.round(v + (i % 2 ? l.y : l.x))) : null;
  });
  const undoLabel = () => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel);
  const n = await layers();
  await page.locator(".tool-rail").getByRole("button", { name: "Rectangle", exact: true }).click();
  await page.evaluate(() => (window.__editor as unknown as { floating: { minimise(k: string): void } }).floating.minimise("tool"));
  await drag(page, [10, 10], [60, 40]);
  await expect.poll(layers).toBe(n + 1);
  expect(await box()).toEqual([10, 10, 60, 40]);
  await drag(page, [60, 40], [90, 70]); // the bottom-right handle
  await expect.poll(box).toEqual([10, 10, 90, 70]);
  expect(await layers()).toBe(n + 1);
  expect(await undoLabel()).toBe("Edit shape");
  await page.locator(".layers li").first().click({ button: "right" });
  await page.getByRole("menu", { name: "Layer actions" }).getByRole("menuitem", { name: /^Rasterize layer/ }).click();
  await expect.poll(box).toBeNull();
});

test("layer effects: a window adds a drop shadow live, one undo step, with an fx badge", async ({ page }) => {
  await openEditor(page);
  const fx = () => page.evaluate(() => {
    const d = (window.__editor as EditorHook).doc as EditorHook["doc"] & { active: number; layers: { fx?: { shadow: boolean; shadow_distance: number } | null }[] };
    return d.layers[d.active].fx ?? null;
  });
  const undoLabel = () => page.evaluate(() => (window.__editor as EditorHook & { history: { undoLabel: string } }).history.undoLabel);
  await page.locator(".tool-rail").getByRole("button", { name: /^Drop shadow/ }).click();
  const win = page.locator("dialog.fx_shadow");
  await expect(win).toBeVisible();
  await expect.poll(async () => (await fx())?.shadow).toBe(true); // opening it adds the shadow
  await expect(page.locator(".layers .fx-badge")).toHaveCount(1);
  await expect(win.getByLabel("Show drop shadow")).toBeChecked();
  const distance = win.getByLabel("Distance").first();
  await distance.fill("30");
  await distance.dispatchEvent("input");
  await distance.dispatchEvent("change");
  await expect.poll(async () => (await fx())?.shadow_distance).toBe(30);
  expect(await undoLabel()).toBe("Drop shadow");
  await win.getByRole("button", { name: "Done" }).click();
  await expect(win).toBeHidden();
  await page.locator(".editor-top").getByRole("button", { name: /^Undo/ }).click();
  await expect.poll(fx).toBeNull(); // the whole run was one step
  await expect(page.locator(".layers .fx-badge")).toHaveCount(0);
});
