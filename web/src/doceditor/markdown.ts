/**
 * Reading Markdown for the document editor: front matter, markdown-it set up to read like the
 * desktop's markdown-it-py, and the "parts that will be lost" checks. Writing is `md-writer.ts`.
 *
 * Port of the Markdown part of the desktop's `src/umc/doceditor/io.py`; the output must be
 * byte-identical. The desktop's regexes are copied where they stay linear; the ones that can
 * backtrack badly on long input (fences, code spans, comments, picture descriptions, table
 * alignment rows) are replaced by scans that give the same answer.
 */

import MarkdownIt, { type StateInline } from "markdown-it";
import { PY_WS, isPySpace } from "./pycompat";

// The desktop reads Markdown with the same parser and configuration (GitHub-style CommonMark).
const MD = new MarkdownIt("commonmark", { html: true }).enable(["table", "strikethrough"]);

// markdown-it-py (the desktop) trims paragraph and heading text with its `mdTrim` (CommonMark
// white space plus the Unicode spaces), markdown-it 15 only with space / tab / CR / LF. Trimming
// the rest afterwards gives the desktop's text (its set includes markdown-it's).
const MD_TRIM = new Set(
  "\t\n\x0b\x0c\r \xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a" +
    "\u2028\u2029\u202f\u205f\u3000",
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

// Block HTML the way markdown-it-py starts it: the same rule, but with Python's \s (U+001C-1F
// and U+0085 are spaces there, U+FEFF isn't) and its list of block tag names.
const BLOCK_NAMES =
  "address article aside base basefont blockquote body caption center col colgroup dd details " +
  "dialog dir div dl dt fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 " +
  "head header hr html iframe legend li link main menu menuitem nav noframes ol optgroup option " +
  "p param search section summary table tbody td tfoot th thead title tr track ul";
const HTML_SEQUENCES: [RegExp, RegExp, boolean][] = [
  [
    new RegExp(`^<(script|pre|style|textarea)(?=(${W}|>|$))`, "i"),
    /<\/(script|pre|style|textarea)>/i,
    true,
  ],
  [/^<!--/, /-->/, true],
  [/^<\?/, /\?>/, true],
  [/^<![A-Za-z]/, />/, true],
  [/^<!\[CDATA\[/, /\]\]>/, true],
  [
    new RegExp(`^</?(${BLOCK_NAMES.split(" ").join("|")})(?=(${W}|/?>|$))`, "i"),
    /^$/,
    true,
  ],
  [new RegExp(`^(?:${OPEN_TAG}|${CLOSE_TAG})${W}*$`), /^$/, false],
];
MD.block.ruler.at(
  "html_block",
  (state, startLine, endLine, silent) => {
    let pos = state.bMarks[startLine] + state.tShift[startLine];
    let max = state.eMarks[startLine];
    if (state.sCount[startLine] - state.blkIndent >= 4) return false;
    if (!state.md.options.html || state.src[pos] !== "<") return false;
    let lineText = state.src.slice(pos, max);
    const seq = HTML_SEQUENCES.find(([start]) => start.test(lineText));
    if (!seq) return false;
    if (silent) return seq[2];
    let nextLine = startLine + 1;
    const endsOnBlankLine = seq[1].test("");
    if (!seq[1].test(lineText)) {
      for (; nextLine < endLine; nextLine++) {
        if (state.sCount[nextLine] < state.blkIndent && (endsOnBlankLine || !state.isEmpty(nextLine))) {
          break;
        }
        pos = state.bMarks[nextLine] + state.tShift[nextLine];
        max = state.eMarks[nextLine];
        lineText = state.src.slice(pos, max);
        if (seq[1].test(lineText)) {
          if (lineText.length !== 0) nextLine++;
          break;
        }
      }
    }
    state.line = nextLine;
    const token = state.push("html_block", "", 0);
    token.map = [startLine, nextLine];
    token.content = state.getLines(startLine, nextLine, state.blkIndent, true);
    return true;
  },
  { alt: ["paragraph", "reference", "blockquote"] },
);

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

/** markdown-it's link address check and rewrite (used by the writer to keep addresses exact). */
export const validateLink = (url: string): boolean => MD.validateLink(url);
export const normalizeLink = (url: string): string => MD.normalizeLink(url);

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

// Tags raw HTML blocks may use without losing anything (what md-writer writes, and synonyms).
const KEPT_TAGS = new Set(
  "p h1 h2 h3 ul ol li table thead tbody tfoot tr td th pre code br b strong i em u s del strike a img span".split(" "),
);
const ANY_TAG = /<\/?([A-Za-z][A-Za-z0-9-]*)/g;
const BLANK_LINE = /\n[ \t]*\r?(?:\n|$)/g;
const BLOCK_HTML_ALL = new RegExp(BLOCK_HTML.source, "giu");

/** A block of raw HTML (from its first line up to a blank line) with a tag the editor doesn't
 * keep; the writer's own blocks (alignment, indent, tables) use only kept tags. */
function dropsBlockHtml(text: string): boolean {
  let pos = 0;
  for (;;) {
    BLOCK_HTML_ALL.lastIndex = pos;
    const m = BLOCK_HTML_ALL.exec(text);
    if (!m) return false;
    BLANK_LINE.lastIndex = m.index;
    const blank = BLANK_LINE.exec(text);
    const stop = blank ? blank.index : text.length;
    const block = text.slice(m.index, stop);
    for (const t of block.matchAll(ANY_TAG)) if (!KEPT_TAGS.has(t[1].toLowerCase())) return true;
    pos = Math.max(stop, m.index + m[0].length);
  }
}

/** Footnotes or block-level raw HTML the editor drops, outside code (W_MDEXTRA). */
export function hasMarkdownExtras(text: string): boolean {
  text = codeSpans(stripFences(text), true) as string;
  return FOOTNOTE.test(text) || dropsBlockHtml(text);
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
