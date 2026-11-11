/**
 * Write the editor's HTML subset (plus its pictures) as a DOCX file: a port of the desktop's
 * `src/umc/doceditor/docx_writer.py` (read it for the mapping, which `docx_reader` mirrors).
 *
 * h1-h3 -> Heading 1-3, blockquote > p -> Quote, pre -> Code, lists -> List Bullet / List
 * Number paragraphs with their own `w:num`, tables -> Table Grid with explicit borders
 * (`style="border:none"` -> no borders), links -> `w:hyperlink`, spans -> run formatting.
 * Lengths are rounded the way python-docx rounds them.
 *
 * With a `template` (the DOCX being saved over) the body is written into a copy of that file,
 * so its styles, document defaults, page setup, headers / footers and theme are kept. Needs
 * DOMParser / XMLSerializer (the page, not a worker).
 */

import { strToU8 } from "fflate";
import { CT, HYPERLINK_STYLE, NS, PARTS, RT, STYLES, XML_HEAD, codeStyle } from "./docx-template";
import { type ImageInfo, extent, imageInfo } from "./docx-image";
import {
  type Files,
  Rels,
  Xml,
  actualName,
  dirOf,
  isW,
  parseXml,
  partKey,
  partText,
  reachable,
  relsOf,
  resolve,
  serialise,
  unzipPackage,
  wAttr,
  wKids,
  zipPackage,
} from "./docx-package";
import { pyRound } from "./pycompat";
import { normaliseHtml } from "./subset";
import type { DocImages } from "./types";

const XML_NS = "http://www.w3.org/XML/1998/namespace";
const EMU_PER_PX = 9525;
const NBSP = " ";
const HEADINGS: Record<string, string> = { h1: "Heading 1", h2: "Heading 2", h3: "Heading 3" };
const FLAGS: Record<string, "bold" | "italic" | "underline" | "strike"> = {
  b: "bold",
  i: "italic",
  u: "underline",
  s: "strike",
};
const ALIGN: Record<string, string> = {
  left: "left",
  center: "center",
  right: "right",
  justify: "both",
};
const USED_STYLES = [
  "Normal",
  "Heading 1",
  "Heading 2",
  "Heading 3",
  "Quote",
  "List Bullet",
  "List Number",
  "Table Grid",
];
// Schema order of the w:pPr children the writer sets.
const PPR_ORDER = ["pStyle", "pageBreakBefore", "numPr", "pBdr", "ind", "jc"];

interface Fmt {
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strike: boolean;
  family: string | null;
  size: number | null;
  color: string | null;
  background: string | null;
  link: boolean;
}
const PLAIN: Fmt = {
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  family: null,
  size: null,
  color: null,
  background: null,
  link: false,
};

// ----- numbers, the way Python / python-docx compute them ---------------------------------

const ptToEmu = (pt: number) => Math.trunc(pt * 12700); // docx.shared.Pt
const emuToTwips = (emu: number) => pyRound(emu / 635);

function ptValue(value: string | undefined): number | null {
  const m = /^(-?\d+(?:\.\d+)?)\s*pt$/.exec((value ?? "").trim().toLowerCase());
  return m ? Number(m[1]) : null;
}

function styleDict(node: Element): Record<string, string> {
  const out: Record<string, string> = {};
  for (const decl of (node.getAttribute("style") ?? "").split(";")) {
    const at = decl.indexOf(":");
    if (at >= 0) out[decl.slice(0, at).trim().toLowerCase()] = decl.slice(at + 1).trim();
  }
  return out;
}

interface Container {
  el: Element;
  before: Element | null;
  widthEmu: number;
}

interface StyleInfo {
  id: string;
  type: string;
  isDefault: boolean;
}

/** One write: the open package plus the state python-docx keeps while adding content. */
class Writer {
  files: Files;
  types!: Document;
  docName!: string;
  doc!: Xml;
  body!: Element;
  rels!: Rels;
  styles!: Document;
  stylesName!: string;
  stylesChanged = false;
  numbering!: Xml;
  numberingName!: string;
  numberingChanged = false;
  styleIds = new Map<string, StyleInfo>();
  private maxAbstract = -1;
  private numIds = new Set<number>();
  private nextNum = 1;
  private firstNum: Element | null = null;
  private media = new Map<string, string>(); // src -> rId
  private mediaBytes: [Uint8Array, string][] = [];
  private pictureId = 0;

  constructor(
    files: Files,
    readonly images: DocImages,
  ) {
    this.files = files;
    this.open();
  }

  private text(name: string): string | null {
    return partText(this.files, name);
  }

  private addPart(name: string, type: string, text: string): void {
    this.files.set(name, strToU8(text));
    const o = this.types.createElementNS(NS.ct, "Override");
    o.setAttribute("PartName", `/${name}`);
    o.setAttribute("ContentType", type);
    this.types.documentElement.appendChild(o);
  }

  /** "base.ext", "base2.ext", … (or "base1.ext", … when `numbered`) that no part uses, in
   * any letter case (part names are case-insensitive). */
  private freeName(base: string, ext: string, numbered = false): string {
    const taken = new Set(Array.from(this.files.keys(), partKey));
    for (let n = 1; ; n++) {
      const name = `${base}${numbered || n > 1 ? n : ""}.${ext}`;
      if (!taken.has(partKey(name))) return name;
    }
  }

  private open(): void {
    this.types = parseXml(this.text("[Content_Types].xml") ?? "");
    const root = new Rels(this.text("_rels/.rels"));
    this.docName = actualName(this.files, resolve("/", root.target(RT.officeDocument) ?? ""));
    this.doc = new Xml(parseXml(this.text(this.docName) ?? ""));
    const body = this.doc.doc.getElementsByTagNameNS(NS.w, "body")[0];
    if (!body) throw new Error("No document body");
    this.body = body;
    for (const child of Array.from(body.childNodes)) if (!isW(child, "sectPr")) child.remove();
    this.rels = new Rels(this.text(relsOf(this.docName)));
    for (const rel of this.rels.all()) {
      // what the old body pointed at
      const kind = (rel.getAttribute("Type") ?? "").split("/").pop() ?? "";
      if (kind === "image" || kind === "hyperlink" || kind.startsWith("comments")) rel.remove();
    }
    this.files.set(relsOf(this.docName), strToU8(serialise(this.rels.xml.doc)));
    this.rels = new Rels(this.text(relsOf(this.docName))); // python-docx reuses the freed ids
    const keep = reachable(this.files);
    for (const name of Array.from(this.files.keys())) {
      if (name !== "[Content_Types].xml" && !keep.has(name)) this.files.delete(name);
    }
    const present = new Set(Array.from(this.files.keys(), partKey));
    for (const o of Array.from(this.types.documentElement.children)) {
      const part = o.localName === "Override" ? (o.getAttribute("PartName") ?? "").slice(1) : null;
      if (part !== null && !present.has(partKey(part))) o.remove();
    }
    this.openStyles();
    this.openNumbering();
  }

  /**
   * The document's part of relationship `type`: its name and text. A missing part is created
   * from `fallback` (where the relationship points, or a new relationship when there is none).
   */
  private relatedPart(type: string, base: string, contentType: string, fallback: string) {
    const target = this.rels.target(type);
    const name = target
      ? actualName(this.files, resolve(this.docName, target))
      : this.freeName(`${dirOf(this.docName)}${base}`, "xml");
    let text = this.text(name);
    if (text === null) {
      if (!target) this.rels.add(type, name.slice(dirOf(this.docName).length));
      this.addPart(name, contentType, fallback);
      text = fallback;
    }
    return { name, text };
  }

  private openStyles(): void {
    const { name, text } = this.relatedPart(RT.styles, "styles", CT.styles, STYLES);
    this.stylesName = name;
    this.styles = parseXml(text);
    this.ensureStyles();
    for (const s of wKids(this.styles.documentElement, "style")) {
      const name = (wAttr(wKids(s, "name")[0], "val") ?? "").toLowerCase();
      const info = {
        id: wAttr(s, "styleId") ?? "",
        type: wAttr(s, "type") ?? "",
        isDefault: false,
      };
      info.isDefault = ["1", "true", "on"].includes(wAttr(s, "default") ?? "");
      if (!this.styleIds.has(`${info.type}:${name}`))
        this.styleIds.set(`${info.type}:${name}`, info);
    }
  }

  /** Every style the writer uses exists: missing ones are copied from the built-in template. */
  private ensureStyles(): void {
    const root = this.styles.documentElement;
    const nameOf = (s: Element) => (wAttr(wKids(s, "name")[0], "val") ?? "").toLowerCase();
    const have = new Set(wKids(root, "style").map(nameOf));
    const missing = new Set(USED_STYLES.map((n) => n.toLowerCase()).filter((n) => !have.has(n)));
    const append = (style: Element) => {
      const copy = this.styles.importNode(style, true) as Element;
      for (const n of Array.from(copy.getElementsByTagNameNS(NS.w, "numPr"))) n.remove();
      root.appendChild(copy); // the writer numbers lists itself
      this.stylesChanged = true;
    };
    /** `style` with a style id no other style uses ("Code", else "Code1", …). */
    const freeId = (style: Element) => {
      const taken = new Set(wKids(root, "style").map((s) => wAttr(s, "styleId")));
      const base = wAttr(style, "styleId") ?? "";
      let id = base;
      for (let n = 1; taken.has(id); n++) id = `${base}${n}`;
      style.setAttributeNS(NS.w, "w:styleId", id);
      return style;
    };
    const parsed = (xml: string) =>
      freeId(
        parseXml(`<w:styles xmlns:w="${NS.w}">${xml}</w:styles>`).documentElement
          .firstElementChild as Element,
      );
    if (missing.size) {
      for (const s of wKids(parseXml(STYLES).documentElement, "style")) {
        if (missing.has(nameOf(s))) append(freeId(s)); // referenced by name, so a new id is fine
      }
    }
    const names = new Set(wKids(root, "style").map(nameOf));
    if (!names.has("hyperlink")) append(parsed(HYPERLINK_STYLE));
    if (!names.has("code")) {
      const normal = wKids(root, "style").find((s) => nameOf(s) === "normal");
      append(parsed(codeStyle(wAttr(normal, "styleId") ?? "Normal")));
    }
  }

  private openNumbering(): void {
    const empty = `${XML_HEAD}<w:numbering xmlns:w="${NS.w}"/>`;
    const { name, text } = this.relatedPart(RT.numbering, "numbering", CT.numbering, empty);
    this.numberingName = name;
    this.numbering = new Xml(parseXml(text));
    const root = this.numbering.doc.documentElement;
    for (const a of wKids(root, "abstractNum")) {
      const id = Number.parseInt(wAttr(a, "abstractNumId") ?? "", 10);
      if (Number.isFinite(id)) this.maxAbstract = Math.max(this.maxAbstract, id);
    }
    for (const n of wKids(root, "num"))
      this.numIds.add(Number.parseInt(wAttr(n, "numId") ?? "", 10));
    this.firstNum = wKids(root, "num")[0] ?? null;
  }

  /** The text width of the last section, in EMU (python-docx `_block_width`). */
  blockWidth(): number {
    const sect = wKids(this.body, "sectPr").pop();
    const twips = (el: Element | undefined, name: string, fallback: number) => {
      const n = Number.parseInt(wAttr(el, name) ?? "", 10);
      return n ? n * 635 : fallback;
    };
    const pgSz = sect ? wKids(sect, "pgSz")[0] : undefined;
    const pgMar = sect ? wKids(sect, "pgMar")[0] : undefined;
    return twips(pgSz, "w", 7772400) - twips(pgMar, "left", 914400) - twips(pgMar, "right", 914400);
  }

  styleId(name: string, type: string): string | null {
    const info = this.styleIds.get(`${type}:${name.toLowerCase()}`);
    if (!info) return name.replaceAll(" ", "");
    return info.isDefault ? null : info.id;
  }

  /** A new numbering with its own definition of one kind (decimal / bullet) at all 9 levels. */
  ownNum(ordered: boolean): number {
    const x = this.numbering;
    const root = x.doc.documentElement;
    const abstractId = ++this.maxAbstract;
    const abstract = x.el("w:abstractNum", { "w:abstractNumId": String(abstractId) });
    abstract.appendChild(x.el("w:multiLevelType", { "w:val": "hybridMultilevel" }));
    for (let level = 0; level < 9; level++) {
      abstract.appendChild(
        x.el(
          "w:lvl",
          { "w:ilvl": String(level) },
          x.el("w:start", { "w:val": "1" }),
          x.el("w:numFmt", { "w:val": ordered ? "decimal" : "bullet" }),
          x.el("w:lvlText", { "w:val": ordered ? `%${level + 1}.` : "•" }),
          x.el("w:lvlJc", { "w:val": "left" }),
          x.el(
            "w:pPr",
            {},
            x.el("w:ind", { "w:left": String(720 * (level + 1)), "w:hanging": "360" }),
          ),
        ),
      );
    }
    const cleanup = wKids(root, "numIdMacAtCleanup")[0] ?? null;
    root.insertBefore(abstract, this.firstNum ?? cleanup); // schema: abstractNum*, num*, cleanup
    while (this.numIds.has(this.nextNum)) this.nextNum++;
    const numId = this.nextNum;
    this.numIds.add(numId);
    const num = x.el(
      "w:num",
      { "w:numId": String(numId) },
      x.el("w:abstractNumId", { "w:val": String(abstractId) }),
    );
    if (ordered) {
      num.appendChild(
        x.el("w:lvlOverride", { "w:ilvl": "0" }, x.el("w:startOverride", { "w:val": "1" })),
      );
    }
    root.insertBefore(num, cleanup);
    this.firstNum ??= num;
    this.numberingChanged = true;
    return numId;
  }

  /** The relationship id of a picture part (one part per picture, like python-docx). */
  imageRel(src: string, data: Uint8Array, info: ImageInfo): string {
    const known = this.media.get(src) ?? this.mediaBytes.find(([d]) => sameBytes(d, data))?.[1];
    if (known) return known;
    const name = this.freeName(`${dirOf(this.docName)}media/image`, info.ext, true);
    this.files.set(name, data);
    const id = this.rels.add(RT.image, name.slice(dirOf(this.docName).length));
    this.media.set(src, id);
    this.mediaBytes.push([data, id]);
    const root = this.types.documentElement;
    const exts = Array.from(root.children).map((d) =>
      (d.getAttribute("Extension") ?? "").toLowerCase(),
    );
    if (!exts.includes(info.ext)) {
      const def = this.types.createElementNS(NS.ct, "Default");
      def.setAttribute("Extension", info.ext);
      def.setAttribute("ContentType", info.type);
      root.insertBefore(def, root.firstElementChild);
    }
    return id;
  }

  nextPictureId(): number {
    return ++this.pictureId;
  }

  finish(): Uint8Array {
    if (!wKids(this.body, "p").length)
      this.body.insertBefore(this.doc.el("w:p"), wKids(this.body, "sectPr")[0] ?? null);
    this.files.set(this.docName, strToU8(serialise(this.doc.doc)));
    this.files.set(relsOf(this.docName), strToU8(serialise(this.rels.xml.doc)));
    if (this.stylesChanged) this.files.set(this.stylesName, strToU8(serialise(this.styles)));
    if (this.numberingChanged)
      this.files.set(this.numberingName, strToU8(serialise(this.numbering.doc)));
    this.files.set("[Content_Types].xml", strToU8(serialise(this.types)));
    return zipPackage(this.files);
  }
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// ----- body content -----------------------------------------------------------------------

const tagOf = (node: Node): string =>
  node.nodeType === 1 ? (node as Element).localName.toLowerCase() : "";

/** Insert `child` into `parent` (a w:pPr) in schema order. */
function addOrdered(parent: Element, child: Element): void {
  const rank = PPR_ORDER.indexOf(child.localName);
  const after = Array.from(parent.children).find((c) => PPR_ORDER.indexOf(c.localName) > rank);
  parent.insertBefore(child, after ?? null);
}

const BLOCKS = new Set([
  "p",
  "div",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "table",
  "hr",
  "h1",
  "h2",
  "h3",
]);
const BORDER_EDGES = ["top", "left", "bottom", "right", "insideH", "insideV"];
const TABLE_LOOK = {
  "w:firstColumn": "1",
  "w:firstRow": "1",
  "w:lastColumn": "0",
  "w:lastRow": "0",
  "w:noHBand": "0",
  "w:noVBand": "1",
  "w:val": "04A0",
};

type LiPart = ["text" | "p" | "list", Element];

class Body {
  private x: Xml;
  constructor(private w: Writer) {
    this.x = w.doc;
  }

  private pPr(p: Element): Element {
    const first = p.firstElementChild;
    if (isW(first, "pPr")) return first;
    const pPr = this.x.el("w:pPr");
    p.insertBefore(pPr, first);
    return pPr;
  }

  private newPar(cont: Container, reuse: Element[], style?: string): Element {
    let p = reuse.pop();
    if (!p) {
      p = this.x.el("w:p");
      cont.el.insertBefore(p, cont.before);
    }
    const id = style ? this.w.styleId(style, "paragraph") : null;
    if (id) addOrdered(this.pPr(p), this.x.el("w:pStyle", { "w:val": id }));
    return p;
  }

  private paragraphProps(p: Element, node: Element): void {
    const style = styleDict(node);
    const align = ALIGN[style["text-align"] ?? ""];
    if (align) addOrdered(this.pPr(p), this.x.el("w:jc", { "w:val": align }));
    const indent = ptValue(style["margin-left"]);
    if (indent) {
      const left = String(emuToTwips(ptToEmu(indent)));
      addOrdered(this.pPr(p), this.x.el("w:ind", { "w:left": left }));
    }
    if (style["page-break-before"] === "always") {
      addOrdered(this.pPr(p), this.x.el("w:pageBreakBefore"));
    }
  }

  /** A run of `text`: tabs become w:tab and line breaks w:br, like python-docx's `add_run`. */
  private run(holder: Element, text: string, fmt: Fmt): void {
    const r = this.x.el("w:r");
    const rPr = this.runProps(fmt);
    if (rPr) r.appendChild(rPr);
    let start = 0;
    const flush = (end: number) => {
      if (end > start) {
        const t = this.x.el("w:t");
        t.setAttributeNS(XML_NS, "xml:space", "preserve");
        t.textContent = text.slice(start, end);
        r.appendChild(t);
      }
    };
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === "\t" || c === "\n" || c === "\r") {
        flush(i);
        r.appendChild(this.x.el(c === "\t" ? "w:tab" : "w:br"));
        start = i + 1;
      }
    }
    flush(text.length);
    holder.appendChild(r);
  }

  private runProps(fmt: Fmt): Element | null {
    const x = this.x;
    const props: Element[] = [];
    if (fmt.link) {
      const id = this.w.styleId("Hyperlink", "character") ?? "Hyperlink";
      props.push(x.el("w:rStyle", { "w:val": id }));
    }
    if (fmt.family) {
      const f = fmt.family;
      props.push(x.el("w:rFonts", { "w:ascii": f, "w:hAnsi": f, "w:eastAsia": f }));
    }
    if (fmt.bold) props.push(x.el("w:b"));
    if (fmt.italic) props.push(x.el("w:i"));
    if (fmt.strike) props.push(x.el("w:strike"));
    const color = (fmt.color ?? "").replace(/^#/, "").toUpperCase();
    if (/^[0-9A-F]{6}$/.test(color)) props.push(x.el("w:color", { "w:val": color }));
    if (fmt.size) {
      const halfPoints = Math.trunc((ptToEmu(fmt.size) / 12700) * 2);
      props.push(x.el("w:sz", { "w:val": String(halfPoints) }));
    }
    if (fmt.underline) props.push(x.el("w:u", { "w:val": "single" }));
    if (fmt.background) {
      const fill = fmt.background.replace(/^#/, "").toUpperCase();
      props.push(x.el("w:shd", { "w:val": "clear", "w:color": "auto", "w:fill": fill }));
    }
    return props.length ? x.el("w:rPr", {}, ...props) : null;
  }

  /** Write a node's text and children as runs inside `holder` (a w:p or w:hyperlink). */
  private inline(node: Element, fmt: Fmt, holder: Element): void {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = (child as Text).data;
        if (text) this.run(holder, text.replaceAll(NBSP, " "), fmt);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const el = child as Element;
      const tag = tagOf(el);
      const href = el.getAttribute("href");
      if (tag in FLAGS) this.inline(el, { ...fmt, [FLAGS[tag]]: true }, holder);
      else if (tag === "span") {
        const st = styleDict(el);
        const sub: Fmt = {
          ...fmt,
          family: st["font-family"] || fmt.family,
          size: ptValue(st["font-size"]) || fmt.size,
          color: st.color || fmt.color,
          background: st["background-color"] || fmt.background,
        };
        this.inline(el, sub, holder);
      } else if (tag === "a" && href) {
        const link = this.x.el("w:hyperlink", {
          "r:id": this.w.rels.add(RT.hyperlink, href, true),
        });
        holder.appendChild(link);
        this.inline(el, { ...fmt, link: true }, link);
      } else if (tag === "br") holder.appendChild(this.x.el("w:r", {}, this.x.el("w:br")));
      else if (tag === "img") this.picture(el, holder);
      else this.inline(el, fmt, holder);
    }
  }

  private picture(node: Element, holder: Element): void {
    const src = node.getAttribute("src") ?? "";
    const data = this.w.images.get(src);
    if (!data) {
      console.warn(`Picture ${src} has no data; left out of the document`);
      return;
    }
    const size = (key: string): number | null => {
      const raw = (node.getAttribute(key) ?? "").trim();
      const n = raw ? Number(raw) : Number.NaN;
      return Number.isFinite(n) ? pyRound(n * EMU_PER_PX) : null;
    };
    const info = imageInfo(data);
    const [cx, cy] =
      info && info.dpiX && info.dpiY ? extent(info, size("width"), size("height")) : [NaN, NaN];
    if (!info || !Number.isFinite(cx) || !Number.isFinite(cy)) {
      console.warn(`Picture ${src} is not a readable image; left out of the document`);
      return;
    }
    const x = this.x;
    const id = this.w.nextPictureId();
    const ext = { cx: String(cx), cy: String(cy) };
    const nvPicPr = x.el(
      "pic:nvPicPr",
      {},
      x.el("pic:cNvPr", { id: "0", name: `image.${info.ext}` }),
      x.el("pic:cNvPicPr"),
    );
    const blipFill = x.el(
      "pic:blipFill",
      {},
      x.el("a:blip", { "r:embed": this.w.imageRel(src, data, info) }),
      x.el("a:stretch", {}, x.el("a:fillRect")),
    );
    const spPr = x.el(
      "pic:spPr",
      {},
      x.el("a:xfrm", {}, x.el("a:off", { x: "0", y: "0" }), x.el("a:ext", ext)),
      x.el("a:prstGeom", { prst: "rect" }),
    );
    const uri = "http://schemas.openxmlformats.org/drawingml/2006/picture";
    const graphic = x.el(
      "a:graphic",
      {},
      x.el("a:graphicData", { uri }, x.el("pic:pic", {}, nvPicPr, blipFill, spPr)),
    );
    const inline = x.el(
      "wp:inline",
      {},
      x.el("wp:extent", ext),
      x.el("wp:docPr", { id: String(id), name: `Picture ${id}` }),
      x.el("wp:cNvGraphicFramePr", {}, x.el("a:graphicFrameLocks", { noChangeAspect: "1" })),
      graphic,
    );
    holder.appendChild(x.el("w:r", {}, x.el("w:drawing", {}, inline)));
  }

  private textBlock(cont: Container, reuse: Element[], node: Element, style: string): void {
    const p = this.newPar(cont, reuse, style);
    this.paragraphProps(p, node);
    this.inline(node, PLAIN, p);
  }

  /** A list item as ("text", inline holder) / ("p", paragraph) / ("list", nested list). */
  private liParts(li: Element): LiPart[] {
    const parts: LiPart[] = [];
    let cur: Element | null = null;
    const flush = () => {
      if (cur?.childNodes.length) parts.push(["text", cur]);
      cur = null;
    };
    for (const child of Array.from(li.childNodes)) {
      const tag = tagOf(child);
      if (tag === "ul" || tag === "ol" || tag === "p" || tag === "div") {
        flush();
        parts.push([tag === "ul" || tag === "ol" ? "list" : "p", child as Element]);
      } else {
        cur ??= li.ownerDocument.createElement("span");
        cur.appendChild(child); // moves it, like the desktop's holder
      }
    }
    flush();
    return parts;
  }

  private list(
    cont: Container,
    reuse: Element[],
    node: Element,
    level: number,
    parentNum: number | null,
    parentOrdered: boolean | null,
  ): void {
    const ordered = tagOf(node) === "ol";
    // every top-level list, and a nested list of the other kind, gets its own numbering
    const numId =
      parentNum === null || parentOrdered !== ordered ? this.w.ownNum(ordered) : parentNum;
    const style = ordered ? "List Number" : "List Bullet";
    for (const li of Array.from(node.children)) {
      if (tagOf(li) !== "li") continue;
      const parts = this.liParts(li);
      if (!parts.length) parts.push(["text", li.ownerDocument.createElement("span")]); // an empty item stays
      for (const [kind, item] of parts) {
        if (kind === "list") {
          this.list(cont, reuse, item, Math.min(level + 1, 9), numId, ordered);
          continue;
        }
        const p = this.newPar(cont, reuse, style);
        this.paragraphProps(p, item);
        const numPr = this.x.el(
          "w:numPr",
          {},
          this.x.el("w:ilvl", { "w:val": String(level - 1) }),
          this.x.el("w:numId", { "w:val": String(numId) }),
        );
        addOrdered(this.pPr(p), numPr);
        this.inline(item, PLAIN, p);
      }
    }
  }

  private table(cont: Container, reuse: Element[], node: Element): void {
    const rows: Element[] = []; // the table's own rows (not those of nested tables)
    for (const child of Array.from(node.children)) {
      const tag = tagOf(child);
      if (tag === "tr") rows.push(child);
      else if (tag === "thead" || tag === "tbody" || tag === "tfoot") {
        for (const tr of Array.from(child.children)) if (tagOf(tr) === "tr") rows.push(tr);
      }
    }
    const span = (td: Element, name: string) =>
      Math.max(Number.parseInt(td.getAttribute(name) ?? "", 10) || 1, 1);
    const placed: { r: number; c: number; rs: number; cs: number; td: Element | null }[] = [];
    const owner = new Map<string, number>(); // "row,col" -> index into placed
    let ncols = 0;
    rows.forEach((tr, r) => {
      let c = 0;
      for (const td of Array.from(tr.children)) {
        if (tagOf(td) !== "td" && tagOf(td) !== "th") continue;
        while (owner.has(`${r},${c}`)) c++;
        const cs = span(td, "colspan");
        const rs = Math.min(span(td, "rowspan"), rows.length - r);
        for (let rr = r; rr < r + rs; rr++) {
          for (let cc = c; cc < c + cs; cc++) owner.set(`${rr},${cc}`, placed.length);
        }
        placed.push({ r, c, rs, cs, td });
        c += cs;
        ncols = Math.max(ncols, c);
      }
    });
    if (!placed.length) return;
    // Not on the desktop (python-docx): an unused first paragraph of a cell would stay in
    // front of the table and take the text that follows it.
    for (const p of reuse.splice(0)) p.remove();
    const x = this.x;
    const colTwips = emuToTwips(Math.floor(cont.widthEmu / ncols));
    const plain = (node.getAttribute("style") ?? "").includes("border:none");
    const line = { "w:val": "single", "w:sz": "4", "w:space": "0", "w:color": "auto" };
    const edges = BORDER_EDGES.map((e) => x.el(`w:${e}`, plain ? { "w:val": "none" } : line));
    const tblPr = x.el("w:tblPr");
    const gridStyle = plain ? null : this.w.styleId("Table Grid", "table");
    if (gridStyle) tblPr.appendChild(x.el("w:tblStyle", { "w:val": gridStyle }));
    tblPr.appendChild(x.el("w:tblW", { "w:type": "auto", "w:w": "0" }));
    tblPr.appendChild(x.el("w:tblBorders", {}, ...edges));
    tblPr.appendChild(x.el("w:tblLook", TABLE_LOOK));
    const grid = x.el("w:tblGrid");
    for (let c = 0; c < ncols; c++)
      grid.appendChild(x.el("w:gridCol", { "w:w": String(colTwips) }));
    const tbl = x.el("w:tbl", {}, tblPr, grid);
    cont.el.insertBefore(tbl, cont.before);
    const fill: [Element, Element, number][] = [];
    for (let r = 0; r < rows.length; r++) {
      const tr = x.el("w:tr");
      tbl.appendChild(tr);
      for (let c = 0; c < ncols;) {
        const at = owner.get(`${r},${c}`);
        const cell = at === undefined ? { r, c, rs: 1, cs: 1, td: null } : placed[at];
        const width = colTwips * cell.cs;
        const tcPr = x.el("w:tcPr", {}, x.el("w:tcW", { "w:type": "dxa", "w:w": String(width) }));
        if (cell.cs > 1) tcPr.appendChild(x.el("w:gridSpan", { "w:val": String(cell.cs) }));
        if (cell.rs > 1)
          tcPr.appendChild(x.el("w:vMerge", cell.r === r ? { "w:val": "restart" } : {}));
        const tc = x.el("w:tc", {}, tcPr, x.el("w:p"));
        tr.appendChild(tc);
        if (cell.td && cell.r === r) fill.push([tc, cell.td, width * 635]);
        c += cell.cs;
      }
    }
    for (const [tc, td, widthEmu] of fill) {
      this.blocks({ el: tc, before: null, widthEmu }, [tc.lastElementChild as Element], td);
      if (isW(tc.lastElementChild, "tbl")) tc.appendChild(x.el("w:p")); // a cell ends with a paragraph
    }
  }

  private hr(cont: Container, reuse: Element[]): void {
    const p = this.newPar(cont, reuse);
    const bottom = { "w:val": "single", "w:sz": "6", "w:space": "1", "w:color": "auto" };
    addOrdered(this.pPr(p), this.x.el("w:pBdr", {}, this.x.el("w:bottom", bottom)));
  }

  /** Write the block children of `root`; `reuse` holds a paragraph to fill first. */
  blocks(cont: Container, reuse: Element[], root: Element): void {
    let stray: Element | null = null; // loose inline content between blocks
    let afterInline = false;
    const flush = () => {
      if (stray?.childNodes.length) this.textBlock(cont, reuse, stray, "Normal");
      stray = null;
    };
    const loose = (): Element => (stray ??= root.ownerDocument.createElement("p"));
    for (const child of Array.from(root.childNodes)) {
      if (child.nodeType === 3) {
        // an inline element's tail goes with it; other text only when it isn't blank
        if (afterInline || (child as Text).data.trim()) loose().appendChild(child);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const el = child as Element;
      const tag = tagOf(el);
      afterInline = !BLOCKS.has(tag);
      if (afterInline) {
        loose().appendChild(el); // inline element at block level
        continue;
      }
      flush();
      if (tag in HEADINGS) this.textBlock(cont, reuse, el, HEADINGS[tag]);
      else if (tag === "p" || tag === "div") this.textBlock(cont, reuse, el, "Normal");
      else if (tag === "pre") this.textBlock(cont, reuse, el, "Code");
      else if (tag === "blockquote") {
        for (const p of Array.from(el.children)) {
          if (tagOf(p) === "p") this.textBlock(cont, reuse, p, "Quote");
        }
      } else if (tag === "ul" || tag === "ol") this.list(cont, reuse, el, 1, null, null);
      else if (tag === "table") this.table(cont, reuse, el);
      else this.hr(cont, reuse);
    }
    flush();
  }
}

export const TEMPLATE_LOST =
  "Couldn't keep the original file's layout; it was saved with default styles.";

export interface WriteOptions {
  /** Called with a plain-language message the page shows (e.g. the template was unusable). */
  onWarning?: (message: string) => void;
}

/**
 * Write `html` (the editor's subset) and its `images` as a DOCX package.
 *
 * `template`: a DOCX whose styles, page setup, headers and footers the result keeps.
 */
export function writeDocx(
  html: string,
  images: DocImages,
  template?: Uint8Array | null,
  opts: WriteOptions = {},
): Uint8Array {
  const fragment = normaliseHtml(html).html || "<p></p>";
  let writer: Writer | null = null;
  if (template) {
    try {
      writer = new Writer(unzipPackage(template), images);
    } catch (err) {
      // changed, damaged or not a Word file: start from scratch, and say so
      console.error("Can't use the original file as the template; writing a new document", err);
      opts.onWarning?.(TEMPLATE_LOST);
    }
  }
  writer ??= new Writer(new Map(PARTS.map(([name, text]) => [name, strToU8(text)])), images);
  // An HTML parser drops the newline right after <pre>; libxml2 (the desktop) keeps it.
  const source = fragment.split("<pre>\n").join("<pre>\n\n");
  const root = new DOMParser().parseFromString(`<!doctype html><body>${source}`, "text/html").body;
  const sect = wKids(writer.body, "sectPr")[0] ?? null;
  const body = { el: writer.body, before: sect, widthEmu: writer.blockWidth() };
  new Body(writer).blocks(body, [], root);
  return writer.finish();
}
