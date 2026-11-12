/**
 * The website document editor's find & replace bar (the desktop's `gui/doceditor/find_bar.py`),
 * reading text the way prosemirror-search does (a leaf is one character). As on the desktop:
 * - a search matches across an empty link (an invisible `empty_inline`), never across a picture
 *   or a line break, and a space also finds a no-break space;
 * - "Whole word" wants no letter or digit right before and after the match;
 * - Find wraps around; Replace replaces the selected match and selects the next; Replace all is
 *   one undo step. Esc closes, Enter finds the next match, Shift+Enter the previous one.
 * - Matches aren't all marked (nor on the desktop): marking them redraws on every keystroke,
 *   which took seconds on a long document.
 * The search is its own, one block at a time (`scanMatches`): prosemirror-search's `findPrev`
 * and its retry after a refused match step by one code unit, which never ends on an emoji.
 */

import { Fragment, Mark, type Node as PMNode } from "prosemirror-model";
import { Selection, TextSelection, type Transaction } from "prosemirror-state";
import { closeHistory } from "prosemirror-history";
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

/** The matches of `pattern` (regexp `flags`, with "g") in `text`, as [start, end) offsets, each
 * starting after the previous one; `accept` may refuse a match, which is then retried one
 * character on. Every step moves on by a whole character (a surrogate pair is one): with the "u"
 * flag a `lastIndex` inside a pair is moved back to its start, so stepping by one code unit
 * would find the same match forever. Empty matches are never taken. */
export function scanMatches(
  text: string,
  pattern: string,
  flags: string,
  accept: (start: number, end: number) => boolean,
): [number, number][] {
  const out: [number, number][] = [];
  let re: RegExp;
  try {
    re = new RegExp(pattern, flags);
  } catch {
    return out;
  }
  let from = 0;
  while (from <= text.length) {
    re.lastIndex = from;
    const m = re.exec(text);
    if (!m) break;
    const start = m.index;
    const end = start + m[0].length;
    if (end > start && start >= from && accept(start, end)) {
      out.push([start, end]);
      from = end;
    } else {
      const cp = text.codePointAt(Math.max(start, from)) ?? 0;
      from = Math.max(start, from) + (cp > 0xffff ? 2 : 1);
    }
  }
  return out;
}

/** The text of a textblock, and where a textblock is (`pos` = before it). */
interface Block {
  node: PMNode;
  pos: number;
}

/** Visit the textblocks of `doc` that end after `from` (forward) or start before it
 * (backward, last first), until `visit` returns true. */
function walkBlocks(doc: PMNode, from: number, backward: boolean, visit: (b: Block) => boolean): boolean {
  const walk = (node: PMNode, start: number): boolean => {
    const kids: [PMNode, number][] = [];
    node.forEach((child, offset) => {
      const pos = start + offset;
      if (child.isLeaf) return;
      if (backward ? pos < from : pos + child.nodeSize > from) kids.push([child, pos]);
    });
    if (backward) kids.reverse();
    for (const [child, pos] of kids) {
      if (child.inlineContent ? visit({ node: child, pos }) : walk(child, pos + 1)) return true;
    }
    return false;
  };
  return walk(doc, 0);
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
    close.setAttribute("aria-label", "Close");
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
        if (e.isComposing) return; // the key belongs to the input method
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

  /** The good matches in `block` (content offsets), for `text` with `opts`. */
  private blockMatches(block: PMNode, text: string, opts: FindOptions): [number, number][] {
    const whole = !!opts.wholeWord;
    return scanMatches(blockText(block), findPattern(text), opts.matchCase ? "gu" : "giu", (a, b) =>
      goodIn(block, a, b, whole),
    );
  }

  /** The first match (document positions) starting at or after `from`, or the last one starting
   * before it (`backward`); null when there is none. */
  private matchFrom(text: string, opts: FindOptions, from: number, backward: boolean): Match | null {
    let found: Match | null = null;
    walkBlocks(this.view.view.state.doc, from, backward, ({ node, pos }) => {
      const start = pos + 1;
      const ranges = this.blockMatches(node, text, opts).map(([a, b]) => ({ from: start + a, to: start + b }));
      found = (backward ? ranges.filter((r) => r.from < from).pop() : ranges.find((r) => r.from >= from)) ?? null;
      return found !== null;
    });
    return found;
  }

  /** The next (or previous) match from the selection, wrapping around with no limit (as on the
   * desktop: a match the cursor is in, or the only match already selected, is found again). */
  private next(text: string, opts: FindOptions): Match | null {
    const { from, to } = this.view.view.state.selection;
    const end = this.view.view.state.doc.content.size;
    return opts.backward
      ? (this.matchFrom(text, opts, from, true) ?? this.matchFrom(text, opts, end + 1, true))
      : (this.matchFrom(text, opts, to, false) ?? this.matchFrom(text, opts, 0, false));
  }

  private selectMatch(match: Match): void {
    const tr = this.view.view.state.tr;
    this.view.view.dispatch(tr.setSelection(TextSelection.create(tr.doc, match.from, match.to)).scrollIntoView());
  }

  /** Select the next (or previous) match, wrapping around. */
  find(text: string, opts: FindOptions = {}): boolean {
    const match = text ? this.next(text, opts) : null;
    if (match) this.selectMatch(match);
    this.status.textContent = match ? "" : "No matches.";
    return match !== null;
  }

  /** Replace the selected match and select the next one (wrapping around); without one selected,
   * select the next match (false: nothing was replaced). */
  replace(text: string, replacement: string, opts: FindOptions = {}): boolean {
    if (!text) return false;
    const state = this.view.view.state;
    const { from, to, $from } = state.selection;
    const start = $from.start();
    const isMatch =
      from < to &&
      $from.parent.inlineContent &&
      state.selection.$to.parent === $from.parent &&
      this.blockMatches($from.parent, text, opts).some(([a, b]) => start + a === from && start + b === to);
    if (!isMatch) {
      this.find(text, opts);
      return false;
    }
    const tr = state.tr;
    const marks = $from.marksAcross(state.selection.$to) ?? Mark.none;
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
      walkBlocks(state.doc, 0, false, ({ node, pos }) => {
        const ranges = this.blockMatches(node, text, opts);
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
