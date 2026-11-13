// The document editor: Convert | Edit for one text document — in real Chromium.
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { zipSync, strToU8 } from "fflate";
import { type Dialog, expect, type Page, test } from "@playwright/test";
import { FIXTURES, findPython } from "./global-setup";

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
  test.skip(bar.w === 0, "this browser uses overlay scrollbars: nothing to press");
  const top = await page.locator(".doc-scroll").evaluate((el) => el.scrollTop);
  await page.mouse.move(bar.x + bar.w / 2, bar.y);
  await page.mouse.down();
  await page.mouse.up();
  expect(await sel()).toBe("Line 1");
  await expect.poll(() => page.locator(".doc-scroll").evaluate((el) => el.scrollTop)).toBeGreaterThan(top); // it scrolled
});

/** Holds Pandoc's wasm download until `release()`; `held()` counts the requests caught, so a
 * test can prove the download really waited. */
async function holdPandoc(page: Page): Promise<{ release: () => void; held: () => number }> {
  let release!: () => void;
  const gate = new Promise<void>((r) => (release = r));
  let hits = 0;
  await page.route(/pandoc-[^/]*\.wasm$/, async (route) => {
    hits++;
    await gate;
    await route.continue();
  });
  return { release, held: () => hits };
}
/** A real (if tiny) RTF file: String.raw keeps the backslashes ("\r" and "\a" in a plain string
 * are escapes, so the old sample was "{<CR>tf1<BEL>nsi …", not RTF). */
const RTF = { name: "memo.rtf", mimeType: "application/rtf", buffer: Buffer.from(String.raw`{\rtf1\ansi Hello from RTF\par}`) };
/** The RTF opened as exactly its one line of text. */
const rtfOpened = (page: Page) => expect(page.locator(".doc-editor .ProseMirror p").first()).toHaveText("Hello from RTF", { timeout: 120_000 });

test("the page shows while a document opens (the converter's download visible there)", async ({ page }) => {
  const pandoc = await holdPandoc(page);
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([RTF]);
  await editBtn(page).dblclick(); // a double click opens it once
  await expect(page.locator(".doc-editor")).toBeVisible();
  await expect(page.locator("main.app")).toBeHidden();
  await expect(page.locator(".doc-editor .doc-message")).toContainText(/Opening|Downloading/);
  await expect.poll(pandoc.held, { timeout: 60_000 }).toBeGreaterThan(0); // the download is waiting
  await expect(page.locator(".doc-editor .doc-message")).toContainText(/Opening|Downloading/);
  pandoc.release();
  await rtfOpened(page);
  await expect(page.locator(".doc-editor")).toHaveCount(1);
});

test("an open that fails goes back to Convert with the reason", async ({ page }) => {
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([{ name: "broken.docx", mimeType: "application/octet-stream", buffer: Buffer.from("not a zip at all") }]);
  await editBtn(page).click();
  await expect(page.locator(".note")).toBeVisible();
  await expect(page.locator(".note")).not.toHaveText("");
  await expect(page.locator(".doc-editor")).toBeHidden();
  await expect(page.locator("main.app")).toBeVisible();
  await expect(editBtn(page)).toBeEnabled();
});

test("the Convert and Properties mode buttons ask first with unsaved edits", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await page.locator(".doc-editor .ProseMirror").click();
  await page.keyboard.type("changed ");
  // the mode bar sits on the hidden converter page: press its buttons from script
  const press = (sel: string) => page.locator(sel).evaluate((b: HTMLElement) => b.click());
  for (const sel of ['button[aria-label="Convert mode"]', '.mode button:has-text("Properties")']) {
    const asked: string[] = [];
    page.once("dialog", (d) => {
      asked.push(d.message());
      void d.dismiss();
    });
    await press(sel);
    await expect.poll(() => asked, sel).toEqual(["Discard unsaved changes to this document?"]);
    await expect(page.locator(".doc-editor")).toBeVisible();
  }
  page.once("dialog", (d) => void d.accept());
  await press('.mode button:has-text("Properties")');
  await expect(page.locator(".doc-editor")).toBeHidden();
  await expect(page.locator("main.app")).toBeVisible();
});

// ---------------------------------------------------------------- Task 11

test("Back during a running open leaves Edit working", async ({ page }) => {
  const pandoc = await holdPandoc(page);
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([RTF]);
  await editBtn(page).click();
  await expect(page.locator(".doc-editor .doc-message")).toContainText(/Opening|Downloading/);
  await expect.poll(pandoc.held, { timeout: 60_000 }).toBeGreaterThan(0); // still opening: held
  await page.getByRole("button", { name: "← Back to Convert" }).click();
  await expect(page.locator("main.app")).toBeVisible();
  await expect(editBtn(page)).toBeEnabled();
  await editBtn(page).click();
  await expect(page.locator(".doc-editor")).toBeVisible();
  pandoc.release();
  await rtfOpened(page);
});

test("a document dropped during an open that then fails keeps the editor", async ({ page }) => {
  writeFileSync(fx("broken.docx"), "not a zip at all");
  const pandoc = await holdPandoc(page);
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([RTF]);
  await editBtn(page).click();
  await expect(page.locator(".doc-editor .doc-message")).toContainText(/Opening|Downloading/);
  await expect.poll(pandoc.held, { timeout: 60_000 }).toBeGreaterThan(0); // the RTF open is waiting
  await dropOn(page, "body", [fx("broken.docx")]);
  await expect(page.locator(".doc-editor .doc-message")).toHaveClass(/error/);
  expect(await opening(page)).toBe(1); // the taken-over RTF open is still running
  pandoc.release();
  // the first open (taken over) finishing must not send the user back to Convert
  await expect.poll(() => opening(page), { timeout: 120_000 }).toBe(0);
  await expect(page.locator(".doc-editor")).toBeVisible();
  await expect(page.locator("main.app")).toBeHidden();
});

interface DocHook {
  html(): string;
  text(): string;
  dirty: boolean;
  opening: number;
  unloadAsks: boolean;
}
type HookWindow = Window & { __doceditor: DocHook };
const docHtml = (page: Page) => page.evaluate(() => (window as unknown as HookWindow).__doceditor.html());
const docText = (page: Page) => page.evaluate(() => (window as unknown as HookWindow).__doceditor.text());
const opening = (page: Page) => page.evaluate(() => (window as unknown as HookWindow).__doceditor.opening);
const unloadAsks = (page: Page) => page.evaluate(() => (window as unknown as HookWindow).__doceditor.unloadAsks);
const pm = (page: Page) => page.locator(".doc-editor .ProseMirror");
/** The document's pictures (ProseMirror adds a `ProseMirror-separator` <img> after a trailing one). */
const PICTURE = "img:not(.ProseMirror-separator)";
const message = (page: Page) => page.locator(".doc-editor .doc-message");

/** Press Save (or run `how`) and return the download's path and name. */
async function saved(page: Page, how: () => Promise<unknown> = () => page.getByRole("button", { name: "Save", exact: true }).click()) {
  // the first save over a source with parts the editor can't keep asks first: yes
  const lost = (d: Dialog) => void (d.message().startsWith("Parts of this document will be lost") ? d.accept() : d.dismiss());
  page.on("dialog", lost);
  try {
    const download = page.waitForEvent("download", { timeout: 120_000 });
    await how();
    const d = await download;
    return { path: (await d.path())!, name: d.suggestedFilename() };
  } finally {
    page.off("dialog", lost);
  }
}

/** Save as `format` with the Save as dialog. */
async function saveAs(page: Page, format: string) {
  return saved(page, async () => {
    await page.getByRole("button", { name: "Save as…" }).click();
    const dlg = page.locator("dialog.doc-save-dialog");
    await dlg.getByLabel("Format").selectOption(format);
    await dlg.getByRole("button", { name: "Save", exact: true }).click();
  });
}

/** Open a downloaded file in the editor shown (dropped, as a user would). */
async function reopen(page: Page, path: string, name: string) {
  const files = [{ name, data: [...readFileSync(path)] }];
  await page.evaluate((list) => {
    const dt = new DataTransfer();
    for (const f of list) dt.items.add(new File([new Uint8Array(f.data)], f.name));
    document.body.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
  }, files);
  await expect.poll(() => opening(page), { timeout: 120_000 }).toBe(0);
  await expect(message(page)).not.toHaveClass(/error/);
}

test("edit opens a docx", async ({ page }) => {
  await openDoc(page, fx("editable.docx"));
  await expect(pm(page).locator("h1")).toHaveText("Editable report");
  await expect(pm(page).locator("b, strong").first()).toHaveText("bold words");
  await expect(pm(page).locator("li")).toHaveCount(2);
  await expect(pm(page).locator("table tr")).toHaveCount(2);
  await expect(page.locator(".doc-editor .doc-status")).toHaveText(/^\d+ words · \d+ characters$/);
});

test("bold + add table row + save docx and pdf", async ({ page }) => {
  await openDoc(page, fx("editable.docx"));
  // bold the word "here" with the format bar
  await pm(page).locator("p", { hasText: "here." }).evaluate((p) => {
    const text = [...p.childNodes].find((n) => n.nodeType === 3 && n.textContent!.includes("here"))!;
    const at = text.textContent!.indexOf("here");
    getSelection()!.setBaseAndExtent(text, at, text, at + 4);
  });
  await page.getByRole("button", { name: "Bold" }).click();
  // a row below "Ada"
  await pm(page).locator("td", { hasText: "Ada" }).click();
  await page.getByTitle("Edit the table").click();
  await page.getByRole("menuitem", { name: "Insert row below" }).click();
  await expect(pm(page).locator("table tr")).toHaveCount(3);
  const docx = await saved(page);
  expect(docx.name).toBe("editable.docx");
  const pdf = await saveAs(page, "pdf");
  expect(pdf.name).toBe("editable.pdf");
  // the DOCX opens again with the bold text and three rows
  await reopen(page, docx.path, docx.name);
  await expect(pm(page).locator("table tr")).toHaveCount(3);
  await expect(pm(page).locator("b, strong", { hasText: "here" })).toHaveCount(1);
  // the PDF has the heading
  const mupdf = await import("mupdf");
  const bytes = readFileSync(pdf.path);
  expect(bytes.length).toBeGreaterThan(500);
  const doc = mupdf.Document.openDocument(bytes, "application/pdf");
  expect(doc.loadPage(0).toStructuredText("preserve-whitespace").asText()).toContain("Editable report");
});

for (const name of ["aligned.txt", "readme.md", "editable.docx"]) {
  test(`three saves stay identical: ${name}`, async ({ page }) => {
    await openDoc(page, fx(name));
    const first = { html: await docHtml(page), text: await docText(page) };
    let previous: Buffer | null = null;
    for (let round = 1; round <= 3; round++) {
      const out = await saved(page);
      expect(out.name).toBe(name);
      const bytes = readFileSync(out.path);
      if (previous && !name.endsWith(".docx")) expect(bytes.equals(previous), `save ${round}`).toBe(true);
      previous = bytes;
      await reopen(page, out.path, out.name);
      expect(await docHtml(page), `round ${round}`).toBe(first.html);
      expect(await docText(page), `round ${round}`).toBe(first.text);
    }
    if (name === "aligned.txt") expect(previous!.toString("utf8")).toBe(readFileSync(fx(name), "utf8"));
  });
}

test("no pandoc download for docx", async ({ page }) => {
  const urls: string[] = [];
  page.context().on("request", (r) => urls.push(r.url()));
  await openDoc(page, fx("editable.docx"));
  await pm(page).locator("h1").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" edited");
  const out = await saved(page);
  await reopen(page, out.path, out.name);
  await expect(pm(page).locator("h1")).toHaveText("Editable report edited");
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((u) => /pandoc/i.test(u))).toEqual([]);
});

test("odt opens via pandoc", async ({ page }) => {
  await openDoc(page, fx("editable.odt"));
  await expect(pm(page).locator("h1")).toHaveText("Editable report", { timeout: 120_000 });
  await expect(pm(page).locator("table tr")).toHaveCount(2);
});

test("beforeunload set while dirty", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  expect(await unloadAsks(page)).toBe(false);
  await pm(page).click();
  await page.keyboard.type("x");
  expect(await unloadAsks(page)).toBe(true);
  // the browser's own question when the tab closes; staying keeps the page
  const asked: string[] = [];
  page.once("dialog", (d) => {
    asked.push(d.type());
    void d.dismiss();
  });
  await page.close({ runBeforeUnload: true });
  await expect.poll(() => asked).toEqual(["beforeunload"]);
  expect(page.isClosed()).toBe(false);
});

test("images still open the image editor", async ({ page }) => {
  await load(page, [fx("img0.png")]);
  await editBtn(page).click();
  await expect(page.locator(".editor:not(.doc-editor) canvas").first()).toBeVisible();
  await expect(page.locator(".doc-editor")).toHaveCount(0);
});

test("big table stays responsive", async ({ page }) => {
  const rows = Array.from({ length: 20_000 }, (_, i) => `| r${i} | ${i} |`).join("\n");
  const md = `# Big\n\n| a | b |\n|---|---|\n${rows}\n`;
  // the editor's code is loaded (and warm) first, so the timing is the document's alone
  await openDoc(page, fx("notes.txt"));
  // from now on, the longest the page goes without drawing a frame
  await page.evaluate(() => {
    const w = window as unknown as { __gap: number; __last: number; __drewOpening: boolean };
    w.__gap = 0;
    w.__last = performance.now();
    w.__drewOpening = false;
    const tick = (now: number) => {
      w.__gap = Math.max(w.__gap, now - w.__last);
      w.__last = now;
      if (document.querySelector(".doc-editor .doc-message")?.textContent?.startsWith("Opening")) w.__drewOpening = true;
      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
  const gap = () => page.evaluate(() => (window as unknown as { __gap: number }).__gap);
  let t = Date.now();
  await page.evaluate((text) => {
    const dt = new DataTransfer();
    dt.items.add(new File([text], "big.md", { type: "text/markdown" }));
    document.body.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
  }, md);
  await expect(pm(page).locator("td", { hasText: "r19999" })).toHaveCount(1, { timeout: 5_000 });
  const openMs = Date.now() - t;
  const openGap = await gap();
  // a frame was drawn with "Opening…" showing: the page waits for one before the parse, which
  // is one synchronous block (the freeze itself is bounded below)
  const drewOpening = await page.evaluate(() => (window as unknown as { __drewOpening: boolean }).__drewOpening);
  expect(drewOpening).toBe(true);
  // and straight after
  t = Date.now();
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(r)));
  expect(Date.now() - t).toBeLessThan(1_000);
  t = Date.now();
  const out = await saved(page, () => page.keyboard.press("Control+s"));
  const saveMs = Date.now() - t;
  console.log(`big table: open ${openMs} ms (longest frame gap ${Math.round(openGap)} ms, "Opening…" drawn: ${drewOpening}), save ${saveMs} ms (longest gap since open ${Math.round(await gap())} ms)`);
  expect(out.name).toBe("big.md");
  expect(readFileSync(out.path, "utf8")).toContain("| r19999 | 19999 |");
  expect(openMs, "open").toBeLessThan(5_000);
  expect(saveMs, "save").toBeLessThan(5_000);
  // the page stops drawing for ~1.7 s here (Edge, 12-core PC): never for 3 s
  expect(openGap, "the longest the page froze while opening").toBeLessThan(3_000);
});

test("test_cross_app_docx", async ({ page }) => {
  const python = findPython();
  test.skip(!python || !existsSync(fx("desktop.docx")) || !existsSync(fx("desktop.html")), "needs python with python-docx (UMC_PYTHON, python3 or the desktop venv)");
  await openDoc(page, fx("desktop.docx"));
  // the web reads what the desktop reads
  expect(await docHtml(page)).toBe(readFileSync(fx("desktop.html"), "utf8"));
  await expect(pm(page).locator("h1")).toHaveText("From the desktop");
  await expect(pm(page).locator("b, strong", { hasText: "bold" })).toHaveCount(1);
  await expect(pm(page).locator("b, strong", { hasText: "Strong" })).toHaveCount(1);
  await expect(pm(page).locator("table tr")).toHaveCount(2);
  await expect(pm(page)).toContainText("Name\tScore");
  // an edit saved by the web reads the same on the desktop
  await pm(page).locator("td", { hasText: "Lin" }).click();
  await page.keyboard.press("End");
  await page.keyboard.type(" Lovelace");
  const out = await saved(page);
  const desktop = execFileSync(python!, ["-c", [
    "import sys",
    "sys.path.insert(0, sys.argv[1])",
    "from pathlib import Path",
    "from umc.doceditor import docx_reader",
    "sys.stdout.buffer.write(docx_reader.read(Path(sys.argv[2])).html.encode('utf-8'))",
  ].join("\n"), fileURLToPath(new URL("../../src", import.meta.url)), out.path]).toString("utf8");
  expect(desktop).toBe(await docHtml(page));
  expect(desktop).toContain("Lin Lovelace");
});

// ---------------------------------------------------------------- real-browser checks (Task 7–9 reviews)

/** Records, for every key press, whether the page kept it from the browser (a listener added
 * after the page's own, on the window). */
async function watchKeys(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __keys: string[] };
    w.__keys = [];
    window.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.key !== "Control" && e.key !== "Shift") w.__keys.push(`${e.key.toLowerCase()}:${e.defaultPrevented}`);
    });
  });
  return () => page.evaluate(() => (window as unknown as { __keys: string[] }).__keys);
}
const inPage = (page: Page) => page.evaluate(() => !!document.activeElement?.closest(".ProseMirror"));

test("a press on the grey margin or the empty paper puts the caret in the page; Ctrl+S / F / K work there", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  const keys = await watchKeys(page);
  await page.locator(".doc-editor .doc-status").click(); // the focus leaves the page
  expect(await inPage(page)).toBe(false);
  const scroll = (await page.locator(".doc-scroll").boundingBox())!;
  const paper = (await page.locator(".doc-page").boundingBox())!;
  await page.mouse.click(scroll.x + 4, paper.y + 20); // the grey area left of the paper
  expect(await inPage(page)).toBe(true);
  const out = await saved(page, () => page.keyboard.press("Control+s"));
  expect(out.name).toBe("notes.txt");
  await page.mouse.click(paper.x + paper.width / 2, paper.y + 400); // the empty paper below the text
  expect(await inPage(page)).toBe(true);
  await page.keyboard.press("Control+f");
  await expect(page.locator(".doc-editor .doc-find")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.mouse.click(scroll.x + 4, paper.y + 20);
  page.once("dialog", (d) => void d.dismiss()); // the link's address question (ours, not the browser's)
  await page.keyboard.press("Control+k");
  await expect.poll(keys).toEqual(["s:true", "f:true", "k:true"]);
});

test("Ctrl+S / K / H / L / E / J / R in the text are the editor's, not the browser's", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await pm(page).locator("p").first().click();
  const keys = await watchKeys(page);
  const download = page.waitForEvent("download");
  await page.keyboard.press("Control+s");
  await download;
  page.once("dialog", (d) => void d.dismiss());
  await page.keyboard.press("Control+k");
  for (const k of ["h", "l", "e", "j", "r"]) {
    await pm(page).locator("p").first().click();
    await page.keyboard.press(`Control+${k}`);
  }
  await expect.poll(keys).toEqual(["s:true", "k:true", "h:true", "l:true", "e:true", "j:true", "r:true"]);
  // the alignment keys did align (last: right)
  expect(await docHtml(page)).toContain("text-align:right");
});

test("a dropped picture lands where it was dropped; a dropped document never leaves the page", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  const url = page.url();
  // drop img0.png at the end of the first line
  const end = await pm(page).locator("p").first().evaluate((p) => {
    const r = document.createRange();
    r.selectNodeContents(p);
    const box = r.getBoundingClientRect();
    return { x: box.right - 1, y: box.top + box.height / 2 };
  });
  const png = [...readFileSync(fx("img0.png"))];
  const prevented = await page.evaluate(([at, data]) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(data as number[])], "img0.png", { type: "image/png" }));
    const target = document.elementFromPoint((at as { x: number }).x, (at as { y: number }).y)!;
    const init = { bubbles: true, cancelable: true, dataTransfer: dt, clientX: (at as { x: number }).x, clientY: (at as { y: number }).y };
    target.dispatchEvent(new DragEvent("dragover", init));
    const drop = new DragEvent("drop", init);
    target.dispatchEvent(drop);
    return drop.defaultPrevented;
  }, [end, png] as const);
  expect(prevented).toBe(true);
  await expect(pm(page).locator("p").first().locator(PICTURE)).toHaveCount(1);
  await expect(pm(page).locator("p").nth(1).locator(PICTURE)).toHaveCount(0);
  // a document dropped on the text: opened here, the browser's default (opening it) stopped
  const doc = [...readFileSync(fx("doc.md"))];
  page.once("dialog", (d) => void d.accept()); // the picture made the document dirty
  const kept = await page.evaluate((data) => {
    const dt = new DataTransfer();
    dt.items.add(new File([new Uint8Array(data)], "doc.md"));
    const target = document.querySelector(".doc-editor .ProseMirror p")!;
    target.dispatchEvent(new DragEvent("dragover", { bubbles: true, cancelable: true, dataTransfer: dt }));
    const drop = new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt });
    target.dispatchEvent(drop);
    return drop.defaultPrevented;
  }, doc);
  expect(kept).toBe(true);
  await expect(pm(page).locator("h1")).toHaveText("Title ÆØÅ");
  expect(page.url()).toBe(url);
});

test("Save as is a modal dialog; Esc closes it without saving", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await pm(page).click();
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.keyboard.press("Control+Shift+s");
  const dlg = page.locator("dialog.doc-save-dialog");
  await expect(dlg).toBeVisible();
  expect(await dlg.evaluate((d) => d.matches(":modal"))).toBe(true);
  await expect(dlg.getByLabel("File name")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  expect(await inPage(page)).toBe(true);
  await page.waitForTimeout(300);
  expect(downloads).toBe(0);
});

test("Save to a file… then Ctrl+S writes the same file (no downloads)", async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { __writes: { name: string; text: string }[]; showSaveFilePicker: unknown };
    w.__writes = [];
    w.showSaveFilePicker = async (o: { suggestedName: string }) => ({
      name: o.suggestedName,
      createWritable: async () => {
        const parts: string[] = [];
        return {
          write: async (b: Blob) => void parts.push(await b.text()),
          close: async () => void w.__writes.push({ name: o.suggestedName, text: parts.join("") }),
        };
      },
    });
  });
  await openDoc(page, fx("notes.txt"));
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.getByRole("button", { name: "Save as…" }).click();
  const dlg = page.locator("dialog.doc-save-dialog");
  await dlg.getByRole("button", { name: "Save to a file…" }).click();
  const writes = () => page.evaluate(() => (window as unknown as { __writes: { name: string; text: string }[] }).__writes);
  await expect.poll(writes).toEqual([{ name: "notes.txt", text: "Plain text line 1\nLine 2 with ÆØÅ\n" }]);
  await pm(page).locator("p").first().click();
  await page.keyboard.press("End");
  await page.keyboard.type("!");
  await page.keyboard.press("Control+s");
  await expect.poll(async () => (await writes()).length).toBe(2);
  expect((await writes())[1]).toEqual({ name: "notes.txt", text: "Plain text line 1!\nLine 2 with ÆØÅ\n" });
  await expect(message(page)).toHaveText("Saved “notes.txt”.");
  expect(downloads).toBe(0);
});

test("Insert picture uses the browser's file window", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await pm(page).locator("p").first().click();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Insert picture" }).click();
  await (await chooser).setFiles(fx("img1.png"));
  await expect(pm(page).locator(PICTURE)).toHaveCount(1);
});

test("the banner closes; the status line follows typing", async ({ page }) => {
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([{ name: "styled.html", mimeType: "text/html", buffer: Buffer.from("<style>p{color:red}</style><p>Two words</p>") }]);
  await editBtn(page).click();
  const banner = page.locator(".doc-editor .doc-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("can't keep");
  await banner.getByRole("button", { name: "Close this notice" }).click();
  await expect(banner).toBeHidden();
  const status = page.locator(".doc-editor .doc-status");
  await expect(status).toHaveText("2 words · 9 characters"); // spaces count, as on the desktop
  await pm(page).locator("p").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" more");
  await expect(status).toHaveText("3 words · 14 characters · Unsaved changes");
});

test("the arrow keys step over an empty link, and typing keeps it", async ({ page }) => {
  await load(page, []);
  await page.locator('input[type="file"]').first().setInputFiles([{ name: "links.html", mimeType: "text/html", buffer: Buffer.from('<p>ab<a href="https://example.com/"></a>cd</p>') }]);
  await editBtn(page).click();
  await pm(page).locator("p").click();
  await page.keyboard.press("Home");
  // a short pause after each press: ProseMirror reads the browser's move on `selectionchange`
  const press = async (key: string) => {
    await page.keyboard.press(key);
    await page.waitForTimeout(100);
  };
  for (let i = 0; i < 3; i++) await press("ArrowRight"); // a, b, then the link + c
  await page.keyboard.type("X");
  expect(await docHtml(page)).toBe('<p>ab<a href="https://example.com/"></a>cXd</p>');
  for (let i = 0; i < 3; i++) await press("ArrowLeft"); // X, c, then the link + b
  await page.keyboard.type("Y");
  expect(await docHtml(page)).toBe('<p>aYb<a href="https://example.com/"></a>cXd</p>');
});

test("copy and paste inside the editor keep the formatting", async ({ page }) => {
  await openDoc(page, fx("doc.md"));
  await pm(page).locator("p", { hasText: "Hello" }).click();
  await page.keyboard.press("Home");
  await page.keyboard.press("Shift+End");
  type Sel = Window & { __doceditor: { selection: [number, number] } };
  await expect.poll(() => page.evaluate(() => { const [a, h] = (window as unknown as Sel).__doceditor.selection; return h - a; })).toBeGreaterThan(0);
  // the browser's copy / paste events with one DataTransfer (headless keys don't reach the
  // system clipboard): what the editor puts there on copy is what it reads back on paste
  const types = await page.evaluate(() => {
    const dt = new DataTransfer();
    const dom = document.querySelector(".doc-editor .ProseMirror")!;
    dom.dispatchEvent(new ClipboardEvent("copy", { bubbles: true, cancelable: true, clipboardData: dt }));
    (window as unknown as { __clip: DataTransfer }).__clip = dt;
    return [...dt.types];
  });
  expect(types).toEqual(expect.arrayContaining(["text/html", "text/plain"]));
  await pm(page).locator("h1").click();
  await page.keyboard.press("End");
  await page.waitForTimeout(100); // ProseMirror takes the browser's move on `selectionchange`
  await page.keyboard.press("Enter");
  await page.evaluate(() => {
    const dt = (window as unknown as { __clip: DataTransfer }).__clip;
    document.querySelector(".doc-editor .ProseMirror")!.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: dt }));
  });
  await expect(pm(page).locator("p", { hasText: "Hello" })).toHaveCount(2);
  await expect(pm(page).locator("strong, b", { hasText: "world" })).toHaveCount(2);
});

test("a pasted wide picture is shrunk to the page width", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await pm(page).locator("p").first().click();
  await page.evaluate(async () => {
    const canvas = new OffscreenCanvas(1600, 100);
    const g = canvas.getContext("2d")!;
    g.fillStyle = "#c00";
    g.fillRect(0, 0, 1600, 100);
    const blob = await canvas.convertToBlob({ type: "image/png" });
    const dt = new DataTransfer();
    dt.items.add(new File([blob], "wide.png", { type: "image/png" }));
    document.querySelector(".doc-editor .ProseMirror")!.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData: dt }));
  });
  const img = pm(page).locator(PICTURE);
  await expect(img).toHaveCount(1);
  await expect(img).toHaveAttribute("width", "642");
  await expect(img).toHaveAttribute("height", "40");
  const box = (await img.boundingBox())!;
  const paper = (await page.locator(".doc-page").boundingBox())!;
  expect(box.width).toBeLessThanOrEqual(paper.width);
});

test("Pandoc's download shows its progress", async ({ page }) => {
  const encodings: string[] = [];
  page.on("response", (r) => {
    if (/pandoc-[^/]*\.wasm$/.test(r.url())) encodings.push(r.headers()["content-encoding"] ?? "none");
  });
  await load(page, []);
  await page.evaluate(() => {
    const w = window as unknown as { __messages: string[] };
    w.__messages = [];
    new MutationObserver(() => {
      const m = document.querySelector(".doc-editor .doc-message")?.textContent ?? "";
      if (w.__messages.at(-1) !== m) w.__messages.push(m);
    }).observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  await page.locator('input[type="file"]').first().setInputFiles([RTF]);
  await editBtn(page).click();
  await rtfOpened(page);
  const seen = await page.evaluate(() => (window as unknown as { __messages: string[] }).__messages);
  const pcts = seen.map((m) => /Downloading the document converter… (\d+) %/.exec(m)?.[1]).filter(Boolean).map(Number);
  console.log(`pandoc wasm content-encoding: ${encodings.join(", ")}; progress seen: ${pcts.join(" ")}`);
  // a cached engine shows nothing; otherwise the percent only climbs and never passes 100
  if (pcts.length) {
    expect(pcts).toEqual([...pcts].sort((a, b) => a - b));
    expect(Math.max(...pcts)).toBeLessThanOrEqual(100);
  }
  expect(seen.at(-1)).toBe("");
});

test("accepting the Convert mode prompt leaves the editor", async ({ page }) => {
  await openDoc(page, fx("notes.txt"));
  await pm(page).click();
  await page.keyboard.type("changed ");
  page.once("dialog", (d) => void d.accept());
  await page.locator('button[aria-label="Convert mode"]').evaluate((b: HTMLElement) => b.click());
  await expect(page.locator(".doc-editor")).toBeHidden();
  await expect(page.locator("main.app")).toBeVisible();
  await expect(page.getByRole("button", { name: "Convert", exact: true })).toBeVisible();
});

test("Properties from the audio editor asks first with unsaved edits", async ({ page }) => {
  test.skip(!existsSync(fx("tone.wav")), "needs ffmpeg for the sample sound");
  await load(page, [fx("tone.wav")]);
  await editBtn(page).click();
  await expect(page.locator(".audio-info")).toContainText("kHz", { timeout: 120_000 });
  const box = (await page.locator(".audio-canvas").boundingBox())!;
  await page.mouse.move(box.x + box.width / 3, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + (box.width * 2) / 3, box.y + box.height / 2, { steps: 5 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Fade in", exact: true }).click();
  const props = () => page.locator('.mode button:has-text("Properties")').evaluate((b: HTMLElement) => b.click());
  const asked: string[] = [];
  page.once("dialog", (d) => {
    asked.push(d.message());
    void d.dismiss();
  });
  await props();
  await expect.poll(() => asked).toEqual(["Discard unsaved changes to this sound?"]);
  await expect(page.locator(".audio-editor")).toBeVisible();
  page.once("dialog", (d) => void d.accept());
  await props();
  await expect(page.locator(".audio-editor")).toBeHidden();
  await expect(page.locator("main.app")).toBeVisible();
});
