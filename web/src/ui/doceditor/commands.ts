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

import { Fragment, Mark, type MarkType, type Node as PMNode, type ResolvedPos } from "prosemirror-model";
import { EditorState, Selection, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { Mapping, StepMap, findWrapping, liftTarget } from "prosemirror-transform";
import { closeHistory } from "prosemirror-history";
import { liftListItem, sinkListItem } from "prosemirror-schema-list";
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
}

/** The textblocks the selection touches, in document order (a selection that ends right at the
 * start of a block leaves that block out, as on the desktop). */
export function selectedTextblocks(state: EditorState): Located[] {
  const out: Located[] = [];
  const seen = new Set<number>();
  for (const r of state.selection.ranges) {
    const from = r.$from.pos;
    const to = r.$to.pos;
    state.doc.nodesBetween(from, to, (node, pos) => {
      if (!node.isTextblock) return true;
      const skip = to > from && to <= pos + 1 && pos + 1 > from;
      if (!skip && !seen.has(pos)) {
        seen.add(pos);
        out.push({ node, pos });
      }
      return false;
    });
  }
  if (!out.length) {
    const $f = state.selection.$from;
    if ($f.parent.isTextblock) out.push({ node: $f.parent, pos: $f.before() });
  }
  return out.sort((a, b) => a.pos - b.pos);
}

/** A list item's own line (its first paragraph). */
const isListLine = (doc: PMNode, pos: number) => doc.resolve(pos).parent.type === nodes.list_item;

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
      cur = cur.apply(tr);
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
    const blocks = selectedTextblocks(state).filter((b) => !isListLine(state.doc, b.pos));
    if (!blocks.length) return false;
    if (!dispatch) return true;
    const tr = state.tr;
    const level = HEADINGS[style] ?? 0;
    const sel = { from: new Tracked(tr, state.selection.from), to: new Tracked(tr, state.selection.to) };
    const made: { at: Tracked; quoted: boolean }[] = [];
    for (const b of blocks) {
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
    } else {
      for (const r of state.selection.ranges) {
        if (r.$from.pos === r.$to.pos) continue;
        tr.removeMark(r.$from.pos, r.$to.pos, type);
        if (mark) tr.addMark(r.$from.pos, r.$to.pos, mark);
      }
    }
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

export const setFont = (family: string): Command => applyMark(marks.font, marks.font.create({ family }));
export const setSize = (pt: number): Command => applyMark(marks.size, marks.size.create({ pt }));
export const setColor = (rgb: string): Command => applyMark(marks.color, marks.color.create({ rgb: rgb.toLowerCase() }));
export const setHighlight = (rgb: string | null): Command =>
  applyMark(marks.highlight, rgb ? marks.highlight.create({ rgb: rgb.toLowerCase() }) : null);

export function setAlign(align: Align): Command {
  return (state, dispatch) => {
    const blocks = selectedTextblocks(state).filter((b) => b.node.type !== nodes.code_block);
    if (!blocks.length) return false;
    if (!dispatch) return true;
    const tr = state.tr;
    for (const b of blocks) tr.setNodeMarkup(b.pos, null, { ...b.node.attrs, align: align === "left" ? null : align });
    finish(tr, dispatch);
    return true;
  };
}

// ----- lists and indentation -----------------------------------------------------------------

/** How deep the list line at `pos` is (1 = top level). */
function listLevel(doc: PMNode, pos: number): number {
  const $p = doc.resolve(pos);
  let n = 0;
  for (let d = $p.depth; d > 0; d--) {
    const t = $p.node(d).type;
    if (t === nodes.list_item) n++;
    else if (t !== nodes.bullet_list && t !== nodes.ordered_list) break;
  }
  return n;
}

const listKind = (doc: PMNode, pos: number) => {
  const $p = doc.resolve(pos);
  return $p.depth >= 2 && $p.node($p.depth - 1).type === nodes.ordered_list ? "number" : "bullet";
};

/** Take `lines` out of their lists one line at a time (prosemirror-schema-list lifts several
 * items by first merging them into one, which a list item here can't hold). */
function liftLines(state: EditorState, lines: Located[]): Transaction {
  const master = state.tr;
  const lift = liftListItem(nodes.list_item);
  let inside = lines.map((b) => b.pos + 1);
  for (let round = 0; round < 1000; round++) {
    let lifted = false;
    for (let k = inside.length - 1; k >= 0; k--) {
      if (!isListLine(master.doc, inside[k] - 1)) continue;
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
    const off = lines.every((b) => isListLine(state.doc, b.pos) && listKind(state.doc, b.pos) === kind);
    if (off) {
      const tr = liftLines(state, lines);
      for (const b of selectedTextblocks(EditorState.create({ doc: tr.doc, selection: tr.selection }))) {
        if (b.node.attrs.indent) tr.setNodeMarkup(b.pos, null, { ...b.node.attrs, indent: 0 });
      }
      finish(tr, dispatch);
      return true;
    }
    const tr = state.tr;
    // lines on a list of the other kind: that list changes kind
    const lists = new Set<number>();
    for (const b of lines) {
      if (!isListLine(state.doc, b.pos)) continue;
      const $p = state.doc.resolve(b.pos);
      const list = $p.before($p.depth - 1);
      if (state.doc.nodeAt(list)?.type !== type) lists.add(list);
    }
    for (const list of lists) tr.setNodeMarkup(list, type);
    // the other lines become a list, a run of neighbours one list
    const others = lines.filter((b) => !isListLine(state.doc, b.pos)).map((b) => b.pos);
    const runs = siblingRuns(tr, others);
    for (let i = runs.length - 1; i >= 0; i--) {
      const run = runs[i];
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
    if (lines.every((b) => isListLine(state.doc, b.pos))) {
      const steps: Command[] = [];
      const item = nodes.list_item;
      for (let i = 0; i < Math.min(Math.abs(delta), MAX_LEVEL); i++) {
        steps.push((s, d) => {
          const blocks = selectedTextblocks(s);
          if (!blocks.every((b) => isListLine(s.doc, b.pos))) return false;
          if (delta > 0) {
            // at most MAX_LEVEL deep; sinkListItem only nests under an item above (the cap)
            if (blocks.some((b) => listLevel(s.doc, b.pos) >= MAX_LEVEL)) return false;
            return sinkListItem(item)(s, d);
          }
          // the top level stays in its list
          if (blocks.some((b) => listLevel(s.doc, b.pos) <= 1)) return false;
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
    for (const b of lines) {
      if (b.node.type === nodes.code_block) continue;
      const indent = Math.min(MAX_LEVEL, Math.max(0, (Number(b.node.attrs.indent) || 0) + delta));
      tr.setNodeMarkup(b.pos, null, { ...b.node.attrs, indent });
    }
    finish(tr, dispatch);
    return true;
  };
}

// ----- inserts -----------------------------------------------------------------------------------

/** A link on the selection (no `text`), or `text` (else the address) typed as a link. */
export function insertLink(href: string, text?: string | null): Command {
  return (state, dispatch) => {
    if (!dispatch) return true;
    const tr = state.tr;
    const link = marks.link.create({ href });
    if (!text && !state.selection.empty) {
      for (const r of state.selection.ranges) {
        tr.removeMark(r.$from.pos, r.$to.pos, marks.link);
        tr.addMark(r.$from.pos, r.$to.pos, link);
      }
    } else {
      tr.replaceSelectionWith(docSchema.text(text || href, [link]), false);
    }
    finish(tr, dispatch);
    return true;
  };
}

export function insertImage(name: string, width?: number | null, height?: number | null): Command {
  return (state, dispatch) => {
    if (!dispatch) return true;
    const image = nodes.image.create({ src: name, width: width || null, height: height || null });
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
  if (!state.selection.empty) tr.deleteSelection();
  const $p = tr.selection.$from;
  const block = $p.parent;
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
      removeMarks(tr, a, b, null);
    }
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
    list: block && isListLine(state.doc, blockPos) ? listKind(state.doc, blockPos) : null,
    inTable: isInTable(state),
  };
}
