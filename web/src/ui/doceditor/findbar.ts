/**
 * The website document editor's find & replace bar (the desktop's `gui/doceditor/find_bar.py`),
 * built on prosemirror-search's `SearchQuery`. As on the desktop:
 * - a search matches across an empty link (an invisible `empty_inline`), never across a picture
 *   or a line break, and a space also finds a no-break space;
 * - "Whole word" wants no letter or digit right before and after the match;
 * - Find wraps around; Replace replaces the selected match and selects the next; Replace all is
 *   one undo step. Esc closes, Enter finds the next match, Shift+Enter the previous one.
 * - Matches aren't all marked (nor on the desktop): marking them redraws on every keystroke,
 *   which took seconds on a long document.
 */

import { Fragment, Mark, type Node as PMNode } from "prosemirror-model";
import { Selection, TextSelection, type EditorState, type Transaction } from "prosemirror-state";
import { closeHistory } from "prosemirror-history";
import { SearchQuery } from "prosemirror-search";
import { BULK_MIN, COMMAND_META, rebuildBlocks } from "./commands";
import { docSchema } from "./schema";
import type { DocView } from "./view";

export interface FindOptions {
  backward?: boolean;
  matchCase?: boolean;
  wholeWord?: boolean;
}

interface Match {
  from: number;
  to: number;
}

const EMPTY = docSchema.nodes.empty_inline;
/** What prosemirror-search reads for an inline leaf (a picture, a line break, an empty tag). */
const LEAF = "\ufffc";
const SPACES = "[ \u00a0]";
const SPECIAL = /[\\^$.*+?()[\]{}|/]/g;
const WORD_END = /[\p{L}\p{N}]$/u;
const WORD_START = /^[\p{L}\p{N}]/u;

/** `text` as a pattern: every character literal (a space also a no-break space), with any empty
 * tags allowed between them. */
export function findPattern(text: string): string {
  return Array.from(text)
    .map((c) => (c === " " || c === "\u00a0" ? SPACES : c.replace(SPECIAL, "\\$&")))
    .join(`${LEAF}*`);
}

/** The text right before (`dir` -1) or after (1) content offset `at` of `block`, empty tags
 * skipped ("" at the block's edge; the leaf character next to a picture or line break). */
function textNear(block: PMNode, at: number, dir: 1 | -1): string {
  for (let p = at, i = 0; i < 10000; i++) {
    const $p = block.resolve(p);
    const n = dir < 0 ? $p.nodeBefore : $p.nodeAfter;
    if (!n) return "";
    if (n.type === EMPTY) {
      p += dir;
      continue;
    }
    return n.isText ? (n.text as string) : LEAF;
  }
  return "";
}

/** Whether content offsets [a, b) of `block` are a good match: no leaf but empty tags in it and,
 * for a whole word, not part of a longer word. Worked out in the block alone, so checking many
 * matches doesn't search the document for each. */
function goodIn(block: PMNode, a: number, b: number, whole: boolean): boolean {
  let ok = true;
  block.nodesBetween(a, b, (node) => {
    if (node.isLeaf && !node.isText && node.type !== EMPTY) ok = false;
    return ok;
  });
  if (!ok || !whole) return ok;
  return !WORD_END.test(textNear(block, a, -1)) && !WORD_START.test(textNear(block, b, 1));
}

/** `goodIn` as prosemirror-search's filter. */
function goodMatch(whole: boolean) {
  return (state: EditorState, result: Match): boolean => {
    const $from = state.doc.resolve(result.from);
    const start = $from.start();
    return goodIn($from.parent, result.from - start, result.to - start, whole);
  };
}

/** A textblock's text as prosemirror-search reads it (a leaf is one character). */
function blockText(block: PMNode): string {
  let out = "";
  block.forEach((child) => {
    out += child.isText ? (child.text as string) : LEAF;
  });
  return out;
}

/** `block` with the content ranges `ranges` (sorted, not overlapping) replaced by `text`, which
 * takes the marks across each range. */
function replaceIn(block: PMNode, ranges: [number, number][], text: string): PMNode {
  const kids: PMNode[] = [];
  let at = 0;
  for (const [a, b] of ranges) {
    if (a > at) block.content.cut(at, a).forEach((n) => kids.push(n));
    const marks = block.resolve(a).marksAcross(block.resolve(b)) ?? Mark.none;
    if (text) kids.push(docSchema.text(text, marks));
    at = b;
  }
  block.content.cut(at).forEach((n) => kids.push(n));
  return block.copy(Fragment.fromArray(kids));
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

export class FindBar {
  readonly el: HTMLElement = el("div", "doc-find");
  readonly findInput: HTMLInputElement = el("input");
  readonly replaceInput: HTMLInputElement = el("input");
  readonly caseBox: HTMLInputElement = el("input");
  readonly wholeBox: HTMLInputElement = el("input");
  readonly status: HTMLElement = el("span", "doc-find-status");
  private readonly replaceWidgets: HTMLElement[] = [];

  constructor(private readonly view: DocView) {
    this.el.hidden = true;
    this.el.setAttribute("role", "search");
    this.findInput.type = "search";
    this.findInput.placeholder = "Find";
    this.findInput.setAttribute("aria-label", "Find");
    this.replaceInput.type = "text";
    this.replaceInput.placeholder = "Replace with";
    this.replaceInput.setAttribute("aria-label", "Replace with");
    this.status.setAttribute("aria-live", "polite");
    const check = (box: HTMLInputElement, label: string) => {
      box.type = "checkbox";
      const wrap = el("label", "check");
      wrap.append(box, label);
      return wrap;
    };
    const prev = this.button("Previous", () => this.findClicked(true));
    const next = this.button("Next", () => this.findClicked(false));
    const replace = this.button("Replace", () => this.replace(this.findInput.value, this.replaceInput.value, this.options()));
    const all = this.button("Replace all", () => this.replaceAll(this.findInput.value, this.replaceInput.value, this.options()));
    const close = this.button("Close", () => this.close());
    close.textContent = "×";
    close.classList.add("doc-find-close");
    this.replaceWidgets.push(this.replaceInput, replace, all);
    this.el.append(
      this.findInput,
      prev,
      next,
      this.replaceInput,
      replace,
      all,
      check(this.caseBox, "Match case"),
      check(this.wholeBox, "Whole word"),
      this.status,
      close,
    );
    for (const input of [this.findInput, this.replaceInput]) {
      input.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          e.preventDefault();
          this.close();
        } else if (e.key === "Enter") {
          e.preventDefault();
          this.findClicked(e.shiftKey);
        }
      });
    }
  }

  private button(text: string, run: () => void): HTMLButtonElement {
    const b = el("button", "btn small", text);
    b.type = "button";
    b.title = text;
    b.addEventListener("click", run);
    return b;
  }

  private options(): FindOptions {
    return { matchCase: this.caseBox.checked, wholeWord: this.wholeBox.checked };
  }

  private findClicked(backward: boolean): void {
    this.find(this.findInput.value, { ...this.options(), backward });
  }

  // ----- showing ------------------------------------------------------------------------------

  /** Show the bar (with the replace part when `replace`), filled with a one-line selection. */
  open(replace: boolean): void {
    this.el.hidden = false;
    for (const w of this.replaceWidgets) w.hidden = !replace;
    const { from, to } = this.view.view.state.selection;
    if (to > from && to - from < 1000) {
      const text = this.view.view.state.doc.textBetween(from, to, "\n", "\n");
      if (text && !text.includes("\n")) this.findInput.value = text;
    }
    this.status.textContent = "";
    this.findInput.focus();
    this.findInput.select();
  }

  /** Hide the bar; the page gets the focus back. */
  close(): void {
    this.el.hidden = true;
    this.view.focus();
  }

  // ----- searching ----------------------------------------------------------------------------

  private query(text: string, opts: FindOptions): SearchQuery {
    return new SearchQuery({
      search: findPattern(text),
      regexp: true,
      caseSensitive: !!opts.matchCase,
      wholeWord: !!opts.wholeWord,
      filter: goodMatch(!!opts.wholeWord),
    });
  }

  /** The next (or previous) match from the selection, wrapping around; the only match, already
   * selected, is found again (as on the desktop). */
  private next(query: SearchQuery, backward: boolean): Match | null {
    const state = this.view.view.state;
    const { from, to } = state.selection;
    const end = state.doc.content.size;
    const found = backward
      ? (query.findPrev(state, from) ?? query.findPrev(state, end, to))
      : (query.findNext(state, to) ?? query.findNext(state, 0, from));
    if (found) return found;
    const same = from < to ? query.findNext(state, from, to) : null;
    return same && same.from === from && same.to === to ? same : null;
  }

  private selectMatch(match: Match): void {
    const tr = this.view.view.state.tr;
    this.view.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, match.from, match.to)).scrollIntoView());
  }

  /** Select the next (or previous) match, wrapping around. */
  find(text: string, opts: FindOptions = {}): boolean {
    const match = text ? this.next(this.query(text, opts), !!opts.backward) : null;
    if (match) this.selectMatch(match);
    this.status.textContent = match ? "" : "No matches.";
    return match !== null;
  }

  /** Replace the selected match and select the next one (wrapping around); without one selected,
   * select the next match (false: nothing was replaced). */
  replace(text: string, replacement: string, opts: FindOptions = {}): boolean {
    if (!text) return false;
    const state = this.view.view.state;
    const { from, to } = state.selection;
    const match = from < to ? this.query(text, opts).findNext(state, from) : null;
    if (!match || match.from !== from || match.to !== to) {
      this.find(text, opts);
      return false;
    }
    const tr = state.tr;
    const marks = state.doc.resolve(from).marksAcross(state.doc.resolve(to)) ?? Mark.none;
    if (replacement) tr.replaceWith(from, to, docSchema.text(replacement, marks));
    else tr.delete(from, to);
    tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map(to)));
    this.commit(tr);
    this.find(text, opts);
    this.status.textContent = "";
    return true;
  }

  /** Replace every match, as one undo step; returns how many. The document is read block by
   * block, and many changed blocks are rebuilt in one step (a step per match copied the document
   * each time). */
  replaceAll(text: string, replacement: string, opts: FindOptions = {}): number {
    const state = this.view.view.state;
    const byBlock = new Map<number, [number, number][]>();
    let count = 0;
    if (text) {
      // the matches prosemirror-search's replaceAll would take: its pattern and flags, each match
      // after the previous one, a refused match retried one character on
      const re = new RegExp(findPattern(text), opts.matchCase ? "gu" : "giu");
      const whole = !!opts.wholeWord;
      state.doc.descendants((node, pos) => {
        if (!node.inlineContent) return true;
        const content = blockText(node);
        const ranges: [number, number][] = [];
        re.lastIndex = 0;
        for (let m = re.exec(content); m; m = re.exec(content)) {
          const end = m.index + m[0].length;
          if (end === m.index || !goodIn(node, m.index, end, whole)) {
            re.lastIndex = m.index + 1;
            continue;
          }
          ranges.push([m.index, end]);
        }
        if (ranges.length) {
          byBlock.set(pos, ranges);
          count += ranges.length;
        }
        return false;
      });
    }
    if (count) {
      const tr = state.tr;
      const edits = new Map(
        [...byBlock].map(([pos, ranges]) => [pos, (b: PMNode) => replaceIn(b, ranges, replacement)] as const),
      );
      if (byBlock.size >= BULK_MIN) {
        rebuildBlocks(tr, edits, false);
        tr.setSelection(Selection.near(tr.doc.resolve(Math.min(state.selection.from, tr.doc.content.size))));
      } else {
        for (const [pos, edit] of [...edits].reverse()) {
          const block = tr.doc.nodeAt(pos) as PMNode;
          tr.replaceWith(pos + 1, pos + block.nodeSize - 1, edit(block).content);
        }
      }
      this.commit(tr);
    }
    this.status.textContent = count ? `Replaced ${count}.` : "No matches.";
    return count;
  }

  /** Dispatch an edit as an undo step of its own. */
  private commit(tr: Transaction): void {
    closeHistory(tr);
    tr.setMeta(COMMAND_META, true);
    this.view.view.dispatch(tr.scrollIntoView());
  }
}
