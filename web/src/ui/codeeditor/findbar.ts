// Find & replace bar of the website code editor — the desktop's gui/codeeditor/find_bar.py:
// match case, whole word, Python-style patterns (codeedit/search.ts), "N matches" / "N replaced",
// Replace needs the selection to be exactly one match, Replace all is one undo step.
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view";
import { SearchError, findAll, replaceAll, replacementAt, type SearchOptions } from "../../codeedit/search";

export const setMatches = StateEffect.define<[number, number][]>();
const matchMark = Decoration.mark({ class: "cm-find-match" });

/** The find matches, painted with --code-match. */
export const matchField = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (e.is(setMatches)) deco = Decoration.set(e.value.map(([a, b]) => matchMark.range(a, b)), true);
    }
    return deco;
  },
  provide: (f) => EditorView.decorations.from(f),
});

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

export class CodeFindBar {
  readonly el = el("div", "code-find");
  readonly query = el("input");
  readonly replacement = el("input");
  readonly count = el("span", "code-find-count");
  readonly caseBox = el("input");
  readonly wholeBox = el("input");
  readonly regexBox = el("input");
  private readonly replaceRow = el("div", "code-find-row code-replace-row");
  private spans: [number, number][] = [];
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly view: () => EditorView) {
    this.el.hidden = true;
    this.query.type = "search";
    this.query.placeholder = "Find";
    this.query.setAttribute("aria-label", "Find");
    this.replacement.type = "text";
    this.replacement.placeholder = "Replace with";
    this.replacement.setAttribute("aria-label", "Replace with");
    const check = (box: HTMLInputElement, label: string, title = "") => {
      box.type = "checkbox";
      const l = el("label", "check");
      if (title) l.title = title;
      l.append(box, ` ${label}`);
      box.addEventListener("change", () => this.refresh());
      return l;
    };
    const btn = (label: string, title: string, run: () => void, cls = "btn small") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.title = title;
      b.setAttribute("aria-label", title);
      b.addEventListener("click", run);
      return b;
    };
    const findRow = el("div", "code-find-row");
    findRow.append(
      this.query,
      btn("↑", "Previous match (Shift+F3)", () => this.findPrev()),
      btn("↓", "Next match (F3)", () => this.findNext()),
      check(this.caseBox, "Match case"),
      check(this.wholeBox, "Whole word"),
      check(this.regexBox, "Pattern (.*)", "Regular expression: \\d, \\w, ( ) groups, …"),
      this.count,
      btn("×", "Close (Esc)", () => this.close(), "btn small code-find-close"),
    );
    this.replaceRow.append(
      this.replacement,
      btn("Replace", "Replace this match", () => this.replace()),
      btn("Replace all", "Replace every match", () => this.replaceAll()),
    );
    this.el.append(findRow, this.replaceRow);
    this.query.addEventListener("input", () => this.refresh());
    this.query.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (e.shiftKey) this.findPrev();
        else this.findNext();
      }
    });
    this.replacement.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.replace();
      }
    });
    this.el.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        this.close();
      }
    });
  }

  get options(): SearchOptions {
    return { caseSensitive: this.caseBox.checked, wholeWord: this.wholeBox.checked, regex: this.regexBox.checked };
  }

  open(replace: boolean): void {
    const v = this.view();
    const sel = v.state.selection.main;
    const picked = v.state.sliceDoc(sel.from, sel.to);
    if (picked && !picked.includes("\n")) this.query.value = picked;
    this.replaceRow.hidden = !replace;
    this.el.hidden = false;
    this.query.focus();
    this.query.select();
    this.refresh();
  }

  close(): void {
    this.el.hidden = true;
    this.spans = [];
    this.view().dispatch({ effects: setMatches.of([]) });
    this.view().focus();
  }

  /** The text changed (typing, another tab): count again shortly. */
  documentChanged(): void {
    if (this.el.hidden) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.refresh(), 200);
  }

  refresh(keepCount = false): void {
    if (this.el.hidden) return;
    const v = this.view();
    try {
      this.spans = findAll(v.state.doc.toString(), this.query.value, this.options);
    } catch (e) {
      this.spans = [];
      this.count.textContent = e instanceof SearchError ? e.message : "That pattern isn't valid.";
      v.dispatch({ effects: setMatches.of([]) });
      return;
    }
    v.dispatch({ effects: setMatches.of(this.spans) });
    if (keepCount) return;
    const n = this.spans.length;
    this.count.textContent = !this.query.value ? "" : n === 0 ? "No matches" : `${n} match${n > 1 ? "es" : ""}`;
  }

  private select([from, to]: [number, number]): void {
    const v = this.view();
    v.dispatch({ selection: { anchor: from, head: to }, scrollIntoView: true });
  }

  findNext(): void {
    this.refresh();
    if (!this.spans.length) return;
    const sel = this.view().state.selection.main;
    this.select(this.spans.find(([a]) => a >= (sel.empty ? sel.head : sel.to)) ?? this.spans[0]);
  }

  findPrev(): void {
    this.refresh();
    if (!this.spans.length) return;
    const from = this.view().state.selection.main.from;
    this.select([...this.spans].reverse().find(([a]) => a < from) ?? this.spans[this.spans.length - 1]);
  }

  replace(): void {
    const v = this.view();
    const sel = v.state.selection.main;
    let hit: [number, number, string] | null = null;
    try {
      hit = sel.empty ? null : replacementAt(v.state.doc.toString(), this.query.value, this.replacement.value, sel.from, this.options);
    } catch (e) {
      this.count.textContent = e instanceof SearchError ? e.message : "That replacement isn't valid.";
      return;
    }
    if (!hit || hit[1] !== sel.to) {
      this.findNext(); // the selection must be exactly one match
      return;
    }
    v.dispatch({ changes: { from: hit[0], to: hit[1], insert: hit[2] }, selection: { anchor: hit[0] + hit[2].length } });
    this.findNext();
  }

  replaceAll(): void {
    const v = this.view();
    const text = v.state.doc.toString();
    let result: [string, number];
    try {
      result = replaceAll(text, this.query.value, this.replacement.value, this.options);
    } catch (e) {
      this.count.textContent = e instanceof SearchError ? e.message : "That pattern isn't valid.";
      return;
    }
    const [next, n] = result;
    if (!n) {
      this.count.textContent = "No matches";
      return;
    }
    v.dispatch({ changes: { from: 0, to: text.length, insert: next } }); // one undo step
    this.refresh(true);
    this.count.textContent = `${n} replaced`;
  }
}
