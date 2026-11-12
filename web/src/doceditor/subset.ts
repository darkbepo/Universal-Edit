/**
 * Normalise arbitrary HTML into the document editor's small, canonical HTML subset.
 *
 * A port of the desktop's `src/umc/doceditor/html_subset.py` (read its docstring for the
 * subset); the output must be byte-identical, so the helpers below copy Python's behaviour
 * (str.strip, round, "%.2f", urlparse, PIL.ImageColor). Parsing uses the page's DOMParser; the
 * input is first adjusted where an HTML5 parser differs from libxml2 (control characters, CR
 * in <pre>, the newline after <pre>). The HTML is serialised from our own tokens.
 */

import { escAttr, escText, pyRound, pyStrip } from "./pycompat";
import {
  MAX_FILE_BYTES,
  W_PAGESTYLE,
  W_PICTURES,
  W_RAWHTML,
  W_WEBPICS,
  type DocImages,
  PictureNames,
  nextImageName,
} from "./types";
import {
  S,
  attr,
  set,
  b64decode,
  colour,
  cssEmphasis,
  fontFamily,
  isPlainRelative,
  notBold,
  parseStyle,
  points,
  spanStyleText,
  suffix,
  unquote,
  urlparse,
} from "./subset-values";
import { P_MARK, html5Refs, libxmlLike, restore } from "./subset-scan";

export interface NormaliseOptions {
  /** Bytes of a relative picture path ("sub/p.png"), or null. Never called for unsafe paths. */
  resolveImage?: (src: string) => Uint8Array | null;
}

// Removed together with everything inside them.
const DROP = set(
  "script style head title meta link iframe object embed noscript template svg canvas audio " +
    "video applet textarea select option button frame frameset",
);
const BLOCK = set(
  "p div h1 h2 h3 h4 h5 h6 blockquote pre ul ol li table thead tbody tfoot tr td th caption hr " +
    "section article header footer nav aside main form figure figcaption address center dl dt " +
    "dd fieldset body html",
);
const LISTS = set("ul ol");
// Embedded content that is dropped (W_RAWHTML); forms keep their text, not their fields.
const RAW_DROPPED = set(
  "iframe object embed applet audio video canvas svg frame frameset form input textarea select button",
);
const INLINE_MAP: Record<string, string> = {
  b: "b",
  strong: "b",
  i: "i",
  em: "i",
  cite: "i",
  u: "u",
  ins: "u",
  s: "s",
  strike: "s",
  del: "s",
};
const HEADINGS: Record<string, string> = {
  h1: "h1",
  h2: "h2",
  h3: "h3",
  h4: "h3",
  h5: "h3",
  h6: "h3",
};
const IMAGE_EXTS: Record<string, string> = {
  png: "png",
  jpg: "jpg",
  jpeg: "jpg",
  gif: "gif",
  bmp: "bmp",
  webp: "webp",
};
const FONT_SIZES_PT: Record<string, number> = { 1: 8, 2: 10, 3: 12, 4: 14, 5: 18, 6: 24, 7: 36 };
const SAFE_SCHEMES = new Set(["", "http", "https", "mailto", "tel"]);

// Python's white space (strip(), split() and re's Unicode classes), as a character class.
const isHtmlSpace = (c: string) =>
  c === " " || c === "\t" || c === "\r" || c === "\n" || c === "\f";
/** Python's `text.strip(" \t\r\n\f")` is not empty. */
const hasContent = (s: string) => {
  for (let i = 0; i < s.length; i++) if (!isHtmlSpace(s[i])) return true;
  return false;
};

/** `WS.sub(" ", text)` in linear time: runs of spaces, and line breaks with the indentation
 * around them, become one space; other tabs stay. */
function collapseWs(s: string): string {
  const parts: string[] = [];
  const n = s.length;
  let i = 0;
  let from = 0; // start of the text not copied yet
  while (i < n) {
    const c = s[i];
    if (c !== " " && c !== "\t" && c !== "\r" && c !== "\n" && c !== "\f") {
      i++;
      continue;
    }
    let j = i;
    while (j < n && (s[j] === " " || s[j] === "\t")) j++;
    if (j < n && (s[j] === "\r" || s[j] === "\n" || s[j] === "\f")) {
      while (j < n && isHtmlSpace(s[j])) j++;
      parts.push(s.slice(from, i), " ");
      i = from = j;
      continue;
    }
    // Spaces and tabs without a line break after them (true at every position of the run):
    // only runs of 2+ spaces collapse.
    for (let k = i; k < j;) {
      if (s[k] !== " ") {
        k++;
        continue;
      }
      let e = k;
      while (e < j && s[e] === " ") e++;
      if (e - k >= 2) {
        parts.push(s.slice(from, k), " ");
        from = e;
      }
      k = e;
    }
    i = j === i ? i + 1 : j;
  }
  parts.push(s.slice(from));
  return parts.join("");
}

const DATA_URI = new RegExp(`^data:image/(png|jpe?g|gif|bmp|webp)(?:;[^,;]*)*;base64,(.*)$`, "is");
const CANON_IMG = /^img\/\d+\.(?:png|jpg|gif|bmp|webp)$/;
const XML_DECL = new RegExp(`^${S}*<\\?xml[^>]*\\?>`, "i");
// Deepest paragraph indent, 720 pt (huge values used to print as "1e+27pt").
const MAX_INDENT = 20;
const UNREADABLE = "This file couldn't be read as HTML.";
// Python's recursion limit while normalising (the desktop raises past it); JS stack overflows
// are reported the same way.
const MAX_DEPTH = 20000;

type Kind = "T" | "O" | "C" | "V" | "S";
// T = text, O = opening tag, C = closing tag, V = void tag,
// S = a line break the normaliser added between blocks (dropped at the ends).
type Token = [Kind, string];
const isBr = (t: Token | undefined) => t !== undefined && t[0] === "V" && t[1] === "<br>";
const BR = (): Token => ["V", "<br>"];


function tagOf(node: Node): string | null {
  return node.nodeType === 1 ? (node as Element).localName.toLowerCase() : null;
}
const elements = (node: Element): Element[] => Array.from(node.children);

/** Collapse white space (outside <pre>), trim the ends, merge repeated tags and serialise the
 * tokens. Outside <pre> this repeats until nothing changes (merging can leave a space to trim or
 * put a space after a space), so a second pass over the output changes nothing. */
function finalise(tokens: Token[], pre = false, dropTrailingBr = false): string {
  if (pre) tokens = mergeAdjacent(tokens);
  else {
    for (;;) {
      const tidied = mergeAdjacent(tidy(tokens, dropTrailingBr));
      if (sameTokens(tidied, tokens)) break;
      tokens = tidied;
    }
  }
  return tokens.map(([k, t]) => (k === "T" ? escText(t) : t)).join("");
}

const sameTokens = (a: Token[], b: Token[]) =>
  a.length === b.length && a.every((t, i) => t[0] === b[i][0] && t[1] === b[i][1]);

/** Collapse white space and trim the ends (into a new list). */
function tidy(tokens: Token[], dropTrailingBr: boolean): Token[] {
  const out: Token[] = [];
  let prevSpace = true;
  for (const [kind, raw] of tokens) {
    let text = raw;
    if (kind === "T") {
      text = collapseWs(text);
      if (prevSpace && text.startsWith(" ")) text = text.slice(1);
      if (!text) continue;
      prevSpace = text.endsWith(" ");
    } else if (kind === "V" || kind === "S") {
      prevSpace = text === "<br>";
    } else if (kind === "C" && out.length && out[out.length - 1][0] === "O" && text === "</a>") {
      prevSpace = false; // an empty link (a badge without its picture) is content
    }
    out.push([kind, text]);
  }
  if (dropTrailingBr) while (out.length && isBr(out[out.length - 1])) out.pop();
  let i = out.length;
  while (i > 0) {
    const [kind, text] = out[i - 1];
    if (kind === "C") i -= 1;
    else if (kind === "S") {
      out.splice(i - 1, 1);
      i -= 1;
    } else if (kind === "T" && text.endsWith(" ")) {
      let end = text.length;
      while (end > 0 && text[end - 1] === " ") end--;
      out[i - 1] = [kind, text.slice(0, end)];
      break;
    } else break;
  }
  return out;
}

/** `<b>x</b><b>y</b>` → `<b>xy</b>`: an inline tag closed and opened again right away is one
 * (so the subset is canonical). An empty element stays its own (two links in a row where the
 * first is an empty badge). */
function mergeAdjacent(tokens: Token[]): Token[] {
  const out: Token[] = [];
  const stack: string[] = []; // the opening tags still open
  let closed: [string, boolean][] = []; // the elements the closing tags ending out close
  for (const [kind, text] of tokens) {
    const top = closed[closed.length - 1];
    if (kind === "O" && top !== undefined && top[0] === text && !top[1]) {
      out.pop(); // reopen it instead (and the one it was in, if that comes next)
      closed.pop();
      stack.push(text);
      continue;
    }
    if (kind === "C" && stack.length) {
      const opener = stack.pop()!;
      const last = out[out.length - 1];
      closed.push([opener, last !== undefined && last[0] === "O" && last[1] === opener]);
    } else {
      closed = [];
      if (kind === "O") stack.push(text);
    }
    out.push([kind, text]);
  }
  return out;
}


// ----- the normaliser --------------------------------------------------------------------

type Group = [Token[], string[]];

class TooDeep extends Error {}

/** The engine's own "too much recursion" error (V8, SpiderMonkey, JavaScriptCore). */
function isStackOverflow(err: unknown): boolean {
  return err instanceof Error && /call stack|too much recursion/i.test(err.message);
}

class Normaliser {
  images: DocImages = new Map();
  private readonly names = new PictureNames();
  /** A relative picture that couldn't be read (W_PICTURES). */
  lostPictures = false;
  private depth = 0;
  /** A link inside a link is only its text (parsers split them). */
  private inLink = false;

  constructor(
    private readonly reserved: Set<string>,
    private readonly resolveImage?: (src: string) => Uint8Array | null,
  ) {}

  private enter(): void {
    if (++this.depth > MAX_DEPTH) throw new TooDeep();
  }

  // ----- pictures -----
  private addImage(data: Uint8Array, ext: string): string {
    const same = this.names.find(data, ext); // one picture used twice keeps one name
    if (same !== null) return same;
    let name = nextImageName(this.images, ext);
    let n = this.images.size + 1;
    while (this.images.has(name) || this.reserved.has(name)) {
      n += 1;
      name = `img/${n}.${ext}`;
    }
    this.images.set(name, data);
    this.names.add(name, data, ext);
    return name;
  }

  private imageSrc(raw: string): string | null {
    const src = pyStrip(raw);
    const m = src.match(DATA_URI);
    if (m) {
      const data = b64decode(m[2].replace(new RegExp(`${S}+`, "g"), ""));
      if (data === null || data.length === 0) return null;
      return this.addImage(data, IMAGE_EXTS[m[1].toLowerCase()]);
    }
    const parsed = urlparse(src);
    if (parsed === null || parsed.scheme || parsed.netloc || !parsed.path) return null;
    const found = this.readRelative(unquote(parsed.path));
    if (found !== null) return this.addImage(found[0], found[1]);
    // Already in canonical form (output of an earlier pass): keep the reference.
    if (CANON_IMG.test(src)) return src;
    this.lostPictures = true; // no reader, missing, outside the folder, or not a picture
    return null;
  }

  private readRelative(rel: string): [Uint8Array, string] | null {
    // Checked before asking for the file: a UNC path would contact the host.
    if (!this.resolveImage || !isPlainRelative(rel)) return null;
    const ext = IMAGE_EXTS[suffix(rel).toLowerCase().replace(/^\./, "")];
    if (ext === undefined) return null;
    try {
      const data = this.resolveImage(rel);
      return data && data.length <= MAX_FILE_BYTES ? [data, ext] : null;
    } catch {
      return null;
    }
  }

  // ----- inline -----
  private inlineChildren(node: Node, toks: Token[]): void {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (text) toks.push(["T", text]);
      } else if (child.nodeType === 1) this.inline(child as Element, toks);
    }
  }

  private inline(node: Element, toks: Token[]): void {
    const tag = tagOf(node);
    if (tag === null || DROP.has(tag)) return;
    this.enter();
    if (tag === "br") toks.push(BR());
    else if (tag === "img") this.img(node, toks);
    else if (Object.hasOwn(INLINE_MAP, tag) && !notBold(tag, node)) {
      const out = INLINE_MAP[tag];
      toks.push(["O", `<${out}>`]);
      this.inlineChildren(node, toks);
      toks.push(["C", `</${out}>`]);
    } else if (tag === "a") {
      const href = pyStrip((attr(node, "href") ?? "").replace(/[\x00-\x1f\x7f]/g, ""));
      const parsed = href ? urlparse(href) : null;
      if (parsed && SAFE_SCHEMES.has(parsed.scheme) && !this.inLink) {
        toks.push(["O", `<a href="${escAttr(href)}">`]);
        this.inLink = true;
        this.inlineChildren(node, toks);
        this.inLink = false;
        toks.push(["C", "</a>"]);
      } else this.inlineChildren(node, toks);
    } else if (tag === "span" || tag === "font") {
      const style = spanStyle(node, tag === "font");
      const wraps = cssEmphasis(parseStyle(attr(node, "style")));
      if (style) toks.push(["O", `<span style="${escAttr(style)}">`]);
      for (const t of wraps) toks.push(["O", `<${t}>`]);
      this.inlineChildren(node, toks);
      for (const t of [...wraps].reverse()) toks.push(["C", `</${t}>`]);
      if (style) toks.push(["C", "</span>"]);
    } else if (BLOCK.has(tag)) {
      // a block inside an inline tag: its content becomes a line
      separate(toks);
      this.inlineChildren(node, toks);
      separate(toks);
    } else this.inlineChildren(node, toks); // unknown inline: keep the content
    this.depth--;
  }

  private img(node: Element, toks: Token[]): void {
    const name = this.imageSrc(attr(node, "src") ?? "");
    if (name === null) return;
    const css = parseStyle(attr(node, "style"));
    let attrs = `src="${escAttr(name)}"`;
    for (const dim of ["width", "height"]) {
      const raw = attr(node, dim) || css.get(dim) || "";
      const m = raw.match(new RegExp(`^${S}*(\\d+)(?:px)?${S}*$`));
      if (m && Number(m[1]) > 0 && Number(m[1]) <= 100000) attrs += ` ${dim}="${Number(m[1])}"`;
    }
    toks.push(["V", `<img ${attrs}>`]);
  }

  // ----- blocks -----
  /** Emit the content of a block container; loose inline content becomes paragraphs. */
  blocks(node: Node, out: string[]): void {
    this.enter();
    const run: Token[] = [];
    const flush = () => {
      if (run.some(([k, t]) => k === "V" || (k === "T" && hasContent(t)))) {
        out.push(`<p>${finalise(run)}</p>`);
      }
      run.length = 0;
    };
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (text) run.push(["T", text]);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = tagOf(child) as string;
      if (BLOCK.has(tag)) {
        flush();
        this.block(child as Element, tag, out);
      } else this.inline(child as Element, run);
    }
    flush();
    this.depth--;
  }

  private leaf(tag: string, node: Element): string {
    const toks: Token[] = [];
    this.inlineChildren(node, toks);
    const style = Object.hasOwn(HEADINGS, tag) ? headingStyle(node) : blockStyle(node);
    return `<${tag}${style}>${finalise(toks)}</${tag}>`;
  }

  private block(node: Element, tag: string, out: string[]): void {
    this.enter();
    if (tag === "p") out.push(this.leaf("p", node));
    else if (Object.hasOwn(HEADINGS, tag)) out.push(this.leaf(HEADINGS[tag], node));
    else if (tag === "pre") {
      const toks: Token[] = [];
      this.inlineChildren(node, toks);
      out.push(`<pre>${finalise(toks, true)}</pre>`);
    } else if (tag === "blockquote") {
      const inner: string[] = [];
      this.blocks(node, inner);
      out.push(`<blockquote>${inner.join("")}</blockquote>`);
    } else if (LISTS.has(tag)) out.push(this.list(node, tag));
    else if (tag === "table") this.table(node, out);
    else if (tag === "hr") out.push("<hr>");
    else if (tag === "caption") out.push(this.leaf("p", node));
    else if (
      tag === "body" ||
      tag === "html" ||
      elements(node).some((c) => BLOCK.has(tagOf(c) as string))
    ) {
      this.blocks(node, out);
    } else out.push(this.leaf("p", node)); // div, section, ...: a paragraph
    this.depth--;
  }

  private list(node: Element, tag: string): string {
    this.enter();
    const items: string[] = [];
    for (const child of elements(node)) {
      const ctag = tagOf(child) as string;
      if (DROP.has(ctag)) continue;
      if (LISTS.has(ctag) && items.length) {
        // a list nested directly in a list belongs to the item above
        const last = items[items.length - 1];
        items[items.length - 1] = last.slice(0, -"</li>".length) + this.list(child, ctag) + "</li>";
      } else for (const item of this.li(child)) items.push(item);
    }
    this.depth--;
    return `<${tag}>${items.join("")}</${tag}>`;
  }

  /** One item, or more when text follows a nested list (that text becomes the next item). */
  private li(node: Element): string[] {
    const groups: Group[] = [[[], []]];
    this.liWalk(node, groups);
    const style = blockStyle(node);
    const out: string[] = [];
    groups.forEach(([toks, nested], n) => {
      const text = finalise(toks, false, true);
      if (n === 0 || text || nested.length) out.push(`<li${style}>${text}${nested.join("")}</li>`);
    });
    return out;
  }

  /** Flatten a list item: its paragraphs become lines, nested lists stay lists. */
  private liWalk(node: Element, groups: Group[]): void {
    this.enter();
    const breakLine = () => breakItemLine(groups[groups.length - 1]);
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (!text) continue;
        if (!hasContent(text)) {
          // White space before a paragraph (the newlines of a loose list) is dropped; elsewhere
          // it is read like any text, so a second pass reads the item the same.
          let next = child.nextSibling;
          while (next && next.nodeType !== 1) next = next.nextSibling;
          const nextTag = next ? (tagOf(next) as string) : "";
          if (BLOCK.has(nextTag) && !LISTS.has(nextTag)) continue;
        }
        add(groups, [["T", text]]);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = tagOf(child) as string;
      if (LISTS.has(tag)) groups[groups.length - 1][1].push(this.list(child as Element, tag));
      else if (BLOCK.has(tag)) {
        breakLine();
        this.liWalk(child as Element, groups);
        breakLine();
      } else {
        const toks: Token[] = [];
        this.inline(child as Element, toks);
        if (toks.length) add(groups, toks);
      }
    }
    this.depth--;
  }

  private table(node: Element, out: string[]): void {
    const rows: Element[] = [];
    const captions: Element[] = [];
    const collect = (parent: Element) => {
      for (const c of elements(parent)) {
        const t = tagOf(c);
        if (t === "tr") rows.push(c);
        else if (t === "thead" || t === "tbody" || t === "tfoot") collect(c);
        else if (t === "caption") captions.push(c);
      }
    };
    collect(node);
    for (const cap of captions) out.push(this.leaf("p", cap));
    const htmlRows: string[] = [];
    for (const tr of rows) {
      const cells: string[] = [];
      for (const td of elements(tr)) {
        const t = tagOf(td);
        if (t !== "td" && t !== "th") continue;
        let span = "";
        for (const name of ["colspan", "rowspan"]) {
          const m = (attr(td, name) ?? "").match(new RegExp(`^${S}*(\\d+)${S}*$`));
          if (m && Number(m[1]) > 1 && Number(m[1]) <= 1000) span += ` ${name}="${Number(m[1])}"`;
        }
        const inner: string[] = [];
        this.blocks(td, inner);
        cells.push(`<td${span}>${inner.join("") || "<p></p>"}</td>`);
      }
      if (cells.length) htmlRows.push(`<tr>${cells.join("")}</tr>`);
    }
    if (htmlRows.length) {
      const css = parseStyle(attr(node, "style"));
      const border = (css.get("border") || css.get("border-style") || "").toLowerCase();
      const plain =
        ["none", "0", "0px", "hidden"].includes(border) || (attr(node, "border") ?? "") === "0";
      out.push(`${plain ? '<table style="border:none">' : "<table>"}${htmlRows.join("")}</table>`);
    }
  }
}

function separate(toks: Token[]): void {
  for (let i = toks.length - 1; i >= 0; i--) {
    const [kind] = toks[i];
    if (kind === "O") continue;
    if (kind === "S" || isBr(toks[i])) return;
    toks.push(["S", "<br>"]);
    return;
  }
}

/** End the item's current line where a paragraph starts or ends. White space before the
 * break is source formatting: dropped (tabs too, as a second pass would), so a blank span,
 * "a <p>b" or "<b> </b><p>" leaves no space and no empty wrapper. */
function breakItemLine([toks, nested]: Group): void {
  if (nested.length) return;
  let i = toks.length;
  while (i > 0) {
    const [kind, raw] = toks[i - 1];
    if (kind === "C") i -= 1;
    else if (kind === "T") {
      const text = collapseWs(raw);
      let end = text.length;
      while (end > 0 && (text[end - 1] === " " || text[end - 1] === "\t")) end--;
      if (end) {
        toks[i - 1] = [kind, text.slice(0, end)];
        break;
      }
      toks.splice(i - 1, 1);
      i -= 1;
    } else if (kind === "O" && i < toks.length && toks[i][0] === "C") {
      toks.splice(i - 1, 2); // an inline wrapper left empty
      i -= 1;
    } else break;
  }
  if (toks.length && !isBr(toks[toks.length - 1])) toks.push(BR());
}

/** Add inline tokens to the item being built (a new item after a nested list). */
function add(groups: Group[], tokens: Token[]): void {
  if (groups[groups.length - 1][1].length) {
    if (tokens.every(([k, t]) => k === "T" && !hasContent(t))) return;
    groups.push([[], []]);
  }
  const toks = groups[groups.length - 1][0];
  for (const t of tokens) toks.push(t); // no push(...): it overflows on huge arrays
}

function spanStyle(node: Element, font: boolean): string {
  const css = parseStyle(attr(node, "style"));
  let fam: string | null = css.get("font-family") ?? null;
  let size = css.has("font-size") ? points(css.get("font-size") as string) : null;
  let fg = css.has("color") ? colour(css.get("color") as string) : null;
  const bg = css.has("background-color") ? colour(css.get("background-color") as string) : null;
  if (font) {
    fam = fam || attr(node, "face");
    const digits = (attr(node, "size") ?? "").match(new RegExp(`^${S}*([1-7])${S}*$`));
    if (size === null && digits) size = FONT_SIZES_PT[digits[1]];
    const c = attr(node, "color");
    if (fg === null && c) fg = colour(c);
  }
  return spanStyleText(fam ? fontFamily(fam) : null, size, fg, bg);
}

function blockStyle(node: Element): string {
  const css = parseStyle(attr(node, "style"));
  const parts: string[] = [];
  const align = pyStrip(css.get("text-align") || attr(node, "align") || "").toLowerCase();
  if (["left", "center", "right", "justify"].includes(align)) parts.push(`text-align:${align}`);
  if (css.has("margin-left")) {
    const pts = points(css.get("margin-left") as string);
    const level = pts === null ? 0 : pyRound(pts / 36);
    if (level >= 1) parts.push(`margin-left:${36 * Math.min(level, MAX_INDENT)}pt`);
  }
  const brk = (css.get("page-break-before") || css.get("break-before") || "").toLowerCase();
  if (brk === "always" || brk === "page") parts.push("page-break-before:always");
  return parts.length ? ` style="${parts.join(";")}"` : "";
}

/** A heading's style; without its own alignment it takes that of the first block inside it that
 * has one (`<h1><p align="center">`: the paragraph is part of the heading). */
function headingStyle(node: Element): string {
  const style = blockStyle(node);
  if (style.includes("text-align:")) return style;
  for (const child of Array.from(node.children)) {
    if (!BLOCK.has(child.localName.toLowerCase())) continue;
    const m = /text-align:[a-z]+/.exec(blockStyle(child));
    if (m) return ` style="${[m[0], ...(style ? [style.slice(8, -1)] : [])].join(";")}"`;
  }
  return style;
}

// ----- nesting cap -------------------------------------------------------------------------

/** Deepest element nesting kept as it is; deeper content is flattened (the same number on the
 * desktop), so how deep a page may go never depends on the call stack. */
export const MAX_NESTING = 100;
const TABLE_PARTS = set("thead tbody tfoot");
const STRUCTURE = set("table thead tbody tfoot tr ul ol");

/** Flatten what is nested more than MAX_NESTING elements deep (the desktop does the same): each
 * element there keeps only its text, line breaks and pictures. thead / tbody / tfoot don't
 * count, because an HTML5 parser adds a tbody where libxml2 doesn't. */
function capDepth(root: Element): void {
  const stack: [Element, number][] = elements(root)
    .reverse()
    .map((c): [Element, number] => [c, 1]);
  while (stack.length) {
    const [el, depth] = stack.pop() as [Element, number];
    const tag = tagOf(el) as string;
    if (DROP.has(tag)) continue;
    if (depth > MAX_NESTING) {
      flatten(el);
      continue;
    }
    const inner = TABLE_PARTS.has(tag) ? depth : depth + 1;
    const kids = elements(el);
    for (let i = kids.length - 1; i >= 0; i--) stack.push([kids[i], inner]);
  }
}

type FlatItem = string | Element;
type FlatEvent = ["text", string] | ["open", Element] | ["close"];

/** The text, <br>s and pictures inside `el`, in order; a block inside it starts and ends a
 * line (white space beside one is dropped). */
function flatItems(el: Element): FlatItem[] {
  const doc = el.ownerDocument;
  const out: FlatItem[] = [];
  let pending = false; // a block started or ended: the next content goes on a new line
  const content = (item: FlatItem) => {
    const last = out[out.length - 1];
    if (pending && out.length && !(typeof last !== "string" && tagOf(last) === "br")) {
      out.push(doc.createElement("br"));
    }
    pending = false;
    out.push(item);
  };
  const stack: FlatEvent[] = [];
  const pushChildren = (node: Element) => {
    const events: FlatEvent[] = [];
    for (const c of Array.from(node.childNodes)) {
      if (c.nodeType === 3) events.push(["text", (c as Text).data]);
      else if (c.nodeType === 1) events.push(["open", c as Element]);
    }
    for (let i = events.length - 1; i >= 0; i--) stack.push(events[i]);
  };
  pushChildren(el);
  while (stack.length) {
    const ev = stack.pop() as FlatEvent;
    if (ev[0] === "text") {
      const text = ev[1];
      if (!text) continue;
      if (!hasContent(text)) {
        if (!pending) out.push(text);
        continue;
      }
      content(text);
    } else if (ev[0] === "close") pending = true;
    else {
      const node = ev[1];
      const tag = tagOf(node) as string;
      if (DROP.has(tag)) continue;
      if (tag === "br") {
        out.push(doc.createElement("br"));
        pending = false;
      } else if (tag === "img") {
        const img = doc.createElement("img");
        for (const name of node.getAttributeNames()) img.setAttribute(name, node.getAttribute(name) as string);
        content(img);
      } else {
        if (BLOCK.has(tag)) {
          pending = true;
          stack.push(["close"]);
        }
        pushChildren(node);
      }
    }
  }
  return out;
}

/** Replace `el`'s content by its flat text (in an element its parent can hold). */
function flatten(el: Element): void {
  const items = flatItems(el);
  const doc = el.ownerDocument;
  const parent = el.parentNode as Element;
  const ptag = parent.nodeType === 1 ? tagOf(parent) : null;
  const tag = tagOf(el) as string;
  let shell: string[];
  if (ptag !== null && LISTS.has(ptag)) shell = tag === "li" ? [] : ["li"];
  else if (ptag !== null && (ptag === "table" || TABLE_PARTS.has(ptag))) shell = ["tr", "td"];
  else if (ptag === "tr") shell = tag === "td" || tag === "th" ? [] : ["td"];
  else shell = STRUCTURE.has(tag) ? ["div"] : [];
  let target: Element = el;
  if (shell.length) {
    const outer = doc.createElement(shell[0]);
    target = shell.length === 1 ? outer : outer.appendChild(doc.createElement(shell[1]));
    parent.replaceChild(outer, el);
  } else {
    while (el.firstChild) el.removeChild(el.firstChild);
  }
  for (const item of items) target.appendChild(typeof item === "string" ? doc.createTextNode(item) : item);
  target.normalize(); // one text node per run, as in the desktop's tree
}

// ----- parsing -----------------------------------------------------------------------------

/** A whole page with nothing but a doctype and comments in it (linear: no regex backtracking). */
function onlyDoctypeAndComments(html: string): boolean {
  let i = 0;
  for (;;) {
    while (i < html.length && /\s/.test(html[i])) i++;
    if (i >= html.length) return true;
    let end = -1;
    if (html.slice(i, i + 9).toLowerCase() === "<!doctype") end = html.indexOf(">", i + 9) + 1;
    else if (html.startsWith("<!--", i)) {
      end = html.indexOf("-->", i + 4);
      if (end >= 0) end += 3;
    }
    if (end <= 0) return false;
    i = end;
  }
}

function parseDocument(html: string): Document {
  const doc = new DOMParser().parseFromString(libxmlLike(html5Refs(html)), "text/html");
  for (const p of Array.from(doc.getElementsByTagName("p"))) {
    if (p.hasAttribute(P_MARK)) p.removeAttribute(P_MARK);
    else if (!p.firstChild) p.remove(); // made for a stray </p>
  }
  return doc;
}

const FULL_HTML = new RegExp(`^${S}*<(?:html|!doctype)`, "i");

/** The parsed body, or null for empty input (lxml's fragment_fromstring). Read the way the
 * normaliser reads it (also by the document editor, so the subset loads exactly); text and
 * attributes still need `restore` from subset-scan. */
export function parseBody(html: string): HTMLElement | null {
  html = html.replace(XML_DECL, "");
  if (!pyStrip(html)) return null;
  // Like lxml's fragment_fromstring: a page that starts with <html> or <!doctype> is read as a
  // whole document (a lone doctype has nothing in it); anything else is put inside a <body>, so
  // a leading <noscript> or <title> stays in the content instead of moving into <head>.
  if (FULL_HTML.test(html)) {
    if (onlyDoctypeAndComments(html)) {
      throw new Error(UNREADABLE);
    }
  } else {
    // No closing tags: after an unclosed <plaintext> or <xmp> they would show up as text.
    html = `<html><body>${html}`;
  }
  const body = parseDocument(html).body;
  if (!body || body.localName !== "body") throw new Error(UNREADABLE);
  return body;
}

/** Every element, <template> contents included (lxml keeps them in the tree). */
function* allElements(root: Node): Generator<Element> {
  const stack: Node[] = [root];
  while (stack.length) {
    const node = stack.pop() as Node;
    if (node.nodeType === 1) yield node as Element;
    const kids = Array.from(
      node.nodeType === 1 && (node as Element).localName === "template"
        ? (node as HTMLTemplateElement).content.childNodes
        : node.childNodes,
    );
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
  }
}

/** Python-Markdown marks fenced code with class="language-x": not the page's styling. */
function isLanguageClass(value: string): boolean {
  return value
    .split(new RegExp(`${S}+`))
    .filter((c) => c)
    .every((c) => c.startsWith("language-"));
}

/** What normalising `html` drops that a reader would miss (warnings, in a fixed order). */
function lostParts(page: Document): string[] {
  const found = new Set<string>();
  for (const el of allElements(page)) {
    const tag = tagOf(el) as string;
    const rel = (attr(el, "rel") ?? "").toLowerCase();
    if (tag === "style" || tag === "script" || (tag === "link" && rel.includes("stylesheet")))
      found.add(W_PAGESTYLE);
    else if (RAW_DROPPED.has(tag)) found.add(W_RAWHTML);
    else if (tag === "img") {
      const parsed = urlparse(pyStrip(attr(el, "src") ?? ""));
      // A URL that can't be split has a broken host part: a web picture, dropped.
      if (!parsed || parsed.scheme === "http" || parsed.scheme === "https" || parsed.netloc)
        found.add(W_WEBPICS);
    }
    const cls = attr(el, "class");
    if (
      (cls && !isLanguageClass(cls)) ||
      el.getAttributeNames().some((k) => k.toLowerCase().startsWith("on"))
    ) {
      found.add(W_PAGESTYLE);
    }
  }
  return [W_PAGESTYLE, W_WEBPICS, W_RAWHTML].filter((w) => found.has(w));
}

/** The page's <title>, with its white space collapsed ("" if there is none). */
export function htmlTitle(html: string): string {
  for (const el of allElements(parseDocument(html.replace(XML_DECL, "")))) {
    if (tagOf(el) === "title") {
      return restore(el.textContent ?? "")
        .split(new RegExp(`${S}+`))
        .filter((w) => w)
        .join(" ");
    }
  }
  return "";
}

/** Rewrite `html` into the editor's subset, plus the warnings for what was dropped. */
export function normaliseHtmlReport(
  html: string,
  opts: NormaliseOptions = {},
): { html: string; images: DocImages; warnings: string[] } {
  const root = parseBody(html);
  if (root === null) return { html: "", images: new Map(), warnings: [] };
  const warnings = lostParts(root.ownerDocument); // the whole page, <head> included
  capDepth(root);
  const reserved = new Set<string>();
  for (const el of allElements(root)) {
    const src = tagOf(el) === "img" ? pyStrip(attr(el, "src") ?? "") : "";
    if (CANON_IMG.test(src)) reserved.add(src);
  }
  const norm = new Normaliser(reserved, opts.resolveImage);
  const out: string[] = [];
  try {
    norm.blocks(root, out);
  } catch (err) {
    if (err instanceof TooDeep || isStackOverflow(err)) throw new Error(UNREADABLE);
    throw err;
  }
  if (norm.lostPictures) warnings.push(W_PICTURES);
  return { html: out.join(""), images: norm.images, warnings };
}

/** Rewrite `html` into the editor's subset; returns the fragment and its pictures. */
export function normaliseHtml(
  html: string,
  opts: NormaliseOptions = {},
): { html: string; images: DocImages } {
  const { html: out, images } = normaliseHtmlReport(html, opts);
  return { html: out, images };
}

/** Number of characters (code points) of visible text in `html`. */
export function textLength(html: string): number {
  const root = parseBody(html);
  if (root === null) return 0;
  let n = 0;
  const stack: Node[] = [root];
  while (stack.length) {
    const node = stack.pop() as Node;
    if (node.nodeType === 3) {
      // Code points, like Python's len (a U+FDD0 standing for \r is one character too).
      const text = (node as Text).data;
      for (let i = 0; i < text.length; i++) {
        const c = text.charCodeAt(i);
        const low = c >= 0xdc00 && c <= 0xdfff;
        if (!low || i === 0 || (text.charCodeAt(i - 1) & 0xfc00) !== 0xd800) n++;
      }
    } else if (node === root || (node.nodeType === 1 && !DROP.has(tagOf(node) as string))) {
      for (const child of Array.from(node.childNodes)) stack.push(child);
    }
  }
  return n;
}
