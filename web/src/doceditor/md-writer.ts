/**
 * Write the editor's HTML subset as Markdown (port of the desktop's `md_writer.py`, byte for
 * byte). The output is read back by `markdown.ts` (markdown-it: CommonMark with tables and
 * strikethrough) to the same subset, so saving a Markdown file over itself never changes it.
 * What Markdown has no syntax for is written as inline HTML (`<u>`, `<s>`, `<br>`, styled
 * `<span>`, `<a href>` for addresses markdown-it would rewrite). Blocks with alignment, an indent
 * or a page break, and tables a pipe table can't hold, are written as one line of raw HTML
 * (alignment as the `align` attribute GitHub shows); an empty paragraph is `<span></span>`.
 */

import { normalizeLink, validateLink } from "./markdown";
import { PY_WS, escAttr, escText, isPySpace, pyStrip } from "./pycompat";
import { P_MARK, html5Refs, libxmlLike, restore } from "./subset-scan";

const NBSP = "\xa0";

// Escaped wherever they appear in text (CommonMark reads "\x" back as "x").
const ESCAPES: Record<string, string> = {
  "\\": "\\\\",
  "`": "\\`",
  "*": "\\*",
  _: "\\_",
  "[": "\\[",
  "]": "\\]",
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  "\t": "&#9;", // Markdown would read a raw tab as spaces
};
const ESCAPED = /[\\`*_[\]&<>\t]/g;
const ESCAPED_TABLE = /[\\`*_[\]&<>\t|]/g;
// At the start of a line these would start a heading, quote, list, rule or code fence.
const LINE_START = new RegExp(`^(?:[#>+=~-]|\\p{Nd}+[.)](?=[${PY_WS}]|$))`, "u");
const TASK = /^(\[[ xX]\])(?: +|(?=\n?$))/;
const TRAILING_HASHES = /(#+)(?=\n?$)/g;
const TARGET_NEEDS_BRACKETS = new RegExp(`[${PY_WS}()]`);
// markdown-it decodes entity references in a link's address.
const ENTITY_LIKE = /&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);/;
const VOID = new Set(["br", "img", "hr"]);
const STYLED_BLOCKS = new Set(["p", "h1", "h2", "h3", "li"]);

const tagOf = (node: Node): string =>
  node.nodeType === 1 ? (node as Element).localName.toLowerCase() : "";
const textOf = (node: Node): string => restore((node as Text).data);

function escape(text: string, table: boolean): string {
  return text.replace(table ? ESCAPED_TABLE : ESCAPED, (c) => (c === "|" ? "\\|" : ESCAPES[c]));
}

/** Python's str.strip(" "). */
function stripSpaces(s: string): string {
  let start = 0;
  let end = s.length;
  while (start < end && s[start] === " ") start++;
  while (end > start && s[end - 1] === " ") end--;
  return s.slice(start, end);
}

/** Keep the start of a paragraph line from being read as Markdown syntax. */
function lineStart(text: string): string {
  let i = 0;
  const lead: string[] = [];
  while (i < text.length && (text[i] === NBSP || text[i] === "\t" || text[i] === " ")) {
    lead.push(text[i] === "\t" ? "&#9;" : "&nbsp;"); // would become indented code
    i++;
  }
  let rest = text.slice(i);
  const m = LINE_START.exec(rest);
  if (m) {
    const first = m[0];
    if (first.length > 1 || /\p{Nd}/u.test(first)) {
      // "1." -> "1\."
      rest = first.slice(0, -1) + "\\" + first.slice(-1) + rest.slice(first.length);
    } else {
      rest = `&#${first.codePointAt(0)};` + rest.slice(1);
    }
  }
  return lead.join("") + rest;
}

/** lxml's `node.text`: the text before the first child element. */
function leadingText(node: Node): string {
  let s = "";
  for (let c = node.firstChild; c && c.nodeType !== 1; c = c.nextSibling) {
    if (c.nodeType === 3) s += textOf(c);
  }
  return s;
}

/** lxml's `node.tail`: the text after the element, up to the next element. */
function tailText(node: Node): string {
  let s = "";
  for (let c = node.nextSibling; c && c.nodeType !== 1; c = c.nextSibling) {
    if (c.nodeType === 3) s += textOf(c);
  }
  return s;
}

function linkTarget(url: string): string {
  url = url.replaceAll("<", "%3C").replaceAll(">", "%3E").replaceAll(" ", "%20"); // as markdown-it reads it
  return TARGET_NEEDS_BRACKETS.test(url) ? `<${url}>` : url;
}

/** Inline content of one block as one Markdown line. */
class Inline {
  constructor(private readonly table = false) {}

  render(node: Node): string {
    return lineStart(stripSpaces(this.children(node)));
  }

  private children(node: Node): string {
    const parts: string[] = [];
    for (let c = node.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3) parts.push(escape(textOf(c), this.table));
      else if (c.nodeType === 1) parts.push(this.node(c as Element));
    }
    return parts.join("");
  }

  private node(node: Element): string {
    const tag = tagOf(node);
    if (tag === "br") return "<br>";
    if (tag === "img") {
      if (node.getAttribute("width") || node.getAttribute("height")) {
        // Markdown has no size: raw HTML
        const attrs = ["src", "width", "height"]
          .filter((k) => node.getAttribute(k))
          .map((k) => ` ${k}="${escAttr(restore(node.getAttribute(k) ?? ""))}"`)
          .join("");
        return `<img${attrs}>`;
      }
      return `![](${linkTarget(restore(node.getAttribute("src") ?? ""))})`;
    }
    const inner = this.children(node);
    if (tag === "b" || tag === "i") {
      const bare = stripSpaces(inner);
      if (!bare) return inner;
      let mark = tag === "b" ? "**" : "*";
      const parent = node.parentNode;
      if (
        // "***x***" is read as <i><b>; "**_x_**" as <b><i>
        tag === "i" &&
        parent !== null &&
        tagOf(parent) === "b" &&
        !leadingText(parent) &&
        (parent as Element).firstElementChild === node &&
        !tailText(node)
      ) {
        mark = "_";
      }
      let a = 0;
      while (a < inner.length && inner[a] === " ") a++;
      let b = inner.length;
      while (b > a && inner[b - 1] === " ") b--;
      return `${inner.slice(0, a)}${mark}${bare}${mark}${inner.slice(b)}`;
    }
    if (tag === "u" || tag === "s") return `<${tag}>${inner}</${tag}>`;
    if (tag === "span" && node.getAttribute("style")) {
      return `<span style="${escAttr(restore(node.getAttribute("style") ?? ""))}">${inner}</span>`;
    }
    if (tag === "a" && node.getAttribute("href")) {
      const href = restore(node.getAttribute("href") ?? "");
      // markdown-it would change the address: raw HTML
      if (!keepsLink(href)) return `<a href="${escAttr(href)}">${inner}</a>`;
      return `[${inner}](${linkTarget(href)})`;
    }
    return inner;
  }
}

/** True when markdown-it reads `[x](url)` back as exactly `url`. */
const keepsLink = (url: string): boolean =>
  validateLink(url) && normalizeLink(url) === url && !ENTITY_LIKE.test(url) && !url.includes("\\");

/** Text inside raw HTML, kept on one line (a blank line would end the HTML block). */
const rawText = (text: string): string =>
  escText(text).replaceAll("\r", "&#13;").replaceAll("\n", "&#10;");

function rawAttrs(node: Element, tag: string): string {
  const attrs: [string, string][] = [];
  for (const a of Array.from(node.attributes)) {
    const value = restore(a.value);
    if (a.name === "style" && STYLED_BLOCKS.has(tag)) {
      const rest: string[] = [];
      for (const part of value.split(";")) {
        // GitHub keeps align="", drops style=""
        if (part.startsWith("text-align:")) attrs.push(["align", part.slice("text-align:".length)]);
        else if (part) rest.push(part);
      }
      if (rest.length) attrs.push(["style", rest.join(";")]);
    } else {
      attrs.push([a.name, value]);
    }
  }
  return attrs.map(([k, v]) => ` ${k}="${escAttr(v)}"`).join("");
}

/** A block of the subset as one line of HTML that reads back to the same subset. */
function raw(node: Element): string {
  const out: string[] = [];
  const walk = (el: Element): void => {
    const tag = tagOf(el);
    const own = tag !== "tbody"; // added by the HTML5 parser (lxml, and the subset, have none)
    if (own) out.push(`<${tag}${rawAttrs(el, tag)}>`);
    if (VOID.has(tag)) return;
    for (let c = el.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3) out.push(rawText(textOf(c)));
      else if (c.nodeType === 1) walk(c as Element);
    }
    if (own) out.push(`</${tag}>`);
  };
  walk(node);
  return out.join("");
}

const styled = (node: Element): boolean =>
  !!node.getAttribute("style") ||
  Array.from(node.getElementsByTagName("li")).some((li) => !!li.getAttribute("style"));

function preText(node: Element): string {
  const parts: string[] = [];
  for (let c = node.firstChild; c; c = c.nextSibling) {
    if (c.nodeType === 3) parts.push(textOf(c));
    else if (c.nodeType === 1) parts.push(tagOf(c) === "br" ? "\n" : restore(c.textContent ?? ""));
  }
  return parts.join("");
}

function fence(code: string): string {
  let longest = 2;
  for (let i = 0; i < code.length; ) {
    if (code[i] !== "`") {
      i++;
      continue;
    }
    let j = i;
    while (j < code.length && code[j] === "`") j++;
    if (j - i >= 3) longest = Math.max(longest, j - i);
    i = j;
  }
  const mark = "`".repeat(Math.max(3, longest + 1));
  return `${mark}\n${code}\n${mark}`;
}

const elementChildren = (node: Element): Element[] => Array.from(node.children);

class Writer {
  blocks(root: Element): string[] {
    const out: string[] = [];
    const children = elementChildren(root);
    let prev = "";
    let i = 0;
    while (i < children.length) {
      const node = children[i];
      const tag = tagOf(node);
      if (tag === "pre") {
        // one <pre> per code line: consecutive ones are one block ...
        const lines: string[] = [];
        while (i < children.length && tagOf(children[i]) === "pre") {
          const t = preText(children[i]);
          lines.push(t.endsWith("\n") ? t.slice(0, -1) : t);
          i++;
          if (t.endsWith("\n")) break; // ... but Markdown's whole code block ends one
        }
        out.push(fence(lines.join("\n")));
        prev = tag;
        continue;
      }
      i++;
      const block = this.block(node, tag);
      if (block === null) continue;
      if (tag === prev && (tag === "ul" || tag === "ol" || tag === "blockquote")) {
        out.push("<!-- -->"); // two in a row would otherwise be read as one
      }
      prev = tag;
      out.push(block);
    }
    return out;
  }

  private block(node: Element, tag: string): string | null {
    if ((STYLED_BLOCKS.has(tag) || tag === "ul" || tag === "ol") && styled(node)) {
      return raw(node); // alignment, indent, page break: no Markdown form
    }
    if (tag === "h1" || tag === "h2" || tag === "h3") {
      const hashes = "#".repeat(Number(tag[1]));
      let text = new Inline().render(node);
      text = text.replace(TRAILING_HASHES, (h) => "\\#".repeat(h.length));
      return text ? `${hashes} ${text}` : hashes; // stays a heading
    }
    if (tag === "p") return new Inline().render(node) || "<span></span>"; // an empty paragraph stays one
    if (tag === "blockquote") {
      const inner = new Writer().blocks(node).join("\n\n");
      return inner
        .split("\n")
        .map((line) => (line ? `> ${line}` : ">"))
        .join("\n");
    }
    if (tag === "ul" || tag === "ol") return this.list(node, tag, 0).join("\n");
    if (tag === "table") return this.table(node);
    if (tag === "hr") return "---";
    return new Inline().render(node) || null;
  }

  /** `alt`: the other marker ("* ", "1) "), which starts a new list right after one. */
  private list(node: Element, tag: string, level: number, lines: string[] = [], alt = false): string[] {
    const marker = tag === "ol" ? (alt ? "1) " : "1. ") : alt ? "* " : "- ";
    const pad = "    ".repeat(level);
    for (const li of elementChildren(node)) {
      if (tagOf(li) !== "li") continue;
      // The item's own content: everything but its nested lists (and the text after them).
      const holder = li.ownerDocument.createElement("li");
      const nested: Element[] = [];
      let afterList = false;
      for (const child of Array.from(li.childNodes)) {
        const t = tagOf(child);
        if (t === "ul" || t === "ol") {
          nested.push(child as Element);
          afterList = true;
        } else if (child.nodeType === 1) {
          holder.appendChild(child);
          afterList = false;
        } else if (child.nodeType === 3 && !afterList) {
          holder.appendChild(child);
        }
      }
      const first = holder.firstChild;
      const lead = first && first.nodeType === 3 ? textOf(first) : "";
      const task = TASK.exec(lead);
      if (task) {
        // "[ ] todo" / "[x] done": a checkbox on GitHub, so not escaped
        (first as Text).data = lead.slice(task[0].length);
      }
      let text = new Inline().render(holder);
      if (task) text = task[1] + (text ? " " + text : "");
      text = text || "<span></span>"; // an empty item stays an item
      lines.push(`${pad}${marker}${text}`);
      let prev = "";
      let altSub = false;
      for (const sub of nested) {
        // two lists of one kind in a row would be read as one
        altSub = tagOf(sub) === prev && !altSub;
        prev = tagOf(sub);
        this.list(sub, prev, level + 1, lines, altSub);
      }
    }
    return lines;
  }

  private table(node: Element): string | null {
    if (!pipeTable(node)) return raw(node);
    const rows: string[][] = [];
    for (const tr of tableRows(node)) {
      const cells: string[] = [];
      for (const td of elementChildren(tr)) {
        const t = tagOf(td);
        if (t === "td" || t === "th") cells.push(cellLines(td).join("<br>"));
      }
      if (cells.length) rows.push(cells);
    }
    if (!rows.length) return null;
    let width = 0;
    for (const r of rows) width = Math.max(width, r.length);
    const lines: string[] = [];
    rows.forEach((cells, n) => {
      const full = cells.concat(new Array<string>(width - cells.length).fill(""));
      lines.push("| " + full.join(" | ") + " |");
      if (n === 0) lines.push("|" + new Array<string>(width).fill(" --- ").join("|") + "|");
    });
    return lines.join("\n");
  }
}

/** Only white space among the node's own text. */
function noText(node: Element): boolean {
  for (let c = node.firstChild; c; c = c.nextSibling) {
    if (c.nodeType === 3 && pyStrip(textOf(c))) return false;
  }
  return true;
}

/**
 * True when a pipe table reads back to `table`: plain rows of equal length, each cell one plain
 * paragraph (what markdown-it's tables become in the subset).
 */
function pipeTable(table: Element): boolean {
  // the <tbody> the HTML5 parser adds (lxml, and the subset, have none)
  const first = table.firstElementChild;
  const body = first && tagOf(first) === "tbody" && table.children.length === 1 ? first : table;
  const rows = elementChildren(body);
  if (table.attributes.length || !rows.length || !noText(table) || !noText(body)) return false;
  const width = rows[0].children.length;
  for (const tr of rows) {
    const cells = elementChildren(tr);
    if (tagOf(tr) !== "tr" || tr.attributes.length || !noText(tr)) return false;
    if (cells.length !== width || !width) return false;
    for (const td of cells) {
      if (tagOf(td) !== "td" || td.attributes.length || td.children.length !== 1 || !noText(td)) {
        return false;
      }
      const p = td.children[0];
      if (tagOf(p) !== "p" || p.attributes.length) return false;
    }
  }
  return true;
}

function tableRows(table: Element): Element[] {
  const rows: Element[] = [];
  for (const child of elementChildren(table)) {
    const t = tagOf(child);
    if (t === "tr") rows.push(child);
    else if (t === "thead" || t === "tbody" || t === "tfoot") {
      for (const tr of elementChildren(child)) if (tagOf(tr) === "tr") rows.push(tr);
    }
  }
  return rows;
}

const CELL_BLOCKS = new Set(["p", "h1", "h2", "h3", "li", "pre"]);
const CELL_SKIPPED = new Set(["ul", "ol", "table"]);

/** The text lines of a table cell (its paragraphs, list items, nested table cells). */
function cellLines(td: Element): string[] {
  const lines: string[] = [];
  for (const el of Array.from(td.querySelectorAll("*"))) {
    if (!CELL_BLOCKS.has(tagOf(el))) continue;
    const holder = td.ownerDocument.createElement("p");
    let skipped = false;
    for (const child of Array.from(el.childNodes)) {
      if (child.nodeType === 1) {
        skipped = CELL_SKIPPED.has(tagOf(child));
        if (!skipped) holder.appendChild(child.cloneNode(true));
      } else if (child.nodeType === 3 && !skipped) {
        holder.appendChild(child.cloneNode(true));
      }
    }
    const text = new Inline(true).render(holder);
    if (text) lines.push(text);
  }
  return lines;
}

/** Parse the subset the way the desktop's lxml does (see subset-scan.ts). */
function parseFragment(html: string): HTMLElement {
  const doc = new DOMParser().parseFromString(
    libxmlLike(html5Refs(`<html><body>${html}`)),
    "text/html",
  );
  for (const p of Array.from(doc.getElementsByTagName("p"))) {
    if (p.hasAttribute(P_MARK)) p.removeAttribute(P_MARK);
    else if (!p.firstChild) p.remove(); // made for a stray </p>
  }
  return doc.body;
}

/** The editor's (normalised) HTML subset as Markdown, ending with one newline. */
export function toMarkdown(html: string): string {
  let blank = true;
  for (let i = 0; i < html.length && blank; i++) blank = isPySpace(html[i]);
  if (blank || html === "<p></p>") return ""; // the empty document (opening "" gives "<p></p>")
  const blocks = new Writer().blocks(parseFragment(html));
  return blocks.length ? blocks.join("\n\n") + "\n" : "";
}
