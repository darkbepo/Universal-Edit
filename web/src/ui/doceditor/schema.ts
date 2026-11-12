/**
 * The website document editor's ProseMirror schema: everything the canonical HTML subset
 * (`doceditor/subset.ts`, the desktop's `html_subset.py` docstring) can hold, without loss.
 *
 * Blocks: `paragraph` / `heading` (1–3) with alignment, indent (`margin-left: 36·n pt`) and a
 * page break before; `blockquote` (any blocks, also none); `code_block` (one `<pre>`, every
 * character kept, inline formatting, line breaks and pictures allowed); lists (`list_item` =
 * its own line as a paragraph, then only nested lists); prosemirror-tables nodes (`table.borderless`
 * = `<table style="border:none">`, cells with colspan / rowspan); `horizontal_rule`.
 * Inline: `text`, `hard_break`, `image` {src, width, height} and `empty_inline` — an inline tag
 * with nothing in it (`<a href="u"></a>`, a badge whose picture was dropped, or `<b></b>`): its
 * marks say which tags it is, since ProseMirror marks need content.
 * Marks: link, font, size, color, highlight (one `<span style>` in the subset), bold, italic,
 * underline, strike, and `nest` — how the source nested a leaf's tags, outermost first ("b a",
 * "span:z,c"; an outer span's value that an inner span overrides is kept in it, "span:c=%23000000
 * span:c"), because a mark set has a fixed order but the subset keeps the source's.
 *
 * The parse rules here also read pasted HTML; `convert.ts` reads the subset itself.
 */

import {
  Schema,
  type DOMOutputSpec,
  type MarkSpec,
  type Node as PMNode,
  type NodeSpec,
} from "prosemirror-model";
import { tableNodes } from "prosemirror-tables";
import { pyRound, pyStrip } from "../../doceditor/pycompat";
import { colour, fontFamily, parseStyle, points } from "../../doceditor/subset-values";

/** Deepest indent level (720 pt), as in the normaliser. */
export const MAX_INDENT = 20;
const ALIGNS = ["left", "center", "right", "justify"];

export interface BlockAttrs {
  align: string | null;
  indent: number;
  pageBreak: boolean;
}

/** A block's alignment, indent and page break, read the way the normaliser reads them. */
export function readBlockAttrs(dom: HTMLElement): BlockAttrs {
  const css = parseStyle(dom.getAttribute("style"));
  const align = pyStrip(css.get("text-align") || dom.getAttribute("align") || "").toLowerCase();
  let indent = 0;
  if (css.has("margin-left")) {
    const pts = points(css.get("margin-left") as string);
    const level = pts === null ? 0 : pyRound(pts / 36);
    if (level >= 1) indent = Math.min(level, MAX_INDENT);
  }
  const brk = (css.get("page-break-before") || css.get("break-before") || "").toLowerCase();
  return {
    align: ALIGNS.includes(align) ? align : null,
    indent,
    pageBreak: brk === "always" || brk === "page",
  };
}

/** The subset's style text for those attributes ("" when there are none). */
export function blockStyleText(attrs: { align?: unknown; indent?: unknown; pageBreak?: unknown }): string {
  const parts: string[] = [];
  if (typeof attrs.align === "string" && ALIGNS.includes(attrs.align)) parts.push(`text-align:${attrs.align}`);
  const indent = Number(attrs.indent) || 0;
  if (indent >= 1) parts.push(`margin-left:${36 * Math.min(Math.round(indent), MAX_INDENT)}pt`);
  if (attrs.pageBreak) parts.push("page-break-before:always");
  return parts.join(";");
}

const blockAttrSpec = {
  align: { default: null },
  indent: { default: 0 },
  pageBreak: { default: false },
};

const styled = (attrs: PMNode["attrs"]): Record<string, string> => {
  const style = blockStyleText(attrs);
  return style ? { style } : {};
};

/** `<table style="border:none">` (or `border="0"`), as the normaliser decides it. */
export function isBorderless(dom: HTMLElement): boolean {
  const css = parseStyle(dom.getAttribute("style"));
  const border = (css.get("border") || css.get("border-style") || "").toLowerCase();
  return ["none", "0", "0px", "hidden"].includes(border) || dom.getAttribute("border") === "0";
}

/** A picture's width or height attribute, as the normaliser keeps it (null: none). */
export function readDimension(dom: HTMLElement, name: string): number | null {
  const css = parseStyle(dom.getAttribute("style"));
  const raw = dom.getAttribute(name) || css.get(name) || "";
  const m = raw.match(/^\s*(\d+)(?:px)?\s*$/);
  const n = m ? Number(m[1]) : 0;
  return n > 0 && n <= 100000 ? n : null;
}

const tables = tableNodes({ tableGroup: "block", cellContent: "block+", cellAttributes: {} });

const nodes: Record<string, NodeSpec> = {
  doc: {
    content: "block+",
    // Loaded from exactly one empty paragraph: written back as "<p></p>" rather than as an
    // empty document (what an editor emptied by the user writes, like the desktop).
    attrs: { emptyParagraph: { default: false } },
  },
  paragraph: {
    group: "block",
    content: "inline*",
    attrs: blockAttrSpec,
    parseDOM: [{ tag: "p", getAttrs: (dom) => readBlockAttrs(dom as HTMLElement) }],
    toDOM: (node): DOMOutputSpec => ["p", styled(node.attrs), 0],
  },
  heading: {
    group: "block",
    content: "inline*",
    defining: true,
    attrs: { level: { default: 1 }, ...blockAttrSpec },
    parseDOM: [1, 2, 3, 4, 5, 6].map((n) => ({
      tag: `h${n}`,
      getAttrs: (dom: HTMLElement) => ({ level: Math.min(n, 3), ...readBlockAttrs(dom) }),
    })),
    toDOM: (node): DOMOutputSpec => [`h${node.attrs.level}`, styled(node.attrs), 0],
  },
  blockquote: {
    group: "block",
    content: "block*",
    defining: true,
    parseDOM: [{ tag: "blockquote" }],
    toDOM: (): DOMOutputSpec => ["blockquote", 0],
  },
  code_block: {
    group: "block",
    content: "inline*",
    code: true,
    defining: true,
    whitespace: "pre",
    parseDOM: [{ tag: "pre", preserveWhitespace: "full" }],
    toDOM: (): DOMOutputSpec => ["pre", 0],
  },
  bullet_list: {
    group: "block",
    content: "list_item*",
    parseDOM: [{ tag: "ul" }],
    toDOM: (): DOMOutputSpec => ["ul", 0],
  },
  ordered_list: {
    group: "block",
    content: "list_item*",
    parseDOM: [{ tag: "ol" }],
    toDOM: (): DOMOutputSpec => ["ol", 0],
  },
  // Its first paragraph is the item's own line (its style is the <li>'s), then nested lists:
  // the only shape the subset has, so pasting or inserting other blocks puts them after the list.
  list_item: {
    content: "paragraph (bullet_list | ordered_list)*",
    defining: true,
    parseDOM: [{ tag: "li" }],
    toDOM: (): DOMOutputSpec => ["li", 0],
  },
  table: {
    ...tables.table,
    attrs: { borderless: { default: false } },
    parseDOM: [{ tag: "table", getAttrs: (dom) => ({ borderless: isBorderless(dom as HTMLElement) }) }],
    toDOM: (node): DOMOutputSpec => [
      "table",
      node.attrs.borderless ? { style: "border:none" } : {},
      ["tbody", 0],
    ],
  },
  // No header cells (the subset writes every cell as <td>) and no empty rows.
  table_row: { ...tables.table_row, content: "table_cell+" },
  table_cell: {
    ...tables.table_cell,
    // a pasted <th> becomes a plain cell (same attributes)
    parseDOM: [...(tables.table_cell.parseDOM ?? []), ...(tables.table_header.parseDOM ?? [])],
  },
  horizontal_rule: {
    group: "block",
    parseDOM: [{ tag: "hr" }],
    toDOM: (): DOMOutputSpec => ["hr"],
  },
  text: { group: "inline" },
  hard_break: {
    inline: true,
    group: "inline",
    selectable: false,
    leafText: () => "\n",
    parseDOM: [{ tag: "br" }],
    toDOM: (): DOMOutputSpec => ["br"],
  },
  image: {
    inline: true,
    group: "inline",
    draggable: true,
    attrs: { src: {}, width: { default: null }, height: { default: null } },
    parseDOM: [
      {
        tag: "img[src]",
        getAttrs: (dom) => {
          const el = dom as HTMLElement;
          return {
            src: el.getAttribute("src"),
            width: readDimension(el, "width"),
            height: readDimension(el, "height"),
          };
        },
      },
    ],
    toDOM: (node): DOMOutputSpec => {
      const attrs: Record<string, string> = { src: node.attrs.src };
      if (node.attrs.width) attrs.width = String(node.attrs.width);
      if (node.attrs.height) attrs.height = String(node.attrs.height);
      return ["img", attrs];
    },
  },
  empty_inline: {
    inline: true,
    group: "inline",
    atom: true,
    selectable: false,
    leafText: () => "",
    parseDOM: [{ tag: "span[data-umc-empty]" }],
    toDOM: (): DOMOutputSpec => ["span", { "data-umc-empty": "" }],
  },
};

const styleMark = (
  prop: string,
  attr: string,
  read: (value: string) => string | number | null,
  write: (value: unknown) => string,
): MarkSpec => ({
  attrs: { [attr]: {} },
  parseDOM: [
    {
      style: prop,
      getAttrs: (value) => {
        const v = read(value as string);
        return v === null ? false : { [attr]: v };
      },
    },
  ],
  toDOM: (mark): DOMOutputSpec => ["span", { style: `${prop}:${write(mark.attrs[attr])}` }, 0],
});

const textDecoration = (word: string) => (value: string) =>
  value.toLowerCase().includes(word) ? null : false;

// The order is the subset's default nesting, outermost first: a, span, b, i, u, s.
const marks: Record<string, MarkSpec> = {
  link: {
    attrs: { href: {} },
    inclusive: false,
    parseDOM: [{ tag: "a[href]", getAttrs: (dom) => ({ href: (dom as HTMLElement).getAttribute("href") }) }],
    toDOM: (mark): DOMOutputSpec => ["a", { href: mark.attrs.href }, 0],
  },
  font: styleMark("font-family", "family", fontFamily, String),
  size: styleMark(
    "font-size",
    "pt",
    (v) => {
      const pt = points(v);
      return pt !== null && pt > 0 && pt <= 1000 ? pt : null;
    },
    (v) => `${v}pt`,
  ),
  color: styleMark("color", "rgb", colour, String),
  highlight: styleMark("background-color", "rgb", colour, String),
  bold: {
    parseDOM: [
      { tag: "b" },
      { tag: "strong" },
      {
        style: "font-weight",
        getAttrs: (v) => {
          const w = (v as string).toLowerCase();
          return w === "bold" || w === "bolder" || (/^\d+$/.test(w) && Number(w) >= 600) ? null : false;
        },
      },
    ],
    toDOM: (): DOMOutputSpec => ["b", 0],
  },
  italic: {
    parseDOM: [
      { tag: "i" },
      { tag: "em" },
      { tag: "cite" },
      { style: "font-style", getAttrs: (v) => ((v as string).toLowerCase() === "italic" ? null : false) },
    ],
    toDOM: (): DOMOutputSpec => ["i", 0],
  },
  underline: {
    parseDOM: [
      { tag: "u" },
      { tag: "ins" },
      { style: "text-decoration", getAttrs: textDecoration("underline"), consuming: false },
      { style: "text-decoration-line", getAttrs: textDecoration("underline"), consuming: false },
    ],
    toDOM: (): DOMOutputSpec => ["u", 0],
  },
  strike: {
    parseDOM: [
      { tag: "s" },
      { tag: "strike" },
      { tag: "del" },
      { style: "text-decoration", getAttrs: textDecoration("line-through") },
      { style: "text-decoration-line", getAttrs: textDecoration("line-through") },
    ],
    toDOM: (): DOMOutputSpec => ["s", 0],
  },
  nest: {
    attrs: { order: {} },
    parseDOM: [
      { tag: "span[data-umc-nest]", getAttrs: (dom) => ({ order: (dom as HTMLElement).getAttribute("data-umc-nest") }) },
    ],
    toDOM: (mark): DOMOutputSpec => ["span", { "data-umc-nest": mark.attrs.order }, 0],
  },
};

export const docSchema = new Schema({ nodes, marks });
