/**
 * Read a DOCX file into the editor's HTML subset, its pictures and "can't keep" warnings: a
 * port of the desktop's `src/umc/doceditor/docx_reader.py` (the mirror image of the writer).
 *
 * Heading 1-9 / Title -> h1-h3, Quote -> blockquote, Code -> pre, list paragraphs
 * (`w:numPr`, also through the paragraph style) -> ul / ol, tables -> table (borderless ones
 * get `style="border:none"`), `w:hyperlink` -> a, direct run properties and the run's
 * character style -> b / i / u / s / span. The result is passed through `normaliseHtml`.
 *
 * Deliberate difference: pictures in a format browsers can't show (TIFF, EMF, …) are left
 * out with W_PICTURES; the desktop converts them to PNG with Pillow. Needs DOMParser (the
 * page, not a worker).
 */

import { CT, NS, RT } from "./docx-template";
import {
  actualName,
  contentTypeOf,
  decodeXml,
  isW,
  parseXml,
  partText,
  relsFor,
  resolve,
  unzipPackage,
  wAttr,
} from "./docx-package";
import {
  BUILTIN_IDS,
  BUILTIN_LIST,
  BUILTIN_LIST_IDS,
  Ctx,
  listInfo,
  numPrOf,
  own,
  styleName,
  wChild,
  wVal,
} from "./docx-styles";
import { escAttr, escText, firstWord, pyG, pyInt, pyRound, pyRoundTo, pyStrip } from "./pycompat";
import { normaliseHtml } from "./subset";
import {
  type OpenedDocument,
  TOO_LARGE,
  W_COLUMNS,
  W_COMMENTS,
  W_EQUATIONS,
  W_FIELDS,
  W_HEADERS,
  W_NOTES,
  W_OBJECTS,
  W_PICTURES,
  W_RTL_RUBY,
  W_TEXTBOX,
  W_TRACKED,
  nextImageName,
} from "./types";

const UNREADABLE = "This Word document could not be opened.";
const EMU_PER_PX = 9525;
const NBSP = " ";
const WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape";
const VML = "urn:schemas-microsoft-com:vml";
const MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math";
const MC = "http://schemas.openxmlformats.org/markup-compatibility/2006";

const HEADINGS: Record<string, string> = { Title: "h1" };
for (let n = 1; n <= 9; n++) HEADINGS[`Heading ${n}`] = `h${Math.min(n, 3)}`;
const BLOCK_STYLES: Record<string, string> = {
  ...HEADINGS,
  Quote: "quote",
  "Intense Quote": "quote",
  Code: "pre",
  "HTML Preformatted": "pre",
  "Source Code": "pre",
};
const HEX = /^[0-9A-Fa-f]{1,6}$/;
const ALIGN: Record<string, string> = {
  left: "left",
  center: "center",
  right: "right",
  both: "justify",
};
const HIGHLIGHT: Record<string, string> = {
  yellow: "#ffff00",
  green: "#00ff00",
  cyan: "#00ffff",
  magenta: "#ff00ff",
  blue: "#0000ff",
  red: "#ff0000",
  darkBlue: "#000080",
  darkCyan: "#008080",
  darkGreen: "#008000",
  darkMagenta: "#800080",
  darkRed: "#800000",
  darkYellow: "#808000",
  darkGray: "#808080",
  lightGray: "#c0c0c0",
  black: "#000000",
  white: "#ffffff",
};
// The formats a browser shows; anything else is left out (the desktop converts it).
const IMAGE_EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/jpg": "jpg",
  "image/gif": "gif",
  "image/bmp": "bmp",
  "image/x-ms-bmp": "bmp",
  "image/webp": "webp",
};
const TRACKED = new Set(["ins", "del", "moveFrom", "moveTo"]);
// A ruby annotation (w:rt) and a right-to-left direction are dropped (their text is kept).
const RTL_RUBY = new Set(["rt", "dir", "bdo"]);
const TRANSPARENT = new Set([
  "sdt",
  "sdtContent",
  "smartTag",
  "customXml",
  "ins",
  "moveTo",
  "fldSimple",
  "dir",
  "bdo",
]);
// Wrappers around block content that are read through (w:del / w:moveFrom are dropped).
const BLOCK_WRAPPERS = new Set(["sdtContent", "customXml", "ins", "moveTo", "smartTag"]);
const UNIVERSAL_EMU: Record<string, number> = {
  mm: 36000,
  cm: 360000,
  in: 914400,
  pt: 12700,
  pc: 152400,
  pi: 152400,
};

interface Fmt {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  family: string | null;
  size: number | null;
  color: string | null;
  background: string | null;
  key: string; // equal formats have equal keys
}

/** kind "t" (text), "br" or "img" (value = the <img> HTML). */
interface Item {
  kind: "t" | "br" | "img";
  value: string;
  fmt: Fmt;
  link: string | null;
}

/** Inline content of a paragraph; a page break inside it starts a new segment. */
interface Segment {
  items: Item[];
  pageBreak: boolean;
  last: string; // the last character so far ("" at the start, "\n" after a break)
}
const segment = (): Segment => ({ items: [], pageBreak: false, last: "" });

// ----- small helpers ----------------------------------------------------------------------

const wFirst = (el: Element, local: string, ns: string = NS.w): Element | null =>
  el.getElementsByTagNameNS(ns, local)[0] ?? null;

const isMc = (el: Element, local: string): boolean =>
  el.namespaceURI === MC && el.localName === local;

/** The branch of an `mc:AlternateContent` that is read: its Fallback, else its Choice. */
function alternative(node: Element): Element | null {
  let choice: Element | null = null;
  for (let c = node.firstElementChild; c; c = c.nextElementSibling) {
    if (isMc(c, "Fallback")) return c;
    if (!choice && isMc(c, "Choice")) choice = c;
  }
  return choice;
}

/** The w:`local` children of `parent` (rows of a table, cells of a row), also those inside
 * content controls (`w:sdt`) and custom XML. */
function wrapped(parent: Element, local: string, into: Element[] = []): Element[] {
  for (let node = parent.firstElementChild; node; node = node.nextElementSibling) {
    if (isW(node, local)) into.push(node);
    else if (isW(node, "sdt")) {
      const inner = wChild(node, "sdtContent");
      if (inner) wrapped(inner, local, into);
    } else if (isW(node, "customXml")) wrapped(node, local, into);
  }
  return into;
}

/** The character of a `w:sym` (its hex code; a Private Use one is kept as it is), or "" for a
 * code that isn't a character XML can hold. */
function symbol(code: string | null): string {
  if (!code || !HEX.test(code)) return "";
  const n = parseInt(code, 16);
  const bad =
    n < 0x20 ||
    (n >= 0xd800 && n <= 0xdfff) ||
    (n >= 0xfdd0 && n <= 0xfdef) ||
    (n & 0xfffe) === 0xfffe ||
    n > 0x10ffff;
  return bad ? "" : String.fromCodePoint(n);
}

/** lxml's `el.text`: the text before the first child node that isn't text. */
function leadText(el: Element): string {
  let out = "";
  for (let n = el.firstChild; n && (n.nodeType === 3 || n.nodeType === 4); n = n.nextSibling) {
    out += (n as CharacterData).data;
  }
  return out;
}

// ----- inline content --------------------------------------------------------------------

/** An on/off run property (null when it isn't set). */
function flag(rPr: Element | null, name: string): boolean | null {
  const el = wChild(rPr, name);
  if (!el) return null;
  const val = (wAttr(el, "val") ?? "").toLowerCase();
  if (name === "u") return val !== "none";
  return val !== "0" && val !== "false" && val !== "off";
}

function rgb(rPr: Element | null): string | null {
  const val = wVal(rPr, "color");
  return val && val.length === 6 && val.toLowerCase() !== "auto" ? `#${val.toLowerCase()}` : null;
}

/** python-docx's `Font.size` in points, rounded to 2 places (null when unset or unreadable). */
function fontSize(rPr: Element | null): number | null {
  const raw = wVal(rPr, "sz");
  if (raw === null) return null;
  let emu: number;
  if (/[mnp]/.test(raw)) {
    const mult = Object.hasOwn(UNIVERSAL_EMU, raw.slice(-2)) ? UNIVERSAL_EMU[raw.slice(-2)] : 0;
    const q = Number(pyStrip(raw.slice(0, -2)));
    if (!mult || !pyStrip(raw.slice(0, -2)) || !Number.isFinite(q)) return null;
    emu = pyRound(q * mult);
  } else {
    const halfPoints = pyInt(raw);
    if (halfPoints === null) return null;
    emu = Math.trunc((halfPoints / 2) * 12700);
  }
  return pyRoundTo(emu / 12700, 2);
}

/** The rPr of the run's character style and the styles it is based on, nearest first. */
function charStyleRprs(ctx: Ctx, rPr: Element | null): (Element | null)[] {
  const out: (Element | null)[] = [];
  let id = wVal(rPr, "rStyle");
  while (id && out.length < 20) {
    const style = ctx.style(id);
    if (!style || wAttr(style, "type") !== "character") break;
    out.push(wChild(style, "rPr"));
    id = wVal(style, "basedOn");
  }
  return out;
}

/** Direct run formatting, falling back to the run's character style chain (never the
 * paragraph style or document defaults: a saved-over file keeps those itself). */
function runFmt(ctx: Ctx, r: Element, inHeading: boolean, inLink: boolean): Fmt {
  const rPr = wChild(r, "rPr");
  const chain = charStyleRprs(ctx, rPr);
  const resolved = (name: string, fromStyles = true): boolean => {
    let value = flag(rPr, name);
    if (fromStyles) for (const s of chain) value ??= flag(s, name);
    return Boolean(value);
  };
  let color = rgb(rPr);
  if (color === null && !inLink) {
    // a link's style colour is how links look anyway
    for (const s of chain) color ??= rgb(s);
  }
  const fill = wAttr(wChild(rPr, "shd"), "fill");
  let background = own(HIGHLIGHT, wVal(rPr, "highlight") ?? "") ?? null;
  if (fill && fill.toLowerCase() !== "auto" && fill.toLowerCase() !== "ffffff") {
    background = `#${fill.toLowerCase()}`;
  }
  const fmt = {
    bold: resolved("b") && !inHeading,
    italic: resolved("i"),
    underline: resolved("u", !inLink),
    strike: resolved("strike"),
    family: wAttr(wChild(rPr, "rFonts"), "ascii"),
    size: inHeading ? null : fontSize(rPr),
    color,
    background,
  };
  return { ...fmt, key: JSON.stringify(fmt) };
}

function picture(ctx: Ctx, drawing: Element): string | null {
  const blip = wFirst(drawing, "blip", NS.a);
  const id = blip?.getAttributeNS(NS.r, "embed") ?? null;
  if (id === null) return null;
  const part = ctx.partOf(id);
  if (!part) {
    ctx.warn(W_PICTURES);
    return null;
  }
  const type = contentTypeOf(ctx.types, part.name) ?? "";
  const ext = own(IMAGE_EXT, type);
  if (ext === undefined) {
    console.warn(`Picture of type ${type} can't be shown here; left out`);
    ctx.warn(W_PICTURES);
    return null;
  }
  const name = nextImageName(ctx.images, ext);
  ctx.images.set(name, part.data);
  let attrs = ` src="${name}"`;
  const extent = wFirst(drawing, "extent", NS.wp);
  if (extent) {
    for (const [key, attr] of [
      ["cx", "width"],
      ["cy", "height"],
    ]) {
      const px = pyRound((pyInt(extent.getAttribute(key) || "0") ?? 0) / EMU_PER_PX);
      if (px > 0) attrs += ` ${attr}="${px}"`;
    }
  }
  return `<img${attrs}>`;
}

/** Spaces HTML would collapse become no-break spaces: one at the start of a line, or one
 * after another space (the writer turns them back into ordinary spaces). */
function keepSpaces(text: string, before: string): string {
  if (!text.includes(" ")) return text;
  const out: string[] = [];
  for (const c of text) {
    const kept =
      c === " " && (before === "" || before === "\n" || before === " " || before === NBSP);
    out.push(kept ? NBSP : c);
    before = kept || c === NBSP ? " " : c;
  }
  return out.join("");
}

function runItems(
  ctx: Ctx,
  r: Element,
  fmt: Fmt,
  link: string | null,
  segs: Segment[],
  heading: boolean,
): void {
  const add = (kind: Item["kind"], value: string) => {
    const seg = segs[segs.length - 1];
    seg.items.push({ kind, value, fmt, link });
    if (kind === "br") seg.last = "\n";
    else if (kind === "img") seg.last = "￼";
    else if (value) seg.last = value[value.length - 1];
  };
  for (let child = r.firstElementChild; child; child = child.nextElementSibling) {
    if (isMc(child, "AlternateContent")) {
      const branch = alternative(child);
      if (branch) runItems(ctx, branch, fmt, link, segs, heading);
      continue;
    }
    if (child.namespaceURI !== NS.w) continue;
    const tag = child.localName;
    if (tag === "ruby") {
      // the base text; the annotation above it is left out
      const base = wChild(child, "rubyBase");
      if (base) inlineNodes(ctx, base, heading, link, segs);
    } else if (tag === "sym") {
      const char = symbol(wAttr(child, "char"));
      const font = wAttr(child, "font");
      if (char && font && char >= "\ue000" && char <= "\uf8ff") {
        // a symbol font's own character: it keeps the font
        const { key: _, ...plain } = fmt;
        const own = { ...plain, family: font };
        const seg = segs[segs.length - 1];
        seg.items.push({ kind: "t", value: char, fmt: { ...own, key: JSON.stringify(own) }, link });
        seg.last = char;
      } else if (char) add("t", keepSpaces(char, segs[segs.length - 1].last));
    } else if (tag === "t") add("t", keepSpaces(leadText(child), segs[segs.length - 1].last));
    else if (tag === "tab") add("t", "\t");
    else if (tag === "noBreakHyphen") add("t", "-");
    else if (tag === "cr") add("br", "");
    else if (tag === "br") {
      if (wAttr(child, "type") === "page") {
        if (segs[segs.length - 1].items.length) segs.push(segment());
        segs[segs.length - 1].pageBreak = true;
      } else add("br", "");
    } else if (tag === "drawing") {
      const img = picture(ctx, child);
      if (img !== null) add("img", img);
    }
  }
}

function inlineNodes(
  ctx: Ctx,
  parent: Element,
  heading: boolean,
  link: string | null,
  segs: Segment[],
): void {
  for (let node = parent.firstElementChild; node; node = node.nextElementSibling) {
    if (isMc(node, "AlternateContent")) {
      const branch = alternative(node);
      if (branch) inlineNodes(ctx, branch, heading, link, segs);
      continue;
    }
    if (node.namespaceURI !== NS.w) continue;
    const tag = node.localName;
    if (tag === "r") {
      runItems(ctx, node, runFmt(ctx, node, heading, link !== null), link, segs, heading);
    }
    else if (tag === "hyperlink") {
      const rel = ctx.rels.get(node.getAttributeNS(NS.r, "id") ?? "");
      const href = rel?.external ? rel.target : null;
      inlineNodes(ctx, node, heading, href || link, segs);
    } else if (TRANSPARENT.has(tag)) {
      const inner = tag === "sdt" ? wChild(node, "sdtContent") : node;
      if (inner) inlineNodes(ctx, inner, heading, link, segs);
    }
  }
}

function renderItems(items: Item[], pre: boolean): string {
  const out: string[] = [];
  let i = 0;
  while (i < items.length) {
    const link = items[i].link;
    let j = i;
    while (j < items.length && items[j].link === link) j++;
    const inner = renderGroup(items, i, j, pre);
    out.push(link ? `<a href="${escAttr(link)}">${inner}</a>` : inner);
    i = j;
  }
  return out.join("");
}

function renderGroup(items: Item[], from: number, to: number, pre: boolean): string {
  const out: string[] = [];
  let i = from;
  while (i < to) {
    const { kind, value, fmt } = items[i];
    i++;
    if (kind === "br") out.push(pre ? "\n" : "<br>");
    else if (kind === "img") out.push(value);
    else {
      // merge neighbouring text runs with identical formatting
      const parts = [value];
      while (i < to && items[i].kind === "t" && items[i].fmt.key === fmt.key) {
        parts.push(items[i++].value);
      }
      let text = parts.join("");
      if (pre) text = text.replaceAll(NBSP, " "); // <pre> keeps every space as it is
      out.push(wrap(escText(text), fmt));
    }
  }
  return out.join("");
}

function wrap(text: string, fmt: Fmt): string {
  const style: string[] = [];
  if (fmt.family) style.push(`font-family:${fmt.family}`);
  if (fmt.size) style.push(`font-size:${pyG(fmt.size)}pt`);
  if (fmt.color) style.push(`color:${fmt.color}`);
  if (fmt.background) style.push(`background-color:${fmt.background}`);
  let out = style.length ? `<span style="${escAttr(style.join(";"))}">${text}</span>` : text;
  if (fmt.strike) out = `<s>${out}</s>`;
  if (fmt.underline) out = `<u>${out}</u>`;
  if (fmt.italic) out = `<i>${out}</i>`;
  if (fmt.bold) out = `<b>${out}</b>`;
  return out;
}

// ----- blocks ----------------------------------------------------------------------------

function isHr(p: Element): boolean {
  const val = wAttr(wChild(wChild(wChild(p, "pPr"), "pBdr"), "bottom"), "val");
  return val !== null && val !== "none" && val !== "nil";
}

/** A paragraph with no text, picture or break. */
function isBlank(p: Element): boolean {
  return !(
    !isW(p, "p") ||
    pyStrip(p.textContent ?? "") ||
    wFirst(p, "drawing") ||
    wFirst(p, "br") ||
    isHr(p)
  );
}

function paraStyle(p: Element, withIndent: boolean, pageBreak: boolean): string {
  const pPr = wChild(p, "pPr");
  const parts: string[] = [];
  const align = own(ALIGN, wVal(pPr, "jc") ?? "");
  if (align) parts.push(`text-align:${align}`);
  const left = wAttr(wChild(pPr, "ind"), "left");
  if (withIndent && left) {
    const twips = pyInt(left);
    const level = twips === null ? 0 : pyRound(twips / 20 / 36);
    if (level >= 1) parts.push(`margin-left:${36 * level}pt`);
  }
  const pbb = wChild(pPr, "pageBreakBefore");
  const pbbVal = pbb ? wAttr(pbb, "val") : null;
  if (pageBreak || (pbb && pbbVal !== "0" && pbbVal !== "false" && pbbVal !== "off")) {
    parts.push("page-break-before:always");
  }
  return parts.length ? ` style="${parts.join(";")}"` : "";
}

const visible = (borders: Element): boolean =>
  Array.from(borders.children).some((edge) => {
    const val = wAttr(edge, "val") || "none";
    return val !== "none" && val !== "nil";
  });

/** Bordered when any cell has a visible border (LibreOffice writes only those), else by
 * the table's own borders, else by its table style's (no style and no borders: none). */
function hasBorders(ctx: Ctx, tbl: Element): boolean {
  for (const tcBorders of Array.from(tbl.getElementsByTagNameNS(NS.w, "tcBorders"))) {
    if (visible(tcBorders)) return true;
  }
  const tblPr = wChild(tbl, "tblPr");
  let borders = wChild(tblPr, "tblBorders");
  if (borders) return visible(borders);
  let id = wVal(tblPr, "tblStyle");
  for (let n = 0; n < 20; n++) {
    const style = id ? ctx.style(id) : null;
    if (!style) return false;
    borders = wChild(wChild(style, "tblPr"), "tblBorders");
    if (borders) return visible(borders);
    id = wVal(style, "basedOn");
  }
  return false;
}

interface Cell {
  tc: Element;
  colspan: number;
  rowspan: number;
}

/** Turns the paragraphs and tables of one container (body or table cell) into HTML. */
class Blocks {
  private out: string[] = [];
  private lists: [number, string][] = []; // open lists (numId, tag); the last <li> is open
  private quote = false;

  constructor(private ctx: Ctx) {}

  run(container: Element): string {
    const nodes: Element[] = [];
    this.children(container, nodes);
    const n = nodes.length;
    if (n > 1 && isW(nodes[n - 2], "tbl") && isBlank(nodes[n - 1])) {
      nodes.pop(); // Word needs a paragraph after a final table; it isn't content
    }
    for (const node of nodes) {
      if (isW(node, "tbl")) {
        this.closeAll();
        this.out.push(this.table(node));
      } else this.paragraph(node);
    }
    this.closeAll();
    return this.out.join("");
  }

  private children(container: Element, into: Element[]): void {
    for (let node = container.firstElementChild; node; node = node.nextElementSibling) {
      if (isMc(node, "AlternateContent")) {
        const branch = alternative(node);
        if (branch) this.children(branch, into);
        continue;
      }
      if (node.namespaceURI !== NS.w) continue;
      if (node.localName === "sdt") {
        const inner = wChild(node, "sdtContent");
        if (inner) this.children(inner, into);
      } else if (BLOCK_WRAPPERS.has(node.localName)) this.children(node, into);
      else if (node.localName === "p" || node.localName === "tbl") into.push(node);
    }
  }

  private closeLists(keep = 0): void {
    while (this.lists.length > keep) {
      this.out.push(`</li></${(this.lists.pop() as [number, string])[1]}>`);
    }
  }

  private closeQuote(): void {
    if (this.quote) {
      this.out.push("</blockquote>");
      this.quote = false;
    }
  }

  private closeAll(): void {
    this.closeLists();
    this.closeQuote();
  }

  private paragraph(p: Element): void {
    const chain = this.ctx.styleChain(p);
    let kind = "p";
    for (const s of chain) {
      const name = styleName(s);
      const block = name === null ? undefined : own(BLOCK_STYLES, name);
      if (block !== undefined) {
        kind = block;
        break;
      }
    }
    const builtin = kind === "p" ? this.ctx.undefinedStyle(p) : null;
    if (builtin !== null) kind = own(BUILTIN_IDS, builtin) ?? kind;
    let info = kind === "p" ? listInfo(this.ctx, p, chain) : null;
    if (info === null && builtin !== null && numPrOf(p) === null) {
      // ListBullet, ListBullet2 (one level deeper), ..., ListNumber
      const m = BUILTIN_LIST.exec(builtin);
      if (m) {
        const tag = m[1] === "Bullet" ? "ul" : "ol";
        info = [BUILTIN_LIST_IDS[m[1]], tag, Number(m[2] || "1") - 1];
      }
    }
    const segs = [segment()];
    inlineNodes(this.ctx, p, kind === "h1" || kind === "h2" || kind === "h3", null, segs);
    if (kind === "p" && info === null && segs.length === 1 && !segs[0].items.length && isHr(p)) {
      this.closeAll();
      this.out.push("<hr>");
      return;
    }
    if (info !== null) {
      const items: Item[] = [];
      for (const s of segs) for (const it of s.items) items.push(it);
      this.listItem(info, p, items);
      return;
    }
    this.closeLists();
    if (kind === "quote") {
      if (!this.quote) {
        this.out.push("<blockquote>");
        this.quote = true;
      }
    } else this.closeQuote();
    const tag = kind === "quote" ? "p" : kind;
    for (const seg of segs) {
      const style = paraStyle(p, true, seg.pageBreak);
      this.out.push(`<${tag}${style}>${renderItems(seg.items, kind === "pre")}</${tag}>`);
    }
  }

  private listItem(info: [number, string, number], p: Element, items: Item[]): void {
    const [numId, tag] = info;
    this.closeQuote();
    const ilvl = Math.min(info[2], this.lists.length);
    this.closeLists(ilvl + 1);
    if (this.lists.length === ilvl + 1) {
      const [openId, openTag] = this.lists[this.lists.length - 1];
      if (openId === numId && openTag === tag) this.out.push("</li>");
      else this.closeLists(ilvl);
    }
    if (this.lists.length === ilvl) {
      this.lists.push([numId, tag]);
      this.out.push(`<${tag}>`);
    }
    this.out.push(`<li${paraStyle(p, false, false)}>${renderItems(items, false)}`);
  }

  private table(tbl: Element): string {
    const rows: Cell[][] = [];
    const started = new Map<number, Cell>(); // grid column -> cell a bare vMerge continues
    for (const tr of wrapped(tbl, "tr")) {
      const cells: Cell[] = [];
      let col = 0;
      for (const tc of wrapped(tr, "tc")) {
        const tcPr = wChild(tc, "tcPr");
        const span = pyInt(wVal(tcPr, "gridSpan") || "1") ?? 1;
        const vm = wChild(tcPr, "vMerge");
        const open = started.get(col);
        if (vm && wAttr(vm, "val") !== "restart" && open) open.rowspan++;
        else {
          const cell = { tc, colspan: span, rowspan: 1 };
          cells.push(cell);
          started.set(col, cell);
        }
        col += span;
      }
      rows.push(cells);
    }
    const out = [hasBorders(this.ctx, tbl) ? "<table>" : '<table style="border:none">'];
    for (const cells of rows) {
      out.push("<tr>");
      for (const cell of cells) {
        let attrs = "";
        if (cell.colspan > 1) attrs += ` colspan="${cell.colspan}"`;
        if (cell.rowspan > 1) attrs += ` rowspan="${cell.rowspan}"`;
        const inner = new Blocks(this.ctx).run(cell.tc) || "<p></p>";
        out.push(`<td${attrs}>${inner}</td>`);
      }
      out.push("</tr>");
    }
    out.push("</table>");
    return out.join("");
  }
}

// ----- warnings --------------------------------------------------------------------------

/** True for a field the editor can't keep (anything but page numbers). */
function unsupportedField(instr: string): boolean {
  const word = firstWord(instr).toUpperCase();
  return word !== "" && word !== "PAGE" && word !== "NUMPAGES";
}

function collectWarnings(ctx: Ctx, root: Element, body: Element): void {
  const sections: Element[] = [];
  for (let node = body.firstElementChild; node; node = node.nextElementSibling) {
    if (isW(node, "sectPr")) sections.push(node);
    else if (isW(node, "p")) {
      const pPr = wChild(node, "pPr");
      for (const s of pPr ? Array.from(pPr.children) : []) if (isW(s, "sectPr")) sections.push(s);
    }
  }
  for (const sect of sections) {
    for (const kind of ["headerReference", "footerReference"]) {
      for (const type of ["default", "first", "even"]) {
        const refs = Array.from(sect.children);
        const ref = refs.find((c) => isW(c, kind) && wAttr(c, "type") === type);
        if (!ref) continue; // linked to the previous section
        const part = ctx.xmlOf(ref.getAttributeNS(NS.r, "id"));
        const el = part?.documentElement;
        if (el && (pyStrip(el.textContent ?? "") || wFirst(el, "drawing"))) ctx.warn(W_HEADERS);
      }
    }
  }
  const found = new Set<string>();
  for (const rel of ctx.rels.values()) if (rel.type.endsWith("/comments")) found.add(W_COMMENTS);
  const stack: Element[] = [root];
  while (stack.length) {
    const el = stack.pop() as Element;
    for (let c = el.lastElementChild; c; c = c.previousElementSibling) stack.push(c);
    const ns = el.namespaceURI;
    const tag = el.localName;
    const box = ns === NS.w ? "txbxContent" : ns === WPS ? "wsp" : ns === VML ? "shape" : null;
    if (tag === box) found.add(W_TEXTBOX);
    if (ns === MATH && (tag === "oMath" || tag === "oMathPara")) found.add(W_EQUATIONS);
    if (ns !== NS.w) continue;
    if (tag === "cols" && (pyInt(wAttr(el, "num") || "1") ?? 1) > 1) found.add(W_COLUMNS);
    else if (TRACKED.has(tag)) found.add(W_TRACKED);
    else if (tag === "commentReference") found.add(W_COMMENTS);
    else if (tag === "footnoteReference" || tag === "endnoteReference") found.add(W_NOTES);
    else if (tag === "fldSimple" || tag === "instrText") {
      if (unsupportedField(wAttr(el, "instr") || leadText(el))) found.add(W_FIELDS);
    } else if (tag === "object") found.add(W_OBJECTS);
    else if (tag === "altChunk") found.add(W_EQUATIONS);
    else if (RTL_RUBY.has(tag)) found.add(W_RTL_RUBY);
  }
  const order = [W_TEXTBOX, W_COLUMNS, W_TRACKED, W_COMMENTS, W_NOTES, W_FIELDS, W_OBJECTS];
  for (const text of [...order, W_EQUATIONS, W_RTL_RUBY]) {
    if (found.has(text)) ctx.warn(text);
  }
}

/** Read `bytes` (a DOCX) as the editor's HTML subset, its pictures and warnings. */
export function readDocx(bytes: Uint8Array): OpenedDocument {
  try {
    const files = unzipPackage(bytes);
    const types = parseXml(partText(files, "[Content_Types].xml") ?? "");
    const main = relsFor(files, "").target(RT.officeDocument);
    if (main === null) throw new Error("No main document relationship");
    const docName = actualName(files, resolve("/", main));
    if (contentTypeOf(types, docName) !== CT.document) throw new Error("Not a Word document");
    const data = files.get(docName);
    if (!data) throw new Error(`No part ${docName}`);
    const root = parseXml(decodeXml(data)).documentElement;
    const body = wChild(root, "body");
    if (!body) throw new Error("No document body");
    const ctx = new Ctx(files, types, docName);
    const html = new Blocks(ctx).run(body);
    collectWarnings(ctx, root, body);
    return {
      html: normaliseHtml(html).html,
      images: ctx.images,
      sourceFormat: "docx",
      warnings: ctx.warnings,
      frontMatter: "",
      title: "",
      template: bytes,
    };
  } catch (err) {
    console.error("Could not open the file as DOCX", err);
    throw new Error(err instanceof Error && err.message === TOO_LARGE ? TOO_LARGE : UNREADABLE);
  }
}
