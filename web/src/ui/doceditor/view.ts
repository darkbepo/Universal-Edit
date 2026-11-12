/**
 * The website document editor's editing view: a ProseMirror `EditorView` over `docSchema`, with
 * the editor's plugins (history, shortcuts, tables, search, and the subset's own housekeeping),
 * pictures shown from object URLs of `images`, and pasting through the subset normaliser.
 *
 * The clipboard carries subset HTML (`docToHtml` of the copied part): clean HTML for other apps,
 * and pasting reads every HTML the way a document is read (`normaliseHtml`, then `htmlToDoc`), so
 * copying inside the editor keeps empty links and the source's tag order.
 */

import {
  DOMParser,
  DOMSerializer,
  Fragment,
  Mark,
  Slice,
  type Node as PMNode,
  type ResolvedPos,
} from "prosemirror-model";
import { EditorState, Plugin, Selection, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { AddMarkStep, AddNodeMarkStep, RemoveMarkStep, RemoveNodeMarkStep } from "prosemirror-transform";
import { EditorView } from "prosemirror-view";
import { baseKeymap } from "prosemirror-commands";
import { closeHistory, history, redo, undo } from "prosemirror-history";
import { keymap } from "prosemirror-keymap";
import { search } from "prosemirror-search";
import { tableEditing } from "prosemirror-tables";
import { CANON_IMG, normaliseHtml } from "../../doceditor/subset";
import type { DocImages, OpenedDocument, SaveContent } from "../../doceditor/types";
import {
  COMMAND_META,
  clearFormatting,
  insertImage,
  insertPageBreak,
  insertTab,
  lineBreak,
  setAlign,
  setBlockStyle,
  splitLine,
  stepOverEmpty,
  toggleList,
  toggleMark,
} from "./commands";
import { docToHtml, htmlToDoc } from "./convert";
import { docSchema } from "./schema";

const nodes = docSchema.nodes;
const marks = docSchema.marks;

/** Pictures the editor can add, by MIME type → file extension (the desktop's list). */
export const PICTURE_TYPES: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/gif": "gif",
  "image/bmp": "bmp",
  "image/webp": "webp",
};
const PICTURE_EXTS: Record<string, string> = { png: "png", jpg: "jpg", jpeg: "jpg", gif: "gif", bmp: "bmp", webp: "webp" };
/** The desktop's message for a file that isn't a picture the editor takes. */
export const NOT_A_PICTURE = "This file isn't a picture the editor can add (PNG, JPG, GIF, BMP or WebP).";
export const MAX_PICTURE_BYTES = 50 * 1024 * 1024;
/** The picture extension the editor stores `file` under (by its type, else its name), or null
 * when it isn't a picture the editor takes. */
export function pictureExt(file: File): string | null {
  return PICTURE_TYPES[file.type] ?? PICTURE_EXTS[file.name.slice(file.name.lastIndexOf(".") + 1).toLowerCase()] ?? null;
}
export const TOO_DEEP = "This document is nested too deeply to edit here.";
/** Deepest node nesting the view takes. The normaliser flattens HTML past 100 elements, so a
 * normalised document stays far below this; it guards the recursive rendering. */
export const MAX_EDIT_DEPTH = 400;

/** How deeply `doc`'s nodes nest (a paragraph at the top is 1), walked without recursion. */
export function docDepth(doc: PMNode): number {
  let deepest = 0;
  const stack: [PMNode, number][] = [[doc, 0]];
  while (stack.length) {
    const [node, depth] = stack.pop() as [PMNode, number];
    if (depth > deepest) deepest = depth;
    if (node.isTextblock) continue;
    node.forEach((child) => {
      if (!child.isLeaf) stack.push([child, depth + 1]);
    });
  }
  return deepest;
}
const MIME: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif", bmp: "image/bmp", webp: "image/webp" };
/** The page column is 794 px (A4 at 96 dpi) with 76 px margins: wider pictures are shrunk. */
const ROOM = 794 - 2 * 76;

// ----- plugins -----------------------------------------------------------------------------

const NEST_TAGS: Record<string, string> = { a: "link", b: "bold", i: "italic", u: "underline", s: "strike" };
const SPAN_LETTERS: Record<string, string> = { f: "font", z: "size", c: "color", h: "highlight" };

/** A `nest` record without the tags `set` no longer has ("" when fewer than two are left). It
 * writes the same HTML: `convert.ts` skips a recorded tag whose mark is gone. */
export function cleanNest(order: string, set: readonly Mark[]): string {
  const has = (name: string) => set.some((m) => m.type.name === name);
  const kept: string[] = [];
  for (const tok of order.split(" ")) {
    if (tok.startsWith("span:")) {
      const parts = tok
        .slice(5)
        .split(",")
        .filter((p) => Object.hasOwn(SPAN_LETTERS, p[0] ?? "") && has(SPAN_LETTERS[p[0]]));
      if (parts.length) kept.push(`span:${parts.join(",")}`);
    } else if (Object.hasOwn(NEST_TAGS, tok) && has(NEST_TAGS[tok])) kept.push(tok);
  }
  return kept.length >= 2 ? kept.join(" ") : "";
}

/** The ranges of the final document that `trs` changed (marks included). The ranges collected
 * so far are carried forward through each step and kept merged, so this is linear in the steps
 * (typing: one step; a big selection: one step from `rebuildBlocks`). */
function changedRanges(trs: readonly Transaction[], size: number): [number, number][] {
  let ranges: [number, number][] = [];
  const add = (a: number, b: number) => {
    ranges.push([a, b]);
    if (ranges.length > 1) {
      ranges.sort((x, y) => x[0] - y[0]);
      const merged: [number, number][] = [];
      for (const r of ranges) {
        const last = merged[merged.length - 1];
        if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
        else merged.push([r[0], r[1]]);
      }
      ranges = merged;
    }
  };
  for (const tr of trs) {
    for (const step of tr.steps) {
      const map = step.getMap();
      const local: [number, number][] = [];
      map.forEach((_a, _b, from, to) => local.push([from, to]));
      if (local.length) ranges = ranges.map(([x, y]) => [map.map(x, -1), map.map(y, 1)]);
      for (const [x, y] of local) add(x, y);
      if (step instanceof AddMarkStep || step instanceof RemoveMarkStep) add(step.from, step.to);
      else if (step instanceof AddNodeMarkStep || step instanceof RemoveNodeMarkStep) add(step.pos, step.pos + 1);
    }
  }
  return ranges.map(([x, y]) => [Math.max(0, x), Math.min(size, y)]);
}

/** Drops the tags of `nest` records whose marks were removed (and records left with one). */
const nestCleanup = new Plugin({
  appendTransaction(trs, _old, state) {
    if (!trs.some((t) => t.docChanged)) return null;
    let tr: Transaction | null = null;
    for (const [a, b] of changedRanges(trs, state.doc.content.size)) {
      state.doc.nodesBetween(a, b, (node, pos) => {
        if (!node.isInline) return true;
        const nest = marks.nest.isInSet(node.marks);
        if (nest) {
          const order = cleanNest(String(nest.attrs.order), node.marks);
          if (order !== nest.attrs.order) {
            tr ??= state.tr;
            if (order) tr.addMark(pos, pos + node.nodeSize, marks.nest.create({ order }));
            else tr.removeMark(pos, pos + node.nodeSize, marks.nest);
          }
        }
        return false;
      });
    }
    return tr;
  },
});

/** The marks text typed at `$c` (right after an empty tag) gets: those of the nearest real leaf
 * before it, else after it — not the empty tag's own. A link only continues into a real link
 * to the same address that follows (as the desktop's `_leave_empty_link`). */
function typingMarks($c: ResolvedPos): readonly Mark[] {
  const parent = $c.parent;
  const real = (i: number) => parent.child(i).type !== nodes.empty_inline;
  let after = $c.index();
  while (after < parent.childCount && !real(after)) after++;
  const next = after < parent.childCount ? parent.child(after).marks : null;
  let before = $c.index() - 1;
  while (before >= 0 && !real(before)) before--;
  const set = before >= 0 ? parent.child(before).marks : (next ?? Mark.none);
  return set.filter((m) => m.type.spec.inclusive !== false || (next !== null && m.isInSet(next) !== undefined && before >= 0));
}

/** Typing right after an invisible empty tag doesn't take its formatting. */
const emptyTyping = new Plugin({
  appendTransaction(trs, _old, state) {
    if (trs.some((t) => t.storedMarksSet) || !trs.some((t) => t.selectionSet || t.docChanged)) return null;
    const sel = state.selection;
    if (!(sel instanceof TextSelection) || !sel.$cursor) return null;
    if (sel.$cursor.nodeBefore?.type !== nodes.empty_inline) return null;
    return state.tr.setStoredMarks(typingMarks(sel.$cursor));
  },
});

/** prosemirror-tables' editing (cell selections, table paste) without padding short rows on every
 * change: loading or typing keeps a ragged table as it is; `tableOp` pads after a table edit. */
const editing = tableEditing();
const tables = new Plugin({
  ...editing.spec,
  appendTransaction: (trs, _old, state) => editing.spec.appendTransaction?.call(editing, trs, state, state),
});

/** Shift+Tab does nothing (as on the desktop), but keeps the focus in the page. */
const nothing: Command = () => true;

/** The editor's shortcuts (the spec's list; Ctrl+K, F, H and S belong to the page). */
export const EDITOR_KEYS: Record<string, Command> = {
  "Mod-b": toggleMark("bold"),
  "Mod-i": toggleMark("italic"),
  "Mod-u": toggleMark("underline"),
  "Mod-Shift-x": toggleMark("strike"),
  "Mod-Alt-1": setBlockStyle("h1"),
  "Mod-Alt-2": setBlockStyle("h2"),
  "Mod-Alt-3": setBlockStyle("h3"),
  "Mod-Alt-0": setBlockStyle("normal"),
  "Mod-l": setAlign("left"),
  "Mod-e": setAlign("center"),
  "Mod-r": setAlign("right"),
  "Mod-j": setAlign("justify"),
  "Mod-Shift-l": toggleList("bullet"),
  "Mod-Enter": insertPageBreak,
  "Mod-Space": clearFormatting,
  "Mod-z": undo,
  "Mod-y": redo,
  "Mod-Shift-z": redo,
  "Shift-Enter": lineBreak,
  Enter: splitLine,
  Tab: insertTab,
  "Shift-Tab": nothing,
  ArrowRight: stepOverEmpty(1),
  ArrowLeft: stepOverEmpty(-1),
};

const PLUGINS = [nestCleanup, emptyTyping, history(), keymap(EDITOR_KEYS), keymap(baseKeymap), tables, search()];

/** An editor state for `doc` with the editor's plugins. */
export function createState(doc: PMNode): EditorState {
  return EditorState.create({ doc, plugins: PLUGINS });
}

// ----- plain text --------------------------------------------------------------------------

const NBSP_ALL = /\u00a0/g;

function lineText(block: PMNode, picture: string): string {
  let out = "";
  block.forEach((child) => {
    if (child.isText) out += (child.text as string).replace(NBSP_ALL, " ");
    else if (child.type === nodes.hard_break) out += "\n";
    else if (child.type === nodes.image) out += picture;
  });
  return block.type === nodes.code_block && out.endsWith("\n") ? out.slice(0, -1) : out;
}

/** The document as plain text, as the desktop's QTextDocument.toPlainText gives it (no-break
 * spaces as spaces, pictures and empty tags left out, an empty line after every table and
 * before one that starts the page or a cell), with a final line break ("" when empty).
 * `picture` stands for each picture (the status line counts one as a space, as the desktop). */
export function plainText(doc: PMNode, picture = ""): string {
  if (doc.childCount === 1 && doc.child(0).type === nodes.paragraph && !doc.child(0).content.size) return "";
  const lines: string[] = [];
  const frame = (container: PMNode) => {
    const start = lines.length;
    const walk = (node: PMNode) => {
      node.forEach((child) => {
        if (child.isTextblock) lines.push(lineText(child, picture));
        else if (child.type === nodes.horizontal_rule) lines.push("");
        else if (child.type === nodes.table) {
          if (lines.length === start) lines.push("");
          child.forEach((row) => row.forEach((cell) => frame(cell)));
          lines.push("");
        } else walk(child);
      });
    };
    walk(container);
  };
  frame(doc);
  const text = lines.join("\n");
  return text ? `${text}\n` : "";
}

// ----- clipboard -----------------------------------------------------------------------------

const LINE_BREAKS = new RegExp("\\r?\\n|\\r", "g");

/** Plain text as paragraphs, one per line, blank lines kept, with `marks` (the formatting at the
 * cursor, as the desktop's insertText). */
export function textSlice(text: string, marks: readonly Mark[] = Mark.none): Slice {
  const lines = text.replace(LINE_BREAKS, "\n").split("\n");
  const paragraphs = lines.map((line) => nodes.paragraph.create(null, line ? docSchema.text(line, marks) : null));
  return new Slice(Fragment.fromArray(paragraphs), 1, 1);
}

/** `fragment` (a copied part) as a document, or null when it can't be one. */
function asDoc(fragment: Fragment): PMNode | null {
  const first = fragment.firstChild;
  if (!first) return null;
  let content = fragment;
  const wrap = nodes.doc.contentMatch.findWrapping(first.type);
  if (wrap) for (let i = wrap.length - 1; i >= 0; i--) content = Fragment.from(wrap[i].create(null, content));
  try {
    return nodes.doc.createChecked(null, content);
  } catch {
    return null;
  }
}

/** The schema's own DOM without the editor's private attributes (a fallback). */
const plainDom = new DOMSerializer(
  { ...DOMSerializer.nodesFromSchema(docSchema), empty_inline: () => ["span"] },
  { ...DOMSerializer.marksFromSchema(docSchema), nest: () => ["span", 0] },
);

/** Writes the copied part as subset HTML (`docToHtml`); `copied` hears what was written. */
class SubsetSerializer extends DOMSerializer {
  constructor(private readonly copied: (html: string) => void) {
    super(plainDom.nodes, plainDom.marks);
  }
  serializeFragment(
    fragment: Fragment,
    options: { document?: Document } = {},
    target?: HTMLElement | DocumentFragment,
  ): HTMLElement | DocumentFragment {
    const doc = asDoc(fragment);
    if (!doc) return plainDom.serializeFragment(fragment, options, target);
    const dom = options.document ?? window.document;
    const holder = dom.createElement("div");
    const html = docToHtml(doc);
    this.copied(html);
    holder.innerHTML = html;
    const out = target ?? dom.createDocumentFragment();
    while (holder.firstChild) out.appendChild(holder.firstChild);
    return out;
  }
}

/** Reads pasted HTML (already normalised by `transformPastedHTML`) with `htmlToDoc`. */
class SubsetParser extends DOMParser {
  constructor(private readonly pasted: () => string | null) {
    super(docSchema, DOMParser.fromSchema(docSchema).rules);
  }
  parseSlice(dom: globalThis.Node, options?: Parameters<DOMParser["parseSlice"]>[1]): Slice {
    const html = this.pasted() ?? (dom as HTMLElement).innerHTML ?? "";
    try {
      const doc = htmlToDoc(html);
      if (doc.childCount === 1 && !doc.child(0).content.size && !html) return Slice.empty;
      return new Slice(doc.content, 0, 0);
    } catch (err) {
      console.error("Pasted HTML couldn't be read by the editor", err);
      return super.parseSlice(dom, options);
    }
  }
}

// ----- the view ------------------------------------------------------------------------------

const IMG_SRC = /<img src="([^"]*)"/g;
const IMG_TAG = /<img src="([^"]*)"[^>]*>/g;
/** A transaction that pastes plain text after a paste failed (it isn't retried again). */
const PLAIN_META = "umc-plain-paste";
const unescapeAttr = (s: string) =>
  s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

export class DocView {
  readonly view: EditorView;
  images: DocImages = new Map();
  /** Called after every change of the document or the selection. */
  onChange: () => void = () => {};
  /** Also called after every change (the bars listen here; `onChange` belongs to the page). */
  private readonly listeners = new Set<() => void>();
  private clean: PMNode;
  private frontMatter = "";
  private title = "";
  private template: Uint8Array | null = null;
  private readonly urls = new Map<string, string>();
  private afterCommand = false;
  private pastedHtml: string | null = null;
  /** The subset HTML of this editor's last copy: a paste of it keeps its picture references. */
  private copiedHtml: string | null = null;
  /** The plain text of the paste being handled, and whether its HTML couldn't be read. */
  private pasteText = "";
  private pasteFailed = false;
  private readonly serializer = new SubsetSerializer((html) => {
    this.copiedHtml = html;
  });
  /** Deepest nesting `load` takes (replaceable in tests). */
  maxDepth = MAX_EDIT_DEPTH;
  /** Files dropped on the page that aren't all pictures (the page decides what to do). */
  onDropFiles: (files: File[]) => void = () => {};
  /** A plain-language problem (a dropped file that can't be added); the page shows it. */
  onError: (message: string) => void = () => {};
  /** How pasted HTML is normalised (replaceable in tests). */
  normalisePasted: typeof normaliseHtml = normaliseHtml;

  constructor(host: HTMLElement) {
    const doc = htmlToDoc("");
    this.clean = doc;
    this.view = new EditorView(host, {
      state: createState(doc),
      dispatchTransaction: (tr) => this.dispatch(tr),
      nodeViews: {
        image: (node) => {
          const img = document.createElement("img");
          this.showPicture(img, node);
          return {
            dom: img,
            update: (next) => {
              if (next.type !== node.type) return false;
              this.showPicture(img, next);
              node = next;
              return true;
            },
          };
        },
      },
      transformPastedHTML: (html) => this.transformPasted(html),
      clipboardParser: new SubsetParser(() => {
        const html = this.pastedHtml;
        this.pastedHtml = null;
        return html;
      }),
      clipboardSerializer: this.serializer,
      clipboardTextParser: (text, $context) => textSlice(text, this.marksAt($context)),
      handlePaste: (_view, event) => this.handlePaste(event),
      // dropped files are handled before ProseMirror looks for a drop position, which it may not
      // find: the browser would then open the file instead of the page
      handleDOMEvents: { drop: (_view, event) => this.handleDrop(event as DragEvent) },
    });
  }

  /** Listen to every change of the document or the selection; returns the way to stop. */
  listen(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private changed(): void {
    this.onChange();
    for (const fn of this.listeners) fn();
  }

  /** Whether the document differs from the one loaded (or last marked clean). */
  get dirty(): boolean {
    return !this.view.state.doc.eq(this.clean);
  }

  /** The document now counts as saved (`doc`: the one that was saved, if it changed since). */
  markClean(doc: PMNode = this.view.state.doc): void {
    this.clean = doc;
    this.changed();
  }

  /** Show `opened` (normalised first, as on the desktop); history and the dirty flag start over.
   * A document nested more deeply than `maxDepth` is refused with `TOO_DEEP` (nothing changes). */
  load(opened: OpenedDocument): void {
    let doc: PMNode;
    try {
      doc = htmlToDoc(normaliseHtml(opened.html).html);
    } catch (err) {
      if (err instanceof RangeError) throw new Error(TOO_DEEP, { cause: err }); // the call stack
      throw err;
    }
    if (docDepth(doc) > this.maxDepth) throw new Error(TOO_DEEP);
    for (const url of this.urls.values()) URL.revokeObjectURL(url);
    this.urls.clear();
    this.images = new Map(opened.images);
    this.frontMatter = opened.frontMatter;
    this.title = opened.title;
    this.template = opened.template;
    this.clean = doc;
    this.afterCommand = false;
    this.copiedHtml = null;
    this.view.updateState(createState(doc));
    this.changed();
  }

  /** What saving writes: the subset HTML, the pictures it still uses, the plain text. */
  content(): SaveContent {
    const doc = this.view.state.doc;
    const html = docToHtml(doc);
    const used = new Set([...html.matchAll(IMG_SRC)].map((m) => unescapeAttr(m[1])));
    const images: DocImages = new Map();
    for (const [name, data] of this.images) if (used.has(name)) images.set(name, data);
    return {
      html,
      images,
      plainText: plainText(doc),
      frontMatter: this.frontMatter,
      title: this.title,
      template: this.template,
    };
  }

  focus(): void {
    this.view.focus();
  }

  /** Run a command on the view (false when it doesn't apply). */
  run(command: Command): boolean {
    return command(this.view.state, (tr) => this.view.dispatch(tr), this.view);
  }

  /** Add a picture's bytes under a fresh name and insert it at the cursor (shrunk to the page). */
  async addPicture(data: Uint8Array, ext: string): Promise<string> {
    let width: number | null = null;
    let height: number | null = null;
    if (typeof createImageBitmap === "function") {
      try {
        const bitmap = await createImageBitmap(new Blob([data as BlobPart], { type: MIME[ext] }));
        if (bitmap.width > ROOM) {
          width = ROOM;
          height = Math.round((bitmap.height * ROOM) / bitmap.width);
        }
        bitmap.close();
      } catch {
        /* the size stays the picture's own */
      }
    }
    const name = this.freeName(ext);
    this.images.set(name, data);
    this.run(insertImage(name, width, height));
    return name;
  }

  /** Add picture files at `pos` (or the cursor); a file that isn't a picture the editor takes is
   * reported with `onError`. */
  async insertPictureFiles(files: File[], pos: number | null = null): Promise<void> {
    if (pos !== null) {
      const $pos = this.view.state.doc.resolve(Math.min(pos, this.view.state.doc.content.size));
      this.view.dispatch(this.view.state.tr.setSelection(Selection.near($pos)));
    }
    for (const file of files) {
      const ext = pictureExt(file);
      if (!ext || file.size > MAX_PICTURE_BYTES) {
        this.onError(NOT_A_PICTURE);
        continue;
      }
      try {
        await this.addPicture(new Uint8Array(await file.arrayBuffer()), ext);
      } catch (err) {
        console.error("A dropped picture couldn't be added", err);
        this.onError("The picture couldn't be read.");
      }
    }
    this.focus();
  }

  /** Files dropped on the page: pictures are inserted where they were dropped, anything else
   * goes to `onDropFiles`. Never left to the browser (it would leave the page). */
  private handleDrop(event: DragEvent): boolean {
    const files = Array.from(event.dataTransfer?.files ?? []);
    if (!files.length) return false;
    event.preventDefault();
    if (!files.every((f) => pictureExt(f) !== null)) {
      this.onDropFiles(files);
      return true;
    }
    let pos: number | null = null;
    try {
      pos = this.view.posAtCoords({ left: event.clientX, top: event.clientY })?.pos ?? null;
    } catch {
      /* no layout to read: the cursor */
    }
    void this.insertPictureFiles(files, pos);
    return true;
  }

  /** What the clipboard gets for `slice` (as `text/html`). */
  clipboardHtml(slice: Slice): string {
    const holder = document.createElement("div");
    holder.appendChild(this.serializer.serializeFragment(slice.content, { document }));
    return holder.innerHTML;
  }

  private dispatch(tr: Transaction): void {
    const ui = tr.getMeta("uiEvent");
    if (tr.docChanged && (ui === "paste" || ui === "drop")) {
      // ProseMirror's fitting can leave an empty table cell for some pasted slices: paste the
      // plain text instead (as the desktop does when pasted HTML can't be used)
      try {
        tr.doc.check();
      } catch (err) {
        console.error("A paste the document can't hold was replaced by its text", err);
        if (!tr.getMeta(PLAIN_META) && ui === "paste") this.pastePlain(this.pasteText);
        return;
      }
    }
    const command = tr.getMeta(COMMAND_META) === true;
    if (command) this.afterCommand = true; // also one that only sets the marks typed next
    else if (tr.docChanged) {
      // typing after a command is an undo step of its own, like the command
      if (this.afterCommand) closeHistory(tr);
      this.afterCommand = false;
    }
    this.view.updateState(this.view.state.apply(tr));
    this.changed();
  }

  /** The formatting pasted plain text takes at `$at` (the marks typed there; none in code). */
  private marksAt($at: ResolvedPos): readonly Mark[] {
    const set = this.view.state.storedMarks ?? $at.marks();
    return set.filter((m) => $at.parent.type.allowsMarkType(m.type));
  }

  /** Paste `text` as plain paragraphs (blank lines kept). */
  private pastePlain(text: string): void {
    if (!text) return;
    const tr = this.view.state.tr.replaceSelection(textSlice(text, this.marksAt(this.view.state.selection.$from)));
    this.view.dispatch(tr.setMeta("uiEvent", "paste").setMeta(PLAIN_META, true).scrollIntoView());
  }

  /** Picture names the document points at now (with or without their bytes). */
  private namesInUse(): Set<string> {
    const names = new Set(this.images.keys());
    this.view.state.doc.descendants((node) => {
      if (node.type === nodes.image) names.add(String(node.attrs.src));
      return !node.isTextblock || node.inlineContent;
    });
    return names;
  }

  /** A picture name nothing uses yet (also none the text points at without bytes). */
  private freeName(ext: string, taken: Set<string> = this.namesInUse()): string {
    let n = this.images.size + 1;
    while (taken.has(`img/${n}.${ext}`)) n++;
    const name = `img/${n}.${ext}`;
    taken.add(name);
    return name;
  }

  private urlFor(name: string): string {
    let url = this.urls.get(name);
    if (url === undefined) {
      const data = this.images.get(name);
      if (!data || typeof URL.createObjectURL !== "function") return "";
      const ext = name.slice(name.lastIndexOf(".") + 1).toLowerCase();
      url = URL.createObjectURL(new Blob([data as BlobPart], { type: MIME[ext] ?? "application/octet-stream" }));
      this.urls.set(name, url);
    }
    return url;
  }

  private showPicture(img: HTMLImageElement, node: PMNode): void {
    const name = String(node.attrs.src);
    img.setAttribute("data-name", name);
    img.src = this.urlFor(name);
    img.alt = "";
    for (const side of ["width", "height"] as const) {
      if (node.attrs[side]) img.setAttribute(side, String(node.attrs[side]));
      else img.removeAttribute(side);
    }
  }

  /** Pasted HTML in the subset: `data:` pictures are kept under fresh names, the web dropped,
   * and pictures of other documents (`img/N` names) dropped unless it is this editor's own copy.
   * HTML that can't be read is pasted as its plain text (`handlePaste`). */
  private transformPasted(html: string): string {
    // a drop comes here without handlePaste: no flag may be left over from one
    this.pasteFailed = false;
    let out: { html: string; images: DocImages };
    try {
      out = this.normalisePasted(html);
    } catch (err) {
      console.error("Pasted HTML couldn't be read; pasting its text", err);
      this.pasteFailed = true;
      this.pastedHtml = "";
      return "";
    }
    let fixed = out.html;
    const own = this.copiedHtml !== null && fixed === this.copiedHtml;
    if (!own) {
      const before = fixed;
      fixed = fixed.replace(IMG_TAG, (m, src: string) => (CANON_IMG.test(src) && !out.images.has(src) ? "" : m));
      if (fixed !== before) fixed = normaliseHtml(fixed).html;
    }
    if (out.images.size) {
      const taken = this.namesInUse();
      for (const m of fixed.matchAll(IMG_SRC)) taken.add(m[1]);
      const renames = new Map<string, string>();
      for (const [old, data] of out.images) {
        const name = this.freeName(old.slice(old.lastIndexOf(".") + 1), taken);
        this.images.set(name, data);
        renames.set(old, name);
      }
      fixed = fixed.replace(IMG_SRC, (m, src: string) => (renames.has(src) ? `<img src="${renames.get(src)}"` : m));
    }
    this.pastedHtml = fixed;
    return fixed;
  }

  /** Remembers the paste's plain text; pastes it when the HTML couldn't be read. */
  private handlePaste(event: ClipboardEvent): boolean {
    this.pasteText = event.clipboardData?.getData("text/plain") ?? "";
    if (this.pasteFailed) {
      this.pasteFailed = false;
      this.pastePlain(this.pasteText);
      return true;
    }
    return this.pasteFiles(event);
  }

  /** Picture files pasted on their own (a screenshot) are added like `insertImage`. */
  private pasteFiles(event: ClipboardEvent): boolean {
    const data = event.clipboardData;
    if (!data || data.getData("text/html")) return false;
    const files = Array.from(data.files ?? []).filter((f) => Object.hasOwn(PICTURE_TYPES, f.type));
    if (!files.length) return false;
    void (async () => {
      for (const file of files) {
        try {
          await this.addPicture(new Uint8Array(await file.arrayBuffer()), PICTURE_TYPES[file.type]);
        } catch (err) {
          console.error("A pasted picture couldn't be added", err);
        }
      }
    })();
    return true;
  }
}
