/**
 * The website's document editor page (the desktop's `gui/doceditor/doc_editor_page.py`): a top
 * bar (Back, Undo / Redo, Save as… / Save), the formatting bar, the find bar, the "parts that will
 * be lost" banner, the white page and a status line ("N words · N characters · Unsaved changes").
 * Loaded on demand from ui/app.ts. ODT / RTF / PDF go through the conversion worker (`Convert`).
 *
 * Saving downloads a file (never overwrites anything); "Save to a file…" (Chrome / Edge) writes to
 * a file the user picked, and Save (Ctrl+S) then writes to that file again.
 */

import { redo, redoDepth, undo, undoDepth } from "prosemirror-history";
import { Selection, TextSelection, type Command } from "prosemirror-state";
import { defaults } from "../../core/options";
import { canonicalExt, detect, familyOfExt } from "../../core/detect";
import { converterById, targetsFor } from "../../core/registry";
import type { InputFile } from "../../core/types";
import { isEditableMarkdownZip } from "../../doceditor/mdzip";
import { openDocument, type Convert } from "../../doceditor/open";
import { PY_WS } from "../../doceditor/pycompat";
import { SAVE_FORMATS, saveDocument } from "../../doceditor/save";
import { MAX_FILE_BYTES, WEB_EDITABLE_DOCUMENT_EXTS, warningText } from "../../doceditor/types";
import type { ConversionClient } from "../../worker/client";
import { FindBar } from "./findbar";
import { FormatBar, browserAsker, type Asker } from "./formatbar";
import { DocView, EDITOR_KEYS, NOT_A_PICTURE, pictureExt, plainText } from "./view";

export const ONLY_ONE = "Only one document can be edited at a time.";
export const DISCARD = "Discard unsaved changes to this document?";
/** The name a new document (the start screen's tile) is offered to be saved as. */
export const UNTITLED = "Untitled";
const STATUS_DELAY_MS = 150;
/** What the Pandoc engine says while it downloads (`engines/pandoc.ts`), with its percent. */
const ENGINE_LOADING = /^Loading document engine.*?(\d+) %$/;

export interface DocEditorOptions {
  client: ConversionClient;
  onBack: () => void;
  /** The questions the formatting bar asks (the browser's own pickers by default). */
  asker?: Asker;
}

/** What Save as chose: the file name without extension, a format id of `SAVE_FORMATS`, and
 * whether to write to a file picked with the browser's save picker. */
export interface SaveChoice {
  stem: string;
  format: string;
  toFile: boolean;
}

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

interface SaveTarget {
  stem: string;
  format: string;
  handle?: FileHandle;
}

const PY_SPLIT = new RegExp(`[${PY_WS}]+`);
const NOT_COUNTED = new Set(["\n", " ", " ", "￼"]);

/** Words and characters of `text`, the desktop's rule: words are split at white space (Python's
 * `str.split()`), characters are code points other than line and paragraph breaks. */
export function countText(text: string): { words: number; chars: number } {
  const words = text.split(PY_SPLIT).filter(Boolean).length;
  let chars = 0;
  for (const c of text) if (!NOT_COUNTED.has(c)) chars++;
  return { words, chars };
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/** The name without its extension ("a.tar.gz" keeps "a.tar"; a name without one stays). */
const stemOf = (name: string) => (name.includes(".") ? name.slice(0, name.lastIndexOf(".")) : name);

const WINDOWS = typeof navigator !== "undefined" && /Win/.test(navigator.platform);

/** The names `e` is looked up by, like ProseMirror's keymaps ("Mod-Shift-s", "Mod-Alt-1",
 * "Mod-Space"): the key typed, then (ProseMirror's rule, so a shortcut works the same in the
 * text and elsewhere on the page) the key code's letter or digit when a modifier is held and it
 * typed another character — except Ctrl+Alt on Windows, which is AltGr typing a character. */
export function keyNames(e: KeyboardEvent, windows = WINDOWS): string[] {
  const mods = `${e.ctrlKey || e.metaKey ? "Mod-" : ""}${e.altKey ? "Alt-" : ""}${e.shiftKey ? "Shift-" : ""}`;
  let key = e.key === " " ? "Space" : e.key;
  if (key.length === 1) key = key.toLowerCase();
  const names = [mods + key];
  const code = e.keyCode;
  const base = code >= 48 && code <= 57 ? String(code - 48) : code >= 65 && code <= 90 ? String.fromCharCode(code + 32) : "";
  if (key.length === 1 && key !== " " && (e.altKey || e.metaKey || e.ctrlKey) && !(windows && e.ctrlKey && e.altKey) && base && base !== key) {
    names.push(mods + base);
  }
  return names;
}

/** Shown when the browser's save window couldn't be opened (other than being cancelled). */
export const PICK_FAILED = "The browser couldn't open its save window. Try again, or use Save as without “Save to a file…”.";

export class DocEditorPage {
  readonly el: HTMLElement = el("div", "editor doc-editor");
  readonly view: DocView;
  readonly formatBar: FormatBar;
  readonly findBar: FindBar;
  /** Asks the Save as questions (replaceable in tests). */
  askSaveAs: (stem: string, format: string) => Promise<SaveChoice | null>;

  private readonly banner = el("div", "doc-banner");
  private readonly bannerText = el("span", "doc-banner-text");
  private readonly message = el("div", "doc-message msg");
  private readonly status = el("div", "doc-status");
  private readonly undoBtn: HTMLButtonElement;
  private readonly redoBtn: HTMLButtonElement;
  private readonly saveBtn: HTMLButtonElement;
  private readonly saveAsBtn: HTMLButtonElement;
  private readonly saveDialog: HTMLDialogElement;
  private source: { name: string; stem: string; format: string } | null = null;
  /** A new document from the start screen: the first Save asks where, like Save as. */
  private untitled = false;
  private warnings: string[] = [];
  private warned = false;
  private lastSave: SaveTarget | null = null;
  private saving: Promise<boolean> | null = null;
  /** Counts opens: a result is used only by the latest one. */
  private opens = 0;
  /** Opens still running (the latest and any taken over), for the e2e hook. */
  private running = 0;
  /** Counts loaded documents: a save finishing after another one opened leaves it alone. */
  private loads = 0;
  private active = false;
  private statusTimer: ReturnType<typeof setTimeout> | undefined;
  /** What the converter is doing, shown while nothing else is (`Opening…`, `Saving…`). */
  private readonly convert: Convert;

  constructor(private readonly opts: DocEditorOptions) {
    this.el.hidden = true;
    const page = el("div", "doc-page");
    this.view = new DocView(page);
    this.formatBar = new FormatBar(this.view, opts.asker ?? browserAsker);
    this.findBar = new FindBar(this.view);
    this.formatBar.onError = (m) => this.showMessage(m, true);
    this.view.onError = (m) => this.showMessage(m, true);
    this.view.onDropFiles = (files) => void this.droppedFiles(files);
    this.convert = this.makeConvert();

    const btn = (label: string, title: string, run: () => void, cls = "btn small") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", run);
      return b;
    };
    this.undoBtn = btn("Undo", "Undo (Ctrl+Z)", () => this.runCommand(undo));
    this.redoBtn = btn("Redo", "Redo (Ctrl+Y)", () => this.runCommand(redo));
    this.saveAsBtn = btn("Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs());
    this.saveBtn = btn("Save", "Save (Ctrl+S)", () => void this.save(), "primary small");
    // the buttons leave the focus (and the selection) in the page, like the format bar's
    for (const b of [this.undoBtn, this.redoBtn]) b.addEventListener("mousedown", (e) => e.preventDefault());
    const gap = () => el("span", "gap");
    const top = el("div", "editor-top");
    top.append(
      btn("← Back to Convert", "Leave the editor", () => this.back(), "link"),
      gap(),
      this.undoBtn,
      this.redoBtn,
      el("div", "spacer"),
      this.saveAsBtn,
      this.saveBtn,
    );

    const close = btn("×", "Close this notice", () => (this.banner.hidden = true), "btn small doc-banner-close");
    close.setAttribute("aria-label", "Close this notice");
    this.banner.append(this.bannerText, close);
    this.banner.hidden = true;
    this.banner.setAttribute("role", "status");
    this.message.setAttribute("aria-live", "polite");

    const bars = el("div", "doc-bars");
    bars.append(this.formatBar.el, this.findBar.el, this.banner);
    const scroll = el("div", "doc-scroll");
    scroll.append(page);
    const statusBar = el("div", "editor-status doc-statusbar");
    statusBar.append(this.message, this.status);
    const body = el("div", "doc-body");
    body.append(bars, scroll);
    this.saveDialog = this.buildSaveDialog();
    this.askSaveAs = (stem, format) => this.askSaveAsDialog(stem, format);
    this.el.append(top, body, statusBar, this.saveDialog);

    this.view.onChange = () => {
      this.updateButtons();
      clearTimeout(this.statusTimer);
      this.statusTimer = setTimeout(() => this.updateStatus(), STATUS_DELAY_MS);
    };
    // the page's keys work wherever the focus is while it is shown (the grey area, the banner…)
    window.addEventListener("keydown", this.onKey);
    // a press on the empty paper or around it puts the caret in the page, like the desktop
    scroll.addEventListener("mousedown", (e) => this.pressOutside(e));
    // links in the page are text to edit: no click (Ctrl+click, middle click) ever follows one
    for (const type of ["click", "auxclick"]) {
      this.view.view.dom.addEventListener(type, (e) => {
        if ((e.target as Element | null)?.closest?.("a")) e.preventDefault();
      });
    }
    // files dropped anywhere on the page stay here: the browser would otherwise open them
    this.el.addEventListener("dragover", (e) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
    });
    this.el.addEventListener("drop", (e) => {
      const files = Array.from(e.dataTransfer?.files ?? []);
      if (!files.length) return;
      e.stopPropagation();
      if (e.defaultPrevented) return; // the page's own drop handled it
      e.preventDefault();
      void this.droppedFiles(files);
    });
    window.addEventListener("beforeunload", this.onUnload);
    this.updateButtons();
    this.updateStatus();
    if (typeof location !== "undefined" && location.search.includes("e2e")) this.installTestHook();
  }

  /** `window.__doceditor` for the e2e tests: state reads, like the image editor's `__editor`. */
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __doceditor: object }).__doceditor = {
      /** The document as the exchange-format HTML a save would write. */
      html: () => page.view.content().html,
      text: () => page.view.content().plainText,
      get dirty() {
        return page.view.dirty;
      },
      /** The editor's selection as [anchor, head] positions. */
      get selection() {
        const s = page.view.view.state.selection;
        return [s.anchor, s.head];
      },
      /** Opens still running, taken-over ones included. */
      get opening() {
        return page.running;
      },
      get unloadAsks() {
        const e = new Event("beforeunload", { cancelable: true });
        page.onUnload(e as BeforeUnloadEvent);
        return e.defaultPrevented;
      },
    };
  }

  private readonly onKey = (e: KeyboardEvent) => this.key(e);
  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.active && this.view.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  /** Remove the page and everything it listens to on the window. */
  dispose(): void {
    this.active = false;
    clearTimeout(this.statusTimer);
    window.removeEventListener("keydown", this.onKey);
    window.removeEventListener("beforeunload", this.onUnload);
    this.view.destroy();
    this.el.remove();
  }

  /** A press on the paper outside the text, or the grey area: the caret goes to the nearest
   * place in the text (the end when there is none to find). */
  private pressOutside(e: MouseEvent): void {
    const v = this.view.view;
    if (e.button !== 0 || v.dom.contains(e.target as Node)) return;
    // a press on the scroll area's own scrollbars (Chrome / Edge fire mousedown there) scrolls
    const scroll = e.currentTarget as HTMLElement | null;
    if (scroll?.getBoundingClientRect) {
      const r = scroll.getBoundingClientRect();
      const x = e.clientX - r.left;
      const y = e.clientY - r.top;
      // outside the content box: a bar on the right, or on the left (right-to-left pages)
      if ((x < scroll.clientLeft || x >= scroll.clientLeft + scroll.clientWidth) && x >= 0 && x < r.width) return;
      if (y >= scroll.clientTop + scroll.clientHeight && y < r.height) return;
    }
    e.preventDefault();
    let pos: number | null = null;
    try {
      // the point moved into the text's box: a press beside a line goes to that line
      const r = v.dom.getBoundingClientRect();
      if (r.width > 2 && r.height > 2) {
        const left = Math.min(Math.max(e.clientX, r.left + 1), r.right - 1);
        const top = Math.min(Math.max(e.clientY, r.top + 1), r.bottom - 1);
        pos = v.posAtCoords({ left, top })?.pos ?? null;
      }
    } catch {
      /* no layout to read */
    }
    const doc = v.state.doc;
    const $pos = doc.resolve(pos ?? doc.content.size);
    const near = pos === null ? Selection.atEnd(doc) : Selection.near($pos);
    // Shift+press extends the selection to that place, like QTextEdit
    const sel = e.shiftKey && near instanceof TextSelection ? TextSelection.create(doc, v.state.selection.anchor, near.head) : near;
    v.dispatch(v.state.tr.setSelection(sel));
    v.focus();
  }

  // ---------------------------------------------------------------- showing
  show(): void {
    this.active = true;
    this.el.hidden = false;
    this.view.focus();
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
  }

  lastError(): string {
    return this.message.classList.contains("error") ? (this.message.textContent ?? "") : "";
  }

  private showMessage(text: string, error = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", error);
  }

  private updateButtons(): void {
    const state = this.view.view.state;
    this.undoBtn.disabled = undoDepth(state) === 0;
    this.redoBtn.disabled = redoDepth(state) === 0;
    const ready = this.source !== null && this.saving === null;
    this.saveBtn.disabled = !ready;
    this.saveAsBtn.disabled = !ready;
  }

  /** The status line now (it otherwise follows changes after a short pause). */
  updateStatus(): void {
    clearTimeout(this.statusTimer);
    if (!this.source) {
      this.status.textContent = "";
      return;
    }
    // a picture counts as a space, empty links not at all (the desktop's rule)
    const { words, chars } = countText(plainText(this.view.view.state.doc, " "));
    this.status.textContent = `${words} words · ${chars} characters${this.view.dirty ? " · Unsaved changes" : ""}`;
  }

  private runCommand(command: Command): void {
    this.view.run(command);
    this.view.focus();
  }

  // ---------------------------------------------------------------- keys
  private key(e: KeyboardEvent): void {
    if (!this.active || e.defaultPrevented || e.isComposing || this.el.hidden) return;
    const names = keyNames(e);
    const page: Record<string, () => void> = {
      "Mod-s": () => void this.save(),
      "Mod-Shift-s": () => void this.saveAs(),
      "Mod-f": () => this.findBar.open(false),
      "Mod-h": () => this.findBar.open(true),
      "Mod-k": () => this.formatBar.buttons.link.click(),
    };
    const t = e.target instanceof Element ? e.target : document.body;
    if (t.closest("dialog")) return;
    const pageKey = names.find((n) => Object.hasOwn(page, n));
    if (pageKey) {
      e.preventDefault();
      page[pageKey]();
      return;
    }
    // the editor's own keys work from the bars too (not in a text field: its own undo, typing);
    // in the text, its keymap has had them already
    if (t.closest("input, select, textarea") || this.view.view.dom.contains(t)) return;
    const name = names.find((n) => n.startsWith("Mod-") && Object.hasOwn(EDITOR_KEYS, n));
    const command = name ? EDITOR_KEYS[name] : null;
    if (command) {
      e.preventDefault();
      this.runCommand(command);
    }
  }

  // ---------------------------------------------------------------- converting
  /** Runs `doc:{to}` in the conversion worker for bytes named `name`, with the converter's
   * default options plus `options`; the engine's download progress goes to the message line
   * while `current()` (an open taken over by a later one leaves it to that one). */
  private makeConvert(current = () => true): Convert {
    return async (bytes, fromExt, toExt, name, options = {}) => {
      const file: InputFile = {
        file: new File([bytes as BlobPart], name),
        name,
        ext: fromExt,
        family: familyOfExt(fromExt) ?? "document",
      };
      const target = targetsFor([file]).targets.find((t) => t.id === `doc:${toExt}`);
      if (!target) throw new Error(`Can't convert ${fromExt.toUpperCase()} to ${toExt.toUpperCase()} here.`);
      const conv = converterById(target.converterId);
      const before = { text: this.message.textContent ?? "", error: this.message.classList.contains("error") };
      const outs = await this.opts.client.run(target, [file], { ...defaults(conv.options), ...options }, (_f, m) => {
        if (!current()) return; // another open has the message line now
        const pct = ENGINE_LOADING.exec(m ?? "");
        if (pct) this.showMessage(`Downloading the document converter… ${pct[1]} %`);
        else if (m?.startsWith("Loading document engine")) this.showMessage("Downloading the document converter…");
      });
      if (current() && this.message.textContent?.startsWith("Downloading the document converter")) this.showMessage(before.text, before.error);
      if (!outs[0]) throw new Error(`Couldn't convert “${name}”.`);
      return outs[0].data;
    };
  }

  // ---------------------------------------------------------------- opening
  /** A new, empty document named "Untitled" (the start screen's tile): nothing to save yet, and
   * the first Save asks for a name and format (DOCX first). A running open is dropped. */
  openEmpty(): void {
    this.opens++;
    this.view.load({ html: "", images: new Map(), sourceFormat: "docx", warnings: [], frontMatter: "", title: "", template: null });
    this.loads++;
    this.source = { name: UNTITLED, stem: UNTITLED, format: "docx" };
    this.untitled = true;
    this.warnings = [];
    this.warned = false;
    this.lastSave = null;
    this.bannerText.textContent = "";
    this.banner.hidden = true;
    this.findBar.el.hidden = true;
    this.showMessage("");
    this.updateButtons();
    this.updateStatus();
    if (this.active) this.view.focus();
  }

  /** Open `file`; false (with the reason in the message line) when it can't be edited. While a
   * save runs, it opens when that is done; a later open wins over one still running, and the
   * one taken over gives null (the message line is then the later one's). */
  async open(file: InputFile): Promise<boolean | null> {
    const token = ++this.opens;
    this.running++;
    try {
      return await this.openNow(file, token);
    } finally {
      this.running--;
    }
  }

  private async openNow(file: InputFile, token: number): Promise<boolean | null> {
    if (this.saving) {
      this.showMessage("Saving… the document will open when it's done.");
      await this.saving;
      if (token !== this.opens) return null;
    }
    this.showMessage(`Opening “${file.name}”…`);
    // one frame first, so "Opening…" is drawn before the (synchronous) parse freezes the page;
    // a hidden tab draws no frames, so it doesn't wait longer than 100 ms for one
    await new Promise<void>((r) => {
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => setTimeout(r, 0));
      setTimeout(r, typeof requestAnimationFrame === "function" ? 100 : 0);
    });
    if (token !== this.opens) return null;
    try {
      const doc = await openDocument(file.file, this.makeConvert(() => token === this.opens));
      if (token !== this.opens) return null;
      this.view.load(doc);
      this.loads++;
      this.source = { name: file.name, stem: stemOf(file.name), format: doc.sourceFormat };
      this.untitled = false;
      this.warnings = [...doc.warnings];
      this.warned = false;
      this.lastSave = null;
      this.bannerText.textContent = warningText(this.warnings);
      this.banner.hidden = this.warnings.length === 0;
      this.findBar.el.hidden = true;
      this.showMessage("");
      this.updateButtons();
      this.updateStatus();
      if (this.active) this.view.focus();
      return true;
    } catch (err) {
      if (token !== this.opens) return null;
      console.error(`Couldn't open ${file.name}`, err);
      // our own errors (and the converter's) are plain language; anything else isn't
      const plain = err instanceof Error && err.constructor === Error && err.message;
      this.showMessage(plain || `Couldn't open “${file.name}”.`, true);
      return false;
    }
  }

  /** Whether `file` is a document this page can edit. */
  private async editable(file: InputFile): Promise<boolean> {
    const ext = canonicalExt(file.name);
    if (WEB_EDITABLE_DOCUMENT_EXTS.includes(ext)) return true;
    if (ext !== "zip" || file.file.size > MAX_FILE_BYTES) return false;
    try {
      return isEditableMarkdownZip(new Uint8Array(await file.file.arrayBuffer()));
    } catch {
      return false;
    }
  }

  /** Dropped files: one editable document replaces the open one (after asking about unsaved
   * changes); anything else is refused. */
  async dropFiles(files: InputFile[]): Promise<void> {
    if (files.length !== 1 || !(await this.editable(files[0]))) {
      this.showMessage(ONLY_ONE, true);
      return;
    }
    if (this.saving) await this.saving;
    if (this.maybeLeave()) await this.open(files[0]);
  }

  /** Files dropped on the page itself: pictures go into the document, anything else is a
   * document to open. */
  private async droppedFiles(files: File[]): Promise<void> {
    if (files.every((f) => pictureExt(f) !== null)) {
      await this.view.insertPictureFiles(files);
      return;
    }
    const found = await Promise.all(files.map((f) => detect(f).catch(() => null)));
    const one = found.length === 1 ? found[0] : null;
    if (files.length === 1 && (files[0].type.startsWith("image/") || one?.family === "image" || one?.family === "vector")) {
      // one picture the editor can't add (SVG, HEIC, TIFF…): say that, not "one document"
      this.showMessage(NOT_A_PICTURE, true);
      return;
    }
    if (found.some((f) => f === null)) {
      this.showMessage(ONLY_ONE, true);
      return;
    }
    await this.dropFiles(found as InputFile[]);
  }

  // ---------------------------------------------------------------- leaving
  /** True when it's fine to leave (nothing unsaved, or the user agreed to discard). */
  maybeLeave(): boolean {
    if (!this.source || !this.view.dirty) return true;
    return window.confirm(DISCARD);
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- saving
  /** Save in the original format under the original name, or as the last Save as did. The first
   * save over a source with parts the editor can't keep asks first. */
  async save(): Promise<boolean> {
    if (!this.source || this.saving) return false;
    if (this.lastSave) return this.write(this.lastSave);
    if (this.untitled) return this.saveAs();
    if (this.warnings.length && !this.warned) {
      if (!window.confirm(`Parts of this document will be lost (${this.warnings.join(", ")}). Save anyway?`)) return false;
      this.warned = true;
    }
    return this.write({ stem: this.source.stem, format: this.source.format });
  }

  /** Ask for a name and a format (and maybe a file), then save. */
  async saveAs(): Promise<boolean> {
    if (!this.source || this.saving) return false;
    const ids = SAVE_FORMATS.map(([id]) => id);
    const format = this.lastSave?.format ?? (ids.includes(this.source.format) ? this.source.format : "docx");
    const choice = await this.askSaveAs(this.lastSave?.stem ?? this.source.stem, format);
    if (!choice || !this.source || this.saving) return false;
    let handle: FileHandle | undefined;
    if (choice.toFile) {
      const ext = this.extFor(choice.format);
      try {
        handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({
          suggestedName: `${choice.stem.replace(/[\\/:*?"<>|]/g, "_")}.${ext}`,
        });
      } catch (err) {
        if ((err as { name?: string } | null)?.name !== "AbortError") {
          console.error("The save window couldn't be opened", err);
          this.showMessage(PICK_FAILED, true);
        }
        return false; // cancelled: nothing to say
      }
    }
    this.lastSave = { stem: choice.stem, format: choice.format, handle };
    return this.write(this.lastSave);
  }

  /** The extension a save in `format` gets now (Markdown with pictures is a ZIP). */
  private extFor(format: string): string {
    return format === "md" && this.view.content().images.size ? "zip" : format;
  }

  private write(target: SaveTarget): Promise<boolean> {
    const run = this.writeNow(target);
    this.saving = run;
    this.updateButtons();
    void run.finally(() => {
      if (this.saving === run) this.saving = null;
      this.updateButtons();
      this.updateStatus();
    });
    return run;
  }

  private async writeNow(target: SaveTarget): Promise<boolean> {
    const content = this.view.content();
    // edits made while saving stay unsaved: this document (the version saved) is marked clean
    const saved = this.view.view.state.doc;
    const loaded = this.loads;
    const notices: string[] = [];
    this.showMessage(`Saving “${target.stem}.${target.format}”…`);
    try {
      const { blob, name } = await saveDocument(content, target.format, target.stem, this.convert, {
        onWarning: (m) => notices.push(m),
      });
      const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
      const handle = target.handle;
      if (handle && (handle.name === undefined || handle.name.toLowerCase().endsWith(`.${ext}`))) {
        const w = await handle.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        // a picked file of another kind (Markdown that now has pictures is a ZIP): download it
        if (handle) notices.push(`It was downloaded as “${name}” instead of written to “${handle.name}”.`);
        const url = URL.createObjectURL(blob);
        const a = el("a");
        a.href = url;
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      if (this.loads === loaded) this.view.markClean(saved);
      this.showMessage([`Saved “${handle?.name ?? name}”.`, ...notices].join(" "));
      return true;
    } catch (err) {
      console.error(`Couldn't save ${target.stem}`, err);
      const plain = err instanceof Error && err.constructor === Error && err.message;
      this.showMessage(plain ? `Couldn't save: ${plain}` : `Couldn't save “${target.stem}.${target.format}”.`, true);
      return false;
    }
  }

  // ---------------------------------------------------------------- the Save as dialog
  private buildSaveDialog(): HTMLDialogElement {
    const dialog = el("dialog", "editor-dialog doc-save-dialog");
    dialog.setAttribute("aria-label", "Save document as");
    const name = el("input");
    name.type = "text";
    name.setAttribute("aria-label", "File name");
    const format = el("select");
    format.setAttribute("aria-label", "Format");
    for (const [id, label] of SAVE_FORMATS) format.append(new Option(label, id));
    const field = (label: string, control: HTMLElement) => {
      const row = el("label", "field");
      row.append(el("span", "", label), control);
      return row;
    };
    const buttons = el("div", "row");
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    const cancel = button("Cancel", "btn", "");
    const toFile = button("Save to a file…", "btn", "file");
    toFile.title = "Choose where to save; Save (Ctrl+S) then writes to the same file";
    const ok = button("Save", "primary small", "save");
    buttons.append(el("div", "spacer"), cancel, toFile, ok);
    dialog.append(el("h2", "", "Save document as"), field("File name", name), field("Format", format), buttons);
    return dialog;
  }

  private askSaveAsDialog(stem: string, format: string): Promise<SaveChoice | null> {
    const dialog = this.saveDialog;
    const name = dialog.querySelector("input") as HTMLInputElement;
    const select = dialog.querySelector("select") as HTMLSelectElement;
    const toFile = dialog.querySelector('[data-value="file"]') as HTMLButtonElement;
    toFile.hidden = !("showSaveFilePicker" in window);
    name.value = stem;
    select.value = format;
    return new Promise((resolve) => {
      const finish = (value: string) => {
        dialog.removeEventListener("click", clicked);
        dialog.removeEventListener("cancel", cancelled);
        dialog.removeEventListener("keydown", keyed);
        if (typeof dialog.close === "function") dialog.close();
        else dialog.removeAttribute("open");
        resolve(value ? { stem: name.value.trim() || stem, format: select.value, toFile: value === "file" } : null);
        this.view.focus();
      };
      const clicked = (e: Event) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-value]");
        if (b) finish(b.dataset.value ?? "");
      };
      const cancelled = (e: Event) => {
        e.preventDefault();
        finish("");
      };
      const keyed = (e: KeyboardEvent) => {
        if (e.key === "Enter" && e.target === name) {
          e.preventDefault();
          finish("save");
        }
      };
      dialog.addEventListener("click", clicked);
      dialog.addEventListener("cancel", cancelled);
      dialog.addEventListener("keydown", keyed);
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
      name.focus();
      name.select();
    });
  }
}

export function mountDocEditor(host: HTMLElement, opts: DocEditorOptions): DocEditorPage {
  const page = new DocEditorPage(opts);
  host.append(page.el);
  return page;
}
