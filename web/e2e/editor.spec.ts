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

test("crop: Apply button, and Esc discards", async ({ page }) => {
  await openEditor(page);
  await page.keyboard.press("c");
  await drag(page, [0, 0], [200, 100]);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Enter");
  expect(await size(page)).toEqual([321, 241]);
  await drag(page, [0, 0], [200, 100]);
  await page.getByRole("button", { name: "Apply crop" }).click();
  await expect.poll(() => size(page)).toEqual([200, 100]);
});
