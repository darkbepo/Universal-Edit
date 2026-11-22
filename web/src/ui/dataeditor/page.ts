/**
 * The website's data editor page (the desktop's gui/dataeditor/data_editor_page.py): a grid of one
 * sheet (only the rows on screen are drawn, so big tables stay quick; tabs for a workbook's
 * sheets), an edit box above it for the chosen cell, rows / columns / sort / header tools, find &
 * replace, copy / paste (the browser's own clipboard events: no permission prompt), undo. The
 * model is dataedit/ (shared cases with the desktop). Loaded on demand.
 *
 * Save downloads a file; "Save to a file…" (Chrome / Edge) writes to a picked file, and Save
 * (Ctrl+S) then writes there again.
 */
import { SearchError, replaceAll as replaceText, type SearchOptions } from "../../codeedit/search";
import type { InputFile } from "../../core/types";
import { display, editText, isNum } from "../../dataedit/cells";
import * as csvtext from "../../dataedit/csvtext";
import { exportBytes } from "../../dataedit/export";
import { History } from "../../dataedit/history";
import { DataError, FORMATS, NAMES, newDocument, openBytes, toBytes, type Opened } from "../../dataedit/io";
import * as table from "../../dataedit/table";
import { EditError, letter, type DataDoc, type Sheet } from "../../dataedit/table";
import { backLabel } from "../back";
import { SHORTCUTS, keyName, showKey } from "./shortcuts";

export const ROW_PX = 28;
const NUM_PX = 56; // the row numbers
const MIN_COL_PX = 60;
const MAX_COL_PX = 320;
const WIDTH_SAMPLE_ROWS = 200; // columns are sized from the first rows only (big files open fast)
const HEADER_FORMATS = ["csv", "tsv", "xlsx"]; // the "First row is the header" switch
const MIME: Record<string, string> = {
  csv: "text/csv", tsv: "text/tab-separated-values", json: "application/json", yaml: "application/yaml",
  xml: "application/xml", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

export interface DataEditorOptions {
  onBack: () => void;
}

type Cellpos = [number, number];

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

const extOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
  return ext === "yml" ? "yaml" : ext;
};
const baseOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
};

/** A column's title: its name, or its letter when it has none (or the first row is data). */
export const columnTitle = (sheet: Sheet, c: number) => (sheet.header && sheet.columns[c]) || letter(c);

export class DataEditorPage {
  readonly el: HTMLElement = el("div", "editor data-editor");
  opened: Opened = newDocument("csv");
  history = new History(this.opened.doc);
  sheetIndex = 0;
  /** The chosen cell, and the other corner of the chosen block. */
  cur: Cellpos = [0, 0];
  anchor: Cellpos = [0, 0];
  private widths: number[] = [];
  private sourceName: string | null = null;
  private saveName: string | null = null; // where Save writes (null: ask first)
  private handle: FileHandle | undefined;
  private active = false;
  private saving = false;
  private askedLost = false; // "can't keep comments" asked once per file
  private dragging = false;
  private editing: Cellpos | null = null; // the cell being typed in place

  private readonly title = el("span", "data-title");
  private readonly message = el("div", "msg");
  private readonly sizeLabel = el("span", "data-size");
  private readonly grid = el("div", "data-grid");
  private readonly head = el("div", "data-head");
  private readonly rowsEl = el("div", "data-rows");
  private readonly cellsEl = el("div", "data-cells");
  private readonly input = el("input", "data-input");
  private readonly box = el("textarea", "data-box");
  private readonly cellName = el("span", "data-cell-name");
  private readonly tabs = el("div", "data-tabs");
  private readonly headerBox = el("input");
  private readonly headerLabel = el("label", "check data-header-switch");
  private readonly menu = el("div", "data-menu");
  private readonly findEl = el("div", "sub-find data-find");
  private readonly findInput = el("input");
  private readonly replaceInput = el("input");
  private readonly findCount = el("span", "sub-find-count");
  private readonly findCase = el("input");
  private readonly findWord = el("input");
  private readonly findRegex = el("input");
  private readonly picker = el("input");
  private readonly helpDialog: HTMLDialogElement;
  private readonly saveDialog: HTMLDialogElement;
  private readonly saveBtn: HTMLButtonElement;
  private readonly saveAsBtn: HTMLButtonElement;
  private readonly buttons: Record<string, HTMLButtonElement> = {};
  private readonly actions: Record<string, () => void>;

  constructor(private readonly opts: DataEditorOptions) {
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
    const tools = el("div", "top-tools");
    tools.append(
      btn("Open…", "Open a table (Ctrl+O)", () => this.picker.click()),
      btn("New", "New table (Alt+N)", () => void this.openEmpty()),
      this.title,
      el("div", "spacer"),
      btn("Find", "Find and replace (Ctrl+F, Ctrl+H)", () => this.openFind(false)),
      btn("?", "Keyboard shortcuts (F1)", () => this.helpDialog.showModal()),
    );
    const top = el("div", "editor-top");
    top.append(backLabel(btn("← Back to Convert", "Leave the editor", () => this.back(), "link")), el("span", "gap"), tools, this.saveAsBtn, this.saveBtn);

    // the tool row and the edit box
    const toolRow = el("div", "data-tools");
    const tool = (id: string, label: string, title: string, run: () => void) => {
      const b = btn(label, title, run);
      b.addEventListener("mousedown", (e) => e.preventDefault()); // the grid keeps the focus
      this.buttons[id] = b;
      toolRow.append(b);
    };
    tool("add_row", "+ Row", "Add a row below (Ctrl+Enter)", () => this.addRow());
    tool("add_column", "+ Column", "Add a column to the right (Ctrl+Shift+Enter)", () => this.addColumn());
    tool("delete_rows", "Delete rows", "Delete the chosen rows (Ctrl+-)", () => this.deleteRows());
    tool("delete_columns", "Delete column", "Delete the chosen columns", () => this.deleteColumns());
    tool("rename", "Rename column…", "Rename this column (or double-click its name)", () => this.askRename(this.cur[1]));
    tool("sort_up", "Sort A→Z", "Sort the rows by this column (numbers by value)", () => this.sort(false));
    tool("sort_down", "Sort Z→A", "Sort the rows by this column, largest first", () => this.sort(true));
    this.headerBox.type = "checkbox";
    this.headerBox.addEventListener("change", () => this.setHeader(this.headerBox.checked));
    this.headerLabel.title = "Use the first row as the column names (CSV, TSV and Excel)";
    this.headerLabel.append(this.headerBox, "First row is the header");
    toolRow.append(this.headerLabel);
    const editRow = el("div", "data-edit-row");
    this.box.setAttribute("aria-label", "Cell");
    this.box.title = "The chosen cell — Enter keeps it, Shift+Enter starts a new line, Esc puts it back";
    this.box.rows = 2;
    this.box.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        this.boxCommit();
        this.grid.focus();
      } else if (e.key === "Escape") {
        e.preventDefault();
        this.fillBox();
        this.grid.focus();
      }
    });
    this.box.addEventListener("change", () => this.boxCommit());
    editRow.append(this.cellName, this.box);

    // find bar
    this.findInput.type = "search";
    this.findInput.placeholder = "Find in the table";
    this.findInput.setAttribute("aria-label", "Find");
    this.replaceInput.type = "text";
    this.replaceInput.placeholder = "Replace with";
    this.replaceInput.setAttribute("aria-label", "Replace with");
    const check = (box: HTMLInputElement, label: string) => {
      box.type = "checkbox";
      box.addEventListener("change", () => this.countMatches());
      const l = el("label", "check");
      l.append(box, label);
      return l;
    };
    const findRow = el("div", "sub-find-row");
    findRow.append(
      this.findInput,
      btn("↑", "Previous match (Shift+F3)", () => this.find(-1)),
      btn("↓", "Next match (F3)", () => this.find(1)),
      this.replaceInput,
      btn("Replace", "Replace in this cell", () => this.replaceOne()),
      btn("Replace all", "Replace every match", () => this.replaceAll()),
      check(this.findCase, "Match case"),
      check(this.findWord, "Whole word"),
      check(this.findRegex, "Regex"),
      this.findCount,
      btn("×", "Close (Esc)", () => (this.findEl.hidden = true)),
    );
    this.findEl.append(findRow);
    this.findEl.hidden = true;
    this.findInput.addEventListener("input", () => this.countMatches());
    for (const [box, run] of [[this.findInput, (e: KeyboardEvent) => this.find(e.shiftKey ? -1 : 1)], [this.replaceInput, () => this.replaceOne()]] as const) {
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          run(e);
        } else if (e.key === "Escape") this.findEl.hidden = true;
      });
    }

    // the grid
    this.grid.tabIndex = 0;
    this.grid.setAttribute("role", "grid");
    this.grid.setAttribute("aria-label", "Table");
    this.rowsEl.append(this.cellsEl, this.input);
    this.grid.append(this.head, this.rowsEl);
    this.input.hidden = true;
    this.input.setAttribute("aria-label", "Cell text");
    this.input.addEventListener("keydown", (e) => this.inputKey(e));
    this.input.addEventListener("blur", () => this.endEdit(true));
    this.grid.addEventListener("scroll", () => this.renderRows());
    this.grid.addEventListener("pointerdown", (e) => this.pointerDown(e));
    this.grid.addEventListener("pointermove", (e) => this.pointerMove(e));
    window.addEventListener("pointerup", () => (this.dragging = false));
    this.grid.addEventListener("dblclick", (e) => this.doubleClick(e));
    this.grid.addEventListener("contextmenu", (e) => this.contextMenu(e));
    this.grid.addEventListener("keydown", (e) => this.gridKey(e));
    this.grid.addEventListener("copy", (e) => this.copyEvent(e, false));
    this.grid.addEventListener("cut", (e) => this.copyEvent(e, true));
    this.grid.addEventListener("paste", (e) => {
      if (e.target !== this.grid) return;
      e.preventDefault();
      this.paste(e.clipboardData?.getData("text/plain") ?? "");
    });
    new ResizeObserver(() => this.renderRows()).observe(this.grid);
    this.menu.setAttribute("role", "menu");
    this.menu.hidden = true;
    document.addEventListener("pointerdown", (e) => {
      if (!this.menu.hidden && !this.menu.contains(e.target as Node)) this.menu.hidden = true;
    });
    this.menu.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        this.menu.hidden = true;
        this.grid.focus();
      }
    });

    const main = el("div", "data-main");
    main.append(toolRow, editRow, this.findEl, this.grid, this.tabs);
    const status = el("div", "editor-status data-status");
    status.append(this.message, this.sizeLabel);
    this.message.setAttribute("aria-live", "polite");

    this.picker.type = "file";
    this.picker.hidden = true;
    this.picker.accept = [...FORMATS, "yml"].map((e) => "." + e).join(",");
    this.picker.addEventListener("change", () => {
      const list = Array.from(this.picker.files ?? []);
      this.picker.value = "";
      void this.openFiles(list);
    });
    this.helpDialog = this.buildHelp();
    this.saveDialog = this.buildSaveDialog();
    this.el.append(top, main, status, this.picker, this.menu, this.helpDialog, this.saveDialog);

    this.actions = {
      new: () => void this.openEmpty(),
      open: () => this.picker.click(),
      save: () => void this.save(),
      save_as: () => void this.saveAs(),
      undo: () => this.undo(),
      redo: () => this.redo(),
      add_row: () => this.addRow(),
      add_column: () => this.addColumn(),
      delete_rows: () => this.deleteRows(),
      edit_box: () => this.box.focus(),
      find: () => this.openFind(false),
      replace: () => this.openFind(true),
      find_next: () => this.find(1),
      find_prev: () => this.find(-1),
      help: () => this.helpDialog.showModal(),
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
    this.showDoc(true);
    if (typeof location !== "undefined" && location.search.includes("e2e")) this.installTestHook();
  }

  // ---------------------------------------------------------------- test hook
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __dataeditor: object }).__dataeditor = {
      columns: () => [...page.sheet.columns],
      rows: () => page.sheet.rows.map((r) => r.map(display)),
      get format() {
        return page.doc.format;
      },
      get dirty() {
        return page.history.dirty;
      },
      get sheet() {
        return page.sheetIndex;
      },
      get current() {
        return [...page.cur];
      },
      select: (r: number, c: number, r2 = r, c2 = c) => page.select([r2, c2], [r, c]),
    };
  }

  // ---------------------------------------------------------------- state
  get doc(): DataDoc {
    return this.history.doc;
  }

  get sheet(): Sheet {
    return this.doc.sheets[this.sheetIndex];
  }

  get dirty(): boolean {
    return this.history.dirty;
  }

  show(): void {
    this.active = true;
    this.el.hidden = false;
    this.grid.focus();
    this.renderRows();
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

  private hasCell(): boolean {
    const s = this.sheet;
    return this.cur[0] >= 0 && this.cur[0] < s.rows.length && this.cur[1] >= 0 && this.cur[1] < s.columns.length;
  }

  /** The chosen block: [top, left, bottom, right]. */
  block(): [number, number, number, number] {
    return [Math.min(this.cur[0], this.anchor[0]), Math.min(this.cur[1], this.anchor[1]), Math.max(this.cur[0], this.anchor[0]), Math.max(this.cur[1], this.anchor[1])];
  }

  private chosenRows(): number[] {
    const [t, , b] = this.block();
    return Array.from({ length: b - t + 1 }, (_, i) => t + i).filter((r) => r < this.sheet.rows.length);
  }

  private chosenColumns(): number[] {
    const [, l, , r] = this.block();
    return Array.from({ length: r - l + 1 }, (_, i) => l + i).filter((c) => c < this.sheet.columns.length);
  }

  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.active && this.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  private readonly onKey = (e: KeyboardEvent) => {
    if (!this.active || this.el.hidden) return;
    if (this.el.querySelector("dialog[open]")) return;
    const name = keyName(e);
    const s = SHORTCUTS.find((x) => x.keys.includes(name) && !x.table);
    if (!s) return;
    // in a box (the cell, find, a cell being typed) its own keys win: only saving and moving
    // between matches
    const t = e.target;
    const inBox = t instanceof HTMLTextAreaElement || (t instanceof HTMLInputElement && t.type !== "checkbox" && t.type !== "radio");
    if (inBox && !["save", "save_as", "find_next", "find_prev"].includes(s.id)) return;
    e.preventDefault();
    e.stopPropagation();
    if (this.editing) this.endEdit(true);
    this.actions[s.id]();
  };

  // ---------------------------------------------------------------- opening
  /** Open the first table in `list`. */
  async openFiles(list: File[]): Promise<boolean> {
    const f = list.find((x) => FORMATS.includes(extOf(x.name)));
    if (!f) {
      this.showMessage("Choose a CSV, TSV, JSON, YAML, XML or Excel (XLSX) file.", true);
      return false;
    }
    if (this.saving || !this.discardOk()) return false;
    let data: Uint8Array;
    try {
      data = new Uint8Array(await f.arrayBuffer());
    } catch {
      this.showMessage(`“${f.name}” couldn't be read.`, true);
      return false;
    }
    let opened: Opened;
    try {
      opened = openBytes(data, extOf(f.name));
    } catch (e) {
      this.showMessage(e instanceof DataError ? `“${f.name}”: ${e.message}` : `“${f.name}” couldn't be opened.`, true);
      if (!(e instanceof DataError)) console.error(e);
      return false;
    }
    this.load(f.name, opened);
    return true;
  }

  /** From the converter page: one table. */
  open(files: InputFile[]): Promise<boolean> {
    return this.openFiles(files.map((f) => f.file));
  }

  openEmpty(): boolean {
    if (this.saving || !this.discardOk()) return false;
    this.load(null, newDocument("csv"));
    return true;
  }

  private discardOk(): boolean {
    return !this.dirty || window.confirm("Discard the unsaved changes to the table?");
  }

  private load(name: string | null, opened: Opened): void {
    this.endEdit(false);
    this.opened = opened;
    this.history = new History(opened.doc);
    this.sourceName = name;
    this.saveName = name;
    this.handle = undefined;
    this.sheetIndex = 0;
    this.askedLost = false;
    this.cur = [0, 0];
    this.anchor = [0, 0];
    this.grid.scrollTop = this.grid.scrollLeft = 0;
    this.fitColumns();
    this.showDoc(true);
    if (!name) {
      this.showMessage("A new table: type into the cells, then Save.");
      return;
    }
    const rows = this.sheet.rows.length, cols = this.sheet.columns.length;
    let note = `Opened “${name}”: ${rows.toLocaleString("en-US")} row${rows === 1 ? "" : "s"}, ${cols} column${cols === 1 ? "" : "s"}.`;
    if (this.doc.sheets.length > 1) note += ` ${this.doc.sheets.length} sheets: pick one below the table.`;
    this.showMessage(note);
  }

  /** True when the editor may close: nothing unsaved, or the user agreed to drop it. */
  maybeLeave(): boolean {
    this.endEdit(true);
    if (!this.discardOk()) return false;
    this.history.markSaved();
    return true;
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- showing the document
  private fitColumns(): void {
    const s = this.sheet;
    this.widths = s.columns.map((_, c) => {
      let longest = columnTitle(s, c).length;
      for (const r of s.rows.slice(0, WIDTH_SAMPLE_ROWS)) longest = Math.max(longest, Math.min(60, display(r[c]).length));
      return Math.min(MAX_COL_PX, Math.max(MIN_COL_PX, longest * 7.5 + 24));
    });
  }

  private showDoc(reset = false): void {
    const doc = this.doc;
    this.sheetIndex = Math.min(this.sheetIndex, doc.sheets.length - 1);
    const s = this.sheet;
    while (this.widths.length < s.columns.length) this.widths.push(110);
    this.widths.length = s.columns.length;
    // tabs
    const names = doc.sheets.map((x) => x.name);
    if ([...this.tabs.children].map((t) => t.textContent).join("\0") !== names.join("\0")) {
      this.tabs.replaceChildren(...names.map((n, i) => {
        const t = el("button", "data-tab", n);
        t.type = "button";
        t.setAttribute("role", "tab");
        t.addEventListener("click", () => this.showSheet(i));
        return t;
      }));
    }
    [...this.tabs.children].forEach((t, i) => t.setAttribute("aria-selected", String(i === this.sheetIndex)));
    this.tabs.setAttribute("role", "tablist");
    this.tabs.hidden = names.length < 2;
    // the chosen cell stays inside the sheet
    const clamp = (p: Cellpos): Cellpos => [Math.min(Math.max(p[0], 0), Math.max(s.rows.length - 1, 0)), Math.min(Math.max(p[1], 0), Math.max(s.columns.length - 1, 0))];
    this.cur = clamp(this.cur);
    this.anchor = reset ? this.cur : clamp(this.anchor);
    this.headerBox.checked = s.header;
    this.headerLabel.hidden = !HEADER_FORMATS.includes(doc.format);
    this.sizeLabel.textContent = `${s.rows.length.toLocaleString("en-US")} rows × ${s.columns.length} columns`;
    const cols = `${NUM_PX}px ${this.widths.map((w) => `${w}px`).join(" ")}`;
    this.grid.style.setProperty("--data-cols", cols);
    this.grid.style.setProperty("--data-width", `${NUM_PX + this.widths.reduce((a, b) => a + b, 0)}px`);
    this.renderHead();
    this.rowsEl.style.height = `${s.rows.length * ROW_PX}px`;
    this.renderRows();
    this.paintSelection(); // (rows drawn before kept their old highlight)
    this.fillBox();
    this.updateTitle();
    this.updateButtons();
  }

  private renderHead(): void {
    const s = this.sheet;
    const titles = s.columns.map((_, c) => columnTitle(s, c));
    if ([...this.head.querySelectorAll(".data-col")].map((h) => h.textContent).join("\0") === titles.join("\0") && this.head.childElementCount === titles.length + 1) return;
    const corner = el("span", "data-corner");
    this.head.replaceChildren(corner, ...s.columns.map((_, c) => {
      const h = el("span", "data-col", columnTitle(s, c));
      h.dataset.col = String(c);
      h.setAttribute("role", "columnheader");
      h.title = `Column ${letter(c)} — double-click to rename, right-click for more`;
      return h;
    }));
  }

  private drawn: { key: string; sheet: Sheet | null } = { key: "", sheet: null };

  private renderRows(force = false): void {
    const s = this.sheet;
    const h = this.grid.clientHeight || ROW_PX * 20;
    const top = Math.max(0, this.grid.scrollTop - ROW_PX); // the sticky head covers one row
    const first = Math.max(0, Math.floor(top / ROW_PX) - 5);
    const last = Math.min(s.rows.length, Math.ceil((this.grid.scrollTop + h) / ROW_PX) + 5);
    const drawn = `${first}:${last}`;
    if (!force && this.drawn.key === drawn && this.drawn.sheet === s) return;
    this.drawn = { key: drawn, sheet: s };
    const [bt, bl, bb, br] = this.block();
    const out: HTMLElement[] = [];
    for (let r = first; r < last; r++) {
      const row = el("div", "data-row");
      row.dataset.row = String(r);
      row.style.top = `${r * ROW_PX}px`;
      row.setAttribute("role", "row");
      row.append(el("span", "data-num", String(r + 1)));
      s.rows[r].forEach((cell, c) => {
        const isNull = cell === null && s.tree;
        const span = el("span", "data-cell", isNull ? "null" : display(cell).replaceAll("\n", " ⏎ "));
        span.dataset.col = String(c);
        span.setAttribute("role", "gridcell");
        if (isNum(cell) || typeof cell === "boolean") span.classList.add("number");
        if (isNull) span.classList.add("null");
        if (r >= bt && r <= bb && c >= bl && c <= br) span.classList.add("chosen");
        if (r === this.cur[0] && c === this.cur[1]) {
          span.classList.add("current");
          span.setAttribute("aria-selected", "true");
        }
        row.append(span);
      });
      out.push(row);
    }
    this.cellsEl.replaceChildren(...out);
  }

  private fillBox(): void {
    if (this.hasCell()) {
      const [r, c] = this.cur;
      this.cellName.textContent = `${letter(c)}${r + 1}`;
      this.box.disabled = false;
      this.box.value = editText(this.sheet.rows[r][c], this.sheet.typed);
    } else {
      this.cellName.textContent = "";
      this.box.value = "";
      this.box.disabled = true;
    }
  }

  private updateTitle(): void {
    this.title.textContent = (this.dirty ? "• " : "") + (this.sourceName ?? "New table");
  }

  private updateButtons(): void {
    const s = this.sheet;
    const has = this.hasCell();
    this.saveBtn.disabled = this.saveAsBtn.disabled = this.saving;
    this.buttons.add_row.disabled = !s.columns.length;
    this.buttons.delete_rows.disabled = !has;
    this.buttons.delete_columns.disabled = !has;
    this.buttons.rename.disabled = !(s.header && s.columns.length);
    this.buttons.sort_up.disabled = this.buttons.sort_down.disabled = !(has && s.rows.length > 1);
  }

  showSheet(i: number): void {
    if (i === this.sheetIndex || i < 0 || i >= this.doc.sheets.length) return;
    this.endEdit(true);
    this.sheetIndex = i;
    this.history.endMerge();
    this.cur = this.anchor = [0, 0];
    this.grid.scrollTop = this.grid.scrollLeft = 0;
    this.fitColumns();
    this.showDoc(true);
  }

  /** Choose a cell (`anchor`: the block's other corner, default the same cell). */
  select(cell: Cellpos, anchor: Cellpos = cell): void {
    this.cur = cell;
    this.anchor = anchor;
    this.scrollTo(cell); // (draws the newly shown rows)
    this.paintSelection();
    this.fillBox();
    this.updateButtons();
  }

  /** Move the highlight on the cells already drawn (no new elements: a double-click's second
   * click must land on the same cell as the first). */
  private paintSelection(): void {
    const [bt, bl, bb, br] = this.block();
    for (const row of this.cellsEl.children as HTMLCollectionOf<HTMLElement>) {
      const r = Number(row.dataset.row);
      for (const span of row.querySelectorAll<HTMLElement>(".data-cell")) {
        const c = Number(span.dataset.col);
        span.classList.toggle("chosen", r >= bt && r <= bb && c >= bl && c <= br);
        const current = r === this.cur[0] && c === this.cur[1];
        span.classList.toggle("current", current);
        if (current) span.setAttribute("aria-selected", "true");
        else span.removeAttribute("aria-selected");
      }
    }
  }

  private scrollTo([r, c]: Cellpos): void {
    const g = this.grid;
    const y = r * ROW_PX;
    const view = g.clientHeight - ROW_PX; // under the sticky head
    if (y < g.scrollTop) g.scrollTop = y;
    else if (y + ROW_PX > g.scrollTop + view) g.scrollTop = y + ROW_PX - view;
    const x = NUM_PX + this.widths.slice(0, c).reduce((a, b) => a + b, 0);
    const w = this.widths[c] ?? 0;
    if (x - NUM_PX < g.scrollLeft) g.scrollLeft = x - NUM_PX;
    else if (x + w > g.scrollLeft + g.clientWidth) g.scrollLeft = x + w - g.clientWidth;
  }

  // ---------------------------------------------------------------- pointer and keys
  private cellAt(e: MouseEvent): Cellpos | null {
    const t = e.target as HTMLElement;
    const cell = t.closest<HTMLElement>(".data-cell");
    const row = t.closest<HTMLElement>(".data-row");
    if (cell && row) return [Number(row.dataset.row), Number(cell.dataset.col)];
    return null;
  }

  private pointerDown(e: PointerEvent): void {
    if (e.target === this.input || e.button !== 0) return;
    const t = e.target as HTMLElement;
    const s = this.sheet;
    const col = t.closest<HTMLElement>(".data-col");
    if (col) { // a column's name: the whole column
      const c = Number(col.dataset.col);
      this.select([0, c], [Math.max(s.rows.length - 1, 0), c]);
      return;
    }
    const num = t.closest<HTMLElement>(".data-num");
    if (num) { // a row's number: the whole row
      const r = Number((num.parentElement as HTMLElement).dataset.row);
      this.select([r, 0], [r, Math.max(s.columns.length - 1, 0)]);
      return;
    }
    const at = this.cellAt(e);
    if (!at) return;
    this.endEdit(true);
    this.dragging = true;
    this.select(at, e.shiftKey ? this.anchor : at);
  }

  private pointerMove(e: PointerEvent): void {
    if (!this.dragging || !(e.buttons & 1)) return;
    const at = this.cellAt(e);
    if (at && (at[0] !== this.cur[0] || at[1] !== this.cur[1])) this.select(at, this.anchor);
  }

  private doubleClick(e: MouseEvent): void {
    const col = (e.target as HTMLElement).closest<HTMLElement>(".data-col");
    if (col) {
      this.askRename(Number(col.dataset.col));
      return;
    }
    if (this.cellAt(e)) this.startEdit();
  }

  private gridKey(e: KeyboardEvent): void {
    if (e.target !== this.grid) return;
    const s = this.sheet;
    const name = keyName(e);
    const move = (dr: number, dc: number) => {
      e.preventDefault();
      if (!s.rows.length || !s.columns.length) return;
      const to: Cellpos = [Math.min(Math.max(this.cur[0] + dr, 0), s.rows.length - 1), Math.min(Math.max(this.cur[1] + dc, 0), s.columns.length - 1)];
      this.select(to, e.shiftKey ? this.anchor : to);
    };
    const page = Math.max(1, Math.floor(this.grid.clientHeight / ROW_PX) - 2);
    switch (e.key) {
      case "ArrowUp": return move(e.ctrlKey || e.metaKey ? -s.rows.length : -1, 0);
      case "ArrowDown": return move(e.ctrlKey || e.metaKey ? s.rows.length : 1, 0);
      case "ArrowLeft": return move(0, e.ctrlKey || e.metaKey ? -s.columns.length : -1);
      case "ArrowRight": return move(0, e.ctrlKey || e.metaKey ? s.columns.length : 1);
      case "PageUp": return move(-page, 0);
      case "PageDown": return move(page, 0);
      case "Home": return move(e.ctrlKey || e.metaKey ? -s.rows.length : 0, -s.columns.length);
      case "End": return move(e.ctrlKey || e.metaKey ? s.rows.length : 0, s.columns.length);
      case "Tab": return move(0, e.shiftKey ? -1 : 1);
    }
    if (name === "F2" || name === "Enter") {
      e.preventDefault();
      this.startEdit();
    } else if (name === "Delete" || name === "Backspace") {
      e.preventDefault();
      this.clearCells();
    } else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault(); // typing starts editing the cell with that letter
      this.startEdit(e.key);
    }
  }

  // ---------------------------------------------------------------- typing in a cell
  /** Type in the chosen cell in place (cells with a line break are edited in the box). */
  startEdit(initial?: string): void {
    if (!this.hasCell()) return;
    const [r, c] = this.cur;
    const text = editText(this.sheet.rows[r][c], this.sheet.typed);
    if (text.includes("\n")) {
      this.box.focus();
      return;
    }
    this.scrollTo(this.cur);
    this.editing = [r, c];
    const left = NUM_PX + this.widths.slice(0, c).reduce((a, b) => a + b, 0);
    Object.assign(this.input.style, { top: `${r * ROW_PX}px`, left: `${left}px`, width: `${this.widths[c]}px`, height: `${ROW_PX}px` });
    this.input.value = initial ?? text;
    this.input.hidden = false;
    this.input.focus();
    if (initial === undefined) this.input.select();
  }

  private endEdit(keep: boolean): void {
    const at = this.editing;
    if (!at) return;
    this.editing = null;
    const value = this.input.value;
    this.input.hidden = true;
    if (keep) this.setCell(at[0], at[1], value);
  }

  private inputKey(e: KeyboardEvent): void {
    const s = this.sheet;
    const go = (dr: number, dc: number) => {
      e.preventDefault();
      const at = this.editing!;
      this.endEdit(true);
      const to: Cellpos = [Math.min(Math.max(at[0] + dr, 0), s.rows.length - 1), Math.min(Math.max(at[1] + dc, 0), s.columns.length - 1)];
      this.select(to);
      this.grid.focus();
    };
    if (e.key === "Enter") go(e.shiftKey ? -1 : 1, 0);
    else if (e.key === "Tab") go(0, e.shiftKey ? -1 : 1);
    else if (e.key === "Escape") {
      e.preventDefault();
      this.endEdit(false);
      this.grid.focus();
    }
  }

  private boxCommit(): void {
    if (!this.hasCell()) return;
    const [r, c] = this.cur;
    if (this.box.value !== editText(this.sheet.rows[r][c], this.sheet.typed)) this.setCell(r, c, this.box.value);
  }

  // ---------------------------------------------------------------- editing
  private push(sheet: Sheet, label: string, merge: unknown = null, select?: Cellpos): void {
    if (sheet === this.sheet) return;
    const before = this.sheet;
    this.history.push(table.withSheet(this.doc, this.sheetIndex, sheet), label, merge);
    if (before.columns.length !== sheet.columns.length) this.fitColumns();
    if (select) this.cur = this.anchor = select;
    this.showDoc();
    if (select) this.scrollTo(select);
  }

  private edit(fn: (s: Sheet) => Sheet, label: string, merge: unknown = null, select?: Cellpos): boolean {
    let sheet: Sheet;
    try {
      sheet = fn(this.sheet);
    } catch (e) {
      if (!(e instanceof EditError)) throw e;
      this.showMessage(e.message, true);
      return false;
    }
    this.push(sheet, label, merge, select);
    return true;
  }

  setCell(r: number, c: number, text: string): boolean {
    return this.edit((s) => table.setCell(s, r, c, text), "Type", `cell:${this.sheetIndex}:${r}:${c}`);
  }

  addRow(): boolean {
    if (!this.sheet.columns.length) return false;
    const at = this.hasCell() ? this.cur[0] + 1 : this.sheet.rows.length;
    return this.edit((s) => table.insertRows(s, at), "Add row", null, [at, Math.max(this.cur[1], 0)]);
  }

  addColumn(name: string | null = null): boolean {
    const at = this.sheet.columns.length ? this.cur[1] + 1 : 0;
    return this.edit((s) => table.insertColumn(s, at, name), "Add column", null, [Math.max(this.cur[0], 0), at]);
  }

  deleteRows(): boolean {
    const rows = this.chosenRows();
    if (!rows.length) return false;
    const n = rows.length;
    return this.edit((s) => table.deleteRows(s, rows), `Delete ${n} row${n === 1 ? "" : "s"}`, null, [rows[0], this.cur[1]]);
  }

  deleteColumns(): boolean {
    const cols = this.chosenColumns();
    if (!cols.length) return false;
    return this.edit((s) => table.deleteColumns(s, cols), "Delete column", null, [this.cur[0], cols[0]]);
  }

  /** Asks a column's new name (tests replace it). */
  askText = (label: string, value: string): string | null => window.prompt(label, value);

  askRename(c: number): boolean {
    if (c < 0 || c >= this.sheet.columns.length) return false;
    if (!this.sheet.header) {
      this.showMessage("Turn on “First row is the header” to name the columns.");
      return false;
    }
    const name = this.askText("Column name:", this.sheet.columns[c]);
    return name !== null && this.edit((s) => table.renameColumn(s, c, name), "Rename column");
  }

  sort(descending: boolean): boolean {
    const c = this.cur[1];
    if (!this.hasCell()) return false;
    return this.edit((s) => table.sortRows(s, c, descending), descending ? "Sort Z→A" : "Sort A→Z", null, [0, c]);
  }

  setHeader(on: boolean): boolean {
    if (!HEADER_FORMATS.includes(this.doc.format)) return false;
    const done = this.edit((s) => table.setHeader(s, on), on ? "First row is the header" : "First row is data");
    this.fitColumns();
    this.showDoc();
    return done;
  }

  clearCells(): boolean {
    const changes: [number, number, string][] = [];
    for (const r of this.chosenRows()) for (const c of this.chosenColumns()) changes.push([r, c, ""]);
    return this.edit((s) => table.setCells(s, changes), "Clear");
  }

  undo(): void {
    const label = this.history.undo();
    if (label) {
      this.fitColumnsIfNeeded();
      this.showDoc();
      this.showMessage(`Undid: ${label}`);
    }
  }

  redo(): void {
    const label = this.history.redo();
    if (label) {
      this.fitColumnsIfNeeded();
      this.showDoc();
      this.showMessage(`Redid: ${label}`);
    }
  }

  private fitColumnsIfNeeded(): void {
    if (this.widths.length !== this.sheet.columns.length) this.fitColumns();
  }

  // ---------------------------------------------------------------- copy / paste
  /** The chosen block as tab-separated text (what spreadsheets paste). */
  copyText(): string {
    const [t, l, b, r] = this.block();
    const s = this.sheet;
    const rows: string[][] = [];
    for (let i = t; i <= b && i < s.rows.length; i++) {
      const row: string[] = [];
      for (let j = l; j <= r && j < s.columns.length; j++) row.push(display(s.rows[i][j]));
      rows.push(row);
    }
    return csvtext.write(rows, "\t", false, false);
  }

  private copyEvent(e: ClipboardEvent, cut: boolean): void {
    if (e.target !== this.grid || !this.hasCell()) return;
    e.preventDefault();
    e.clipboardData?.setData("text/plain", this.copyText());
    const [t, l, b, r] = this.block();
    const n = (b - t + 1) * (r - l + 1);
    if (cut) this.clearCells();
    this.showMessage(`${cut ? "Cut" : "Copied"} ${n} cell${n === 1 ? "" : "s"}.`);
  }

  /** Paste tab-separated text at the chosen cell; rows and columns are added when it doesn't
   * fit. One undo step. */
  paste(text: string): boolean {
    if (!text || !this.sheet.columns.length) return false;
    const rows = csvtext.parse(text.replace(/\r\n?/g, "\n"), "\t").rows;
    if (!rows.length) return false;
    const [r0, c0] = this.hasCell() ? this.cur : [this.sheet.rows.length, 0];
    let width = 0;
    for (const r of rows) width = Math.max(width, r.length);
    return this.edit((sheet) => {
      let s = sheet;
      const extraRows = r0 + rows.length - s.rows.length;
      if (extraRows > 0) s = table.insertRows(s, s.rows.length, extraRows);
      for (let k = s.columns.length; k < c0 + width; k++) s = table.insertColumn(s, s.columns.length);
      const changes: [number, number, string][] = [];
      rows.forEach((row, i) => row.forEach((v, j) => changes.push([r0 + i, c0 + j, v])));
      return table.setCells(s, changes);
    }, "Paste", null, [r0, c0]);
  }

  // ---------------------------------------------------------------- menus
  private contextMenu(e: MouseEvent): void {
    const col = (e.target as HTMLElement).closest<HTMLElement>(".data-col");
    const at = this.cellAt(e);
    if (!col && !at) return;
    e.preventDefault();
    let items: [string, () => void][];
    if (col) {
      const c = Number(col.dataset.col);
      if (this.sheet.rows.length) this.select([Math.max(this.cur[0], 0), c]);
      items = [
        ["Rename…", () => this.askRename(c)],
        ["Sort A→Z", () => this.sort(false)],
        ["Sort Z→A", () => this.sort(true)],
        ["Add a column to the right", () => this.addColumn()],
        ["Delete column", () => this.deleteColumns()],
      ];
    } else {
      const [t, l, b, r] = this.block();
      if (!(at![0] >= t && at![0] <= b && at![1] >= l && at![1] <= r)) this.select(at!);
      items = [
        ["Copy", () => void navigator.clipboard?.writeText(this.copyText())],
        ["Paste", () => void navigator.clipboard?.readText().then((t) => this.paste(t), () => this.showMessage("The browser didn't allow reading the clipboard: press Ctrl+V instead.", true))],
        ["Clear", () => this.clearCells()],
        ["Add a row below", () => this.addRow()],
        ["Delete the chosen rows", () => this.deleteRows()],
      ];
    }
    this.menu.replaceChildren(...items.map(([label, run]) => {
      const item = el("button", "", label);
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.addEventListener("click", () => {
        this.menu.hidden = true;
        run();
        this.grid.focus();
      });
      return item;
    }));
    this.menu.hidden = false;
    const w = this.menu.offsetWidth, h = this.menu.offsetHeight;
    this.menu.style.left = `${Math.max(0, Math.min(e.clientX, window.innerWidth - w - 4))}px`;
    this.menu.style.top = `${Math.max(0, Math.min(e.clientY, window.innerHeight - h - 4))}px`;
    (this.menu.firstElementChild as HTMLElement | null)?.focus();
  }

  // ---------------------------------------------------------------- find
  openFind(replace: boolean): void {
    this.findEl.hidden = false;
    const target = replace && this.findInput.value ? this.replaceInput : this.findInput;
    target.focus();
    target.select();
    this.countMatches();
  }

  private options(): SearchOptions {
    return { caseSensitive: this.findCase.checked, wholeWord: this.findWord.checked, regex: this.findRegex.checked };
  }

  private matches(): [number, number][] | null {
    try {
      return table.find(this.sheet, this.findInput.value, this.options());
    } catch (e) {
      if (!(e instanceof SearchError)) throw e;
      this.findCount.textContent = e.message;
      return null;
    }
  }

  private countMatches(): void {
    const found = this.matches();
    if (found) this.findCount.textContent = this.findInput.value ? `${found.length} found` : "";
  }

  /** Go to the next (1) / previous (-1) cell that matches; false if none does. */
  find(direction: number): boolean {
    const found = this.matches();
    if (!found?.length) return false;
    const cmp = (m: Cellpos) => m[0] - this.cur[0] || m[1] - this.cur[1];
    const hit = direction > 0 ? (found.find((m) => cmp(m) > 0) ?? found[0]) : ([...found].reverse().find((m) => cmp(m) < 0) ?? found[found.length - 1]);
    this.select(hit);
    this.findCount.textContent = `${found.indexOf(hit) + 1} of ${found.length}`;
    return true;
  }

  private replaceOne(): void {
    const [r, c] = this.cur;
    const found = this.matches();
    if (found?.some((m) => m[0] === r && m[1] === c)) {
      const cell = this.sheet.rows[r][c];
      let text: string;
      try {
        [text] = replaceText(display(cell), this.findInput.value, this.replaceInput.value, this.options());
      } catch (e) {
        if (!(e instanceof SearchError)) throw e;
        this.findCount.textContent = e.message;
        return;
      }
      // text stays text (the ' keeps "12" from turning into a number); others are retyped
      const typed = typeof cell === "string" ? editText(text, this.sheet.typed) : text;
      this.edit((s) => table.setCells(s, [[r, c, typed]]), "Replace");
    }
    this.find(1);
    this.countMatches();
  }

  private replaceAll(): void {
    let sheet: Sheet, n: number;
    try {
      [sheet, n] = table.replaceAll(this.sheet, this.findInput.value, this.replaceInput.value, this.options());
    } catch (e) {
      if (!(e instanceof SearchError)) throw e;
      this.findCount.textContent = e.message;
      return;
    }
    if (n) this.push(sheet, "Replace all");
    this.findCount.textContent = `Replaced ${n}`;
  }

  // ---------------------------------------------------------------- saving
  async save(): Promise<boolean> {
    this.endEdit(true);
    if (this.saving) return false;
    if (!this.saveName && !this.handle) return this.saveAs();
    return this.write(this.handle?.name ?? this.saveName!, this.doc.format);
  }

  async saveAs(): Promise<boolean> {
    this.endEdit(true);
    if (this.saving) return false;
    const fmt = this.doc.format;
    const fmts = [fmt, ...FORMATS.filter((f) => f !== fmt)];
    const choice = await this.askSave(`${this.sourceName ? baseOf(this.sourceName) : "Table"}.${fmt}`, fmts);
    if (!choice) return false;
    let target = extOf(choice.name);
    let name = choice.name;
    if (!fmts.includes(target)) {
      target = choice.format;
      name = `${baseOf(name)}.${target}`;
    }
    if (choice.toFile) {
      try {
        this.handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({ suggestedName: name.replace(/[\\/:*?"<>|]/g, "_") });
      } catch (err) {
        if ((err as { name?: string } | null)?.name !== "AbortError") {
          console.error("The save window couldn't be opened", err);
          this.showMessage("The browser couldn't open its save window. Try again, or use Save as without “Save to a file…”.", true);
        }
        return false;
      }
      name = this.handle.name ?? name;
    } else this.handle = undefined;
    return this.write(name, target);
  }

  /** The bytes of the same format, after the questions (null: not saved). */
  private sameFormatBytes(doc: DataDoc): Uint8Array | null {
    const lost = this.opened.lost;
    if (lost.length && !table.sameDoc(doc, this.opened.doc) && !this.askedLost) {
      if (!window.confirm(`Saving this ${NAMES[doc.format]} file can't keep its ${lost.join(" and ")}. Save anyway?`)) return null;
      this.askedLost = true;
    }
    try {
      return toBytes(this.opened, doc);
    } catch (e) {
      if (!(e instanceof Error) || e.message !== "encoding") throw e;
    }
    if (!window.confirm(`Some characters can't be stored in this file's encoding (${this.opened.file.encoding}). Save it as UTF-8 instead?`)) return null;
    return toBytes(this.opened, doc, true);
  }

  private async write(name: string, fmt: string): Promise<boolean> {
    const doc = this.doc;
    let data: Uint8Array;
    try {
      if (fmt === doc.format) {
        const bytes = this.sameFormatBytes(doc);
        if (!bytes) return false;
        data = bytes;
      } else data = await exportBytes(this.opened, doc, fmt);
    } catch (e) {
      console.error(`Couldn't save as ${fmt}`, e);
      this.showMessage(`The table couldn't be saved as ${NAMES[fmt] ?? fmt}${e instanceof Error ? `: ${e.message}` : "."}`, true);
      return false;
    }
    this.saving = true;
    this.updateButtons();
    try {
      const blob = new Blob([data as BlobPart], { type: MIME[fmt] ?? "application/octet-stream" });
      if (this.handle) {
        const w = await this.handle.createWritable();
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
      const reopened = openBytes(data, fmt);
      if (fmt !== doc.format) {
        const handle = this.handle;
        this.load(name, reopened); // now editing the new file
        this.handle = handle;
        this.showMessage(`Saved “${name}”. Now editing the ${NAMES[fmt]} file.`);
      } else {
        this.opened = reopened;
        this.sourceName = this.saveName = name;
        this.history.markSaved(doc);
        this.updateTitle();
        this.showMessage(`Saved “${name}”.`);
      }
      return true;
    } catch (err) {
      console.error(`Couldn't save ${name}`, err);
      this.showMessage(err instanceof DataError ? `Saved “${name}”, but it can't be opened again: ${err.message}` : `Couldn't save “${name}”.`, true);
      return false;
    } finally {
      this.saving = false;
      this.updateButtons();
    }
  }

  // ---------------------------------------------------------------- dialogs
  private dialog(cls: string, title: string): [HTMLDialogElement, HTMLElement] {
    const d = el("dialog", `editor-dialog ${cls}`);
    d.setAttribute("aria-label", title);
    const buttons = el("div", "row");
    d.append(el("h2", "", title), buttons);
    return [d, buttons];
  }

  private buildSaveDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("sub-save-dialog data-save-dialog", "Save as");
    const name = el("input");
    name.type = "text";
    name.setAttribute("aria-label", "File name");
    const format = el("select");
    format.setAttribute("aria-label", "Format");
    format.addEventListener("change", () => (name.value = `${baseOf(name.value)}.${format.value}`));
    const row = el("label", "field");
    row.append(el("span", "", "File name"), name);
    const row2 = el("label", "field");
    row2.append(el("span", "", "Format"), format);
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    const toFile = button("Save to a file…", "btn", "file");
    toFile.title = "Choose where to save; Save (Ctrl+S) then writes to the same file";
    buttons.append(el("div", "spacer"), button("Cancel", "btn", ""), toFile, button("Save", "primary small", "save"));
    dialog.insertBefore(row, buttons);
    dialog.insertBefore(row2, buttons);
    return dialog;
  }

  /** Asks the file name and format (and whether to pick a file); replaceable in tests. */
  askSave = (suggested: string, fmts: string[]): Promise<{ name: string; format: string; toFile: boolean } | null> => {
    const dialog = this.saveDialog;
    const name = dialog.querySelector("input") as HTMLInputElement;
    const format = dialog.querySelector("select") as HTMLSelectElement;
    const toFile = dialog.querySelector('[data-value="file"]') as HTMLButtonElement;
    toFile.hidden = !("showSaveFilePicker" in window);
    format.replaceChildren(...fmts.map((f) => new Option(`${NAMES[f]} (.${f})`, f)));
    name.value = suggested;
    return new Promise((resolve) => {
      const finish = (value: string) => {
        dialog.removeEventListener("click", clicked);
        dialog.removeEventListener("cancel", cancelled);
        dialog.removeEventListener("keydown", keyed);
        dialog.close();
        resolve(value ? { name: name.value.trim() || suggested, format: format.value, toFile: value === "file" } : null);
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
    const [dialog, buttons] = this.dialog("code-help", "Keyboard shortcuts");
    const rows = el("div", "code-help-rows");
    let group = "";
    for (const s of SHORTCUTS) {
      if (s.group !== group) {
        group = s.group;
        rows.append(el("h3", "", group));
      }
      const row = el("div", "code-help-row");
      row.append(el("kbd", "", s.keys.map(showKey).join(" / ") + (s.table ? " (in the table)" : "")), el("span", "", s.label));
      rows.append(row);
    }
    const close = el("button", "primary small", "Close");
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    buttons.append(el("div", "spacer"), close);
    dialog.insertBefore(rows, buttons);
    return dialog;
  }
}

export function mountDataEditor(host: HTMLElement, opts: DataEditorOptions): DataEditorPage {
  const page = new DataEditorPage(opts);
  host.append(page.el);
  return page;
}
