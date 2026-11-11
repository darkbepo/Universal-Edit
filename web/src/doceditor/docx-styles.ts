/**
 * What the DOCX reader looks up in the package: the `Ctx` class (relationships, styles and
 * numbering of one document), Word's built-in style ids and the paragraph style / list helpers
 * (a port of the matching parts of the desktop's `docx_reader.py`).
 */

import { NS, RT } from "./docx-template";
import {
  type Files,
  type Rel,
  actualName,
  decodeXml,
  isW,
  parseXml,
  relsFor,
  resolve,
  wAttr,
} from "./docx-package";
import { pyInt } from "./pycompat";
import { type DocImages, W_LISTSTYLE } from "./types";
// Word's built-in style ids, used when the file doesn't define the paragraph's style (no
// styles part, or an id that isn't in it).
export const BUILTIN_IDS: Record<string, string> = {
  Title: "h1",
  Quote: "quote",
  IntenseQuote: "quote",
  Code: "pre",
  HTMLPreformatted: "pre",
  SourceCode: "pre",
};
for (let n = 1; n <= 9; n++) BUILTIN_IDS[`Heading${n}`] = `h${Math.min(n, 3)}`;
export const BUILTIN_LIST = /^List(Bullet|Number)([2-9]?)$/;
export const BUILTIN_LIST_IDS: Record<string, number> = { Bullet: -1, Number: -2 }; // numIds of their own
// python-docx's BabelFish: the names styles.xml uses for these -> the names shown in Word.
const UI_NAMES: Record<string, string> = { caption: "Caption", footer: "Footer", header: "Header" };
for (let n = 1; n <= 9; n++) UI_NAMES[`heading ${n}`] = `Heading ${n}`;
/** `table[key]` for the table's own keys only ("constructor" is not a colour). */
export const own = (table: Record<string, string>, key: string): string | undefined =>
  Object.hasOwn(table, key) ? table[key] : undefined;

/** The first child of `el` that is w:`local` (lxml's `el.find(qn("w:local"))`). */
export function wChild(el: Element | null | undefined, local: string): Element | null {
  if (!el) return null;
  for (let c = el.firstElementChild; c !== null; c = c.nextElementSibling) {
    if (c.namespaceURI === NS.w && c.localName === local) return c;
  }
  return null;
}
export const wVal = (el: Element | null | undefined, local: string): string | null =>
  wAttr(wChild(el, local), "val");

// ----- the package ------------------------------------------------------------------------

export class Ctx {
  images: DocImages = new Map();
  warnings: string[] = [];
  rels: Map<string, Rel>;
  private styleById = new Map<string, Element>();
  private defaultParagraph: Element | null = null;
  private nums = new Map<string, Element>();
  private abstracts = new Map<string | null, Element>();

  constructor(
    readonly files: Files,
    readonly types: Document,
    readonly docName: string,
  ) {
    this.rels = relsFor(files, docName).entries();
    const styles = this.related(RT.styles);
    for (const s of styles ? Array.from(styles.documentElement.children) : []) {
      if (!isW(s, "style")) continue;
      const id = wAttr(s, "styleId");
      if (id !== null && !this.styleById.has(id)) this.styleById.set(id, s);
      const isDefault = ["1", "true", "on"].includes(wAttr(s, "default") ?? "");
      if (isDefault && wAttr(s, "type") === "paragraph") this.defaultParagraph = s; // the last one
    }
    const numbering = this.related(RT.numbering);
    for (const n of numbering ? Array.from(numbering.documentElement.children) : []) {
      if (isW(n, "num")) {
        const id = wAttr(n, "numId");
        if (id !== null && !this.nums.has(id)) this.nums.set(id, n);
      } else if (isW(n, "abstractNum")) {
        const id = wAttr(n, "abstractNumId");
        if (!this.abstracts.has(id)) this.abstracts.set(id, n);
      }
    }
  }

  warn(text: string): void {
    if (!this.warnings.includes(text)) this.warnings.push(text);
  }

  /** The bytes of the part relationship `id` of the document points at (internal only). */
  partOf(id: string | null): { name: string; data: Uint8Array } | null {
    const rel = id === null ? undefined : this.rels.get(id);
    if (!rel || rel.external) return null;
    const name = actualName(this.files, resolve(this.docName, rel.target));
    const data = this.files.get(name);
    return data ? { name, data } : null;
  }

  xmlOf(id: string | null): Document | null {
    const part = this.partOf(id);
    return part ? parseXml(decodeXml(part.data)) : null;
  }

  private related(type: string): Document | null {
    for (const rel of this.rels.values()) if (rel.type === type) return this.xmlOf(rel.id);
    return null;
  }

  style(id: string | null): Element | null {
    return id === null ? null : (this.styleById.get(id) ?? null);
  }

  /** The paragraph's style id when the file doesn't define it as a paragraph style. */
  undefinedStyle(p: Element): string | null {
    const id = wVal(wChild(p, "pPr"), "pStyle");
    if (!id) return null;
    const style = this.style(id);
    return !style || wAttr(style, "type") !== "paragraph" ? id : null;
  }

  /** The paragraph's style and the styles it is based on (python-docx's get_by_id). */
  styleChain(p: Element): Element[] {
    const id = wVal(wChild(p, "pPr"), "pStyle");
    let style = id ? this.style(id) : null;
    if (!style || wAttr(style, "type") !== "paragraph") style = this.defaultParagraph;
    const chain: Element[] = [];
    while (style && chain.length < 20) {
      chain.push(style);
      const based = wChild(style, "basedOn");
      style = based ? this.style(wAttr(based, "val")) : null;
    }
    return chain;
  }

  numKind(numId: string, ilvl: string): string {
    const abstractId = wVal(this.nums.get(numId), "abstractNumId");
    const abstract = this.abstracts.get(abstractId);
    if (!abstract) return "ul";
    const levels = Array.from(abstract.children).filter((c) => isW(c, "lvl"));
    const lvl = levels.find((x) => wAttr(x, "ilvl") === ilvl) ?? levels[0];
    const fmt = wVal(lvl, "numFmt");
    if (fmt !== null && fmt !== "none" && fmt !== "bullet" && fmt !== "decimal") {
      this.warn(W_LISTSTYLE); // still opened as a numbered list
    }
    return fmt === "none" ? "none" : fmt === "bullet" ? "ul" : "ol";
  }
}

// ----- styles and lists ------------------------------------------------------------------

export const styleName = (style: Element): string | null => {
  const name = wVal(style, "name");
  return name === null ? null : (own(UI_NAMES, name) ?? name);
};
export const numPrOf = (el: Element) => wChild(wChild(el, "pPr"), "numPr");

/** [numId, "ul" | "ol", ilvl] for a list paragraph, else null. */
export function listInfo(ctx: Ctx, p: Element, chain: Element[]): [number, string, number] | null {
  const sources = [numPrOf(p), ...chain.map(numPrOf)];
  let numId: string | null = null;
  let ilvl: string | null = null;
  for (const s of sources) numId ??= wVal(s, "numId");
  for (const s of sources) ilvl ??= wVal(s, "ilvl");
  ilvl ??= "0";
  if (!numId || numId === "0") return null;
  const kind = ctx.numKind(numId, ilvl);
  if (kind === "none") return null;
  const id = pyInt(numId);
  const level = pyInt(ilvl);
  return id === null || level === null ? null : [id, kind, Math.max(level, 0)];
}
