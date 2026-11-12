/**
 * The canonical HTML subset ↔ a ProseMirror document of `docSchema`.
 *
 * `htmlToDoc` reads subset HTML (what `normaliseHtml` writes) with the normaliser's own parser
 * and builds the document directly, so every character, empty tag and nesting order is kept.
 * `docToHtml` writes the document back in the subset's order, applies the typed-space rule (a
 * space at the start of a line or after another space becomes U+00A0, as the desktop's
 * `html_export` does) and normalises the result. For subset input the two are exact inverses.
 *
 * Both walk the tree with explicit stacks: the normaliser accepts nesting thousands of levels
 * deep (ProseMirror's own DOMParser overflows the call stack at about 3000 quotes).
 */

import { Mark, type Node as PMNode } from "prosemirror-model";
import { escAttr, escText } from "../../doceditor/pycompat";
import { normaliseHtml, parseBody } from "../../doceditor/subset";
import { restore } from "../../doceditor/subset-scan";
import { attr, colour, fontFamily, parseStyle, points, spanStyleText } from "../../doceditor/subset-values";
import { blockStyleText, docSchema, isBorderless, readBlockAttrs, readDimension } from "./schema";

const nodes = docSchema.nodes;
const marks = docSchema.marks;

// ----- subset HTML → document ------------------------------------------------------------

/** The tags around an inline leaf: its marks and their nesting, outermost first. */
interface InlineCtx {
  marks: readonly Mark[];
  tokens: string[]; // "a", "b", "i", "u", "s", or "span:" + the letters of its properties
  leaf?: readonly Mark[]; // marks + the `nest` record, made on first use
}

const SIMPLE_TAGS: Record<string, string> = { b: "bold", i: "italic", u: "underline", s: "strike" };
// A span's properties as record letters, in the subset's order.
const SPAN_PROPS: [string, string][] = [
  ["f", "font"],
  ["z", "size"],
  ["c", "color"],
  ["h", "highlight"],
];

function leafMarks(ctx: InlineCtx): readonly Mark[] {
  if (!ctx.leaf) {
    ctx.leaf =
      ctx.tokens.length >= 2
        ? marks.nest.create({ order: ctx.tokens.join(" ") }).addToSet(ctx.marks)
        : ctx.marks;
  }
  return ctx.leaf;
}

/** The context inside an inline element, or null when it adds no tag (kept transparent). */
function enterInline(el: Element, ctx: InlineCtx): InlineCtx | null {
  const tag = el.localName;
  const found: Mark[] = [];
  let token = tag;
  if (Object.hasOwn(SIMPLE_TAGS, tag)) found.push(marks[SIMPLE_TAGS[tag]].create());
  else if (tag === "a") {
    const href = attr(el, "href");
    if (href === null) return null;
    found.push(marks.link.create({ href }));
  } else if (tag === "span") {
    const css = parseStyle(attr(el, "style"));
    const family = css.has("font-family") ? fontFamily(css.get("font-family") as string) : null;
    const pt = css.has("font-size") ? points(css.get("font-size") as string) : null;
    const fg = css.has("color") ? colour(css.get("color") as string) : null;
    const bg = css.has("background-color") ? colour(css.get("background-color") as string) : null;
    token = "span:";
    if (family) (found.push(marks.font.create({ family })), (token += "f"));
    if (pt !== null && pt > 0 && pt <= 1000) (found.push(marks.size.create({ pt })), (token += "z"));
    if (fg) (found.push(marks.color.create({ rgb: fg })), (token += "c"));
    if (bg) (found.push(marks.highlight.create({ rgb: bg })), (token += "h"));
  }
  if (!found.length) return null;
  let set = ctx.marks;
  for (const m of found) set = m.addToSet(set);
  return { marks: set, tokens: [...ctx.tokens, token] };
}

const ROOT_CTX: InlineCtx = { marks: Mark.none, tokens: [] };

/** The inline content of `items` (text, line breaks, pictures, inline tags). */
function inlineNodes(items: ArrayLike<ChildNode>): PMNode[] {
  const out: PMNode[] = [];
  const stack: { items: ArrayLike<ChildNode>; i: number; ctx: InlineCtx }[] = [
    { items, i: 0, ctx: ROOT_CTX },
  ];
  while (stack.length) {
    const top = stack[stack.length - 1];
    if (top.i >= top.items.length) {
      stack.pop();
      continue;
    }
    const node = top.items[top.i++];
    if (node.nodeType === 3) {
      const text = restore((node as Text).data);
      if (text) out.push(docSchema.text(text, leafMarks(top.ctx)));
      continue;
    }
    if (node.nodeType !== 1) continue;
    const el = node as HTMLElement;
    const tag = el.localName;
    if (tag === "br") out.push(nodes.hard_break.create(null, null, leafMarks(top.ctx)));
    else if (tag === "img") {
      const src = attr(el, "src");
      if (src) {
        const dims = { width: readDimension(el, "width"), height: readDimension(el, "height") };
        out.push(nodes.image.create({ src, ...dims }, null, leafMarks(top.ctx)));
      }
    } else {
      const inner = enterInline(el, top.ctx);
      if (inner && !el.firstChild) out.push(nodes.empty_inline.create(null, null, leafMarks(inner)));
      else stack.push({ items: el.childNodes, i: 0, ctx: inner ?? top.ctx });
    }
  }
  return out;
}

const BLOCK_TAGS = new Set(
  "p h1 h2 h3 h4 h5 h6 pre blockquote ul ol li table hr div tr td th thead tbody tfoot".split(" "),
);
const isList = (n: ChildNode) => n.nodeType === 1 && ((n as Element).localName === "ul" || (n as Element).localName === "ol");
const isBlock = (n: ChildNode) => n.nodeType === 1 && BLOCK_TAGS.has((n as Element).localName);

type Mode = "blocks" | "list" | "item" | "rows" | "cells";
interface Frame {
  mode: Mode;
  items: ChildNode[];
  i: number;
  kids: PMNode[];
  make: (kids: PMNode[]) => PMNode[];
}

const frame = (mode: Mode, items: ArrayLike<ChildNode>, make: Frame["make"], kids: PMNode[] = []): Frame => ({
  mode,
  items: Array.from(items),
  i: 0,
  kids,
  make,
});

/** A paragraph of loose inline content, or nothing when it is only white space. */
function looseParagraph(run: ChildNode[]): PMNode[] {
  if (run.every((n) => n.nodeType !== 1 && !/\S/.test(n.textContent ?? ""))) return [];
  const content = inlineNodes(run);
  return content.length ? [nodes.paragraph.create(null, content)] : [];
}

/** The rows of a table element (inside thead / tbody / tfoot too). */
function tableRows(table: Element): Element[] {
  const rows: Element[] = [];
  for (const c of Array.from(table.children)) {
    if (c.localName === "tr") rows.push(c);
    else if (c.localName === "thead" || c.localName === "tbody" || c.localName === "tfoot")
      for (const r of Array.from(c.children)) if (r.localName === "tr") rows.push(r);
  }
  return rows;
}

function span(el: Element, name: string): number {
  const m = (attr(el, name) ?? "").match(/^\s*(\d+)\s*$/);
  const n = m ? Number(m[1]) : 1;
  return n > 1 && n <= 1000 ? n : 1;
}

/** A list item frame: its own line (before the first nested list) is the first paragraph. */
function itemFrame(li: Element): Frame {
  const all = Array.from(li.childNodes);
  let cut = all.findIndex(isList);
  if (cut < 0) cut = all.length;
  const line = nodes.paragraph.create(readBlockAttrs(li as HTMLElement), inlineNodes(all.slice(0, cut)));
  return frame("item", all.slice(cut), (kids) => [nodes.list_item.create(null, kids)], [line]);
}

/** Read one child of a frame: a finished block goes into `top.kids`, a container is returned. */
function step(top: Frame, child: ChildNode): Frame | null {
  const el = child as HTMLElement;
  const tag = child.nodeType === 1 ? el.localName : "";
  if (top.mode === "list") {
    return child.nodeType === 1 ? itemFrame(el) : null; // text between items is formatting
  }
  if (top.mode === "rows") {
    return tag === "tr"
      ? frame("cells", el.children, (kids) => (kids.length ? [nodes.table_row.create(null, kids)] : []))
      : null;
  }
  if (top.mode === "cells") {
    if (tag !== "td" && tag !== "th") return null;
    const attrs = { colspan: span(el, "colspan"), rowspan: span(el, "rowspan") };
    return frame("blocks", el.childNodes, (kids) => [
      nodes.table_cell.create(attrs, kids.length ? kids : nodes.paragraph.create()),
    ]);
  }
  // "blocks" and "item": loose inline content becomes a paragraph
  if (!isBlock(child)) {
    const run = [child];
    while (top.i < top.items.length && !isBlock(top.items[top.i])) run.push(top.items[top.i++]);
    top.kids.push(...looseParagraph(run));
    return null;
  }
  if (tag === "p") top.kids.push(nodes.paragraph.create(readBlockAttrs(el), inlineNodes(el.childNodes)));
  else if (/^h[1-6]$/.test(tag)) {
    const level = Math.min(Number(tag[1]), 3);
    top.kids.push(nodes.heading.create({ level, ...readBlockAttrs(el) }, inlineNodes(el.childNodes)));
  } else if (tag === "pre") top.kids.push(nodes.code_block.create(null, inlineNodes(el.childNodes)));
  else if (tag === "hr") top.kids.push(nodes.horizontal_rule.create());
  else if (tag === "blockquote") return frame("blocks", el.childNodes, (kids) => [nodes.blockquote.create(null, kids)]);
  else if (tag === "ul" || tag === "ol") {
    const type = tag === "ul" ? nodes.bullet_list : nodes.ordered_list;
    return frame("list", el.children, (kids) => [type.create(null, kids)]);
  } else if (tag === "li") return itemFrame(el);
  else if (tag === "table") {
    const borderless = isBorderless(el);
    return frame("rows", tableRows(el), (kids) => (kids.length ? [nodes.table.create({ borderless }, kids)] : []));
  } else return frame("blocks", el.childNodes, (kids) => kids); // a div: its blocks
  return null;
}

const isPlainEmpty = (node: PMNode) =>
  node.type === nodes.paragraph && node.content.size === 0 && !blockStyleText(node.attrs);

/** Subset HTML (`normaliseHtml` output) as a document of `docSchema`. */
export function htmlToDoc(html: string): PMNode {
  const body = parseBody(html);
  const blocks: PMNode[] = [];
  if (body) {
    const stack: Frame[] = [frame("blocks", body.childNodes, (kids) => kids)];
    while (stack.length) {
      const top = stack[stack.length - 1];
      if (top.i < top.items.length) {
        const inner = step(top, top.items[top.i++]);
        if (inner) stack.push(inner);
        continue;
      }
      stack.pop();
      const made = top.make(top.kids);
      const parent = stack.length ? stack[stack.length - 1].kids : blocks;
      for (const n of made) parent.push(n);
    }
  }
  const emptyParagraph = blocks.length === 1 && isPlainEmpty(blocks[0]);
  return nodes.doc.create({ emptyParagraph }, blocks.length ? blocks : nodes.paragraph.create());
}

// ----- document → subset HTML ------------------------------------------------------------

interface Wrap {
  name: string;
  open: string;
  close: string;
}

const NBSP = " ";
const OBJECT = "￼"; // what a picture or an empty link is to the typed-space rule
const SIMPLE_WRAPS: Record<string, Wrap> = Object.fromEntries(
  ["b", "i", "u", "s"].map((t) => [t, { name: t, open: `<${t}>`, close: `</${t}>` }]),
);
const MARK_TAG: Record<string, string> = { bold: "b", italic: "i", underline: "u", strike: "s" };

/** The tags a leaf with `set` is wrapped in, outermost first: as recorded by its `nest` mark,
 * else the tags already open first; then the rest in the default order a, span, b, i, u, s. */
function wrapsFor(set: readonly Mark[], stack: Wrap[]): Wrap[] {
  let href: string | null = null;
  let order: string | null = null;
  const props: Record<string, unknown> = {};
  const simple = new Set<string>();
  for (const m of set) {
    const name = m.type.name;
    if (name === "link") href = String(m.attrs.href);
    else if (name === "nest") order = String(m.attrs.order);
    else if (name === "font") props.f = m.attrs.family;
    else if (name === "size") props.z = m.attrs.pt;
    else if (name === "color") props.c = m.attrs.rgb;
    else if (name === "highlight") props.h = m.attrs.rgb;
    else if (Object.hasOwn(MARK_TAG, name)) simple.add(MARK_TAG[name]);
  }
  const link = (): Wrap => ({ name: "a", open: `<a href="${escAttr(href as string)}">`, close: "</a>" });
  const spanOf = (letters: string[]): Wrap | null => {
    const has = (l: string) => letters.includes(l) && props[l] != null;
    const style = spanStyleText(
      has("f") ? String(props.f) : null,
      has("z") ? Number(props.z) : null,
      has("c") ? String(props.c) : null,
      has("h") ? String(props.h) : null,
    );
    return style ? { name: "span", open: `<span style="${escAttr(style)}">`, close: "</span>" } : null;
  };
  const first: Wrap[] = [];
  const used = new Set<string>(); // tags and span letters written by the record
  if (order !== null) {
    for (const tok of order.split(" ")) {
      let w: Wrap | null = null;
      if (tok === "a" && href !== null) w = link();
      else if (tok.startsWith("span:")) {
        const letters = [...tok.slice(5)].filter((l) => props[l] != null);
        w = letters.length ? spanOf(letters) : null;
        for (const l of letters) used.add(l);
      } else if (simple.has(tok)) w = SIMPLE_WRAPS[tok];
      if (w) {
        first.push(w);
        used.add(tok);
      }
    }
  }
  const rest: Wrap[] = [];
  if (href !== null && !used.has("a")) rest.push(link());
  const left = SPAN_PROPS.map(([l]) => l).filter((l) => props[l] != null && !used.has(l));
  const sp = left.length ? spanOf(left) : null;
  if (sp) rest.push(sp);
  for (const t of ["b", "i", "u", "s"]) if (simple.has(t) && !used.has(t)) rest.push(SIMPLE_WRAPS[t]);
  if (order === null) {
    for (const w of stack) {
      const k = rest.findIndex((r) => r.open === w.open);
      if (k < 0) break;
      first.push(rest.splice(k, 1)[0]);
    }
  }
  return first.concat(rest);
}

/** The typed-space rule: a space at the start of a line or after a plain space becomes a
 * no-break space (a space after a no-break space is kept). Returns [text, last character]. */
function keepSpaces(text: string, before: string): [string, string] {
  let out = "";
  for (const c of text) {
    out += c === " " && (before === "" || before === " ") ? NBSP : c;
    before = c;
  }
  return [out, before];
}

/** The inline HTML of a textblock. */
function inlineHtml(block: PMNode, code: boolean): string {
  const out: string[] = [];
  const stack: Wrap[] = []; // the tags open now, outermost first
  let fresh = Infinity; // after an empty tag: how many open tags the next leaf may stay in
  let before = ""; // the last character, for the typed-space rule ("" = start of a line)

  const write = (wraps: Wrap[], body: string, cap: number) => {
    const limit = Math.min(cap, fresh, wraps.length);
    let keep = 0;
    while (keep < limit && keep < stack.length && stack[keep].open === wraps[keep].open) keep++;
    while (stack.length > keep) out.push((stack.pop() as Wrap).close);
    for (const w of wraps.slice(keep)) {
      out.push(w.open);
      stack.push(w);
    }
    out.push(body);
  };

  block.forEach((child) => {
    const wraps = wrapsFor(child.marks, stack);
    if (child.isText) {
      let text = child.text as string;
      if (!code) [text, before] = keepSpaces(text, before);
      write(wraps, escText(text), Infinity);
      fresh = Infinity;
    } else if (child.type === nodes.hard_break) {
      write(wraps, "<br>", Infinity);
      fresh = Infinity;
      before = "";
    } else if (child.type === nodes.image) {
      const { src, width, height } = child.attrs;
      let tag = `<img src="${escAttr(String(src))}"`;
      if (width) tag += ` width="${Math.round(width)}"`;
      if (height) tag += ` height="${Math.round(height)}"`;
      write(wraps, `${tag}>`, Infinity);
      fresh = Infinity;
      before = OBJECT;
    } else if (child.type === nodes.empty_inline && wraps.length) {
      // its innermost tag is the empty element: opened afresh, and closed before what follows
      const cap = wraps.length - 1;
      write(wraps, "", cap);
      fresh = cap;
      if (wraps[cap].name === "a") before = OBJECT; // the space after an empty link is kept
    }
  });
  while (stack.length) out.push((stack.pop() as Wrap).close);
  return out.join("");
}

const styleAttr = (node: PMNode) => {
  const style = blockStyleText(node.attrs);
  return style ? ` style="${style}"` : "";
};

/** The document as the editor's (normalised) HTML subset. */
export function docToHtml(doc: PMNode): string {
  if (doc.childCount === 1 && isPlainEmpty(doc.child(0))) {
    return doc.attrs.emptyParagraph ? "<p></p>" : ""; // an emptied document is written as nothing
  }
  const out: string[] = [];
  const todo: (PMNode | string)[] = []; // a stack: closing tags and nodes still to write
  const children = (node: PMNode, from = 0) => {
    for (let i = node.childCount - 1; i >= from; i--) todo.push(node.child(i));
  };
  const container = (node: PMNode, open: string, close: string, from = 0) => {
    out.push(open);
    todo.push(close);
    children(node, from);
  };
  children(doc);
  while (todo.length) {
    const item = todo.pop() as PMNode | string;
    if (typeof item === "string") {
      out.push(item);
      continue;
    }
    switch (item.type) {
      case nodes.paragraph:
        out.push(`<p${styleAttr(item)}>${inlineHtml(item, false)}</p>`);
        break;
      case nodes.heading: {
        const tag = `h${Math.min(Math.max(Number(item.attrs.level) || 1, 1), 3)}`;
        out.push(`<${tag}${styleAttr(item)}>${inlineHtml(item, false)}</${tag}>`);
        break;
      }
      case nodes.code_block:
        out.push(`<pre>${inlineHtml(item, true)}</pre>`);
        break;
      case nodes.horizontal_rule:
        out.push("<hr>");
        break;
      case nodes.blockquote:
        container(item, "<blockquote>", "</blockquote>");
        break;
      case nodes.bullet_list:
        container(item, "<ul>", "</ul>");
        break;
      case nodes.ordered_list:
        container(item, "<ol>", "</ol>");
        break;
      case nodes.list_item: {
        const line = item.firstChild;
        if (line && line.type === nodes.paragraph) {
          container(item, `<li${styleAttr(line)}>${inlineHtml(line, false)}`, "</li>", 1);
        } else container(item, "<li>", "</li>");
        break;
      }
      case nodes.table:
        container(item, item.attrs.borderless ? '<table style="border:none">' : "<table>", "</table>");
        break;
      case nodes.table_row:
        container(item, "<tr>", "</tr>");
        break;
      case nodes.table_cell:
      case nodes.table_header: {
        let spans = "";
        if (item.attrs.colspan > 1) spans += ` colspan="${item.attrs.colspan}"`;
        if (item.attrs.rowspan > 1) spans += ` rowspan="${item.attrs.rowspan}"`;
        container(item, `<td${spans}>`, "</td>");
        break;
      }
      default:
        if (item.isTextblock) out.push(`<p>${inlineHtml(item, false)}</p>`);
        else children(item);
    }
  }
  return normaliseHtml(out.join("")).html;
}
