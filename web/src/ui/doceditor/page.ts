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
import type { Command } from "prosemirror-state";
import { defaults } from "../../core/options";
import { canonicalExt, detect, familyOfExt } from "../../core/detect";
import { converterById, targetsFor } from "../../core/registry";
import type { InputFile } from "../../core/types";
import { isEditableMarkdownZip, openDocument, type Convert } from "../../doceditor/open";
import { PY_WS } from "../../doceditor/pycompat";
import { SAVE_FORMATS, saveDocument } from "../../doceditor/save";
import { MAX_FILE_BYTES, WEB_EDITABLE_DOCUMENT_EXTS, warningText } from "../../doceditor/types";
import type { ConversionClient } from "../../worker/client";
import { FindBar } from "./findbar";
import { FormatBar, browserAsker, type Asker } from "./formatbar";
import { DocView, EDITOR_KEYS, pictureExt, plainText } from "./view";

export const ONLY_ONE = "Only one document can be edited at a time.";
export const DISCARD = "Discard unsaved changes to this document?";
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

/** `e` as a key name like ProseMirror's keymaps ("Mod-Shift-s", "Mod-Alt-1", "Mod-Space"). */
function keyName(e: KeyboardEvent): string {
  let key = e.key === " " ? "Space" : e.key;
  if (key.length === 1) key = key.toLowerCase();
  // with Alt, some layouts turn a digit into another character: the digit is in `code`
  if (e.altKey && /^Digit\d$/.test(e.code)) key = e.code.slice(5);
  return `${e.ctrlKey || e.metaKey ? "Mod-" : ""}${e.altKey ? "Alt-" : ""}${e.shiftKey ? "Shift-" : ""}${key}`;
}

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
  private warnings: string[] = [];
  private warned = false;
  private lastSave: SaveTarget | null = null;
  private saving: Promise<boolean> | null = null;
  /** Counts opens: a result is used only by the latest one. */
  private opens = 0;
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
    this.el.addEventListener("keydown", (e) => this.key(e));
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
    window.addEventListener("beforeunload", (e) => {
      if (this.active && this.view.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
    this.updateButtons();
    this.updateStatus();
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
    if (e.defaultPrevented || e.isComposing || this.el.hidden) return;
    const name = keyName(e);
    const page: Record<string, () => void> = {
      "Mod-s": () => void this.save(),
      "Mod-Shift-s": () => void this.saveAs(),
      "Mod-f": () => this.findBar.open(false),
      "Mod-h": () => this.findBar.open(true),
      "Mod-k": () => this.formatBar.buttons.link.click(),
    };
    const t = e.target as HTMLElement;
    if (t.closest("dialog")) return;
    if (Object.hasOwn(page, name)) {
      e.preventDefault();
      page[name]();
      return;
    }
    // the editor's own keys work from the bars too (not in a text field: its own undo, typing)
    if (t.closest("input, select, textarea") || this.view.view.dom.contains(t)) return;
    const command = Object.hasOwn(EDITOR_KEYS, name) && name.startsWith("Mod-") ? EDITOR_KEYS[name] : null;
    if (command) {
      e.preventDefault();
      this.runCommand(command);
    }
  }

  // ---------------------------------------------------------------- converting
  /** Runs `doc:{to}` in the conversion worker for bytes named `name`, with the converter's
   * default options plus `options`; the engine's download progress goes to the message line. */
  private makeConvert(): Convert {
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
        const pct = ENGINE_LOADING.exec(m ?? "");
        if (pct) this.showMessage(`Downloading the document converter… ${pct[1]} %`);
        else if (m?.startsWith("Loading document engine")) this.showMessage("Downloading the document converter…");
      });
      if (this.message.textContent?.startsWith("Downloading the document converter")) this.showMessage(before.text, before.error);
      if (!outs[0]) throw new Error(`Couldn't convert “${name}”.`);
      return outs[0].data;
    };
  }

  // ---------------------------------------------------------------- opening
  /** Open `file`; false (with the reason in the message line) when it can't be edited. While a
   * save runs, it opens when that is done; a later open wins over one still running. */
  async open(file: InputFile): Promise<boolean> {
    const token = ++this.opens;
    if (this.saving) {
      this.showMessage("Saving… the document will open when it's done.");
      await this.saving;
      if (token !== this.opens) return false;
    }
    this.showMessage(`Opening “${file.name}”…`);
    try {
      const doc = await openDocument(file.file, this.convert);
      if (token !== this.opens) return false;
      this.view.load(doc);
      this.loads++;
      this.source = { name: file.name, stem: stemOf(file.name), format: doc.sourceFormat };
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
      if (token !== this.opens) return false;
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
      } catch {
        return false; // picker cancelled
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
