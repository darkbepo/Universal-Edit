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
import { EditorState, Plugin, TextSelection, type Command, type Transaction } from "prosemirror-state";
import { AddMarkStep, AddNodeMarkStep, RemoveMarkStep, RemoveNodeMarkStep } from "prosemirror-transform";
import { EditorView } from "prosemirror-view";
import { baseKeymap } from "prosemirror-commands";
import { closeHistory, history, redo, undo } from "prosemirror-history";
import { keymap } from "prosemirror-keymap";
import { splitListItem } from "prosemirror-schema-list";
import { search } from "prosemirror-search";
import { goToNextCell, isInTable, tableEditing } from "prosemirror-tables";
import { normaliseHtml } from "../../doceditor/subset";
import type { DocImages, OpenedDocument, SaveContent } from "../../doceditor/types";
import {
  COMMAND_META,
  changeIndent,
  clearFormatting,
  insertImage,
  insertPageBreak,
  selectedTextblocks,
  setAlign,
  setBlockStyle,
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

/** The ranges of the final document that `trs` changed (marks included). */
function changedRanges(trs: readonly Transaction[], size: number): [number, number][] {
  const out: [number, number][] = [];
  trs.forEach((tr, t) => {
    tr.steps.forEach((step, i) => {
      const local: [number, number][] = [];
      step.getMap().forEach((_a, _b, from, to) => local.push([from, to]));
      if (step instanceof AddMarkStep || step instanceof RemoveMarkStep) local.push([step.from, step.to]);
      else if (step instanceof AddNodeMarkStep || step instanceof RemoveNodeMarkStep) local.push([step.pos, step.pos + 1]);
      const rest = tr.mapping.slice(i + 1);
      for (const range of local) {
        let [a, b] = [rest.map(range[0], -1), rest.map(range[1], 1)];
        for (const later of trs.slice(t + 1)) [a, b] = [later.mapping.map(a, -1), later.mapping.map(b, 1)];
        out.push([Math.max(0, a), Math.min(size, b)]);
      }
    });
  });
  out.sort((x, y) => x[0] - y[0]);
  const merged: [number, number][] = [];
  for (const r of out) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push(r);
  }
  return merged;
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

const hardBreak: Command = (state, dispatch) => {
  if (state.selection.$from.parent.type === nodes.code_block) {
    dispatch?.(state.tr.insertText("\n").scrollIntoView());
  } else dispatch?.(state.tr.replaceSelectionWith(nodes.hard_break.create()).scrollIntoView());
  return true;
};

/** Tab: the next cell in a table; a list line's level at its start (or over a selection);
 * else a tab character. Shift+Tab goes back. */
const tab =
  (dir: 1 | -1): Command =>
  (state, dispatch) => {
    if (isInTable(state)) {
      goToNextCell(dir)(state, dispatch);
      return true;
    }
    const sel = state.selection;
    const lines = selectedTextblocks(state);
    const inList = lines.length > 0 && lines.every((b) => state.doc.resolve(b.pos).parent.type === nodes.list_item);
    if (inList && (!sel.empty || sel.$from.parentOffset === 0)) {
      changeIndent(dir)(state, dispatch);
      return true;
    }
    if (dir < 0) return false;
    dispatch?.(state.tr.insertText("\t").scrollIntoView());
    return true;
  };

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
  "Shift-Enter": hardBreak,
  Enter: splitListItem(nodes.list_item),
  Tab: tab(1),
  "Shift-Tab": tab(-1),
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

function lineText(block: PMNode): string {
  let out = "";
  block.forEach((child) => {
    if (child.isText) out += (child.text as string).replace(NBSP_ALL, " ");
    else if (child.type === nodes.hard_break) out += "\n";
  });
  return block.type === nodes.code_block && out.endsWith("\n") ? out.slice(0, -1) : out;
}

/** The document as plain text, as the desktop's QTextDocument.toPlainText gives it (no-break
 * spaces as spaces, pictures and empty tags left out, an empty line after every table and
 * before one that starts the page or a cell), with a final line break ("" when empty). */
export function plainText(doc: PMNode): string {
  if (doc.childCount === 1 && doc.child(0).type === nodes.paragraph && !doc.child(0).content.size) return "";
  const lines: string[] = [];
  const frame = (container: PMNode) => {
    const start = lines.length;
    const walk = (node: PMNode) => {
      node.forEach((child) => {
        if (child.isTextblock) lines.push(lineText(child));
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

/** Writes the copied part as subset HTML (`docToHtml`). */
class SubsetSerializer extends DOMSerializer {
  constructor() {
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
    holder.innerHTML = docToHtml(doc);
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
const unescapeAttr = (s: string) =>
  s.replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

export class DocView {
  readonly view: EditorView;
  images: DocImages = new Map();
  /** Called after every change of the document or the selection. */
  onChange: () => void = () => {};
  private clean: PMNode;
  private frontMatter = "";
  private title = "";
  private template: Uint8Array | null = null;
  private readonly urls = new Map<string, string>();
  private afterCommand = false;
  private pastedHtml: string | null = null;
  private readonly serializer = new SubsetSerializer();

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
      handlePaste: (_view, event) => this.pasteFiles(event),
    });
  }

  /** Whether the document differs from the one loaded (or last marked clean). */
  get dirty(): boolean {
    return !this.view.state.doc.eq(this.clean);
  }

  /** The document now counts as saved (`doc`: the one that was saved, if it changed since). */
  markClean(doc: PMNode = this.view.state.doc): void {
    this.clean = doc;
    this.onChange();
  }

  /** Show `opened` (normalised first, as on the desktop); history and the dirty flag start over. */
  load(opened: OpenedDocument): void {
    for (const url of this.urls.values()) URL.revokeObjectURL(url);
    this.urls.clear();
    this.images = new Map(opened.images);
    this.frontMatter = opened.frontMatter;
    this.title = opened.title;
    this.template = opened.template;
    const doc = htmlToDoc(normaliseHtml(opened.html).html);
    this.clean = doc;
    this.afterCommand = false;
    this.view.updateState(createState(doc));
    this.onChange();
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

  /** What the clipboard gets for `slice` (as `text/html`). */
  clipboardHtml(slice: Slice): string {
    const holder = document.createElement("div");
    holder.appendChild(this.serializer.serializeFragment(slice.content, { document }));
    return holder.innerHTML;
  }

  private dispatch(tr: Transaction): void {
    const ui = tr.getMeta("uiEvent");
    if (tr.docChanged && (ui === "paste" || ui === "drop")) {
      // ProseMirror's fitting can leave an empty table cell for some pasted slices
      try {
        tr.doc.check();
      } catch (err) {
        console.error("A paste the document can't hold was left out", err);
        return;
      }
    }
    if (tr.docChanged) {
      const command = tr.getMeta(COMMAND_META) === true;
      // typing after a command is an undo step of its own, like the command
      if (!command && this.afterCommand) closeHistory(tr);
      this.afterCommand = command;
    }
    this.view.updateState(this.view.state.apply(tr));
    this.onChange();
  }

  private freeName(ext: string): string {
    let n = this.images.size + 1;
    while (this.images.has(`img/${n}.${ext}`)) n++;
    return `img/${n}.${ext}`;
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

  /** Pasted HTML in the subset: `data:` pictures are kept under fresh names, the web dropped. */
  private transformPasted(html: string): string {
    let out: { html: string; images: DocImages };
    try {
      out = normaliseHtml(html);
    } catch (err) {
      console.error("Pasted HTML couldn't be read", err);
      this.pastedHtml = "";
      return "";
    }
    const renames = new Map<string, string>();
    for (const [old, data] of out.images) {
      const name = this.freeName(old.slice(old.lastIndexOf(".") + 1));
      this.images.set(name, data);
      renames.set(old, name);
    }
    const fixed = renames.size
      ? out.html.replace(IMG_SRC, (m, src: string) => (renames.has(src) ? `<img src="${renames.get(src)}"` : m))
      : out.html;
    this.pastedHtml = fixed;
    return fixed;
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
