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
}

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

test("switch hidden for non-images, shown for images", async ({ page }) => {
  await load(page, [fx("doc.md")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Clear" }).click();
  await page.locator('input[type="file"]').first().setInputFiles([fx("img0.png"), fx("img1.png")]);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeVisible();
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

const adjustMenu = async (page: Page, item: string) => {
  await page.getByText("Adjust ▾").click();
  await page.getByRole("button", { name: item }).click();
};
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

const filterMenu = async (page: Page, item: string) => {
  await page.getByText("Filters ▾").click();
  await page.getByRole("button", { name: item }).click();
};

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
