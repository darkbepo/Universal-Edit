/**
 * Markdown for the document editor: reading (front matter, markdown-it, the "parts that will
 * be lost" checks) and writing the editor's HTML subset back as Markdown.
 *
 * Ports of the desktop's `src/umc/doceditor/io.py` (Markdown part) and `md_writer.py`; the
 * output must be byte-identical. The desktop's regexes are copied where they stay linear; the
 * ones that can backtrack badly on long input (fences, code spans, comments, picture
 * descriptions, table alignment rows) are replaced by scans that give the same answer.
 */

import MarkdownIt, { type StateInline } from "markdown-it";
import { PY_WS, escAttr, isPySpace } from "./pycompat";
import { P_MARK, html5Refs, libxmlLike, restore } from "./subset-scan";

const NBSP = "\xa0";
// The desktop reads Markdown with the same parser and configuration (GitHub-style CommonMark).
const MD = new MarkdownIt("commonmark", { html: true }).enable(["table", "strikethrough"]);

// markdown-it-py (the desktop) trims paragraph and heading text with its `mdTrim` (CommonMark
// white space plus the Unicode spaces), markdown-it 15 only with space / tab / CR / LF. Trimming
// the rest afterwards gives the desktop's text (its set includes markdown-it's).
const MD_TRIM = new Set(
  "\t\n\x0b\x0c\r \xa0            " +
    "    　",
);
function mdTrim(s: string): string {
  let start = 0;
  let end = s.length;
  while (start < end && MD_TRIM.has(s[start])) start++;
  while (end > start && MD_TRIM.has(s[end - 1])) end--;
  return start === 0 && end === s.length ? s : s.slice(start, end);
}

// Inline HTML the way markdown-it-py reads it. markdown-it 15 differs in three ways: once one
// "<!--" / "<?" / "<![CDATA[" / "<!X" has no end it stops reading them for the rest of the
// paragraph (but a link label is scanned ahead first, so "[<!-- a --><!--" lost a complete
// comment), it ends a comment at the first "-->" (markdown-it-py: the older rule, no "--->"),
// and its \s is JavaScript's. markdown-it-py checks that the end marker occurs anywhere later
// (cached per paragraph), so a run of unclosed openers stays linear.
const W = `[${PY_WS}]`;
const OPEN_TAG =
  `<[A-Za-z][A-Za-z0-9\-]*(?:${W}+[a-zA-Z_:][a-zA-Z0-9:._-]*` +
  `(?:${W}*=${W}*(?:[^"'=<>\`\x00-\x20]+|'[^']*'|"[^"]*"))?)*${W}*\/?>`;
const CLOSE_TAG = `<\/[A-Za-z][A-Za-z0-9\-]*${W}*>`;
const HTML_TAG = new RegExp(
  `(?:${OPEN_TAG}|${CLOSE_TAG}|<!---?>|<!--(?:[^-]|-[^-]|--[^>])*-->|<[?][\s\S]*?[?]>` +
    `|<![A-Za-z][^>]*>|<!\[CDATA\[[\s\S]*?\]\]>)`,
  "y",
);
const LINK_OPEN = new RegExp(`^<a[>${PY_WS}]`, "i");
const LINK_CLOSE = new RegExp(`^</a${W}*>`, "i");
const lastIndex = new WeakMap<StateInline, Map<string, number>>();
function lastOf(state: StateInline, marker: string): number {
  let found = lastIndex.get(state);
  if (!found) lastIndex.set(state, (found = new Map()));
  let at = found.get(marker);
  if (at === undefined) found.set(marker, (at = state.src.lastIndexOf(marker)));
  return at;
}
const isLetter = (c: string) => /^[A-Za-z]$/.test(c);
MD.inline.ruler.at("html_inline", (state, silent) => {
  const { src, pos } = state;
  if (!state.md.options.html || src[pos] !== "<" || pos + 2 >= state.posMax) return false;
  const ch = src[pos + 1];
  if (ch !== "!" && ch !== "?" && ch !== "/" && !isLetter(ch)) return false;
  if (src.startsWith("<!--", pos)) {
    if (lastOf(state, ">") < pos + 4) return false;
    const short = src.startsWith("<!-->", pos) || src.startsWith("<!--->", pos);
    if (!short && lastOf(state, "-->") < pos + 4) return false;
  } else if (src.startsWith("<![CDATA[", pos)) {
    if (lastOf(state, "]]>") < pos + 9) return false;
  } else if (ch === "?") {
    if (lastOf(state, "?>") < pos + 2) return false;
  } else if (ch === "!") {
    if (lastOf(state, ">") < pos + 3) return false;
  } else if (lastOf(state, ">") < pos + 2) {
    return false;
  }
  HTML_TAG.lastIndex = pos;
  const m = HTML_TAG.exec(src);
  if (!m) return false;
  if (!silent) {
    const token = state.push("html_inline", "", 0);
    token.content = m[0];
    if (LINK_OPEN.test(m[0])) state.linkLevel++;
    if (LINK_CLOSE.test(m[0])) state.linkLevel--;
  }
  state.pos += m[0].length;
  return true;
});

// Backslash escapes the way markdown-it-py reads them: "\ " keeps the space with the backslash
// (markdown-it 15 leaves it to the text, so "\   ⏎" lost a space before the line break).
const ESCAPABLE = new Set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");
MD.inline.ruler.at("escape", (state, silent) => {
  let pos = state.pos;
  const max = state.posMax;
  if (state.src[pos] !== "\\") return false;
  pos++;
  if (pos >= max) return false;
  const ch1 = state.src[pos];
  if (ch1 === "\n") {
    if (!silent) state.push("hardbreak", "br", 0);
    pos++;
    while (pos < max && (state.src[pos] === " " || state.src[pos] === "\t")) pos++;
    state.pos = pos;
    return true;
  }
  let escaped = ch1;
  const code = ch1.charCodeAt(0);
  if (code >= 0xd800 && code <= 0xdbff && pos + 1 < max) {
    const c2 = state.src.charCodeAt(pos + 1);
    if (c2 >= 0xdc00 && c2 <= 0xdfff) {
      escaped += state.src[pos + 1];
      pos++;
    }
  }
  if (!silent) {
    const token = state.push("text_special", "", 0);
    token.content = ESCAPABLE.has(ch1) ? escaped : "\\" + escaped;
    token.markup = "\\" + escaped;
    token.info = "escape";
  }
  state.pos = pos + 1;
  return true;
});

MD.core.ruler.after("block", "desktop_trim", (state) => {
  const tokens = state.tokens;
  for (let i = 1; i < tokens.length; i++) {
    const before = tokens[i - 1].type;
    if (tokens[i].type === "inline" && (before === "paragraph_open" || before === "heading_open")) {
      tokens[i].content = mdTrim(tokens[i].content);
    }
  }
});

/** Markdown as HTML the way GitHub reads it (markdown-it, CommonMark + tables + strike). */
export function mdToHtml(text: string): string {
  return MD.render(text);
}

// ----- front matter ------------------------------------------------------------------------

const FRONT_KEY = new RegExp(`^[A-Za-z0-9_-]+[${PY_WS}]*:`);
// Python's str.splitlines() line ends ("\r\n" counts as one).
const LINE_END = new RegExp("\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]", "g");

function rstrip(s: string): string {
  let end = s.length;
  while (end > 0 && isPySpace(s[end - 1])) end--;
  return s.slice(0, end);
}

/**
 * (front matter, rest): a first line "---", then a "key:" line, up to a later "---" / "..."
 * line, verbatim (CRLF saved as LF). Anything else is ordinary Markdown.
 */
export function splitFrontMatter(text: string): [string, string] {
  // Line i is text[starts[i] .. starts[i + 1]) with its line end.
  const starts = [0];
  LINE_END.lastIndex = 0;
  const lineAt = (i: number): string | null => {
    while (starts.length <= i + 1) {
      const from = starts[starts.length - 1];
      if (from >= text.length) return null;
      const m = LINE_END.exec(text);
      starts.push(m ? m.index + m[0].length : text.length);
      if (!m) LINE_END.lastIndex = text.length;
      if (starts[starts.length - 1] === from) return null;
    }
    return text.slice(starts[i], starts[i + 1]);
  };
  const first = lineAt(0);
  const second = lineAt(1);
  if (first === null || second === null || lineAt(2) === null) return ["", text];
  if (rstrip(first) !== "---" || !FRONT_KEY.test(second)) return ["", text];
  for (let i = 1; ; i++) {
    const line = lineAt(i);
    if (line === null) break;
    const bare = rstrip(line);
    if (bare === "---" || bare === "...") {
      const end = starts[i + 1];
      return [text.slice(0, end).replaceAll("\r\n", "\n"), text.slice(end)];
    }
  }
  return ["", text];
}

// ----- what the editor can't keep ----------------------------------------------------------

/** Start offset of every "\n"-separated line, plus text.length + 1 at the end. */
function lineStarts(text: string): number[] {
  const starts = [0];
  for (let i = text.indexOf("\n"); i >= 0; i = text.indexOf("\n", i + 1)) starts.push(i + 1);
  starts.push(text.length + 1);
  return starts;
}

/** Length of the fence run (` or ~) at the start of a line after at most 3 spaces, and its char. */
function fenceRun(text: string, start: number, end: number): [string, number] {
  let i = start;
  while (i < end && i - start < 4 && text[i] === " ") i++;
  if (i - start > 3 || i >= end) return ["", 0];
  const c = text[i];
  if (c !== "`" && c !== "~") return ["", 0];
  let j = i;
  while (j < end && text[j] === c) j++;
  return [c, j - i];
}

/**
 * The text without fenced code blocks — the desktop's
 * `^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[^\n]*$` (re.M | re.S) removed with re.sub. The regex
 * backtracks to the longest fence (≤ the opener's) that some later line starts with, then takes
 * the first such line; this scan gives the same matches in linear time.
 */
function stripFences(text: string): string {
  const starts = lineStarts(text);
  const n = starts.length - 1; // number of lines
  const runs: [string, number][] = [];
  for (let i = 0; i < n; i++) runs.push(fenceRun(text, starts[i], starts[i + 1] - 1));
  // Longest run of each fence character on line i or later.
  const sufTick = new Array<number>(n + 1).fill(0);
  const sufTilde = new Array<number>(n + 1).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    const [c, len] = runs[i];
    sufTick[i] = Math.max(sufTick[i + 1], c === "`" ? len : 0);
    sufTilde[i] = Math.max(sufTilde[i + 1], c === "~" ? len : 0);
  }
  const out: string[] = [];
  let from = 0;
  for (let i = 0; i < n; i++) {
    const [c, len] = runs[i];
    const hasNewline = starts[i + 1] - 1 < text.length; // the opener line ends with "\n"
    if (len < 3 || !hasNewline) continue;
    const k = Math.min(len, (c === "`" ? sufTick : sufTilde)[i + 1]);
    if (k < 3) continue;
    let j = i + 1;
    while (!(runs[j][0] === c && runs[j][1] >= k)) j++;
    out.push(text.slice(from, starts[i]));
    from = starts[j + 1] - 1; // the end of the closing line (before its "\n")
    i = j;
  }
  out.push(text.slice(from));
  return out.join("");
}

/**
 * Code spans (the desktop's (?<!\\)`[^`\n]+`): removed (`remove`), or just found.
 * Returns the text without them, or null when `remove` is false and none was found.
 */
function codeSpans(text: string, remove: boolean): string | null {
  const out: string[] = [];
  let from = 0;
  let found = false;
  let i = text.indexOf("`");
  while (i >= 0) {
    if (i > 0 && text[i - 1] === "\\") {
      i = text.indexOf("`", i + 1);
      continue;
    }
    let j = i + 1;
    while (j < text.length && text[j] !== "`" && text[j] !== "\n") j++;
    if (j >= text.length) break;
    if (text[j] === "`" && j > i + 1) {
      if (!remove) return text;
      found = true;
      out.push(text.slice(from, i));
      from = j + 1;
      i = text.indexOf("`", j + 1);
    } else if (text[j] === "`") {
      i = j; // "``": the second one may start a span
    } else {
      i = text.indexOf("`", j);
    }
  }
  if (!remove) return found ? text : null;
  out.push(text.slice(from));
  return out.join("");
}

const FOOTNOTE = /(?<!\\)\[\^/;
// Python's \b after a tag name: the next character is not a (Unicode) word character.
const BLOCK_HTML = new RegExp(
  "(?<![^\\n]) {0,3}</?(?:address|article|aside|blockquote|details|div|dl|fieldset|figcaption" +
    "|figure|footer|form|h[1-6]|header|hgroup|hr|main|menu|nav|ol|p|pre|section|table|ul|canvas" +
    "|dd|dt|iframe|li|math|noscript|object|script|style|summary|tbody|td|tfoot|th|thead|tr" +
    "|video|audio|svg)(?![\\p{L}\\p{N}_])",
  "iu",
);
const SMALL_HEADING = /(?<![^\n]) {0,3}#{4,6}(?:[ \t]|(?![^\n]))/;
const WS = new RegExp(`[${PY_WS}]`);

/** (?<!\\)!\[[^\]\n]+\]\( : a picture with a description. */
function hasAltText(text: string): boolean {
  let stop = 0; // the first "]" or "\n" at or after some position, moving forward only
  for (let i = text.indexOf("!["); i >= 0; i = text.indexOf("![", i + 1)) {
    if (i > 0 && text[i - 1] === "\\") continue;
    if (stop < i + 2) stop = i + 2;
    while (stop < text.length && text[stop] !== "]" && text[stop] !== "\n") stop++;
    if (stop < text.length && text[stop] === "]" && stop > i + 2 && text[stop + 1] === "(") {
      return true;
    }
  }
  return false;
}

/** <!--(?!\s*-->).*?--> : an author's comment (the writer's "<!-- -->" doesn't count). */
function hasComment(text: string): boolean {
  for (let p = text.indexOf("<!--"); p >= 0; p = text.indexOf("<!--", p + 1)) {
    let i = p + 4;
    while (i < text.length && WS.test(text[i])) i++;
    if (text.startsWith("-->", i)) continue;
    return text.indexOf("-->", p + 4) >= 0;
  }
  return false;
}

/** A table delimiter row with an alignment colon (CRLF-safe). */
function hasAlignRow(text: string): boolean {
  const starts = lineStarts(text);
  for (let n = 0; n + 1 < starts.length; n++) {
    let line = text.slice(starts[n], starts[n + 1] - 1);
    if (line.endsWith("\r")) line = line.slice(0, -1);
    if (/^[ \t|:-]*$/.test(line) && line.includes("|") && line.includes(":") && line.includes("-")) {
      return true;
    }
  }
  return false;
}

/** Footnotes or block-level raw HTML, outside code (W_MDEXTRA). */
export function hasMarkdownExtras(text: string): boolean {
  text = codeSpans(stripFences(text), true) as string;
  return FOOTNOTE.test(text) || BLOCK_HTML.test(text);
}

/** Inline code, level 4-6 headings, picture descriptions, comments or table alignment,
 * outside fenced code (W_MDFORMAT). */
export function hasDroppedFormats(text: string): boolean {
  text = stripFences(text);
  return (
    codeSpans(text, false) !== null ||
    SMALL_HEADING.test(text) ||
    hasAltText(text) ||
    hasComment(text) ||
    hasAlignRow(text)
  );
}

// ----- writing (md_writer.py) --------------------------------------------------------------

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
      return `[${inner}](${linkTarget(restore(node.getAttribute("href") ?? ""))})`;
    }
    return inner;
  }
}

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
        // one <pre> per code line: consecutive ones are one block
        const lines: string[] = [];
        while (i < children.length && tagOf(children[i]) === "pre") {
          const t = preText(children[i]);
          lines.push(t.endsWith("\n") ? t.slice(0, -1) : t);
          i++;
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
    if (tag === "h1" || tag === "h2" || tag === "h3") {
      const hashes = "#".repeat(Number(tag[1]));
      let text = new Inline().render(node);
      text = text.replace(TRAILING_HASHES, (h) => "\\#".repeat(h.length));
      return text ? `${hashes} ${text}` : hashes; // stays a heading
    }
    if (tag === "p") return new Inline().render(node) || null;
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

  private list(node: Element, tag: string, level: number, lines: string[] = []): string[] {
    const marker = tag === "ol" ? "1. " : "- ";
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
      for (const sub of nested) this.list(sub, tagOf(sub), level + 1, lines);
    }
    return lines;
  }

  private table(node: Element): string | null {
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
  if (blank) return "";
  const blocks = new Writer().blocks(parseFragment(html));
  return blocks.length ? blocks.join("\n\n") + "\n" : "";
}
