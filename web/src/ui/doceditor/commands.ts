/**
 * The website document editor's formatting commands: ProseMirror `Command`s mirroring the
 * desktop's `gui/doceditor/formatting.py`. Every command is one transaction and one undo step
 * (`closeHistory`), and only writes documents the subset can hold (see `schema.ts`).
 *
 * Desktop behaviour kept here:
 * - A heading has no bold or font size of its own and a code block no font or size (the desktop
 *   shows them in its own heading / code look and never writes those), so turning a block into
 *   one removes them; going back to normal from a heading or code removes size and bold too.
 * - A line made code joins the code block right above it, else it is a block of its own; a code
 *   line given another style leaves its code block (which is split around it).
 * - List lines keep their list (block styles don't apply to them); lists nest at most one level
 *   below the item above and at most `MAX_LEVEL` deep; the top level never leaves its list by
 *   an outdent. Block indent outside lists is 0…`MAX_LEVEL` (36 pt each).
 * - Typed links and pictures don't take the formatting around them.
 */

import { Fragment, Mark, type MarkType, type Node as PMNode, type NodeType, type ResolvedPos } from "prosemirror-model";
import { EditorState, Selection, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { chainCommands, createParagraphNear, liftEmptyBlock, newlineInCode, splitBlock } from "prosemirror-commands";
import { Mapping, StepMap, findWrapping, liftTarget } from "prosemirror-transform";
import { closeHistory } from "prosemirror-history";
import { liftListItem, sinkListItem, splitListItem } from "prosemirror-schema-list";
import {
  CellSelection,
  TableMap,
  addColumnAfter,
  addColumnBefore,
  addRowAfter,
  addRowBefore,
  deleteColumn,
  deleteRow,
  fixTables,
  isInTable,
  mergeCells,
  selectedRect,
  splitCell,
} from "prosemirror-tables";
import { CANON_IMG, safeHref } from "../../doceditor/subset";
import { colour, fontFamily } from "../../doceditor/subset-values";
import { docSchema } from "./schema";

const nodes = docSchema.nodes;
const marks = docSchema.marks;

/** Deepest list level and block indent (as the desktop's MAX_INDENT). */
export const MAX_LEVEL = 8;

export type BlockStyle = "normal" | "h1" | "h2" | "h3" | "quote" | "code";
export type Align = "left" | "center" | "right" | "justify";
export type ToggleMark = "bold" | "italic" | "underline" | "strike";
export type TableOp =
  | "row_above"
  | "row_below"
  | "col_left"
  | "col_right"
  | "del_row"
  | "del_col"
  | "del_table"
  | "merge"
  | "split";

const HEADINGS: Record<string, number> = { h1: 1, h2: 2, h3: 3 };

/** Marks a command's transaction (the view keeps the typing after it a separate undo step). */
export const COMMAND_META = "umc-command";

/** Finish a command's transaction: its own undo step. */
function finish(tr: Transaction, dispatch: (tr: Transaction) => void): void {
  closeHistory(tr);
  tr.setMeta(COMMAND_META, true);
  dispatch(tr.scrollIntoView());
}

// ----- where the selection is ------------------------------------------------------------

interface Located {
  node: PMNode;
  pos: number;
  /** The type of the node holding it (list_item for a list line), where that node is (-1: the
   * document) and the block's index in it. */
  parent: NodeType;
  parentPos: number;
  index: number;
  /** In a quote (a blockquote around it before any list item or table cell). */
  quoted: boolean;
  /** List nesting (1 = a top-level list line, 0 = not in a list). */
  level: number;
  /** The kind of the list it is in, if any. */
  kind: "bullet" | "number" | null;
  /** For a list line: where its list is and the item's index in it. */
  item: { listPos: number; index: number } | null;
}

/** The textblocks touching [from, to] with their context (ProseMirror's nodesBetween rule), found
 * in one walk: resolving every block's position would scan its parent's children each time. */
function textblocksIn(doc: PMNode, from: number, to: number, out: Located[], seen: Set<number>): void {
  const visit = (
    node: PMNode,
    start: number,
    quoted: boolean,
    level: number,
    kind: Located["kind"],
    item: Located["item"],
  ) => {
    node.forEach((child, offset, index) => {
      const pos = start + offset;
      if (!(pos + child.nodeSize > from && pos < to) || child.isLeaf) return;
      if (child.isTextblock) {
        // a selection that ends right at the start of a block leaves it out, as on the desktop
        const skip = to > from && to <= pos + 1 && pos + 1 > from;
        if (!skip && !seen.has(pos)) {
          seen.add(pos);
          const line = node.type === nodes.list_item ? item : null;
          out.push({ node: child, pos, parent: node.type, parentPos: start - 1, index, quoted, level, kind, item: line });
        }
        return;
      }
      const t = child.type;
      const inList = t === nodes.list_item || t === nodes.bullet_list || t === nodes.ordered_list;
      visit(
        child,
        pos + 1,
        t === nodes.blockquote ? true : t === nodes.list_item || t === nodes.table_cell ? false : quoted,
        t === nodes.list_item ? level + 1 : inList ? level : 0,
        t === nodes.bullet_list ? "bullet" : t === nodes.ordered_list ? "number" : inList ? kind : null,
        t === nodes.list_item ? { listPos: start - 1, index } : null,
      );
    });
  };
  visit(doc, 0, false, 0, null, null);
}

/** The textblocks the selection touches, in document order (a selection that ends right at the
 * start of a block leaves that block out, as on the desktop). */
export function selectedTextblocks(state: EditorState): Located[] {
  const out: Located[] = [];
  const seen = new Set<number>();
  for (const r of state.selection.ranges) textblocksIn(state.doc, r.$from.pos, r.$to.pos, out, seen);
  if (!out.length) {
    const $f = state.selection.$from;
    if ($f.parent.isTextblock) {
      const pos = $f.before();
      textblocksIn(state.doc, pos + 1, pos + 1, out, seen);
    }
  }
  return out.sort((x, y) => x.pos - y.pos);
}

/** Where a text selection is, as (textblock number, offset): kept across a change that wraps or
 * unwraps blocks without changing them (lists in one step). Null for other selections. */
function textBookmark(doc: PMNode, sel: Selection): [number, number][] | null {
  if (!(sel instanceof TextSelection)) return null;
  const starts: number[] = [];
  doc.descendants((node, pos) => {
    if (node.isTextblock) starts.push(pos + 1);
    return !node.isTextblock;
  });
  const at = (p: number): [number, number] => {
    let lo = 0;
    let hi = starts.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (starts[mid] <= p) lo = mid;
      else hi = mid - 1;
    }
    return [lo, p - starts[lo]];
  };
  return [at(sel.anchor), at(sel.head)];
}

function restoreBookmark(tr: Transaction, mark: [number, number][] | null, before: Selection): void {
  if (!mark) {
    if (!(before instanceof TextSelection)) tr.setSelection(Selection.fromJSON(tr.doc, before.toJSON()));
    return;
  }
  const starts: number[] = [];
  tr.doc.descendants((node, pos) => {
    if (node.isTextblock) starts.push(pos + 1);
    return !node.isTextblock;
  });
  const pos = ([k, off]: [number, number]) => Math.min((starts[k] ?? 0) + off, tr.doc.content.size);
  try {
    tr.setSelection(TextSelection.create(tr.doc, pos(mark[0]), pos(mark[1])));
  } catch {
    /* keep the mapped selection */
  }
}

/** Inside a quote: a blockquote around the block before any list item or table cell. */
function inQuote($pos: ResolvedPos): boolean {
  for (let d = $pos.depth; d > 0; d--) {
    const t = $pos.node(d).type;
    if (t === nodes.blockquote) return true;
    if (t === nodes.list_item || t === nodes.table_cell) return false;
  }
  return false;
}

/** A paragraph's or heading's alignment, indent and page break (none for code). */
const blockAttrs = (node: PMNode) =>
  node.type === nodes.code_block
    ? {}
    : { align: node.attrs.align, indent: node.attrs.indent, pageBreak: node.attrs.pageBreak };

/** Positions tracked through the steps added to `tr` since they were recorded. */
class Tracked {
  private steps: number;
  constructor(
    private tr: Transaction,
    public pos: number,
  ) {
    this.steps = tr.steps.length;
  }
  get now(): number {
    const p = this.tr.mapping.slice(this.steps).map(this.pos);
    this.pos = p;
    this.steps = this.tr.steps.length;
    return p;
  }
}

/** Run `commands` one after another as one transaction (null when none of them applied). */
function chain(state: EditorState, commands: Command[]): Transaction | null {
  let cur = state;
  const master = state.tr;
  let applied = false;
  for (const cmd of commands) {
    cmd(cur, (tr) => {
      for (const step of tr.steps) master.step(step);
      // a plain state: no plugin may add steps the master doesn't have
      cur = EditorState.create({ doc: tr.doc, selection: tr.selection, storedMarks: tr.storedMarks });
      applied = true;
    });
  }
  if (!applied) return null;
  master.setSelection(Selection.fromJSON(master.doc, cur.selection.toJSON()));
  if (cur.storedMarks) master.setStoredMarks(cur.storedMarks);
  return master;
}

/** Remove `types` (all marks when null) from the inline content of [from, to), pictures aside. */
function removeMarks(tr: Transaction, from: number, to: number, types: MarkType[] | null): void {
  const runs: [number, number][] = [];
  tr.doc.nodesBetween(from, to, (node, pos) => {
    if (!node.isInline) return true;
    if (node.type === nodes.image) return false;
    const a = Math.max(from, pos);
    const b = Math.min(to, pos + node.nodeSize);
    const last = runs[runs.length - 1];
    if (last && last[1] === a) last[1] = b;
    else runs.push([a, b]);
    return false;
  });
  for (const [a, b] of runs) {
    if (types === null) tr.removeMark(a, b);
    else for (const t of types) tr.removeMark(a, b, t);
  }
}

// ----- big selections ------------------------------------------------------------------------

/** From this many textblocks on, a change is made by rebuilding the blocks and replacing them in
 * one step: a step per block costs a copy of the whole document each (Ctrl+A on 16 000
 * paragraphs took seconds). Below it, ordinary steps keep the mapping exact. */
export const BULK_MIN = 64;

type MarkChange = (set: readonly Mark[]) => readonly Mark[];

/** `block` with `change` applied to the marks of its inline content in [a, b) (content offsets);
 * pictures only when `images`. The same result as removeMark / addMark steps. */
function markInline(block: PMNode, a: number, b: number, change: MarkChange, images: boolean): PMNode {
  const kids: PMNode[] = [];
  let changed = false;
  block.forEach((child, offset) => {
    const end = offset + child.nodeSize;
    if (end <= a || offset >= b || (!images && child.type === nodes.image)) {
      kids.push(child);
      return;
    }
    const next = change(child.marks);
    if (Mark.sameSet(next, child.marks)) {
      kids.push(child);
      return;
    }
    changed = true;
    if (child.isText) {
      const s = Math.max(a, offset) - offset;
      const e = Math.min(b, end) - offset;
      if (s > 0) kids.push(child.cut(0, s));
      kids.push(child.cut(s, e).mark(next));
      if (e < child.nodeSize) kids.push(child.cut(e));
    } else kids.push(child.mark(next));
  });
  return changed ? block.copy(Fragment.fromArray(kids)) : block;
}

/** Replace the textblocks at the positions of `edits` in one step. When every edit keeps the
 * block's size (`sameSize`), the selection stays where it was; otherwise the caller places it. */
export function rebuildBlocks(tr: Transaction, edits: Map<number, (block: PMNode) => PMNode>, sameSize = true): void {
  const positions = [...edits.keys()].sort((x, y) => x - y);
  if (!positions.length) return;
  const hit = (from: number, to: number) => {
    let lo = 0;
    let hi = positions.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (positions[mid] < from) lo = mid + 1;
      else hi = mid;
    }
    return lo < positions.length && positions[lo] < to;
  };
  const rebuild = (node: PMNode, pos: number): PMNode => {
    const edit = edits.get(pos);
    if (node.isTextblock) return edit ? edit(node) : node;
    const kids: PMNode[] = [];
    let changed = false;
    node.forEach((child, offset) => {
      const at = pos + 1 + offset;
      const next = child.isLeaf || !hit(at, at + child.nodeSize) ? child : rebuild(child, at);
      if (next !== child) changed = true;
      kids.push(next);
    });
    return changed ? node.copy(Fragment.fromArray(kids)) : node;
  };
  const doc = tr.doc;
  const first = doc.resolve(positions[0]).index(0);
  const last = doc.resolve(positions[positions.length - 1]).index(0);
  const start = doc.resolve(0).posAtIndex(first, 0);
  const kids: PMNode[] = [];
  let at = start;
  for (let i = first; i <= last; i++) {
    const child = doc.child(i);
    kids.push(hit(at, at + child.nodeSize) ? rebuild(child, at) : child);
    at += child.nodeSize;
  }
  const selection = tr.selection.toJSON();
  const stored = tr.storedMarks;
  tr.replaceWith(start, at, kids);
  if (sameSize) tr.setSelection(Selection.fromJSON(tr.doc, selection));
  if (stored) tr.setStoredMarks(stored);
}

/** Rebuild the containers at the positions of `edits` (-1: the document) in one step. An edit
 * gets the container with its children already rebuilt and may regroup its children; positions
 * move, so the caller keeps the selection with a text bookmark. */
function rebuildTree(tr: Transaction, edits: Map<number, (node: PMNode) => PMNode>): void {
  const positions = [...edits.keys()].filter((p) => p >= 0).sort((x, y) => x - y);
  const hit = (from: number, to: number) => {
    let lo = 0;
    let hi = positions.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (positions[mid] < from) lo = mid + 1;
      else hi = mid;
    }
    return lo < positions.length && positions[lo] < to;
  };
  const rebuild = (node: PMNode, pos: number): PMNode => {
    let out = node;
    if (!node.isTextblock && hit(pos + 1, pos + node.nodeSize)) {
      const kids: PMNode[] = [];
      let changed = false;
      node.forEach((child, offset) => {
        const at = pos + 1 + offset;
        const next = child.isLeaf || !hit(at, at + child.nodeSize) ? child : rebuild(child, at);
        if (next !== child) changed = true;
        kids.push(next);
      });
      if (changed) out = node.copy(Fragment.fromArray(kids));
    }
    const edit = edits.get(pos);
    return edit ? edit(out) : out;
  };
  const doc = rebuild(tr.doc, -1);
  if (doc !== tr.doc) tr.replaceWith(0, tr.doc.content.size, doc.content);
}

/** `node` with the children at `runs` (lists of child indices, each a run of neighbours)
 * replaced by `make(children)`. */
function replaceRuns(node: PMNode, runs: number[][], make: (kids: PMNode[]) => PMNode[]): PMNode {
  const start = new Map(runs.map((r) => [r[0], r]));
  const kids: PMNode[] = [];
  for (let i = 0; i < node.childCount; i++) {
    const run = start.get(i);
    if (run) {
      kids.push(...make(run.map((k) => node.child(k))));
      i = run[run.length - 1];
    } else kids.push(node.child(i));
  }
  return node.copy(Fragment.fromArray(kids));
}

interface Covered {
  pos: number;
  a: number;
  b: number;
}

/** The textblocks of `ranges`, with the part of their content each range covers. */
function blocksInRanges(doc: PMNode, ranges: [number, number][]): Covered[] {
  const out: Covered[] = [];
  for (const [from, to] of ranges) {
    doc.nodesBetween(from, to, (node, pos) => {
      if (!node.isTextblock) return true;
      const a = Math.max(from, pos + 1) - (pos + 1);
      const b = Math.min(to, pos + node.nodeSize - 1) - (pos + 1);
      if (b > a) out.push({ pos, a, b });
      return false;
    });
  }
  return out;
}

/** Change the marks over `ranges` (remove `types`, all when null; then add `mark`): one step
 * for a big selection, else ordinary mark steps. */
function changeMarks(
  tr: Transaction,
  ranges: [number, number][],
  types: MarkType[] | null,
  mark: Mark | null,
  images: boolean,
): void {
  const blocks = blocksInRanges(tr.doc, ranges);
  if (blocks.length >= BULK_MIN) {
    const change: MarkChange = (set) => {
      let next = types === null ? Mark.none : set;
      if (types !== null) for (const t of types) next = t.removeFromSet(next);
      return mark ? mark.addToSet(next) : next;
    };
    const edits = new Map<number, (block: PMNode) => PMNode>();
    for (const { pos, a, b } of blocks) {
      const prev = edits.get(pos);
      edits.set(pos, (block) => markInline(prev ? prev(block) : block, a, b, change, images));
    }
    rebuildBlocks(tr, edits);
    return;
  }
  for (const [from, to] of ranges) {
    if (from === to) continue;
    if (images) {
      if (types === null) tr.removeMark(from, to);
      else for (const t of types) tr.removeMark(from, to, t);
    } else removeMarks(tr, from, to, types);
    if (mark) tr.addMark(from, to, mark);
  }
}

/** Set attributes / types of textblocks (`make` keeps their content): one step for many, else
 * setNodeMarkup each. */
function retype(tr: Transaction, blocks: Located[], make: (block: PMNode) => PMNode): void {
  if (blocks.length >= BULK_MIN) {
    rebuildBlocks(tr, new Map(blocks.map((b) => [b.pos, make] as const)));
    return;
  }
  for (const b of blocks) {
    const node = tr.doc.nodeAt(b.pos) as PMNode;
    const next = make(node);
    if (next !== node) tr.setNodeMarkup(b.pos, next.type, next.attrs, next.marks);
  }
}

const selectionRanges = (state: EditorState): [number, number][] =>
  state.selection.ranges.map((r) => [r.$from.pos, r.$to.pos]);

// ----- paragraph style ---------------------------------------------------------------------

/** "normal", "h1"…"h3", "quote" or "code" at the cursor (a quote first, as on the desktop). */
function blockStyleAt($pos: ResolvedPos): BlockStyle {
  const block = $pos.parent;
  if (!block.isTextblock) return "normal";
  if (inQuote($pos)) return "quote";
  if (block.type === nodes.heading) return `h${Math.min(Math.max(block.attrs.level, 1), 3)}` as BlockStyle;
  if (block.type === nodes.code_block) return "code";
  return "normal";
}

/** The newline characters of a code block (absolute positions). */
function newlines(node: PMNode, pos: number): number[] {
  const out: number[] = [];
  node.forEach((child, offset) => {
    if (!child.isText) return;
    const text = child.text as string;
    for (let i = text.indexOf("\n"); i >= 0; i = text.indexOf("\n", i + 1)) out.push(pos + 1 + offset + i);
  });
  return out;
}

/** Split the code block at `pos` so the lines the selection [from, to] touches are code blocks of
 * their own; returns their positions. A final newline (the code block's last line break) stays
 * with the last block unless that line is one of them. */
function splitCodeLines(tr: Transaction, pos: number, from: number, to: number): number[] {
  const node = tr.doc.nodeAt(pos) as PMNode;
  const end = pos + node.nodeSize - 1; // content end
  const nl = newlines(node, pos);
  const trailing = nl.length > 0 && nl[nl.length - 1] === end - 1;
  const boundaries = trailing ? nl.slice(0, -1) : nl; // between line k and k + 1
  const lineStart = (k: number) => (k === 0 ? pos + 1 : boundaries[k - 1] + 1);
  const lineEnd = (k: number) => (k < boundaries.length ? boundaries[k] : trailing ? end - 1 : end);
  const count = boundaries.length + 1;
  const picked: number[] = [];
  for (let k = 0; k < count; k++) {
    if (lineEnd(k) >= from && (lineStart(k) < to || (from === to && lineStart(k) === to))) picked.push(k);
  }
  if (!picked.length) picked.push(0);
  const s = picked[0];
  const e = picked[picked.length - 1];
  if (e === count - 1 && trailing) tr.delete(end - 1, end); // the converted lines don't carry it
  // from the last boundary back, so the earlier positions stay valid
  for (let k = Math.min(e, boundaries.length - 1); k >= Math.max(s - 1, 0); k--) {
    const at = boundaries[k];
    tr.delete(at, at + 1);
    tr.split(at);
  }
  const out: number[] = [];
  let p = pos;
  if (s > 0) p += (tr.doc.nodeAt(p) as PMNode).nodeSize;
  for (let k = s; k <= e; k++) {
    out.push(p);
    p += (tr.doc.nodeAt(p) as PMNode).nodeSize;
  }
  return out;
}

/** Lift the block at `pos` out of the quotes it sits in directly. */
function leaveQuotes(tr: Transaction, pos: number): number {
  const t = new Tracked(tr, pos);
  for (let i = 0; i < 1000; i++) {
    const p = t.now;
    const $p = tr.doc.resolve(p);
    if ($p.parent.type !== nodes.blockquote) break;
    const node = tr.doc.nodeAt(p) as PMNode;
    const range = tr.doc.resolve(p + 1).blockRange(tr.doc.resolve(p + node.nodeSize - 1));
    const target = range && liftTarget(range);
    if (!range || target == null) break;
    tr.lift(range, target);
  }
  return t.now;
}

/** Siblings that follow each other directly, as runs (positions are of the current `tr`). */
function siblingRuns(tr: Transaction, positions: number[]): number[][] {
  const runs: number[][] = [];
  for (const p of [...positions].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    const prev = last?.[last.length - 1];
    if (prev !== undefined && prev + (tr.doc.nodeAt(prev) as PMNode).nodeSize === p) last.push(p);
    else runs.push([p]);
  }
  return runs;
}

/** Make a run of sibling textblocks one code block, joined to a code block right above it. */
function joinCode(tr: Transaction, run: number[]): void {
  for (const p of run) {
    const node = tr.doc.nodeAt(p) as PMNode;
    if (node.type !== nodes.code_block) tr.setNodeMarkup(p, nodes.code_block);
  }
  const $first = tr.doc.resolve(run[0]);
  const above = $first.nodeBefore;
  const blocks = above?.type === nodes.code_block ? [run[0] - above.nodeSize, ...run] : run;
  const ends = (p: number) => (tr.doc.nodeAt(p) as PMNode).textContent.endsWith("\n");
  const trailing = ends(blocks[0]) && blocks.length > 1 ? blocks[0] !== run[0] : false;
  const lastEnds = ends(blocks[blocks.length - 1]);
  // join from the last boundary back: the earlier positions stay valid
  for (let i = blocks.length - 1; i >= 1; i--) {
    const boundary = blocks[i];
    const before = tr.doc.nodeAt(blocks[i - 1]) as PMNode;
    const sep = !before.textContent.endsWith("\n");
    tr.join(boundary);
    if (sep) tr.insertText("\n", boundary - 1);
  }
  if (trailing && !lastEnds) {
    const node = tr.doc.nodeAt(blocks[0]) as PMNode;
    tr.insertText("\n", blocks[0] + node.nodeSize - 1);
  }
}

export function setBlockStyle(style: BlockStyle): Command {
  return (state, dispatch) => {
    const blocks = selectedTextblocks(state).filter((b) => b.parent !== nodes.list_item);
    if (!blocks.length) return false;
    if (!dispatch) return true;
    const tr = state.tr;
    const level = HEADINGS[style] ?? 0;
    // Many plain paragraphs / headings outside quotes made normal or a heading: those in one
    // step at the end (`simple`); quotes and code (`slow`) step by step first.
    const plain = (b: Located) =>
      (b.node.type === nodes.paragraph || b.node.type === nodes.heading) && !b.quoted;
    let simple: Located[] = [];
    let slow = blocks;
    if (blocks.length >= BULK_MIN && (style === "normal" || level)) {
      simple = blocks.filter(plain);
      if (simple.length >= BULK_MIN) slow = blocks.filter((b) => !plain(b));
      else simple = [];
    }
    const sel = { from: new Tracked(tr, state.selection.from), to: new Tracked(tr, state.selection.to) };
    const made: { at: Tracked; quoted: boolean }[] = [];
    for (const b of slow) {
      const pos = tr.mapping.map(b.pos);
      const node = tr.doc.nodeAt(pos) as PMNode;
      const wasCode = node.type === nodes.code_block;
      const wasQuote = inQuote(tr.doc.resolve(pos));
      const drop = new Set<MarkType>();
      if ((node.type === nodes.heading && !wasQuote) || wasCode) [marks.size, marks.bold].forEach((m) => drop.add(m));
      if (wasCode) drop.add(marks.font);
      if (level) [marks.bold, marks.size].forEach((m) => drop.add(m));
      if (style === "code") [marks.font, marks.size].forEach((m) => drop.add(m));
      let pieces: number[] = [pos];
      if (wasCode && style !== "code") {
        const from = Math.max(sel.from.now, pos + 1);
        const to = Math.min(sel.to.now, pos + node.nodeSize - 1);
        pieces = splitCodeLines(tr, pos, Math.min(from, to), Math.max(from, to));
      }
      const tracked = pieces.map((p) => new Tracked(tr, p));
      for (const t of tracked) {
        let p = t.now;
        const piece = tr.doc.nodeAt(p) as PMNode;
        if (drop.size) removeMarks(tr, p + 1, p + piece.nodeSize - 1, [...drop]);
        if (style !== "quote") p = leaveQuotes(tr, p);
        const cur = tr.doc.nodeAt(p) as PMNode;
        const attrs = blockAttrs(cur);
        if (level) tr.setNodeMarkup(p, nodes.heading, { level, ...attrs });
        else if (style !== "code" && cur.type !== nodes.paragraph) tr.setNodeMarkup(p, nodes.paragraph, attrs);
        made.push({ at: new Tracked(tr, p), quoted: inQuote(tr.doc.resolve(p)) });
      }
    }
    if (style === "code" || style === "quote") {
      const targets = made.filter((m) => style === "code" || !m.quoted).map((m) => m.at.now);
      const runs = siblingRuns(tr, targets);
      for (let i = runs.length - 1; i >= 0; i--) {
        const run = runs[i];
        if (style === "code") joinCode(tr, run);
        else {
          const last = tr.doc.nodeAt(run[run.length - 1]) as PMNode;
          const range = tr.doc
            .resolve(run[0] + 1)
            .blockRange(tr.doc.resolve(run[run.length - 1] + last.nodeSize - 1));
          const wrapping = range && findWrapping(range, nodes.blockquote);
          if (range && wrapping) tr.wrap(range, wrapping);
        }
      }
    }
    if (state.selection instanceof TextSelection) {
      // a split code block maps the cursor away from its line: put it back
      tr.setSelection(TextSelection.between(tr.doc.resolve(sel.from.now), tr.doc.resolve(sel.to.now)));
    }
    if (simple.length) {
      const moved = simple.map((b) => ({ node: b.node, pos: tr.mapping.map(b.pos) }));
      rebuildBlocks(
        tr,
        new Map(
          moved.map((b) => [
            b.pos,
            (block: PMNode) => {
              const drop = level || block.type === nodes.heading ? [marks.size, marks.bold] : [];
              const change: MarkChange = (set) => drop.reduce((acc, t) => t.removeFromSet(acc), set);
              const content = drop.length ? markInline(block, 0, block.content.size, change, false).content : block.content;
              return level
                ? nodes.heading.create({ level, ...blockAttrs(block) }, content, block.marks)
                : nodes.paragraph.create(blockAttrs(block), content, block.marks);
            },
          ]),
        ),
      );
    }
    finish(tr, dispatch);
    return true;
  };
}

// ----- character formatting --------------------------------------------------------------

/** The marks at the cursor; for a selection, those of the character before its moving end (the
 * one after it at a line's start), as the desktop's QTextCursor.charFormat. */
function marksAt(state: EditorState): readonly Mark[] {
  const sel = state.selection;
  if (sel.empty) return state.storedMarks ?? sel.$from.marks();
  const $h = sel.$head;
  if ($h.parent.inlineContent) {
    const leaf = $h.nodeBefore ?? $h.nodeAfter;
    if (leaf) return leaf.marks;
  }
  let found: readonly Mark[] | null = null;
  state.doc.nodesBetween(sel.from, sel.to, (node) => {
    if (found) return false;
    if (node.isInline) found = node.marks;
    return !node.isInline;
  });
  return found ?? Mark.none;
}

/** Put `mark` on the selection (or the marks typed next), replacing one of its kind. */
function applyMark(type: MarkType, mark: Mark | null): Command {
  return (state, dispatch) => {
    if (!dispatch) return true;
    const tr = state.tr;
    if (state.selection.empty) {
      if (mark) tr.addStoredMark(mark);
      else tr.removeStoredMark(type);
    } else changeMarks(tr, selectionRanges(state), [type], mark, true);
    finish(tr, dispatch);
    return true;
  };
}

/** Bold, italic, underline or strike on or off, as the cursor's formatting says. */
export function toggleMark(name: ToggleMark): Command {
  const type = marks[name];
  return (state, dispatch) => {
    const on = !type.isInSet(marksAt(state));
    return applyMark(type, on ? type.create() : null)(state, dispatch);
  };
}

/** Refuses (false) a value the export would drop, so the editor never shows what won't be saved. */
const refused: Command = () => false;

/** A font family as the subset writes it (its first name, no quotes or CSS characters). */
export function setFont(family: string): Command {
  const name = fontFamily(family);
  return name ? applyMark(marks.font, marks.font.create({ family: name })) : refused;
}

/** A size in points, 0 < pt <= 1000, rounded to the subset's two decimals. */
export function setSize(pt: number): Command {
  const v = Math.round(pt * 100) / 100;
  return Number.isFinite(v) && v > 0 && v <= 1000 ? applyMark(marks.size, marks.size.create({ pt: v })) : refused;
}

/** Any CSS colour the subset reads ("red", "#F00"), kept as "#rrggbb". */
export function setColor(value: string): Command {
  const rgb = colour(value);
  return rgb ? applyMark(marks.color, marks.color.create({ rgb })) : refused;
}

export function setHighlight(value: string | null): Command {
  if (value === null) return applyMark(marks.highlight, null);
  const rgb = colour(value);
  return rgb ? applyMark(marks.highlight, marks.highlight.create({ rgb })) : refused;
}

export function setAlign(align: Align): Command {
  return (state, dispatch) => {
    const blocks = selectedTextblocks(state).filter((b) => b.node.type !== nodes.code_block);
    if (!blocks.length) return false;
    if (!dispatch) return true;
    const tr = state.tr;
    const value = align === "left" ? null : align;
    retype(tr, blocks, (block) =>
      block.attrs.align === value ? block : block.type.create({ ...block.attrs, align: value }, block.content, block.marks),
    );
    finish(tr, dispatch);
    return true;
  };
}

// ----- lists and indentation -----------------------------------------------------------------

/** Many top-level list lines that make up whole flat lists: the lists become their lines in one
 * rebuild (lifting line by line costs a step, a copy of the document, per line). */
function liftWholeLists(tr: Transaction, lines: Located[]): boolean {
  if (lines.length < BULK_MIN || !lines.every((b) => b.parent === nodes.list_item && b.level === 1)) return false;
  // the lists holding them (an item's parent is its list): every item one selected line
  const items = new Map<number, Set<number>>(); // list position → indices of selected items
  for (const b of lines) {
    const { listPos, index } = b.item as { listPos: number; index: number };
    if (!items.has(listPos)) items.set(listPos, new Set());
    (items.get(listPos) as Set<number>).add(index);
  }
  const byParent = new Map<number, number[][]>();
  for (const [listPos, picked] of items) {
    const list = tr.doc.nodeAt(listPos) as PMNode;
    let whole = picked.size === list.childCount;
    list.forEach((item) => {
      if (item.childCount !== 1) whole = false;
    });
    if (!whole) return false;
    const $list = tr.doc.resolve(listPos);
    const parentPos = $list.depth ? $list.before() : -1;
    byParent.set(parentPos, [...(byParent.get(parentPos) ?? []), [$list.index()]]);
  }
  const mark = textBookmark(tr.doc, tr.selection);
  const before = tr.selection;
  const unwrap = (kids: PMNode[]) => {
    const out: PMNode[] = [];
    for (const list of kids) list.forEach((item) => out.push(item.child(0)));
    return out;
  };
  rebuildTree(tr, new Map([...byParent].map(([pos, r]) => [pos, (node: PMNode) => replaceRuns(node, r, unwrap)])));
  restoreBookmark(tr, mark, before);
  return true;
}

/** Take `lines` out of their lists one line at a time (prosemirror-schema-list lifts several
 * items by first merging them into one, which a list item here can't hold). */
function liftLines(state: EditorState, lines: Located[]): Transaction {
  const master = state.tr;
  if (liftWholeLists(master, lines)) return master;
  const lift = liftListItem(nodes.list_item);
  let inside = lines.map((b) => b.pos + 1);
  for (let round = 0; round < 1000; round++) {
    let lifted = false;
    for (let k = inside.length - 1; k >= 0; k--) {
      if (master.doc.resolve(inside[k]).node(-1)?.type !== nodes.list_item) continue;
      const at = EditorState.create({ doc: master.doc, selection: TextSelection.create(master.doc, inside[k]) });
      lift(at, (tr) => {
        for (const step of tr.steps) master.step(step);
        inside = inside.map((p) => tr.mapping.map(p));
        lifted = true;
      });
    }
    if (!lifted) break;
  }
  master.setSelection(state.selection.map(master.doc, master.mapping));
  return master;
}

export function toggleList(kind: "bullet" | "number"): Command {
  const type = kind === "bullet" ? nodes.bullet_list : nodes.ordered_list;
  return (state, dispatch) => {
    const lines = selectedTextblocks(state).filter((b) => b.node.type !== nodes.code_block);
    if (!lines.length) return false;
    if (!dispatch) return true;
    const off = lines.every((b) => b.parent === nodes.list_item && b.kind === kind);
    if (off) {
      const tr = liftLines(state, lines);
      const after = selectedTextblocks(EditorState.create({ doc: tr.doc, selection: tr.selection }));
      retype(
        tr,
        after.filter((b) => b.node.attrs.indent),
        (block) => block.type.create({ ...block.attrs, indent: 0 }, block.content, block.marks),
      );
      finish(tr, dispatch);
      return true;
    }
    const tr = state.tr;
    // lines on a list of the other kind: that list changes kind
    const lists = new Set<number>();
    for (const b of lines) {
      if (b.parent !== nodes.list_item || b.kind === kind) continue;
      const $p = state.doc.resolve(b.pos); // (only lines on a list of the other kind)
      lists.add($p.before($p.depth - 1));
    }
    for (const list of lists) tr.setNodeMarkup(list, type);
    // the other lines become a list, a run of neighbours one list
    const others = lines.filter((b) => b.parent !== nodes.list_item);
    const runs: Located[][] = [];
    for (const b of others) {
      const last = runs[runs.length - 1];
      const prev = last?.[last.length - 1];
      if (prev && prev.pos + prev.node.nodeSize === b.pos) last.push(b);
      else runs.push([b]);
    }
    if (others.length >= BULK_MIN) {
      // many lines: every run becomes a list in one rebuild of their containers (a step per
      // run would copy the document each time); the cursor is kept by a text bookmark
      const mark = textBookmark(tr.doc, tr.selection);
      const before = tr.selection;
      const byParent = new Map<number, number[][]>();
      for (const run of runs) {
        const key = run[0].parentPos;
        byParent.set(key, [...(byParent.get(key) ?? []), run.map((b) => b.index)]);
      }
      const make = (kids: PMNode[]) => [
        type.create(
          null,
          kids.map((k) => nodes.list_item.create(null, nodes.paragraph.create(blockAttrs(k), k.content, k.marks))),
        ),
      ];
      rebuildTree(tr, new Map([...byParent].map(([pos, r]) => [pos, (node: PMNode) => replaceRuns(node, r, make)])));
      restoreBookmark(tr, mark, before);
      finish(tr, dispatch);
      return true;
    }
    for (let i = runs.length - 1; i >= 0; i--) {
      const located = runs[i];
      const run = located.map((b) => b.pos);
      // each line its own list item (an item holds one line), then the lists joined: steps that
      // keep the cursor where it was
      for (let k = run.length - 1; k >= 0; k--) {
        const p = run[k];
        const node = tr.doc.nodeAt(p) as PMNode;
        if (node.type === nodes.heading) tr.setNodeMarkup(p, nodes.paragraph, blockAttrs(node));
        const range = tr.doc.resolve(p + 1).blockRange(tr.doc.resolve(p + node.nodeSize - 1));
        const wrapping = range && findWrapping(range, type);
        if (range && wrapping) tr.wrap(range, wrapping);
      }
      const starts: number[] = [];
      let p = run[0];
      for (let k = 0; k < run.length; k++) {
        starts.push(p);
        p += (tr.doc.nodeAt(p) as PMNode).nodeSize;
      }
      for (let k = starts.length - 1; k >= 1; k--) {
        const a = tr.doc.nodeAt(starts[k - 1]);
        const b = tr.doc.nodeAt(starts[k]);
        if (a?.type === type && b?.type === type) tr.join(starts[k]);
      }
    }
    if (!tr.docChanged) return false;
    finish(tr, dispatch);
    return true;
  };
}

export function changeIndent(delta: number): Command {
  return (state, dispatch) => {
    const lines = selectedTextblocks(state);
    if (!lines.length || !delta) return false;
    if (lines.every((b) => b.parent === nodes.list_item)) {
      const steps: Command[] = [];
      const item = nodes.list_item;
      for (let i = 0; i < Math.min(Math.abs(delta), MAX_LEVEL); i++) {
        steps.push((s, d) => {
          const blocks = selectedTextblocks(s);
          if (!blocks.every((b) => b.parent === nodes.list_item)) return false;
          if (delta > 0) {
            // at most MAX_LEVEL deep; sinkListItem only nests under an item above (the cap)
            if (blocks.some((b) => b.level >= MAX_LEVEL)) return false;
            return sinkListItem(item)(s, d);
          }
          // the top level stays in its list
          if (blocks.some((b) => b.level <= 1)) return false;
          return liftListItem(item)(s, d);
        });
      }
      if (!dispatch) return true;
      const tr = chain(state, steps);
      if (!tr) return false;
      finish(tr, dispatch);
      return true;
    }
    if (!dispatch) return true;
    const tr = state.tr;
    const blocks = lines.filter((b) => b.node.type !== nodes.code_block);
    retype(tr, blocks, (block) => {
      const indent = Math.min(MAX_LEVEL, Math.max(0, (Number(block.attrs.indent) || 0) + delta));
      return indent === block.attrs.indent ? block : block.type.create({ ...block.attrs, indent }, block.content, block.marks);
    });
    finish(tr, dispatch);
    return true;
  };
}

// ----- inserts -----------------------------------------------------------------------------------

/** A link on the selection (no `text`), or `text` (else the address) typed as a link. */
export function insertLink(address: string, text?: string | null): Command {
  // the normaliser's rule: no javascript:, data: or other schemes, nothing empty
  const href = safeHref(address);
  if (href === null) return refused;
  return (state, dispatch) => {
    if (!dispatch) return true;
    const tr = state.tr;
    const link = marks.link.create({ href });
    if (!text && !state.selection.empty) changeMarks(tr, selectionRanges(state), [marks.link], link, true);
    else tr.replaceSelectionWith(docSchema.text(text || href, [link]), false);
    finish(tr, dispatch);
    return true;
  };
}

/** A picture of the document (`img/N.ext`); a size the subset doesn't keep (not 1 to 100 000)
 * is left out. */
export function insertImage(name: string, width?: number | null, height?: number | null): Command {
  if (!CANON_IMG.test(name)) return refused;
  // the normaliser's rule (readDimension): a whole number of pixels, 1 to 100 000
  const size = (n: number | null | undefined) => {
    const v = n == null || !Number.isFinite(n) ? 0 : Math.round(n);
    return v > 0 && v <= 100000 ? v : null;
  };
  return (state, dispatch) => {
    if (!dispatch) return true;
    const image = nodes.image.create({ src: name, width: size(width), height: size(height) });
    finish(state.tr.replaceSelectionWith(image, false), dispatch);
    return true;
  };
}

/** Insert a block node near the cursor: before or after its textblock when the cursor is at the
 * start or end, splitting the textblock in the middle (`split`); up the tree until a parent can
 * hold it (a table in a list goes after the list). Returns its position, or null. */
function placeBlock(tr: Transaction, node: PMNode, split: boolean): number | null {
  const $p = tr.selection.$from;
  if (!$p.parent.isTextblock) return null;
  const depth = $p.depth;
  const parent = $p.node(depth - 1);
  const index = $p.index(depth - 1);
  const middle = $p.parentOffset > 0 && $p.parentOffset < $p.parent.content.size;
  if (split && middle && $p.parent.type !== nodes.code_block && parent.canReplaceWith(index + 1, index + 1, node.type)) {
    tr.split($p.pos);
    tr.insert($p.pos + 1, node);
    return $p.pos + 1;
  }
  const after = $p.parentOffset > 0;
  for (let d = depth; d > 0; d--) {
    const at = $p.index(d - 1) + (d < depth || after ? 1 : 0);
    if ($p.node(d - 1).canReplaceWith(at, at, node.type)) {
      const pos = $p.posAtIndex(at, d - 1);
      tr.insert(pos, node);
      return pos;
    }
  }
  return null;
}

export function insertTable(rows: number, cols: number): Command {
  return (state, dispatch) => {
    if (!dispatch) return true;
    const cell = () => nodes.table_cell.create(null, nodes.paragraph.create());
    const row = () => nodes.table_row.create(null, Array.from({ length: Math.max(1, cols) }, cell));
    const table = nodes.table.create(null, Array.from({ length: Math.max(1, rows) }, row));
    const tr = state.tr;
    if (!state.selection.empty) tr.deleteSelection();
    const pos = placeBlock(tr, table, true);
    if (pos === null) return false;
    tr.setSelection(TextSelection.near(tr.doc.resolve(pos + 4)));
    finish(tr, dispatch);
    return true;
  };
}

/** Delete the table around the cursor (an empty paragraph stays where a block is needed). */
const deleteWholeTable: Command = (state, dispatch) => {
  const $p = state.selection.$anchor;
  for (let d = $p.depth; d > 0; d--) {
    const table = $p.node(d);
    if (table.type !== nodes.table) continue;
    if (!dispatch) return true;
    const pos = $p.before(d);
    const parent = $p.node(d - 1);
    const tr = state.tr;
    if (parent.childCount === 1 && !parent.type.validContent(Fragment.empty)) {
      tr.replaceWith(pos, pos + table.nodeSize, nodes.paragraph.create());
    } else tr.delete(pos, pos + table.nodeSize);
    tr.setSelection(Selection.near(tr.doc.resolve(Math.min(pos, tr.doc.content.size))));
    dispatch(tr);
    return true;
  }
  return false;
};

/** Pad the table around the cursor when its rows are short (`fixTables` on that table alone). */
function padTable(tr: Transaction): void {
  const $p = tr.selection.$anchor;
  for (let d = $p.depth; d > 0; d--) {
    const table = $p.node(d);
    if (table.type !== nodes.table) continue;
    if (!TableMap.get(table).problems) return;
    const pos = $p.before(d);
    const alone = EditorState.create({ doc: nodes.doc.create(null, table) });
    const fix = fixTables(alone);
    if (!fix) return;
    const shift = new Mapping([new StepMap([0, 0, pos])]);
    for (const step of fix.steps) {
      const moved = step.map(shift);
      if (moved) tr.step(moved);
    }
    return;
  }
}

/** The desktop's table operations, on the table around the cursor. */
export function tableOp(op: TableOp): Command {
  return (state, dispatch) => {
    if (!isInTable(state)) return false;
    let cmd: Command;
    const rect = selectedRect(state);
    switch (op) {
      case "row_above":
        cmd = addRowBefore;
        break;
      case "row_below":
        cmd = addRowAfter;
        break;
      case "col_left":
        cmd = addColumnBefore;
        break;
      case "col_right":
        cmd = addColumnAfter;
        break;
      case "del_row":
        cmd = rect.top === 0 && rect.bottom === rect.map.height ? deleteWholeTable : deleteRow;
        break;
      case "del_col":
        cmd = rect.left === 0 && rect.right === rect.map.width ? deleteWholeTable : deleteColumn;
        break;
      case "del_table":
        cmd = deleteWholeTable;
        break;
      case "merge":
        if (!(state.selection instanceof CellSelection)) return false;
        cmd = mergeCells;
        break;
      case "split":
        cmd = splitCell;
        break;
      default:
        return false;
    }
    // Plain states (no plugin adds steps in between). A ragged table (the subset keeps short
    // rows; loading never pads them) is padded first: prosemirror-tables' operations need it.
    const done = state.tr;
    padTable(done);
    const base = EditorState.create({ doc: done.doc, selection: done.selection });
    let tr: Transaction | null = null;
    const ok = cmd(base, (t) => {
      tr = t;
    });
    if (!ok || !tr) return false;
    if (!dispatch) return true;
    const opTr = tr as Transaction;
    for (const step of opTr.steps) done.step(step);
    done.setSelection(Selection.fromJSON(done.doc, opTr.selection.toJSON()));
    // and after the edit, as prosemirror-tables expects
    const fix = fixTables(EditorState.create({ doc: done.doc }), EditorState.create({ doc: base.doc }));
    if (fix) for (const step of fix.steps) done.step(step);
    finish(done, dispatch);
    return true;
  };
}

/** A paragraph break with "page break before" on the new paragraph (it keeps the block's kind). */
export const insertPageBreak: Command = (state, dispatch) => {
  const $f = state.selection.$from;
  if (!$f.parent.isTextblock || $f.parent.type === nodes.code_block) return false;
  if (!dispatch) return true;
  const tr = state.tr;
  if (!state.selection.empty) {
    tr.deleteSelection();
    if (!ensureLine(tr, tr.mapping.map(state.selection.from))) return false;
  }
  const $p = tr.selection.$from;
  const block = $p.parent;
  // deleting can leave the cursor outside a line (or in code): then nothing happens
  if (!block.isTextblock || block.type === nodes.code_block || $p.depth < 1) return false;
  const attrs = { ...block.attrs, pageBreak: true };
  if ($p.node($p.depth - 1).type === nodes.list_item) {
    tr.split($p.pos, 2, [{ type: nodes.list_item }, { type: nodes.paragraph, attrs }]);
  } else tr.split($p.pos, 1, [{ type: block.type, attrs }]);
  finish(tr, dispatch);
  return true;
};

/** A horizontal rule after the cursor's line (an empty line becomes it), with a line after it. */
export const insertRule: Command = (state, dispatch) => {
  const $f = state.selection.$from;
  if (!$f.parent.isTextblock) return false;
  if (!dispatch) return true;
  const tr = state.tr;
  const rule = nodes.horizontal_rule.create();
  const block = $f.parent;
  const before = $f.before();
  const parent = $f.node($f.depth - 1);
  const index = $f.index($f.depth - 1);
  let pos: number | null;
  if (!block.content.size && parent.canReplaceWith(index, index + 1, rule.type)) {
    tr.replaceWith(before, before + block.nodeSize, rule);
    pos = before;
  } else {
    tr.setSelection(TextSelection.create(tr.doc, $f.end()));
    pos = placeBlock(tr, rule, false);
  }
  if (pos === null) return false;
  const $after = tr.doc.resolve(pos + 1);
  if ($after.index() >= $after.parent.childCount) tr.insert(pos + 1, nodes.paragraph.create());
  tr.setSelection(Selection.near(tr.doc.resolve(pos + 1), 1));
  finish(tr, dispatch);
  return true;
};

/** Remove the character formatting (pictures keep theirs; empty tags go) and make the blocks
 * normal; lists stay. Without a selection the cursor's whole line is cleared. */
export const clearFormatting: Command = (state, dispatch) => {
  if (!dispatch) return true;
  const clear: Command = (s, d) => {
    const tr = s.tr;
    const ranges: [number, number][] = [];
    if (s.selection.empty) {
      for (const b of selectedTextblocks(s)) ranges.push([b.pos + 1, b.pos + b.node.nodeSize - 1]);
    } else for (const r of s.selection.ranges) ranges.push([r.$from.pos, r.$to.pos]);
    const empties: number[] = [];
    for (const [a, b] of ranges) {
      s.doc.nodesBetween(a, b, (node, pos) => {
        if (node.type === nodes.empty_inline) empties.push(pos);
        return !node.isInline;
      });
    }
    changeMarks(tr, ranges, null, null, false); // sizes stay: the positions below are still valid
    for (const p of [...new Set(empties)].sort((x, y) => y - x)) tr.delete(p, p + 1);
    tr.setStoredMarks(null);
    d?.(tr);
    return true;
  };
  const tr = chain(state, [clear, setBlockStyle("normal")]);
  if (!tr) return false;
  finish(tr, dispatch);
  return true;
};

// ----- Enter, line breaks, Tab --------------------------------------------------------------

/** Put the cursor in a line after a deletion: the nearest one, else a new empty one where the
 * deletion was (deleting across list items can leave only an empty list). False if impossible. */
function ensureLine(tr: Transaction, at: number): boolean {
  if (tr.selection.$from.parent.isTextblock) return true;
  const $at = tr.doc.resolve(Math.min(at, tr.doc.content.size));
  // an emptied list (or other container) gets the line, rather than the next block
  const wrap = $at.parent.contentMatchAt($at.index()).findWrapping(nodes.paragraph);
  if (!wrap || $at.parent.childCount) {
    const near = Selection.findFrom($at, 1, true) ?? Selection.findFrom($at, -1, true);
    if (near) {
      tr.setSelection(near);
      return true;
    }
  }
  if (!wrap) return false;
  let line = nodes.paragraph.create();
  for (let i = wrap.length - 1; i >= 0; i--) line = wrap[i].create(null, line);
  tr.insert($at.pos, line);
  tr.setSelection(TextSelection.create(tr.doc, $at.pos + wrap.length + 1));
  return true;
}

/** Run `then` after deleting the selection, in one transaction (like the desktop, Enter over a
 * selection replaces it). The cursor goes into a line when deleting left it between blocks. */
function afterDeleting(then: Command): Command {
  return (state, dispatch) => {
    if (state.selection.empty) return then(state, dispatch);
    const tr = state.tr.deleteSelection();
    if (!ensureLine(tr, tr.mapping.map(state.selection.from))) {
      dispatch?.(tr.scrollIntoView()); // nothing to split: the deletion alone
      return true;
    }
    const mid = EditorState.create({ doc: tr.doc, selection: tr.selection });
    let next: Transaction | null = null;
    then(mid, (t) => {
      next = t;
    });
    if (!dispatch) return true;
    if (next) {
      const t = next as Transaction;
      for (const step of t.steps) tr.step(step);
      tr.setSelection(Selection.fromJSON(tr.doc, t.selection.toJSON()));
      if (t.storedMarks) tr.setStoredMarks(t.storedMarks);
    }
    dispatch(tr.scrollIntoView());
    return true;
  };
}

/** Enter: a new line (a new list item, or the line split), over a selection too. */
export const splitLine: Command = afterDeleting(
  chainCommands(newlineInCode, splitListItem(nodes.list_item), createParagraphNear, liftEmptyBlock, splitBlock),
);

/** Shift+Enter: a line break in the paragraph (a newline in code), over a selection too. */
export const lineBreak: Command = afterDeleting((state, dispatch) => {
  const $f = state.selection.$from;
  if (!$f.parent.isTextblock) return false;
  if ($f.parent.type === nodes.code_block) dispatch?.(state.tr.insertText("\n").scrollIntoView());
  else dispatch?.(state.tr.replaceSelectionWith(nodes.hard_break.create()).scrollIntoView());
  return true;
});

/** Tab: a tab character, in tables and lists too (the desktop's QTextEdit does the same). Over
 * a cell selection (or no line) it does nothing, but is still taken, so the focus stays. */
export const insertTab: Command = (state, dispatch) => {
  if (state.selection instanceof CellSelection || !state.selection.$from.parent.isTextblock) return true;
  dispatch?.(state.tr.insertText("\t").scrollIntoView());
  return true;
};

// ----- the cursor and empty tags ---------------------------------------------------------------

/** An arrow press next to an invisible empty tag (`empty_inline`): step over the empty tags and
 * one more character, so the cursor visibly moves. False when there's none (the browser moves). */
export function stepOverEmpty(dir: 1 | -1): Command {
  return (state, dispatch) => {
    const sel = state.selection;
    if (!(sel instanceof TextSelection) || !sel.$cursor) return false;
    const doc = state.doc;
    const next = (p: number) => (dir > 0 ? doc.resolve(p).nodeAfter : doc.resolve(p).nodeBefore);
    let pos = sel.$cursor.pos;
    if (next(pos)?.type !== nodes.empty_inline) return false;
    while (next(pos)?.type === nodes.empty_inline) pos += dir;
    const n = next(pos);
    let target: Selection | null = null;
    if (n?.isText) {
      const text = n.text as string;
      const c = dir > 0 ? text.charCodeAt(0) : text.charCodeAt(text.length - 1);
      const pair = text.length > 1 && (dir > 0 ? c >= 0xd800 && c <= 0xdbff : c >= 0xdc00 && c <= 0xdfff);
      target = TextSelection.create(doc, pos + dir * (pair ? 2 : 1));
    } else if (n) target = TextSelection.create(doc, pos + dir * n.nodeSize);
    else {
      const $p = doc.resolve(pos);
      const edge = dir > 0 ? $p.after() : $p.before();
      target = Selection.findFrom(doc.resolve(edge), dir, true) ?? TextSelection.create(doc, pos);
    }
    if (dispatch) dispatch(state.tr.setSelection(target).scrollIntoView());
    return true;
  };
}

// ----- what the format bar shows -------------------------------------------------------------

export interface ActiveState {
  style: BlockStyle;
  marks: Set<string>;
  font: string | null;
  size: number | null;
  color: string | null;
  highlight: string | null;
  href: string | null;
  align: Align;
  list: "bullet" | "number" | null;
  inTable: boolean;
}

/** The kind of list the textblock at `pos` is a line of (null: not a list line). */
function listKindAt(doc: PMNode, pos: number): Located["kind"] {
  const found: Located[] = [];
  textblocksIn(doc, pos + 1, pos + 1, found, new Set());
  const line = found.find((b) => b.pos === pos);
  return line && line.parent === nodes.list_item ? line.kind : null;
}

export function activeState(state: EditorState): ActiveState {
  const sel = state.selection;
  let $p = sel instanceof TextSelection ? sel.$head : sel.$from;
  if (!$p.parent.isTextblock) {
    const first = selectedTextblocks(state)[0];
    if (first) $p = state.doc.resolve(first.pos + 1);
  }
  const set = marksAt(state);
  const attr = (type: MarkType, name: string) => (type.isInSet(set)?.attrs[name] ?? null) as never;
  const block = $p.parent.isTextblock ? $p.parent : null;
  const blockPos = block ? $p.before() : -1;
  return {
    style: blockStyleAt($p),
    marks: new Set(set.filter((m) => m.type !== marks.nest).map((m) => m.type.name)),
    font: attr(marks.font, "family"),
    size: attr(marks.size, "pt"),
    color: attr(marks.color, "rgb"),
    highlight: attr(marks.highlight, "rgb"),
    href: attr(marks.link, "href"),
    align: ((block?.attrs.align as Align | null) ?? "left") as Align,
    list: block ? listKindAt(state.doc, blockPos) : null,
    inTable: isInTable(state),
  };
}
