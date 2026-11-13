// The document editor: Convert | Edit for one text document — in real Chromium.
import { readFileSync, writeFileSync } from "node:fs";
import { zipSync, strToU8 } from "fflate";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;
// headless Chromium hides scrollbars unless asked not to (the scrollbar test presses one)
test.use({ launchOptions: { ignoreDefaultArgs: ["--hide-scrollbars"] } });
const editBtn = (page: Page) => page.getByRole("button", { name: "Edit", exact: true });
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==", "base64");

async function load(page: Page, files: string[]) {
  await page.goto("./?e2e");
  await page.locator('input[type="file"]').first().setInputFiles(files);
}

async function openDoc(page: Page, file: string) {
  await load(page, [file]);
  await editBtn(page).click();
  await expect(page.locator(".doc-editor")).toBeVisible();
}

/** Drop `names` (fixture files) on `selector` the way the browser does. */
async function dropOn(page: Page, selector: string, names: string[]) {
  const files = names.map((n) => ({ name: n.slice(n.lastIndexOf("/") + 1), data: [...readFileSync(n)] }));
  await page.evaluate(([sel, list]) => {
    const dt = new DataTransfer();
    for (const f of list as { name: string; data: number[] }[]) dt.items.add(new File([new Uint8Array(f.data)], f.name));
    const target = document.querySelector(sel as string) as HTMLElement;
    target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
    target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
  }, [selector, files] as const);
}

test.beforeAll(() => {
  // a Markdown ZIP as the website saves it, and an ordinary ZIP
  writeFileSync(fx("notes-md.zip"), zipSync({ "notes.md": strToU8("# Notes\n\n![](notes_files/img1.png)\n"), "notes_files/img1.png": new Uint8Array(PNG) }));
});

test("Edit: one editable document, a Markdown ZIP; not DOC, two documents or a mix", async ({ page }) => {
  await load(page, [fx("doc.md")]);
  await expect(editBtn(page)).toBeEnabled();
  await expect(editBtn(page)).toHaveAttribute("title", "Open the document in the document editor");
  const clear = page.getByRole("button", { name: "Clear" });
  const add = (files: string[]) => page.locator('input[type="file"]').first().setInputFiles(files);
  for (const files of [[fx("letter.doc")], [fx("doc.md"), fx("notes.txt")], [fx("notes.txt"), fx("img0.png")], [fx("bundle.zip")]]) {
    await clear.click();
    await add(files);
    await expect(page.locator(".queue li")).toHaveCount(files.length);
    await expect(editBtn(page), files.join(" + ")).toBeDisabled();
    await expect(editBtn(page)).toHaveAttribute("title", "Add images, audio, one text document, or 3D models to edit them");
  }
  await clear.click();
  await add([fx("notes-md.zip")]);
  await expect(editBtn(page)).toBeEnabled();
  await expect(editBtn(page)).toHaveAttribute("title", "Open the document in the document editor");
  await editBtn(page).click();
  await expect(page.locator(".doc-editor .ProseMirror h1")).toHaveText("Notes");
  await expect(page.locator(".doc-editor .ProseMirror img[data-name]")).toHaveCount(1);
  // the image editor's tooltip is unchanged
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await clear.click();
  await add([fx("img0.png"), fx("img1.png")]);
  await expect(editBtn(page)).toHaveAttribute("title", "Open the images in the image editor (each image becomes a layer)");
});

test("Edit opens the document; Back returns to the converter with the queue kept", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await expect(page.locator(".doc-editor .ProseMirror")).toContainText("Line 2 with ÆØÅ");
  await expect(page.locator("main.app")).toBeHidden();
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".doc-editor")).toBeHidden();
  await expect(page.locator(".queue li")).toHaveCount(1);
  // a second open reuses the same page
  await editBtn(page).click();
  await expect(page.locator(".doc-editor")).toHaveCount(1);
  await expect(page.locator(".doc-editor")).toBeVisible();
});

test("leaving with unsaved edits asks first", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await page.locator(".doc-editor .ProseMirror").click();
  await page.keyboard.type("changed ");
  const asked: string[] = [];
  page.once("dialog", (d) => {
    asked.push(d.message());
    void d.dismiss();
  });
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect.poll(() => asked).toEqual(["Discard unsaved changes to this document?"]);
  await expect(page.locator(".doc-editor")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".doc-editor")).toBeHidden();
  await expect(page.locator("main.app")).toBeVisible();
});

test("drops while the editor is shown open the document there, never in the queue", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  // on the page itself
  await dropOn(page, ".doc-editor .doc-page", [fx("doc.md")]);
  await expect(page.locator(".doc-editor .ProseMirror h1")).toHaveText("Title ÆØÅ");
  // anywhere else on the window (the body's handler)
  await dropOn(page, "body", [fx("notes.txt")]);
  await expect(page.locator(".doc-editor .ProseMirror")).toContainText("Plain text line 1");
  // more than one file is refused with a plain message
  await dropOn(page, "body", [fx("doc.md"), fx("notes.txt")]);
  await expect(page.locator(".doc-editor .doc-message")).toHaveText("Only one document can be edited at a time.");
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator(".queue li")).toHaveCount(1);
  await expect(page.locator(".queue li")).toContainText("notes.txt");
});

test("a press on the page's scrollbar scrolls and keeps the selection", async ({ page }) => {
  await page.goto("./?e2e");
  const text = Array.from({ length: 300 }, (_, i) => `Line ${i + 1}`).join("\n") + "\n";
  await page.locator('input[type="file"]').first().setInputFiles([{ name: "long.txt", mimeType: "text/plain", buffer: Buffer.from(text) }]);
  await editBtn(page).click();
  await expect(page.locator(".doc-editor .ProseMirror")).toContainText("Line 300");
  await page.locator(".doc-editor .ProseMirror p").first().click();
  await page.keyboard.press("Home");
  await page.keyboard.press("Shift+End");
  const sel = () => page.evaluate(() => String(getSelection()));
  expect(await sel()).toBe("Line 1");
  const bar = await page.locator(".doc-scroll").evaluate((el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + el.clientLeft + el.clientWidth, w: r.right - (r.left + el.clientLeft + el.clientWidth), y: r.top + r.height / 2 };
  });
  expect(bar.w).toBeGreaterThan(8);
  const top = await page.locator(".doc-scroll").evaluate((el) => el.scrollTop);
  await page.mouse.move(bar.x + bar.w / 2, bar.y);
  await page.mouse.down();
  await page.mouse.up();
  expect(await sel()).toBe("Line 1");
  await expect.poll(() => page.locator(".doc-scroll").evaluate((el) => el.scrollTop)).toBeGreaterThan(top); // it scrolled
});
