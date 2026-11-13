/**
 * The website document editor's formatting bar (the desktop's `gui/doceditor/format_bar.py`):
 * style, font and size boxes, B / I / U / S, colour, highlight, link, the alignments, lists,
 * indent, picture, table, the table menu, page break, rule and clear formatting. Every control
 * runs a command from `commands.ts` and gives the focus back to the page. Dialogs go through an
 * `Asker`, so tests can answer them.
 */

import type { Node as PMNode } from "prosemirror-model";
import type { Command, EditorState } from "prosemirror-state";
import {
  activeState,
  changeIndent,
  clearFormatting,
  insertLink,
  insertPageBreak,
  insertRule,
  insertTable,
  selectedTextblocks,
  setAlign,
  setBlockStyle,
  setColor,
  setFont,
  setHighlight,
  setSize,
  tableOp,
  toggleList,
  toggleMark,
  type Align,
  type BlockStyle,
  type TableOp,
  type ToggleMark,
} from "./commands";
import { docSchema } from "./schema";
import { MAX_PICTURE_BYTES, NOT_A_PICTURE, PICTURE_TYPES, pictureExt, type DocView } from "./view";

/** The questions the bar asks (a dialog each); null = cancelled. */
export interface Asker {
  text(label: string, value?: string): Promise<string | null>;
  color(label?: string): Promise<string | null>;
  file(accept: string): Promise<File | null>;
  tableSize(): Promise<[number, number] | null>;
}

export const STYLES: [BlockStyle, string][] = [
  ["normal", "Normal"],
  ["h1", "Heading 1"],
  ["h2", "Heading 2"],
  ["h3", "Heading 3"],
  ["quote", "Quote"],
  ["code", "Code"],
];
export const SIZES = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 32, 36, 48, 60, 72];
/** The size text shows in when it has no size of its own (the page's, see styles.css). */
export const DEFAULT_PT = 12;
/** Common fonts offered in the font box (the document's own are added to them). */
export const FONTS = [
  "Arial",
  "Calibri",
  "Cambria",
  "Comic Sans MS",
  "Courier New",
  "Garamond",
  "Georgia",
  "Helvetica",
  "Segoe UI",
  "Tahoma",
  "Times New Roman",
  "Trebuchet MS",
  "Verdana",
];
const TOGGLES: [ToggleMark, string, string][] = [
  ["bold", "B", "Bold"],
  ["italic", "I", "Italic"],
  ["underline", "U", "Underline"],
  ["strike", "S", "Strikethrough"],
];
const ALIGNS: [Align, string][] = [
  ["left", "Left"],
  ["center", "Centre"],
  ["right", "Right"],
  ["justify", "Justify"],
];
const LISTS: ["bullet" | "number", string, string][] = [
  ["bullet", "• List", "Bulleted list"],
  ["number", "1. List", "Numbered list"],
];
export const TABLE_OPS: [TableOp, string][] = [
  ["row_above", "Insert row above"],
  ["row_below", "Insert row below"],
  ["col_left", "Insert column left"],
  ["col_right", "Insert column right"],
  ["del_row", "Delete row"],
  ["del_col", "Delete column"],
  ["del_table", "Delete table"],
  ["merge", "Merge selected cells"],
  ["split", "Split cell"],
];
const PICTURE_ACCEPT = Object.keys(PICTURE_TYPES).join(",");
export { NOT_A_PICTURE };

/** The style a textblock shows in the style box (as `activeState`: a quote first). */
function styleOf(b: { node: PMNode; quoted: boolean }): string {
  if (b.quoted) return "quote";
  if (b.node.type === docSchema.nodes.heading) return `h${Math.min(Math.max(Number(b.node.attrs.level), 1), 3)}`;
  return b.node.type === docSchema.nodes.code_block ? "code" : "normal";
}

/** Whether a selection covers lines of different styles (list lines aside: the style box leaves
 * them alone). */
function mixedStyles(state: EditorState): boolean {
  if (state.selection.empty) return false;
  const seen = new Set<string>();
  for (const b of selectedTextblocks(state)) {
    if (b.parent === docSchema.nodes.list_item) continue;
    seen.add(styleOf(b));
    if (seen.size > 1) return true;
  }
  return false;
}

/** Whether a selection holds text in different fonts (or some text with none). */
function mixedFonts(state: EditorState): boolean {
  const sel = state.selection;
  if (sel.empty) return false;
  let first: string | undefined;
  let mixed = false;
  state.doc.nodesBetween(sel.from, sel.to, (node) => {
    if (mixed) return false;
    if (!node.isText) return true;
    const family = String(docSchema.marks.font.isInSet(node.marks)?.attrs.family ?? "");
    if (first === undefined) first = family;
    else if (family !== first) mixed = true;
    return false;
  });
  return mixed;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

/** The font families the document uses (a walk over its text). */
function documentFonts(view: DocView): string[] {
  const found = new Set<string>();
  view.view.state.doc.descendants((node) => {
    if (node.isInline) {
      const font = docSchema.marks.font.isInSet(node.marks);
      if (font) found.add(String(font.attrs.family));
      return false;
    }
    return true;
  });
  return [...found];
}

export class FormatBar {
  readonly el: HTMLElement = el("div", "doc-format");
  readonly buttons: Record<string, HTMLButtonElement> = {};
  readonly styleBox: HTMLSelectElement = el("select");
  readonly fontBox: HTMLSelectElement = el("select");
  readonly sizeBox: HTMLSelectElement = el("select");
  readonly tableMenu: HTMLDetailsElement = el("details", "menu doc-table-menu");
  /** Where a plain-language problem goes (the page shows it); the details go to the console. */
  onError: (message: string) => void = () => {};

  constructor(
    private readonly view: DocView,
    private readonly ask: Asker,
  ) {
    const add = (...nodes: HTMLElement[]) => this.el.append(...nodes);
    this.el.setAttribute("role", "toolbar");
    this.el.setAttribute("aria-label", "Formatting");

    // a hidden blank entry shows a selection of mixed styles, so choosing any style applies it
    const mixed = new Option("", "");
    mixed.hidden = true;
    this.styleBox.append(mixed);
    for (const [key, label] of STYLES) this.styleBox.append(new Option(label, key));
    this.styleBox.title = "Paragraph style";
    this.styleBox.setAttribute("aria-label", "Paragraph style");
    this.styleBox.addEventListener("change", () => {
      if (this.styleBox.value) this.apply(setBlockStyle(this.styleBox.value as BlockStyle));
    });

    this.fontBox.title = "Font";
    this.fontBox.setAttribute("aria-label", "Font");
    this.fontBox.classList.add("doc-font");
    this.fillFonts([]);
    // the document's fonts are looked up when the box is about to open, not on every change
    for (const type of ["focus", "pointerdown"]) this.fontBox.addEventListener(type, () => this.fillFonts(documentFonts(view)));
    this.fontBox.addEventListener("change", () => {
      if (this.fontBox.value) this.apply(setFont(this.fontBox.value));
    });

    this.sizeBox.title = "Font size";
    this.sizeBox.setAttribute("aria-label", "Font size");
    this.sizeBox.classList.add("doc-size");
    // applied on "change" alone: focusing the box and leaving it changes nothing (a mixed
    // selection keeps its sizes), and choosing applies once
    this.sizeBox.addEventListener("change", () => {
      const pt = Number(this.sizeBox.value);
      if (this.sizeBox.value && Number.isFinite(pt)) this.apply(setSize(pt));
    });
    add(this.styleBox, this.fontBox, this.sizeBox);

    for (const [key, text, tip] of TOGGLES) add(this.toggle(key, text, tip, () => this.apply(toggleMark(key))));
    add(
      this.button("color", "A", "Text colour", () => void this.pickColor(false)),
      this.button("highlight", "Mark", "Highlight", () => void this.pickColor(true)),
      this.button("link", "Link", "Insert link", () => void this.insertLink()),
    );
    for (const [key, tip] of ALIGNS) add(this.toggle(key, tip, `Align ${tip.toLowerCase()}`, () => this.apply(setAlign(key))));
    for (const [key, text, tip] of LISTS) add(this.toggle(key, text, tip, () => this.apply(toggleList(key))));
    add(
      this.button("indent", "→", "Indent", () => this.apply(changeIndent(1))),
      this.button("outdent", "←", "Outdent", () => this.apply(changeIndent(-1))),
      this.button("picture", "Picture", "Insert picture", () => void this.insertPicture()),
      this.button("table", "Table", "Insert table", () => void this.insertTable()),
    );

    const summary = el("summary", "btn small", "Table ▾");
    summary.title = "Edit the table";
    const items = el("div", "menu-items");
    items.setAttribute("role", "menu");
    for (const [op, label] of TABLE_OPS) {
      const item = el("button", "menu-item", label);
      item.type = "button";
      item.setAttribute("role", "menuitem");
      item.addEventListener("mousedown", (e) => e.preventDefault());
      item.addEventListener("click", () => {
        this.tableMenu.open = false;
        this.apply(tableOp(op));
      });
      items.append(item);
    }
    this.tableMenu.append(summary, items);
    // a click elsewhere closes the menu (listened to only while it is open)
    const outside = (e: Event) => {
      if (!this.tableMenu.contains(e.target as Node)) this.tableMenu.open = false;
    };
    // its keys, listened to on the whole page while it is open (opened with the mouse, the
    // focus may still be in the page): Esc closes it, the arrows / Home / End move through it
    const keys = (e: KeyboardEvent) => {
      const all = [...this.tableMenu.querySelectorAll<HTMLButtonElement>(".menu-item:not(:disabled)")];
      const at = all.indexOf(document.activeElement as HTMLButtonElement);
      let next = -1;
      if (e.key === "Escape") {
        this.tableMenu.open = false;
        closed();
        view.focus();
      } else if (e.key === "ArrowDown") next = (at + 1) % all.length;
      else if (e.key === "ArrowUp") next = (at - 1 + all.length) % all.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = all.length - 1;
      else return;
      e.preventDefault();
      e.stopPropagation();
      if (next >= 0) all[next]?.focus();
    };
    const opened = () => {
      // fixed under its button: on phones the bar scrolls sideways and would clip the menu
      const r = summary.getBoundingClientRect();
      items.style.position = "fixed";
      items.style.top = `${r.bottom + 4}px`;
      items.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - items.offsetWidth - 8))}px`;
      document.addEventListener("pointerdown", outside, true);
      document.addEventListener("keydown", keys, true);
      this.tableMenu.querySelector<HTMLButtonElement>(".menu-item:not(:disabled)")?.focus();
    };
    const closed = () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", keys, true);
    };
    this.tableMenu.addEventListener("toggle", () => {
      if (this.tableMenu.open && this.tableMenu.classList.contains("disabled")) this.tableMenu.open = false;
      if (this.tableMenu.open) opened();
      else closed();
    });
    summary.addEventListener("mousedown", (e) => e.preventDefault());
    add(this.tableMenu);

    add(
      this.button("page_break", "Break", "Page break", () => this.apply(insertPageBreak)),
      this.button("rule", "—", "Horizontal line", () => this.apply(insertRule)),
      this.button("clear", "Clear", "Clear formatting", () => this.apply(clearFormatting)),
    );

    view.listen(() => this.refresh());
    this.refresh();
  }

  // ----- widgets ------------------------------------------------------------------------------

  private button(key: string, text: string, tip: string, run: () => void): HTMLButtonElement {
    const b = el("button", "btn small", text);
    b.type = "button";
    b.title = tip;
    b.setAttribute("aria-label", tip);
    // like the desktop's NoFocus: the page keeps the focus and its selection
    b.addEventListener("mousedown", (e) => e.preventDefault());
    b.addEventListener("click", run);
    this.buttons[key] = b;
    return b;
  }

  private toggle(key: string, text: string, tip: string, run: () => void): HTMLButtonElement {
    const b = this.button(key, text, tip, run);
    b.classList.add("toggle", `fmt-${key}`);
    b.setAttribute("aria-pressed", "false");
    return b;
  }

  private fillFonts(used: string[]): void {
    const current = this.fontBox.value;
    const shown = this.fontBox.options[0]?.text ?? "Default font";
    const names = [...new Set([...FONTS, ...used])].sort((a, b) => a.localeCompare(b));
    const blank = new Option(shown, "");
    blank.hidden = true;
    this.fontBox.replaceChildren(blank, ...names.map((n) => new Option(n, n)));
    this.fontBox.value = current;
  }

  // ----- applying -----------------------------------------------------------------------------

  private apply(command: Command): void {
    this.view.run(command);
    this.view.focus();
    this.refresh();
  }

  private async pickColor(highlight: boolean): Promise<void> {
    const value = await this.ask.color(highlight ? "Highlight colour" : "Text colour");
    if (value) this.apply(highlight ? setHighlight(value) : setColor(value));
  }

  private async insertLink(): Promise<void> {
    const url = (await this.ask.text("Web address:", "https://"))?.trim();
    if (url && url !== "https://") this.apply(insertLink(url, null));
  }

  private async insertPicture(): Promise<void> {
    const file = await this.ask.file(PICTURE_ACCEPT);
    if (!file) return;
    const ext = pictureExt(file);
    if (!ext || file.size > MAX_PICTURE_BYTES) {
      this.onError(NOT_A_PICTURE);
      return;
    }
    try {
      await this.view.addPicture(new Uint8Array(await file.arrayBuffer()), ext);
    } catch (err) {
      console.error("The picture couldn't be added", err);
      this.onError("The picture couldn't be read.");
    }
    this.view.focus();
  }

  private async insertTable(): Promise<void> {
    const size = await this.ask.tableSize();
    if (size) this.apply(insertTable(size[0], size[1]));
  }

  // ----- state --------------------------------------------------------------------------------

  /** Show the formatting at the cursor. */
  refresh(): void {
    const s = activeState(this.view.view.state);
    const press = (key: string, on: boolean) => this.buttons[key].setAttribute("aria-pressed", String(on));
    for (const [key] of TOGGLES) press(key, s.marks.has(key));
    for (const [key] of ALIGNS) press(key, s.align === key);
    press("bullet", s.list === "bullet");
    press("number", s.list === "number");
    const state = this.view.view.state;
    this.styleBox.value = mixedStyles(state) ? "" : s.style;

    // mixed fonts show blank, text with no font of its own "Default font" (the hidden "" entry)
    const mixedFont = mixedFonts(state);
    const font = mixedFont ? "" : (s.font ?? "");
    if (font && ![...this.fontBox.options].some((o) => o.value === font)) this.fontBox.append(new Option(font, font));
    this.fontBox.options[0].text = mixedFont ? "" : "Default font";
    this.fontBox.value = font;

    // the box shows the size at the cursor; a hidden "" entry stands for the page's own size, so
    // choosing that size from the list still applies it
    const size = s.size === null ? "" : String(s.size);
    const options = [...SIZES.map(String)];
    if (size && !options.includes(size)) options.push(size);
    options.sort((a, b) => Number(a) - Number(b));
    if (this.sizeBox.options.length !== options.length + 1 || [...this.sizeBox.options].slice(1).some((o, i) => o.value !== options[i])) {
      const blank = new Option(String(DEFAULT_PT), "");
      blank.hidden = true;
      this.sizeBox.replaceChildren(blank, ...options.map((v) => new Option(v, v)));
    }
    this.sizeBox.value = size;

    this.tableMenu.classList.toggle("disabled", !s.inTable);
    this.tableMenu.querySelector("summary")?.setAttribute("aria-disabled", String(!s.inTable));
    for (const item of this.tableMenu.querySelectorAll<HTMLButtonElement>(".menu-item")) item.disabled = !s.inTable;
    if (!s.inTable) this.tableMenu.open = false;
  }
}

// ----- the browser's own dialogs -----------------------------------------------------------------

/** The picker waiting for an answer (a new pick ends it, as cancelled). */
let pending: (() => void) | null = null;
/** After the window gets the focus back, how long a colour may still arrive. */
const RETURN_GRACE_MS = 300;

/**
 * An input element the user picks with (a colour or a file). Resolves with what was chosen
 * (`change`), or null when the picker closes without one: its `cancel`, or another pick
 * starting. A colour picker's last `input` counts too, and the window getting the focus back
 * also ends a colour pick (`focusEnds`; some browsers never send `cancel` for one); there a
 * value equal to the starting one is no choice. A file pick never ends on the focus: Chrome
 * can send `change` well after the focus is back. The input is always removed.
 */
function pick<T>(input: HTMLInputElement, read: () => T | null, focusEnds: boolean): Promise<T | null> {
  pending?.();
  return new Promise((resolve) => {
    input.style.position = "fixed";
    input.style.opacity = "0";
    input.style.pointerEvents = "none";
    document.body.append(input);
    const initial = read();
    let latest: T | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const done = (value: T | null) => {
      if (pending !== cancel) return; // already answered
      pending = null;
      clearTimeout(timer);
      window.removeEventListener("focus", back);
      input.remove();
      resolve(value);
    };
    const cancel = () => done(null);
    // a picker that closed: what it last showed counts, unless that is where it started
    const closed = () => done(latest !== null && latest !== initial ? latest : null);
    const back = () => {
      clearTimeout(timer);
      timer = setTimeout(closed, RETURN_GRACE_MS);
    };
    pending = cancel;
    input.addEventListener("input", () => {
      latest = read();
    });
    input.addEventListener("change", () => done(read()));
    input.addEventListener("cancel", closed);
    if (focusEnds) window.addEventListener("focus", back);
    input.click();
  });
}

/** The page's default questions: `prompt`, the colour and file pickers, a small table dialog. */
export const browserAsker: Asker = {
  text: async (label, value = "") => window.prompt(label, value),
  color: (label) => {
    const input = el("input");
    input.type = "color";
    if (label) input.title = label;
    // each picker starts one step from the colour people most often want (yellow, black), so
    // choosing exactly that is a change the input reports
    input.value = label === "Highlight colour" ? "#ffff01" : "#010101";
    return pick(input, () => input.value, true);
  },
  file: (accept) => {
    const input = el("input");
    input.type = "file";
    input.accept = accept;
    return pick(input, () => input.files?.[0] ?? null, false);
  },
  tableSize: () =>
    new Promise((resolve) => {
      // the image editor's dialog look (styles.css .editor-dialog)
      const dialog = el("dialog", "editor-dialog doc-table-dialog");
      dialog.setAttribute("aria-label", "Insert table");
      const form = el("form");
      form.method = "dialog";
      const number = (label: string) => {
        const input = el("input");
        input.type = "number";
        input.min = "1";
        input.max = "50";
        input.value = "3";
        input.required = true;
        const row = el("label", "field");
        row.append(el("span", "", label), input);
        form.append(row);
        return input;
      };
      form.append(el("h2", "", "Insert table"));
      const rows = number("Rows");
      const cols = number("Columns");
      const buttons = el("div", "row");
      const cancel = el("button", "btn small", "Cancel");
      cancel.type = "button";
      const ok = el("button", "primary small", "OK");
      ok.value = "ok";
      buttons.append(el("div", "spacer"), cancel, ok);
      form.append(buttons);
      dialog.append(form);
      document.body.append(dialog);
      const clamp = (v: string) => Math.min(50, Math.max(1, Math.round(Number(v)) || 1));
      cancel.addEventListener("click", () => dialog.close());
      dialog.addEventListener("close", () => {
        dialog.remove();
        resolve(dialog.returnValue === "ok" ? [clamp(rows.value), clamp(cols.value)] : null);
      });
      dialog.showModal();
    }),
};
