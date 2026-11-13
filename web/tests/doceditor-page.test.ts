// @vitest-environment jsdom
// Task 9: the document editor page (ports of the desktop's DocEditorPage tests that need no
// real browser: status line, saving, leaving, drops, paste and the depth check).
import { TextSelection } from "prosemirror-state";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { InputFile, OutputFile, Target } from "../src/core/types";
import type { ConversionClient } from "../src/worker/client";
import { TOO_LARGE, W_PAGESTYLE } from "../src/doceditor/types";
import { DISCARD, ONLY_ONE, PICK_FAILED, countText, keyNames, mountDocEditor, type DocEditorPage } from "../src/ui/doceditor/page";
import { NOT_A_PICTURE, TOO_DEEP } from "../src/ui/doceditor/view";

// jsdom has no ClipboardEvent (EditorView.pasteHTML makes one)
if (typeof globalThis.ClipboardEvent === "undefined") {
  (globalThis as Record<string, unknown>).ClipboardEvent = class extends Event {
    clipboardData = null;
  };
}

// jsdom has no layout: ProseMirror scrolls the selection into view in a focused page
const noRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: [][Symbol.iterator] }) as unknown as DOMRectList;
const zeroRect = () => ({ left: 0, right: 0, top: 0, bottom: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) }) as DOMRect;
for (const proto of [Element.prototype, Range.prototype] as { getClientRects?: () => DOMRectList; getBoundingClientRect?: () => DOMRect }[]) {
  proto.getClientRects ??= noRects;
  proto.getBoundingClientRect ??= zeroRect;
}

type Run = (target: Target, files: InputFile[], options: Record<string, unknown>, onProgress: (f: number, m: string) => void) => Promise<OutputFile[]>;

function fakeClient(run: Run = async () => [{ name: "out", data: new Uint8Array([1, 2, 3]), type: "x" }]) {
  const calls: { target: Target; files: InputFile[]; options: Record<string, unknown> }[] = [];
  const client = {
    run: (target: Target, files: InputFile[], options: Record<string, unknown>, onProgress: (f: number, m: string) => void) => {
      calls.push({ target, files, options });
      return run(target, files, options, onProgress);
    },
  } as unknown as ConversionClient;
  return { client, calls };
}

const input = (name: string, text: string | Uint8Array): InputFile => {
  const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
  return { file: new File([text as BlobPart], name), name, ext: ext === "htm" ? "html" : ext, family: "document" };
};
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0]);
const b64 = (data: Uint8Array) => btoa(String.fromCharCode(...data));

let pages: DocEditorPage[] = [];
let downloads: { name: string; blob: Blob }[] = [];
let confirmAnswer = true;
let confirms: string[] = [];

beforeEach(() => {
  downloads = [];
  confirms = [];
  confirmAnswer = true;
  vi.spyOn(window, "confirm").mockImplementation((m?: string) => {
    confirms.push(String(m));
    return confirmAnswer;
  });
  const blobs = new Map<string, Blob>();
  let n = 0;
  (URL as unknown as Record<string, unknown>).createObjectURL = (b: Blob) => {
    const url = `blob:${++n}`;
    blobs.set(url, b);
    return url;
  };
  (URL as unknown as Record<string, unknown>).revokeObjectURL = () => {};
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push({ name: this.download, blob: blobs.get(this.href) as Blob });
  });
});

afterEach(() => {
  for (const p of pages) p.dispose();
  pages = [];
  vi.restoreAllMocks();
});

function mount(run?: Run): { page: DocEditorPage; calls: ReturnType<typeof fakeClient>["calls"]; backs: number[] } {
  const { client, calls } = fakeClient(run);
  const backs: number[] = [];
  const page = mountDocEditor(document.body, { client, onBack: () => backs.push(1) });
  pages.push(page);
  page.show();
  return { page, calls, backs };
}

const q = (page: DocEditorPage, sel: string) => page.el.querySelector(sel) as HTMLElement;
const buttonNamed = (page: DocEditorPage, name: string) =>
  [...page.el.querySelectorAll("button")].find((b) => (b.textContent ?? "").includes(name) || b.getAttribute("aria-label") === name);
const type = (page: DocEditorPage, text: string) => {
  const v = page.view.view;
  v.dispatch(v.state.tr.insertText(text));
};
const html = (page: DocEditorPage) => page.view.content().html;
const flush = () => new Promise((r) => setTimeout(r, 0));
const textOf = (b: Blob) => b.text();

describe("layout", () => {
  test("has the test hooks and the top bar buttons", () => {
    const { page } = mount();
    for (const sel of [".doc-page", ".doc-banner", ".doc-status", ".doc-message", ".doc-format", ".doc-find"]) {
      expect(q(page, sel), sel).toBeTruthy();
    }
    for (const name of ["Back to Convert", "Undo", "Redo", "Save", "Save as", "Save to a file…"]) {
      expect(buttonNamed(page, name), name).toBeTruthy();
    }
    expect(q(page, ".doc-banner").hidden).toBe(true);
  });

  test("Back to Convert asks first when there are unsaved changes", async () => {
    const { page, backs } = mount();
    await page.open(input("a.txt", "x\n"));
    type(page, "y");
    confirmAnswer = false;
    buttonNamed(page, "Back to Convert")?.click();
    expect(confirms).toEqual([DISCARD]);
    expect(backs.length).toBe(0);
    confirmAnswer = true;
    buttonNamed(page, "Back to Convert")?.click();
    expect(backs.length).toBe(1);
  });
});

describe("status line", () => {
  test("the desktop's counting rule", () => {
    // a picture counts as a space; line breaks don't count; no-break spaces are spaces
    expect(countText("Hello world\n")).toEqual({ words: 2, chars: 11 });
    expect(countText("a b c d\n")).toEqual({ words: 4, chars: 6 });
    expect(countText("")).toEqual({ words: 0, chars: 0 });
    expect(countText("😀 x")).toEqual({ words: 2, chars: 3 });
  });

  test("counts the document and says when there are unsaved changes", async () => {
    const { page } = mount();
    expect(await page.open(input("a.html", `<p>Hello <b>world</b></p><p>a<img src="data:image/png;base64,${b64(PNG)}">b</p>`))).toBe(true);
    page.updateStatus();
    expect(q(page, ".doc-status").textContent).toBe("4 words · 14 characters");
    type(page, "!");
    page.updateStatus();
    expect(q(page, ".doc-status").textContent).toBe("4 words · 15 characters · Unsaved changes");
  });

  test("Undo / Redo follow the history", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    const undoBtn = buttonNamed(page, "Undo") as HTMLButtonElement;
    const redoBtn = buttonNamed(page, "Redo") as HTMLButtonElement;
    expect([undoBtn.disabled, redoBtn.disabled]).toEqual([true, true]);
    type(page, "y");
    expect(undoBtn.disabled).toBe(false);
    undoBtn.click();
    expect(html(page)).toBe("<p>x</p>");
    expect(redoBtn.disabled).toBe(false);
  });
});

describe("opening", () => {
  test("errors go to the message line; warnings show the banner", async () => {
    const { page } = mount();
    const big = input("big.txt", "x");
    Object.defineProperty(big.file, "size", { value: 60 * 1024 * 1024 });
    expect(await page.open(big)).toBe(false);
    expect(page.lastError()).toBe(TOO_LARGE);
    expect(q(page, ".doc-message").textContent).toBe(TOO_LARGE);
    expect(await page.open(input("a.html", "<style>p{}</style><p>x</p>"))).toBe(true);
    expect(q(page, ".doc-message").textContent).toBe("");
    const banner = q(page, ".doc-banner");
    expect(banner.hidden).toBe(false);
    expect(banner.textContent).toContain(W_PAGESTYLE);
    (banner.querySelector("button") as HTMLButtonElement).click();
    expect(banner.hidden).toBe(true);
  });

  test("a document nested too deeply gets a plain message", async () => {
    const { page } = mount();
    page.view.maxDepth = 4;
    expect(await page.open(input("deep.html", "<blockquote><blockquote><blockquote><blockquote><p>x</p></blockquote></blockquote></blockquote></blockquote>"))).toBe(false);
    expect(page.lastError()).toBe(TOO_DEEP);
    expect(TOO_DEEP).toBe("This document is nested too deeply to edit here.");
  });

  test("ODT goes through the converter; its download progress is shown", async () => {
    let page!: DocEditorPage;
    const seen: string[] = [];
    ({ page } = mount(async (_t, _f, _o, onProgress) => {
      onProgress(0.1, "Loading document engine (first time only)… 42 %");
      seen.push(q(page, ".doc-message").textContent ?? "");
      throw new Error("Couldn't read this OpenDocument file.");
    }));
    expect(await page.open(input("a.odt", new Uint8Array([1])))).toBe(false);
    expect(seen).toEqual(["Downloading the document converter… 42 %"]);
    expect(page.lastError()).toBe("Couldn't read this OpenDocument file.");
  });

  test("an open taken over while “Opening…” is being drawn never reads its file", async () => {
    const convert = vi.fn<Run>(async () => []);
    const { page } = mount(convert);
    const first = page.open(input("a.odt", new Uint8Array([1])));
    expect(q(page, ".doc-message").textContent).toBe("Opening “a.odt”…"); // shown at once
    expect(await page.open(input("b.txt", "second\n"))).toBe(true);
    expect(await first).toBe(null);
    expect(convert).not.toHaveBeenCalled();
  });

  test("a later open wins over one still running", async () => {
    let release!: () => void;
    const { page } = mount(() => new Promise((_r, reject) => (release = () => reject(new Error("slow")))));
    const first = page.open(input("a.odt", new Uint8Array([1])));
    await vi.waitFor(() => expect(release).toBeTypeOf("function")); // past "Opening…", converting
    expect(await page.open(input("b.txt", "second\n"))).toBe(true);
    release();
    expect(await first).toBe(null); // superseded, not failed
    expect(html(page)).toBe("<p>second</p>");
    expect(page.lastError()).toBe("");
  });
});

describe("saving", () => {
  test("Save downloads the original format with the original name", async () => {
    const { page } = mount();
    await page.open(input("notes.md", "# Title\n\nText\n"));
    type(page, "x");
    expect(await page.save()).toBe(true);
    expect(downloads.map((d) => d.name)).toEqual(["notes.md"]);
    expect(await textOf(downloads[0].blob)).toContain("# xTitle");
    expect(page.view.dirty).toBe(false);
    expect(q(page, ".doc-message").textContent).toBe("Saved “notes.md”.");
  });

  test("the first save over a source with warnings asks first", async () => {
    const { page } = mount();
    await page.open(input("page.htm", "<style>p{}</style><p>x</p>"));
    confirmAnswer = false;
    expect(await page.save()).toBe(false);
    expect(confirms).toEqual([`Parts of this document will be lost (${W_PAGESTYLE}). Save anyway?`]);
    expect(downloads.length).toBe(0);
    confirmAnswer = true;
    expect(await page.save()).toBe(true);
    expect(await page.save()).toBe(true);
    expect(confirms.length).toBe(2); // asked once more, then not again
    expect(downloads.map((d) => d.name)).toEqual(["page.html", "page.html"]);
  });

  test("edits made while saving stay unsaved; Save as is repeated by Ctrl+S", async () => {
    let finish!: () => void;
    const { page, calls } = mount(
      () => new Promise((resolve) => (finish = () => resolve([{ name: "x.odt", data: new Uint8Array([7]), type: "x" }]))),
    );
    await page.open(input("a.txt", "one\n"));
    page.askSaveAs = async (stem: string, format: string) => {
      expect([stem, format]).toEqual(["a", "txt"]);
      return { stem: "copy", format: "odt", toFile: false };
    };
    const saving = page.saveAs();
    await flush();
    expect(calls.map((c) => [c.target.id, c.files[0].ext])).toEqual([["doc:odt", "docx"]]);
    type(page, "two");
    finish();
    expect(await saving).toBe(true);
    expect(downloads.map((d) => d.name)).toEqual(["copy.odt"]);
    expect(page.view.dirty).toBe(true); // the edit came after the save started
    page.askSaveAs = async () => {
      throw new Error("Ctrl+S must not ask again");
    };
    const again = new KeyboardEvent("keydown", { key: "s", ctrlKey: true, bubbles: true, cancelable: true });
    page.view.view.dom.dispatchEvent(again);
    expect(again.defaultPrevented).toBe(true);
    await flush();
    finish();
    await vi.waitFor(() => expect(downloads.length).toBe(2));
    expect(downloads[1].name).toBe("copy.odt");
    await vi.waitFor(() => expect(page.view.dirty).toBe(false));
  });

  test("the Save as dialog: name, format and Save to a file…", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    const asked = page.askSaveAs("a", "txt");
    const dialog = q(page, ".doc-save-dialog") as HTMLDialogElement;
    const name = dialog.querySelector('input[type="text"]') as HTMLInputElement;
    const format = dialog.querySelector("select") as HTMLSelectElement;
    expect(name.value).toBe("a");
    expect(format.value).toBe("txt");
    expect([...format.options].map((o) => o.value)).toEqual(["docx", "odt", "rtf", "pdf", "md", "txt", "html"]);
    name.value = " b ";
    format.value = "html";
    [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Save")?.click();
    expect(await asked).toEqual({ stem: "b", format: "html", toFile: false });
    const cancelled = page.askSaveAs("a", "txt");
    [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Cancel")?.click();
    expect(await cancelled).toBe(null);
  });
});

describe("leaving", () => {
  test("maybeLeave asks only with unsaved changes; beforeunload is set while dirty", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    const unload = () => {
      const e = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(page.maybeLeave()).toBe(true);
    expect(unload()).toBe(false);
    expect(confirms.length).toBe(0);
    type(page, "y");
    expect(unload()).toBe(true);
    confirmAnswer = false;
    expect(page.maybeLeave()).toBe(false);
    expect(confirms).toEqual([DISCARD]);
    page.hide();
    expect(unload()).toBe(false);
  });
});

describe("drops", () => {
  test("one editable document replaces the open one; anything else is refused", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    await page.dropFiles([input("b.txt", "b\n"), input("c.txt", "c\n")]);
    expect(page.lastError()).toBe(ONLY_ONE);
    await page.dropFiles([{ ...input("p.png", PNG), family: "image" }]);
    expect(page.lastError()).toBe(ONLY_ONE);
    await page.dropFiles([input("old.doc", "x")]);
    expect(page.lastError()).toBe(ONLY_ONE);
    type(page, "y");
    confirmAnswer = false;
    await page.dropFiles([input("b.txt", "b\n")]);
    expect(html(page)).toBe("<p>yx</p>");
    confirmAnswer = true;
    await page.dropFiles([input("b.txt", "b\n")]);
    expect(html(page)).toBe("<p>b</p>");
  });

  function dropEvent(files: File[]): Event {
    const e = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(e, "dataTransfer", {
      value: { files, types: ["Files"], getData: () => "" },
    });
    return e;
  }

  test("a picture dropped on the page is inserted, not opened by the browser", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\n"));
    const v = page.view.view;
    v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 2)));
    const e = dropEvent([new File([PNG], "p.png", { type: "image/png" })]);
    v.dom.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(html(page)).toBe('<p>a<img src="img/1.png">b</p>'));
    // dropped next to the page (outside the editable part) too
    const outside = dropEvent([new File([PNG], "q.png", { type: "image/png" })]);
    q(page, ".doc-status").dispatchEvent(outside);
    expect(outside.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(page.view.images.size).toBe(2));
  });

  test("a document dropped on the page opens through dropFiles; dragover is allowed", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "a\n"));
    const over = new Event("dragover", { bubbles: true, cancelable: true });
    Object.defineProperty(over, "dataTransfer", { value: { types: ["Files"] } });
    q(page, ".doc-page").dispatchEvent(over);
    expect(over.defaultPrevented).toBe(true);
    const e = dropEvent([new File(["new\n"], "b.txt")]);
    page.view.view.dom.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(html(page)).toBe("<p>new</p>"));
  });
});

describe("paste", () => {
  test("goes through the normaliser: no unsafe links or web pictures; data: pictures are kept as img/N", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    page.view.view.pasteHTML(
      `<p><a href="javascript:alert(1)">j</a><img src="https://e.com/p.png"><img src="data:image/png;base64,${b64(PNG)}"></p>`,
    );
    const out = html(page);
    expect(out).not.toContain("javascript");
    expect(out).not.toContain("https://");
    const name = /<img src="(img\/\d+\.png)">/.exec(out)?.[1];
    expect(name).toBeTruthy();
    expect([...page.view.images.keys()]).toEqual([name]);
    expect(page.view.images.get(name as string)).toEqual(PNG);
  });
});

describe("shortcuts", () => {
  test("Ctrl+F / Ctrl+H open the find bar; Ctrl+B is the editor's", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "abc\n"));
    const key = (target: EventTarget, init: KeyboardEventInit) => {
      const e = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
      target.dispatchEvent(e);
      return e;
    };
    expect(key(page.view.view.dom, { key: "f", ctrlKey: true }).defaultPrevented).toBe(true);
    const find = q(page, ".doc-find");
    expect(find.hidden).toBe(false);
    const replace = find.querySelector('input[type="text"]') as HTMLInputElement;
    expect(replace.hidden).toBe(true);
    key(page.view.view.dom, { key: "h", ctrlKey: true });
    expect(replace.hidden).toBe(false);
    // from a bar button, the formatting keys still reach the page
    const v = page.view.view;
    v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 1, 3)));
    key(buttonNamed(page, "Undo") as HTMLElement, { key: "b", ctrlKey: true });
    expect(html(page)).toBe("<p><b>ab</b>c</p>");
  });
});

describe("links", () => {
  test("a link in the page never opens (also with Ctrl or the middle button)", async () => {
    const { page } = mount();
    await page.open(input("a.html", '<p><a href="https://example.com/">go</a></p>'));
    const a = page.view.view.dom.querySelector("a") as HTMLAnchorElement;
    for (const [type, init] of [["click", { ctrlKey: true }], ["click", {}], ["auxclick", { button: 1 }]] as const) {
      const e = new MouseEvent(type, { bubbles: true, cancelable: true, ...init });
      a.dispatchEvent(e);
      expect(e.defaultPrevented, type).toBe(true);
    }
  });
});

describe("Task 9a", () => {
  const keydown = (target: EventTarget, init: KeyboardEventInit) => {
    const e = new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(e);
    return e;
  };

  test("shortcuts work with the focus anywhere while the page is shown, and stop after hide / dispose", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    expect(keydown(document.body, { key: "f", ctrlKey: true }).defaultPrevented).toBe(true);
    expect(q(page, ".doc-find").hidden).toBe(false);
    page.hide();
    expect(keydown(document.body, { key: "s", ctrlKey: true }).defaultPrevented).toBe(false);
    page.show();
    type(page, "y");
    page.dispose();
    expect(keydown(document.body, { key: "s", ctrlKey: true }).defaultPrevented).toBe(false);
    const e = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(false);
    expect(page.el.isConnected).toBe(false);
  });

  test("a press on the empty paper or the grey area puts the caret in the page", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\ncd\n"));
    (document.activeElement as HTMLElement | null)?.blur();
    for (const sel of [".doc-page", ".doc-scroll"]) {
      const v = page.view.view;
      v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 1)));
      const e = new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: 5, clientY: 900 });
      q(page, sel).dispatchEvent(e);
      expect(e.defaultPrevented, sel).toBe(true);
      expect(v.hasFocus(), sel).toBe(true);
      expect(v.state.selection.from, sel).toBe(v.state.doc.content.size - 1); // the end
    }
  });

  test("Save to a file…: a cancelled picker is silent, any other failure is said plainly", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    page.askSaveAs = async () => ({ stem: "a", format: "txt", toFile: true });
    const w = window as unknown as { showSaveFilePicker?: () => Promise<never> };
    try {
      w.showSaveFilePicker = async () => {
        throw new DOMException("cancelled", "AbortError");
      };
      expect(await page.saveAs()).toBe(false);
      expect(q(page, ".doc-message").textContent).toBe("");
      w.showSaveFilePicker = async () => {
        throw new DOMException("no", "SecurityError");
      };
      expect(await page.saveAs()).toBe(false);
      expect(page.lastError()).toBe(PICK_FAILED);
    } finally {
      delete w.showSaveFilePicker;
    }
  });

  test("one dropped picture the editor can't add says so", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "x\n"));
    for (const file of [new File(["<svg/>"], "a.svg", { type: "image/svg+xml" }), new File([new Uint8Array(4)], "b.heic")]) {
      const e = new Event("drop", { bubbles: true, cancelable: true });
      Object.defineProperty(e, "dataTransfer", { value: { files: [file], types: ["Files"], getData: () => "" } });
      page.view.view.dom.dispatchEvent(e);
      await vi.waitFor(() => expect(page.lastError()).toBe(NOT_A_PICTURE));
      q(page, ".doc-message").textContent = "";
    }
  });

  test("Ctrl+Alt+digit: the same in the text and from a button (ProseMirror's keyCode rule)", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\n"));
    // a layout where Ctrl+Alt+1 types another character: the key code decides, as in ProseMirror
    const init = { key: "¹", code: "Digit1", keyCode: 49, ctrlKey: true, altKey: true };
    keydown(page.view.view.dom, init);
    expect(html(page)).toBe("<h1>ab</h1>");
    keydown(page.view.view.dom, { key: "0", code: "Digit0", keyCode: 48, ctrlKey: true, altKey: true });
    keydown(buttonNamed(page, "Undo") as HTMLElement, init);
    expect(html(page)).toBe("<h1>ab</h1>");
    // on Windows Ctrl+Alt is AltGr: the typed character wins (no key code fallback), in both
    const altGr = new KeyboardEvent("keydown", { key: "}", code: "Digit0", keyCode: 48, ctrlKey: true, altKey: true });
    expect(keyNames(altGr, true)).toEqual(["Mod-Alt-}"]);
    expect(keyNames(altGr, false)).toEqual(["Mod-Alt-}", "Mod-Alt-0"]);
    expect(keyNames(new KeyboardEvent("keydown", { key: "S", code: "KeyS", keyCode: 83, ctrlKey: true, shiftKey: true }), true)).toEqual(["Mod-Shift-s"]);
  });
});

describe("Task 10 (9a re-review)", () => {
  const press = (target: Element, init: MouseEventInit) => {
    const e = new MouseEvent("mousedown", { bubbles: true, cancelable: true, ...init });
    target.dispatchEvent(e);
    return e;
  };

  test("a press on the scroll area's own scrollbars leaves the selection alone", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\ncd\n"));
    const scroll = q(page, ".doc-scroll");
    // a 500 × 400 box with a 15 px scrollbar on the right and at the bottom
    const box = { left: 100, top: 50, right: 600, bottom: 450, width: 500, height: 400, x: 100, y: 50, toJSON: () => ({}) } as DOMRect;
    scroll.getBoundingClientRect = () => box;
    for (const [k, v] of Object.entries({ clientLeft: 0, clientTop: 0, clientWidth: 485, clientHeight: 385 }))
      Object.defineProperty(scroll, k, { value: v, configurable: true });
    const v = page.view.view;
    for (const [x, y] of [[590, 200], [300, 440]]) {
      v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 1, 3)));
      const e = press(scroll, { clientX: x, clientY: y });
      expect(e.defaultPrevented, `${x},${y}`).toBe(false);
      expect([v.state.selection.from, v.state.selection.to]).toEqual([1, 3]);
    }
    // inside the box it still moves the caret
    expect(press(scroll, { clientX: 300, clientY: 200 }).defaultPrevented).toBe(true);
    expect(v.state.selection.empty).toBe(true);
  });

  test("Shift+press in the margin extends the selection", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\ncd\n"));
    const v = page.view.view;
    v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 2)));
    expect(press(q(page, ".doc-page"), { clientX: 5, clientY: 900, shiftKey: true }).defaultPrevented).toBe(true);
    expect(v.state.selection.anchor).toBe(2);
    expect(v.state.selection.head).toBe(v.state.doc.content.size - 1);
  });

  test("dispose revokes the pictures' object URLs and destroys the view", async () => {
    const revoked: string[] = [];
    (URL as unknown as Record<string, unknown>).revokeObjectURL = (u: string) => revoked.push(u);
    const { page } = mount();
    await page.open(input("a.html", `<p>x<img src="data:image/png;base64,${b64(PNG)}"></p>`));
    const img = page.view.view.dom.querySelector("img") as HTMLImageElement;
    expect(img.src).toMatch(/^blob:/);
    const view = page.view.view;
    page.dispose();
    expect(revoked).toContain(img.getAttribute("src"));
    expect(view.isDestroyed).toBe(true);
  });
});

describe("Task 11 (10a re-review)", () => {
  test("an open taken over by a later one that fails reports null, not the later one's error", async () => {
    let release!: () => void;
    const { page } = mount(() => new Promise((_r, reject) => (release = () => reject(new Error("slow")))));
    page.view.maxDepth = 4;
    const first = page.open(input("a.odt", new Uint8Array([1])));
    await vi.waitFor(() => expect(release).toBeTypeOf("function")); // past "Opening…", converting
    expect(await page.open(input("deep.html", "<blockquote><blockquote><blockquote><blockquote><p>x</p></blockquote></blockquote></blockquote></blockquote>"))).toBe(false);
    expect(page.lastError()).not.toBe("");
    release();
    expect(await first).toBe(null);
  });

  test("a press on a scrollbar on the left (right-to-left pages) leaves the selection alone", async () => {
    const { page } = mount();
    await page.open(input("a.txt", "ab\ncd\n"));
    const scroll = q(page, ".doc-scroll");
    const box = { left: 100, top: 50, right: 600, bottom: 450, width: 500, height: 400, x: 100, y: 50, toJSON: () => ({}) } as DOMRect;
    scroll.getBoundingClientRect = () => box;
    // RTL: the vertical bar is on the left, so clientLeft includes it
    for (const [k, v] of Object.entries({ clientLeft: 15, clientTop: 0, clientWidth: 485, clientHeight: 385 }))
      Object.defineProperty(scroll, k, { value: v, configurable: true });
    const v = page.view.view;
    v.dispatch(v.state.tr.setSelection(TextSelection.create(v.state.doc, 1, 3)));
    const e = new MouseEvent("mousedown", { bubbles: true, cancelable: true, clientX: 105, clientY: 200 });
    scroll.dispatchEvent(e);
    expect(e.defaultPrevented).toBe(false);
    expect([v.state.selection.from, v.state.selection.to]).toEqual([1, 3]);
  });
});

test("a taken-over open's download progress leaves the later open's message alone", async () => {
  let release!: () => void;
  const { page } = mount((_t, _f, _o, onProgress) => new Promise((_r, reject) => (release = () => {
    onProgress(0, "Loading document engine… 50 %");
    reject(new Error("slow"));
  })));
  page.view.maxDepth = 4;
  const first = page.open(input("a.odt", new Uint8Array([1])));
  await vi.waitFor(() => expect(release).toBeTypeOf("function")); // past "Opening…", converting
  expect(await page.open(input("deep.html", "<blockquote><blockquote><blockquote><blockquote><p>x</p></blockquote></blockquote></blockquote></blockquote>"))).toBe(false);
  release();
  expect(await first).toBe(null);
  expect(page.lastError()).toBe(TOO_DEEP);
});
