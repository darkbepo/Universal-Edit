/**
 * The website's code editor page (the desktop's `gui/codeeditor/code_editor_page.py`): a tab per
 * file, CodeMirror with the shared colours, find & replace, a status bar with the position,
 * language, encoding and line endings. Loaded on demand from ui/app.ts.
 *
 * Saving is byte-exact (`codeedit/textfile.ts`): untouched text saves the bytes that were opened.
 * Save downloads a file; "Save to a file…" (Chrome / Edge) writes to a picked file, and Save
 * (Ctrl+S) then writes there again.
 */
import { EditorState, type Text } from "@codemirror/state";
import { indentUnit } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { type Diagnostic, lintGutter, linter } from "@codemirror/lint";
import { CHECKED, type Problem, check } from "../../codeedit/check";
import { loadLanguage } from "../../codeedit/cmlanguages";
import { TIDY, TidyError, tidy } from "../../codeedit/tidy";
import languages from "../../codeedit/languages.json";
import { type Encoding, type Newline, type TextFile, decode, encode } from "../../codeedit/textfile";
import type { InputFile } from "../../core/types";
import { backLabel } from "../back";
import { CodeFindBar, matchField } from "./findbar";
import { BASE_FONT_PX, baseExtensions, compartments, fontSize } from "./setup";
import { SHORTCUTS, keyName, showKey } from "./shortcuts";

export const MAX_OPEN_BYTES_WEB = 20_000_000;
export const MAX_HIGHLIGHT_BYTES = 2_000_000;
export const MAX_CHECK_BYTES = 2_000_000; // bigger files: no automatic check
const PREVIEWS = new Set(["html", "markdown"]);
export const MAX_WRAP_LINE = 100_000; // longer lines are never wrapped
export const UNTITLED = "Untitled";
const ENCODINGS: [string, Encoding, boolean][] = [
  ["UTF-8", "utf-8", false],
  ["UTF-8 with BOM", "utf-8", true],
  ["UTF-16 LE", "utf-16-le", true],
  ["UTF-16 BE", "utf-16-be", true],
  ["Windows-1252", "windows-1252", false],
];
const NEWLINES: [string, Newline][] = [
  ["Windows (CRLF)", "\r\n"],
  ["Unix (LF)", "\n"],
  ["Old Mac (CR)", "\r"],
];
const LANGS = (languages.languages as { id: string; name: string; exts: string[]; filenames: string[] }[]);

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

interface Tab {
  name: string;
  file: TextFile | null; // null: Untitled
  state: EditorState;
  savedDoc: Text;
  languageId: string;
  encoding: Encoding;
  newline: Newline;
  bom: boolean;
  indent: string;
  colours: boolean;
  longestLine: number;
  source?: string; // name, size and date of the file it came from (two "index.js" are different files)
  handle?: FileHandle;
}

export interface CodeEditorOptions {
  onBack: () => void;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/** The table's language of a file name (whole name first, then extension; else plain text). */
export function languageFor(name: string): string {
  const lower = name.toLowerCase();
  const byName = LANGS.find((l) => l.filenames.includes(lower));
  if (byName) return byName.id;
  const dot = lower.lastIndexOf(".");
  const ext = dot > 0 ? lower.slice(dot + 1) : "";
  return LANGS.find((l) => ext && l.exts.includes(ext === "htm" ? "html" : ext === "yml" ? "yaml" : ext === "markdown" ? "md" : ext))?.id ?? "text";
}

/** Two / four spaces or a tab — the desktop's editing.detect_indent. */
export function detectIndent(text: string): string {
  let tabs = 0;
  let spaces = 0;
  let smallest = 0;
  for (const line of text.split("\n")) {
    const lead = /^[ \t]*/.exec(line)![0];
    if (!lead || !line.trim()) continue;
    if (lead.startsWith("\t")) tabs++;
    else {
      spaces++;
      const n = lead.length - lead.replace(/^ +/, "").length;
      smallest = smallest ? Math.min(smallest, n) : n;
    }
  }
  if (tabs > spaces) return "\t";
  return smallest === 2 ? "  " : "    ";
}

export class CodeEditorPage {
  readonly el: HTMLElement = el("div", "editor code-editor");
  readonly view: EditorView;
  readonly findBar: CodeFindBar;
  private readonly tabsEl = el("div", "code-tabs");
  private readonly message = el("div", "msg");
  private readonly position = el("span", "code-pos");
  private readonly note = el("span", "code-note");
  private readonly tidyBtn: HTMLButtonElement;
  private readonly previewBtn: HTMLButtonElement;
  private readonly previewEl = el("div", "code-preview");
  private readonly previewFrame = el("iframe");
  private previewTimer: ReturnType<typeof setTimeout> | undefined;
  private problems: Problem[] = [];
  private tidyNote = ""; // why Tidy refused, shown until the text changes
  private readonly languageSel = el("select");
  private readonly encodingSel = el("select");
  private readonly newlineSel = el("select");
  private readonly indentEl = el("span", "code-indent");
  private readonly saveBtn: HTMLButtonElement;
  private readonly saveAsBtn: HTMLButtonElement;
  private readonly picker = el("input");
  private readonly helpDialog: HTMLDialogElement;
  private readonly saveDialog: HTMLDialogElement;
  private tabs: Tab[] = [];
  private current = -1;
  private active = false;
  private saving = false;
  private fontPx = BASE_FONT_PX;
  private wrap = false;
  private readonly actions: Record<string, () => void>;
  private readonly base = baseExtensions({
    onChange: () => this.changed(),
    onCursor: (l, c) => (this.position.textContent = `Ln ${l}, Col ${c}`),
  });

  constructor(private readonly opts: CodeEditorOptions) {
    this.el.hidden = true;
    const btn = (label: string, title: string, run: () => void, cls = "btn small") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", run);
      return b;
    };
    this.saveAsBtn = btn("Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs());
    this.saveBtn = btn("Save", "Save (Ctrl+S)", () => void this.save(), "primary small");
    this.tidyBtn = btn("Tidy", "Lay out the JSON or XML neatly (one undo step)", () => this.tidy());
    this.previewBtn = btn("Preview", "Preview the HTML or Markdown", () => this.togglePreview());
    this.previewBtn.setAttribute("aria-pressed", "false");
    const tools = el("div", "top-tools");
    tools.append(
      btn("Open…", "Open files (Ctrl+O)", () => this.picker.click()),
      btn("New", "New file (Alt+N)", () => this.openEmpty()),
      el("div", "spacer"),
      this.tidyBtn,
      this.previewBtn,
      btn("Find", "Find and replace (Ctrl+F, Ctrl+H)", () => this.findBar.open(false)),
      btn("?", "Keyboard shortcuts (F1)", () => this.showHelp()),
    );
    const top = el("div", "editor-top");
    top.append(backLabel(btn("← Back to Convert", "Leave the editor", () => this.back(), "link")), el("span", "gap"), tools, this.saveAsBtn, this.saveBtn);

    this.view = new EditorView({ state: this.newState("") });
    this.findBar = new CodeFindBar(() => this.view);
    const body = el("div", "code-body");
    const host = el("div", "code-host");
    host.append(this.view.dom);
    this.tabsEl.setAttribute("role", "tablist");
    this.tabsEl.setAttribute("aria-label", "Open files");
    // the preview is a separate, sandboxed document: scripts run, but can't reach this page
    this.previewFrame.setAttribute("sandbox", "allow-scripts");
    this.previewFrame.title = "Preview";
    this.previewEl.append(this.previewFrame);
    this.previewEl.hidden = true;
    const split = el("div", "code-split");
    split.append(host, this.previewEl);
    body.append(this.tabsEl, this.findBar.el, split);
    this.note.addEventListener("click", () => {
      const line = Number(this.note.dataset.line);
      if (!line) return;
      const target = this.view.state.doc.line(Math.min(line, this.view.state.doc.lines));
      this.view.dispatch({ selection: { anchor: target.from }, scrollIntoView: true });
      this.view.focus();
    });

    const status = el("div", "editor-status code-status");
    const right = el("div", "code-status-right");
    for (const [sel, label] of [[this.languageSel, "Language"], [this.encodingSel, "Encoding"], [this.newlineSel, "Line endings"]] as const) {
      sel.setAttribute("aria-label", label);
      sel.title = label;
    }
    for (const l of [...LANGS].sort((a, b) => a.name.localeCompare(b.name))) this.languageSel.append(new Option(l.name, l.id));
    ENCODINGS.forEach(([label], i) => this.encodingSel.append(new Option(label, String(i))));
    NEWLINES.forEach(([label], i) => this.newlineSel.append(new Option(label, String(i))));
    this.languageSel.addEventListener("change", () => void this.setLanguage(this.languageSel.value));
    this.encodingSel.addEventListener("change", () => {
      const [, enc, bom] = ENCODINGS[+this.encodingSel.value];
      this.setEncoding(enc, bom);
    });
    this.newlineSel.addEventListener("change", () => this.setNewline(NEWLINES[+this.newlineSel.value][1]));
    right.append(this.note, this.position, this.languageSel, this.encodingSel, this.newlineSel, this.indentEl);
    status.append(this.message, right);
    this.message.setAttribute("aria-live", "polite");

    this.picker.type = "file";
    this.picker.multiple = true;
    this.picker.hidden = true;
    this.picker.addEventListener("change", () => {
      const list = Array.from(this.picker.files ?? []);
      this.picker.value = "";
      void this.openFiles(list);
    });
    this.helpDialog = this.buildHelp();
    this.saveDialog = this.buildSaveDialog();
    this.el.append(top, body, status, this.picker, this.helpDialog, this.saveDialog);

    this.actions = {
      new: () => this.openEmpty(),
      open: () => this.picker.click(),
      save: () => void this.save(),
      save_as: () => void this.saveAs(),
      close: () => void this.closeTab(this.current),
      next_tab: () => this.stepTab(1),
      prev_tab: () => this.stepTab(-1),
      find: () => this.findBar.open(false),
      replace: () => this.findBar.open(true),
      find_next: () => this.findBar.findNext(),
      find_prev: () => this.findBar.findPrev(),
      go_to_line: () => this.goToLine(),
      wrap: () => this.toggleWrap(),
      zoom_in: () => this.zoom(1),
      zoom_out: () => this.zoom(-1),
      zoom_reset: () => this.zoom(0),
      help: () => this.showHelp(),
    };
    window.addEventListener("keydown", this.onKey, true);
    window.addEventListener("beforeunload", this.onUnload);
    this.el.addEventListener("dragover", (e) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
    });
    this.el.addEventListener("drop", (e) => {
      const list = Array.from(e.dataTransfer?.files ?? []);
      if (!list.length) return;
      e.preventDefault();
      e.stopPropagation();
      void this.openFiles(list);
    });
    this.updateStatus();
    if (typeof location !== "undefined" && location.search.includes("e2e")) this.installTestHook();
  }

  // ---------------------------------------------------------------- test hook
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __codeeditor: object }).__codeeditor = {
      text: () => page.view.state.doc.toString(),
      setText: (t: string) => page.view.dispatch({ changes: { from: 0, to: page.view.state.doc.length, insert: t } }),
      tabs: () => page.tabs.map((t) => t.name),
      get dirty() {
        return page.tabs.some((t) => page.isDirty(t));
      },
      get current() {
        return page.current;
      },
    };
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

  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.active && this.tabs.some((t) => this.isDirty(t))) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  private readonly onKey = (e: KeyboardEvent) => {
    if (!this.active || this.el.hidden) return;
    if (this.helpDialog.open || this.saveDialog.open) return;
    const name = keyName(e);
    const s = SHORTCUTS.find((x) => x.bound !== false && x.keys.includes(name));
    if (!s) return;
    e.preventDefault();
    e.stopPropagation();
    this.actions[s.id]();
  };

  // ---------------------------------------------------------------- tabs
  /** A tab's editor state: the shared setup, then this tab's indent and the current text size
   * and word wrap (the language comes when its code has loaded: `applyLanguage`). */
  private newState(text: string, indent = "    "): EditorState {
    const state = EditorState.create({ doc: text, extensions: [this.base, matchField] });
    return state.update({
      effects: [
        compartments.indent.reconfigure(indentUnit.of(indent)),
        compartments.font.reconfigure(fontSize(this.fontPx)),
        compartments.wrap.reconfigure(this.wrapFor(null)),
      ],
    }).state;
  }

  private isDirty(t: Tab): boolean {
    const state = this.tabs[this.current] === t ? this.view.state : t.state;
    if (!state.doc.eq(t.savedDoc)) return true;
    return !!t.file && (t.encoding !== t.file.encoding || t.newline !== t.file.newline || t.bom !== t.file.bom);
  }

  private changed(): void {
    this.findBar.documentChanged();
    this.renderTabs();
    if (this.tidyNote) {
      this.tidyNote = "";
      this.updateNote();
    }
    if (!this.previewEl.hidden) {
      clearTimeout(this.previewTimer);
      this.previewTimer = setTimeout(() => void this.refreshPreview(), 500);
    }
  }

  // ---------------------------------------------------------------- checks, Tidy, preview
  /** The error check for a tab (JSON / YAML / XML under MAX_CHECK_BYTES), as lint extensions. */
  private lintFor(t: Tab) {
    if (!CHECKED.has(t.languageId) || (t.file?.raw.length ?? 0) > MAX_CHECK_BYTES) return [];
    const languageId = t.languageId;
    return [
      linter(
        (view): Diagnostic[] => {
          const doc = view.state.doc;
          const problems = doc.length > MAX_CHECK_BYTES ? [] : check(languageId, doc.toString());
          if (this.tabs[this.current] === t) {
            this.problems = problems;
            this.updateNote();
          }
          return problems.map((p) => {
            const line = doc.line(Math.max(1, Math.min(p.line, doc.lines)));
            const from = p.column ? Math.min(line.from + p.column - 1, line.to) : line.from;
            const to = p.column ? Math.min(Math.max(from + 1, line.to), doc.length) : line.to;
            return { from, to, severity: "error", message: p.message };
          });
        },
        { delay: 400 },
      ),
      lintGutter(),
    ];
  }

  private updateNote(): void {
    const t = this.tab();
    delete this.note.dataset.line;
    this.note.classList.remove("link");
    if (!t) {
      this.note.textContent = "";
    } else if (this.tidyNote) {
      this.note.textContent = this.tidyNote;
    } else if (this.problems.length) {
      const p = this.problems[0];
      this.note.textContent = `Line ${p.line}: ${p.message}`;
      this.note.dataset.line = String(p.line);
      this.note.classList.add("link");
    } else {
      this.note.textContent = t.colours ? "" : "Colours are off for files this big";
    }
  }

  /** Lay out the current JSON / XML neatly, as one undo step (refused while it has a mistake). */
  tidy(): void {
    const t = this.tab();
    if (!t || !TIDY.has(t.languageId)) return;
    const old = this.view.state.doc.toString();
    let next: string;
    try {
      next = tidy(t.languageId, old);
    } catch (e) {
      this.tidyNote = e instanceof TidyError ? e.message : "This couldn't be tidied.";
      this.updateNote();
      return;
    }
    if (next !== old) this.view.dispatch({ changes: { from: 0, to: old.length, insert: next } });
  }

  /** HTML / Markdown: the preview beside the code (instead of it on phones), or hide it again. */
  togglePreview(): void {
    const t = this.tab();
    const show = this.previewEl.hidden && !!t && PREVIEWS.has(t.languageId);
    this.previewEl.hidden = !show;
    this.el.classList.toggle("previewing", show);
    this.previewBtn.setAttribute("aria-pressed", String(show));
    if (show) void this.refreshPreview();
  }

  private async refreshPreview(): Promise<void> {
    const t = this.tab();
    if (!t || this.previewEl.hidden) return;
    const text = this.view.state.doc.toString();
    if (t.languageId === "markdown") {
      const { default: MarkdownIt } = await import("markdown-it");
      const body = new MarkdownIt({ html: false, linkify: true }).render(text);
      this.previewFrame.srcdoc = `<!doctype html><meta charset="utf-8"><style>body{font:15px/1.6 system-ui,sans-serif;margin:16px;max-width:760px}pre,code{font-family:"Cascadia Mono",Consolas,monospace}img{max-width:100%}</style>${body}`;
    } else {
      this.previewFrame.srcdoc = text;
    }
  }

  private renderTabs(): void {
    this.tabsEl.replaceChildren(
      ...this.tabs.map((t, i) => {
        const tab = el("div", "code-tab");
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-selected", String(i === this.current));
        tab.title = t.file ? t.name : "Not saved yet";
        const label = el("button", "code-tab-name", (this.isDirty(t) ? "● " : "") + t.name);
        label.type = "button";
        label.addEventListener("click", () => this.select(i));
        const close = el("button", "code-tab-close", "×");
        close.type = "button";
        close.title = `Close ${t.name} (Alt+W)`;
        close.setAttribute("aria-label", `Close ${t.name}`);
        close.addEventListener("click", () => void this.closeTab(i));
        tab.append(label, close);
        return tab;
      }),
    );
  }

  private select(index: number): void {
    if (index < 0 || index >= this.tabs.length) return;
    if (this.current >= 0 && this.current < this.tabs.length) this.tabs[this.current].state = this.view.state;
    this.current = index;
    this.view.setState(this.tabs[index].state);
    // this tab's mistakes now (the linter only reports after its delay), and its preview
    const t = this.tabs[index];
    const text = CHECKED.has(t.languageId) && t.state.doc.length <= MAX_CHECK_BYTES ? t.state.doc.toString() : "";
    this.problems = text ? check(t.languageId, text) : [];
    this.tidyNote = "";
    if (!this.previewEl.hidden) void this.refreshPreview();
    // text size and word wrap are the editor's, not the tab's: bring this tab up to date
    this.view.dispatch({
      effects: [compartments.font.reconfigure(fontSize(this.fontPx)), compartments.wrap.reconfigure(this.wrapFor(this.tabs[index]))],
    });
    this.findBar.documentChanged();
    this.renderTabs();
    this.updateStatus();
    this.view.focus();
  }

  private stepTab(step: number): void {
    if (this.tabs.length) this.select((this.current + step + this.tabs.length) % this.tabs.length);
  }

  private async addTab(name: string, file: TextFile | null, text: string, source?: string): Promise<Tab> {
    const languageId = languageFor(name);
    const indent = detectIndent(text);
    const tab: Tab = {
      name, file, languageId, indent,
      state: this.newState(text, indent),
      savedDoc: undefined as unknown as Text,
      encoding: file?.encoding ?? "utf-8",
      newline: file?.newline ?? "\n",
      bom: file?.bom ?? false,
      colours: !file || file.raw.length <= MAX_HIGHLIGHT_BYTES,
      longestLine: text.split("\n").reduce((m, l) => Math.max(m, l.length), 0),
      source,
    };
    tab.savedDoc = tab.state.doc;
    this.tabs.push(tab);
    this.select(this.tabs.length - 1);
    if (tab.colours) await this.applyLanguage(tab, languageId);
    return tab;
  }

  private async applyLanguage(tab: Tab, languageId: string): Promise<void> {
    tab.languageId = languageId;
    let ext;
    try {
      ext = tab.colours ? await loadLanguage(languageId) : [];
    } catch (e) {
      console.error(`Couldn't load the colours for ${languageId}`, e);
      ext = [];
    }
    if (tab.languageId !== languageId || !this.tabs.includes(tab)) return;
    const effects = [compartments.language.reconfigure(ext), compartments.lint.reconfigure(this.lintFor(tab))];
    if (this.tabs[this.current] === tab) {
      this.problems = [];
      this.view.dispatch({ effects });
    } else tab.state = tab.state.update({ effects }).state;
    this.updateStatus();
  }

  /** A tab per file (an open one is shown). False if none could be opened (see lastError). */
  async open(files: InputFile[]): Promise<boolean> {
    return this.openFiles(files.map((f) => f.file));
  }

  async openFiles(list: File[]): Promise<boolean> {
    const problems: string[] = [];
    let opened = false;
    for (const f of list) {
      const source = `${f.name}\u0000${f.size}\u0000${f.lastModified}`;
      const existing = this.tabs.findIndex((t) => t.source === source);
      if (existing >= 0) {
        this.select(existing);
        opened = true;
        continue;
      }
      if (f.size > MAX_OPEN_BYTES_WEB) {
        problems.push(`“${f.name}” is too big for the code editor in the browser (${Math.round(f.size / 1e6)} MB; up to ${MAX_OPEN_BYTES_WEB / 1e6} MB).`);
        continue;
      }
      let file: TextFile;
      try {
        file = decode(new Uint8Array(await f.arrayBuffer()));
      } catch {
        problems.push(`“${f.name}” looks like a binary file, not text.`);
        continue;
      }
      if (!this.tabs[this.current]?.file && this.tabs.length === 1 && !this.isDirty(this.tabs[0]) && !this.view.state.doc.length) {
        this.tabs = [];
        this.current = -1;
      }
      await this.addTab(f.name, file, file.text, source);
      opened = true;
    }
    this.showMessage(problems.join(" "), problems.length > 0);
    return opened;
  }

  openEmpty(): void {
    void this.addTab(UNTITLED, null, "");
    this.showMessage("");
  }

  async closeTab(index: number): Promise<boolean> {
    const tab = this.tabs[index];
    if (!tab || this.saving) return false;
    if (this.isDirty(tab) && !window.confirm(`Discard the changes to “${tab.name}”?`)) return false;
    // keep what was typed in the tab being looked at (it may not be the one closing)
    if (this.current >= 0) this.tabs[this.current].state = this.view.state;
    const was = this.current;
    this.tabs.splice(index, 1);
    if (!this.tabs.length) {
      this.current = -1;
      this.openEmpty();
      return true;
    }
    this.current = -1;
    this.select(index < was ? was - 1 : index === was ? Math.min(index, this.tabs.length - 1) : was);
    return true;
  }

  /** True when the editor may close: nothing unsaved, or the user agreed to drop it. */
  maybeLeave(): boolean {
    const dirty = this.tabs.filter((t) => this.isDirty(t));
    if (!dirty.length) return true;
    const names = dirty.slice(0, 3).map((t) => `“${t.name}”`).join(", ") + (dirty.length > 3 ? "…" : "");
    if (!window.confirm(`Discard the unsaved changes to ${names}?`)) return false;
    this.tabs = [];
    this.current = -1;
    this.view.setState(this.newState(""));
    this.renderTabs();
    this.updateStatus();
    return true;
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- settings
  private tab(): Tab | null {
    return this.tabs[this.current] ?? null;
  }

  async setLanguage(languageId: string): Promise<void> {
    const t = this.tab();
    if (t) await this.applyLanguage(t, languageId);
  }

  setEncoding(encoding: Encoding, bom: boolean): void {
    const t = this.tab();
    if (!t) return;
    t.encoding = encoding;
    t.bom = bom;
    this.renderTabs();
    this.updateStatus();
  }

  setNewline(newline: Newline): void {
    const t = this.tab();
    if (!t) return;
    t.newline = newline;
    this.renderTabs();
    this.updateStatus();
  }

  /** Word wrap for a tab: never for very long lines (a wrapped 1 MB line is laid out in full). */
  private wrapFor(t: Tab | null) {
    return this.wrap && !(t && t.longestLine > MAX_WRAP_LINE) ? EditorView.lineWrapping : [];
  }

  private toggleWrap(): void {
    this.wrap = !this.wrap;
    this.view.dispatch({ effects: compartments.wrap.reconfigure(this.wrapFor(this.tab())) });
  }

  private zoom(step: number): void {
    this.fontPx = step === 0 ? BASE_FONT_PX : Math.max(8, Math.min(40, this.fontPx + step));
    this.view.dispatch({ effects: compartments.font.reconfigure(fontSize(this.fontPx)) });
  }

  private goToLine(): void {
    const count = this.view.state.doc.lines;
    const answer = window.prompt(`Go to line (1–${count}):`, String(this.view.state.doc.lineAt(this.view.state.selection.main.head).number));
    const n = Number(answer);
    if (!answer || !Number.isFinite(n)) return;
    const line = this.view.state.doc.line(Math.max(1, Math.min(count, Math.round(n))));
    this.view.dispatch({ selection: { anchor: line.from }, scrollIntoView: true });
    this.view.focus();
  }

  private updateStatus(): void {
    const t = this.tab();
    for (const s of [this.languageSel, this.encodingSel, this.newlineSel]) s.hidden = !t;
    this.saveBtn.disabled = this.saveAsBtn.disabled = !t || this.saving;
    this.tidyBtn.hidden = !t || !TIDY.has(t.languageId);
    this.previewBtn.hidden = !t || !PREVIEWS.has(t.languageId);
    if (!this.previewEl.hidden && (!t || !PREVIEWS.has(t.languageId))) this.togglePreview();
    if (!t) {
      this.position.textContent = this.indentEl.textContent = this.note.textContent = "";
      return;
    }
    this.languageSel.value = t.languageId;
    this.encodingSel.value = String(Math.max(0, ENCODINGS.findIndex(([, e, b]) => e === t.encoding && b === t.bom)));
    this.newlineSel.value = String(NEWLINES.findIndex(([, n]) => n === t.newline));
    this.indentEl.textContent = t.indent === "\t" ? "Tab" : `Spaces: ${t.indent.length}`;
    this.updateNote();
    const head = this.view.state.selection.main.head;
    const line = this.view.state.doc.lineAt(head);
    this.position.textContent = `Ln ${line.number}, Col ${head - line.from + 1}`;
  }

  // ---------------------------------------------------------------- saving
  private encoded(t: Tab, text: string): Uint8Array | null {
    const base = t.file ?? decode(new Uint8Array());
    try {
      return encode(base, text, { encoding: t.encoding, newline: t.newline !== base.newline ? t.newline : undefined, bom: t.bom });
    } catch {
      if (!window.confirm(`This text has characters ${t.encoding.toUpperCase()} can't store. Save as UTF-8 instead?`)) return null;
      t.encoding = "utf-8";
      t.bom = false;
      this.updateStatus();
      return encode(base, text, { encoding: "utf-8", newline: t.newline !== base.newline ? t.newline : undefined, bom: false });
    }
  }

  async save(): Promise<boolean> {
    const t = this.tab();
    if (!t || this.saving) return false;
    if (!t.file && !t.handle) return this.saveAs();
    return this.write(t, t.name);
  }

  async saveAs(): Promise<boolean> {
    const t = this.tab();
    if (!t || this.saving) return false;
    const choice = await this.askName(t.file ? t.name : `${UNTITLED}.txt`);
    if (!choice) return false;
    if (choice.toFile) {
      try {
        t.handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({
          suggestedName: choice.name.replace(/[\\/:*?"<>|]/g, "_"),
        });
      } catch (err) {
        if ((err as { name?: string } | null)?.name !== "AbortError") {
          console.error("The save window couldn't be opened", err);
          this.showMessage("The browser couldn't open its save window. Try again, or use Save as without “Save to a file…”.", true);
        }
        return false;
      }
    } else {
      t.handle = undefined;
    }
    return this.write(t, t.handle?.name ?? choice.name);
  }

  private async write(t: Tab, name: string): Promise<boolean> {
    const state = this.tabs[this.current] === t ? this.view.state : t.state;
    const text = state.doc.toString();
    const data = this.encoded(t, text);
    if (!data) return false;
    this.saving = true;
    this.updateStatus();
    try {
      const blob = new Blob([data as BlobPart], { type: "application/octet-stream" });
      if (t.handle) {
        const w = await t.handle.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        const url = URL.createObjectURL(blob);
        const a = el("a");
        a.href = url;
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      const renamed = t.name !== name;
      t.name = name;
      // keep what was chosen (reading back can't tell 1252 from ASCII, or CRLF in one line)
      t.file = { ...decode(data), encoding: t.encoding, newline: t.newline, bom: t.bom, mixed: false };
      t.savedDoc = state.doc;
      if (renamed && t.languageId === "text") void this.applyLanguage(t, languageFor(name));
      this.showMessage(`Saved “${name}”.`);
      return true;
    } catch (err) {
      console.error(`Couldn't save ${name}`, err);
      this.showMessage(`Couldn't save “${name}”.`, true);
      return false;
    } finally {
      this.saving = false;
      this.renderTabs();
      this.updateStatus();
    }
  }

  // ---------------------------------------------------------------- dialogs
  private buildSaveDialog(): HTMLDialogElement {
    const dialog = el("dialog", "editor-dialog code-save-dialog");
    dialog.setAttribute("aria-label", "Save as");
    const name = el("input");
    name.type = "text";
    name.setAttribute("aria-label", "File name");
    const row = el("label", "field");
    row.append(el("span", "", "File name"), name);
    const buttons = el("div", "row");
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    const toFile = button("Save to a file…", "btn", "file");
    toFile.title = "Choose where to save; Save (Ctrl+S) then writes to the same file";
    buttons.append(el("div", "spacer"), button("Cancel", "btn", ""), toFile, button("Save", "primary small", "save"));
    dialog.append(el("h2", "", "Save as"), row, buttons);
    return dialog;
  }

  /** Asks the file name (and whether to pick a file); replaceable in tests. */
  askName = (suggested: string): Promise<{ name: string; toFile: boolean } | null> => {
    const dialog = this.saveDialog;
    const name = dialog.querySelector("input") as HTMLInputElement;
    const toFile = dialog.querySelector('[data-value="file"]') as HTMLButtonElement;
    toFile.hidden = !("showSaveFilePicker" in window);
    name.value = suggested;
    return new Promise((resolve) => {
      const finish = (value: string) => {
        dialog.removeEventListener("click", clicked);
        dialog.removeEventListener("cancel", cancelled);
        dialog.removeEventListener("keydown", keyed);
        dialog.close();
        resolve(value ? { name: name.value.trim() || suggested, toFile: value === "file" } : null);
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
      dialog.showModal();
      name.focus();
      const dot = name.value.lastIndexOf(".");
      name.setSelectionRange(0, dot > 0 ? dot : name.value.length);
    });
  };

  private buildHelp(): HTMLDialogElement {
    const dialog = el("dialog", "editor-dialog code-help");
    dialog.setAttribute("aria-label", "Keyboard shortcuts");
    const table = el("div", "code-help-rows");
    let group = "";
    for (const s of SHORTCUTS) {
      if (s.group !== group) {
        group = s.group;
        table.append(el("h3", "", group));
      }
      const row = el("div", "code-help-row");
      row.append(el("kbd", "", s.keys.map(showKey).join(" / ")), el("span", "", s.label));
      table.append(row);
    }
    const close = el("button", "primary small", "Close");
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    const buttons = el("div", "row");
    buttons.append(el("div", "spacer"), close);
    dialog.append(el("h2", "", "Keyboard shortcuts"), table, buttons);
    return dialog;
  }

  private showHelp(): void {
    this.helpDialog.showModal();
  }
}

export function mountCodeEditor(host: HTMLElement, opts: CodeEditorOptions): CodeEditorPage {
  const page = new CodeEditorPage(opts);
  host.append(page.el);
  return page;
}
