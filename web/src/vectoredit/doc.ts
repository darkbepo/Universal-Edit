// The vector editor's document and its edits — a line-by-line port of
// src/umc/vectoredit/doc.py. Elements are addressed by paths (element-child indexes from the
// root); every edit returns a new document and only the changed elements lose their source text.

import { Box, IDENTITY, Matrix, num, scale, translate } from "./geom";
import * as shapes from "./shapes";
import { CONTAINERS, HIDDEN, SHAPES, kind, trim } from "./shapes";
import * as xmlsrc from "./xmlsrc";
import { Element, type Node, Text } from "./xmlsrc";

export type Path = number[];

export const DEFAULT_SIZE: [number, number] = [300, 150];
export const LAYER_ATTR = "inkscape:groupmode";

export class VectorError extends Error {}

export class VectorDoc {
  /** Elements by id, worked out once per document (see `byId`). */
  idCache: Map<string, Element> | null = null;
  constructor(readonly xml: xmlsrc.Document) {}
  get root(): Element {
    return this.xml.root;
  }
}

export interface Item {
  path: Path;
  kind: string;
  name: string;
  depth: number;
  hidden: boolean;
  container: boolean;
}

export function parse(text: string): VectorDoc {
  let xml: xmlsrc.Document;
  try {
    xml = xmlsrc.parse(text);
  } catch (e) {
    if (e instanceof xmlsrc.XmlError) throw new VectorError(`This isn't a readable SVG file: ${e.message}`);
    throw e;
  }
  if (kind(xml.root) !== "svg") throw new VectorError("This XML file isn't an SVG drawing.");
  return new VectorDoc(xml);
}

export const write = (doc: VectorDoc) => xmlsrc.write(doc.xml);

// ------------------------------------------------------------------------------- paths

export const samePath = (a: Path, b: Path) => a.length === b.length && a.every((v, i) => v === b[i]);

/** Python's tuple order. */
export function comparePaths(a: Path, b: Path): number {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}

function sortedUnique(paths: Path[]): Path[] {
  const out: Path[] = [];
  for (const p of [...paths].sort(comparePaths)) if (!out.length || !samePath(out[out.length - 1], p)) out.push(p);
  return out;
}

// ------------------------------------------------------------------------------- reading

export function get(doc: VectorDoc, path: Path): Element {
  let el = doc.root;
  for (const k of path) {
    const next = el.elements[k];
    if (next === undefined) throw new RangeError(String(k));
    el = next;
  }
  return el;
}

/** Index in `parent.children` of its `k`-th element. */
function slot(parent: Element, k: number): number {
  let seen = -1;
  for (let i = 0; i < parent.children.length; i++) {
    if (parent.children[i] instanceof Element) {
      seen++;
      if (seen === k) return i;
    }
  }
  throw new RangeError(String(k));
}

/** [width, height] the drawing is shown at, and the `viewBox` it shows. */
export function canvas(doc: VectorDoc): [number, number, Box] {
  const root = doc.root;
  const vb = shapes.numbers(root.get("viewBox"));
  const view = vb.length === 4 && vb[2] > 0 && vb[3] > 0 ? new Box(vb[0], vb[1], vb[0] + vb[2], vb[1] + vb[3]) : null;
  let w = shapes.length(root.get("width"));
  let h = shapes.length(root.get("height"));
  if (view === null) {
    const ww = w || DEFAULT_SIZE[0];
    const hh = h || DEFAULT_SIZE[1];
    return [ww, hh, new Box(0, 0, ww, hh)];
  }
  if (w === null && h === null) return [view.width, view.height, view];
  if (w === null) w = (h! * view.width) / view.height;
  if (h === null) h = (w * view.height) / view.width;
  return [w, h, view];
}

export const isLayer = (el: Element) => kind(el) === "g" && el.get(LAYER_ATTR) === "layer";

export function editable(el: Element): boolean {
  const k = kind(el);
  return SHAPES.includes(k) || CONTAINERS.includes(k);
}

/** Everything the layers list shows, in document order (bottom first). */
export function items(doc: VectorDoc): Item[] {
  const out: Item[] = [];
  const walk = (el: Element, path: Path, depth: number) => {
    el.elements.forEach((child, k) => {
      if (!editable(child)) return;
      const p = [...path, k];
      const container = CONTAINERS.includes(kind(child));
      const name = child.get("inkscape:label") || child.get("id") || kind(child);
      out.push({ path: p, kind: kind(child), name, depth, hidden: shapes.hidden(child), container });
      if (container) walk(child, p, depth + 1);
    });
  };
  walk(doc.root, [], 0);
  return out;
}

/** Root user units → the coordinates `path`'s element is written in (its parent's). */
export function parentMatrix(doc: VectorDoc, path: Path): Matrix {
  let m = IDENTITY;
  let el = doc.root;
  for (const k of path.slice(0, -1)) {
    el = el.elements[k];
    m = m.times(shapes.ownTransform(el));
  }
  return m;
}

/** Every element with an id (the first one wins). */
export function byId(doc: VectorDoc): Map<string, Element> {
  if (doc.idCache === null) {
    const cached = new Map<string, Element>();
    const walk = (el: Element) => {
      const i = el.get("id");
      if (i !== null && !cached.has(i)) cached.set(i, el);
      for (const c of el.elements) walk(c);
    };
    walk(doc.root);
    doc.idCache = cached;
  }
  return doc.idCache;
}

/** The element's box in root user units. */
export function bounds(doc: VectorDoc, path: Path): Box | null {
  const map = byId(doc);
  const b = shapes.bounds(get(doc, path), (id) => map.get(id));
  return b === null ? null : b.transformed(parentMatrix(doc, path));
}

export function selectionBounds(doc: VectorDoc, paths: Path[]): Box | null {
  let box: Box | null = null;
  for (const p of paths) {
    const b = bounds(doc, p);
    if (b !== null) box = box === null ? b : box.union(b);
  }
  return box;
}

/** Paths a click can choose inside `el`: its children, and the children of layers. */
function pickable(doc: VectorDoc, el: Element, path: Path): Path[] {
  const out: Path[] = [];
  el.elements.forEach((child, k) => {
    if (!editable(child) || shapes.hidden(child)) return;
    const p = [...path, k];
    if (isLayer(child)) out.push(...pickable(doc, child, p));
    else out.push(p);
  });
  return out;
}

/** The topmost thing whose box holds (x, y): a direct child of `inside` (layers looked through). */
export function hit(doc: VectorDoc, x: number, y: number, inside: Path = [], pad = 0): Path | null {
  const list = pickable(doc, get(doc, inside), inside);
  for (let i = list.length - 1; i >= 0; i--) {
    const b = bounds(doc, list[i]);
    if (b !== null && b.x0 - pad <= x && x <= b.x1 + pad && b.y0 - pad <= y && y <= b.y1 + pad) return list[i];
  }
  return null;
}

/** Everything (as `hit` would pick it) lying entirely inside `box`. */
export function inBox(doc: VectorDoc, box: Box, inside: Path = []): Path[] {
  return pickable(doc, get(doc, inside), inside).filter((p) => {
    const b = bounds(doc, p);
    return b !== null && box.x0 <= b.x0 && box.y0 <= b.y0 && b.x1 <= box.x1 && b.y1 <= box.y1;
  });
}

export const ids = (doc: VectorDoc) => new Set(byId(doc).keys());

export function uniqueId(taken: Set<string>, baseIn: string): string {
  const base = baseIn.replace(/-?[0-9]+$/, "") || "shape";
  let n = 1;
  while (taken.has(`${base}${n}`)) n++;
  return `${base}${n}`;
}

// ------------------------------------------------------------------------------- rebuilding

/** `doc` with the element at `path` replaced (the same `doc` if it's unchanged). */
export function replaceAt(doc: VectorDoc, path: Path, neu: Element): VectorDoc {
  if (get(doc, path) === neu) return doc;
  const down = (el: Element, rest: Path): Element => {
    if (!rest.length) return neu;
    const kids = [...el.children];
    const i = slot(el, rest[0]);
    kids[i] = down(kids[i] as Element, rest.slice(1));
    return el.withChildren(kids);
  };
  return new VectorDoc(doc.xml.withRoot(down(doc.root, path)));
}

const update = (doc: VectorDoc, path: Path, change: (el: Element) => Element) =>
  replaceAt(doc, path, change(get(doc, path)));

const blank = (n: Node | undefined): n is Text => n instanceof Text && trim(n.raw) === "";

/** The whitespace written before child `i`. */
function indentOf(parent: Element, i: number): string {
  const before = i > 0 ? parent.children[i - 1] : undefined;
  return blank(before) ? before.raw : "";
}

/** Drop the children at `slots` and the whitespace just before each. */
function removeSlots(kids: Node[], slots: number[]): Node[] {
  const drop = new Set(slots);
  for (const i of slots) if (i > 0 && blank(kids[i - 1])) drop.add(i - 1);
  return kids.filter((_c, j) => !drop.has(j));
}

function siblings(paths: Path[]): Path {
  const parents = new Set(paths.map((p) => p.slice(0, -1).join(",")));
  if (parents.size !== 1) throw new VectorError("Pick things that sit in the same group.");
  return paths[0].slice(0, -1);
}

function check(doc: VectorDoc, paths: Path[]): Path[] {
  if (!paths.length) throw new VectorError("Nothing is selected.");
  for (const p of paths) {
    if (!p.length) throw new VectorError("The drawing itself can't be changed this way.");
    try {
      get(doc, p);
    } catch {
      throw new VectorError("That part of the drawing isn't there any more.");
    }
  }
  return sortedUnique(paths);
}

// ------------------------------------------------------------------------------- edits

/** Apply `m` (in root user units) to each element. */
export function transform(docIn: VectorDoc, paths: Path[], m: Matrix): VectorDoc {
  let doc = docIn;
  for (const p of check(doc, paths)) {
    const pm = parentMatrix(doc, p);
    const local = pm.isIdentity() ? m : pm.inverse().times(m).times(pm);
    doc = update(doc, p, (el) => shapes.transformed(el, local));
  }
  return doc;
}

export const move = (doc: VectorDoc, paths: Path[], dx: number, dy: number) => transform(doc, paths, translate(dx, dy));

/** Scale the selection so its box becomes `box` (a side of 0 is refused). */
export function resize(doc: VectorDoc, paths: Path[], box: Box): VectorDoc {
  const old = selectionBounds(doc, check(doc, paths));
  if (old === null) throw new VectorError("The selection has no size.");
  if (box.width <= 0 || box.height <= 0) throw new VectorError("Width and height must be more than 0.");
  const sx = old.width ? box.width / old.width : 1;
  const sy = old.height ? box.height / old.height : 1;
  const m = translate(box.x0, box.y0).times(scale(sx, sy)).times(translate(-old.x0, -old.y0));
  return transform(doc, paths, m);
}

export function setStyle(docIn: VectorDoc, paths: Path[], name: string, value: string | null): VectorDoc {
  let doc = docIn;
  for (const p of check(doc, paths)) doc = update(doc, p, (el) => shapes.styleSet(el, name, value));
  return doc;
}

export const setHidden = (doc: VectorDoc, paths: Path[], hide: boolean) =>
  setStyle(doc, paths, "display", hide ? "none" : null);

export function rename(doc: VectorDoc, path: Path, newIdIn: string): VectorDoc {
  const newId = newIdIn.trim();
  const el = get(doc, path);
  if (newId === (el.get("id") || "")) return doc;
  if (newId && !/^[A-Za-z_][\w.-]*$/.test(newId)) throw new VectorError("A name starts with a letter and has no spaces.");
  if (newId && ids(doc).has(newId)) throw new VectorError(`“${newId}” is already used.`);
  return replaceAt(doc, path, el.set("id", newId || null));
}

export function remove(docIn: VectorDoc, pathsIn: Path[]): VectorDoc {
  let doc = docIn;
  const paths = check(doc, pathsIn);
  const order = [...paths].sort((a, b) => a.length - b.length || comparePaths(a, b)).reverse();
  for (const p of order) {
    if (paths.some((q) => q.length < p.length && samePath(p.slice(0, q.length), q))) continue;
    const parent = get(doc, p.slice(0, -1));
    const kids = removeSlots([...parent.children], [slot(parent, p[p.length - 1])]);
    doc = replaceAt(doc, p.slice(0, -1), parent.withChildren(kids));
  }
  return doc;
}

function reid(el: Element, taken: Set<string>): Element {
  let out = el;
  const old = out.get("id");
  if (old !== null) {
    const neu = uniqueId(taken, old);
    taken.add(neu);
    out = out.set("id", neu);
  }
  return out.withChildren(out.children.map((c) => (c instanceof Element ? reid(c, taken) : c)));
}

/** Copies right above each original (same parent); returns the copies' paths. */
export function duplicate(docIn: VectorDoc, pathsIn: Path[]): [VectorDoc, Path[]] {
  let doc = docIn;
  const paths = check(doc, pathsIn);
  const taken = ids(doc);
  for (const p of [...paths].reverse()) {
    const parent = get(doc, p.slice(0, -1));
    const i = slot(parent, p[p.length - 1]);
    const copy = reid(parent.children[i] as Element, taken);
    const indent = indentOf(parent, i);
    const kids = [...parent.children];
    kids.splice(i + 1, 0, ...(indent ? [new Text(indent)] : []), copy);
    doc = replaceAt(doc, p.slice(0, -1), parent.withChildren(kids));
  }
  const out = paths.map((p) => {
    const before = paths.filter((q) => samePath(q.slice(0, -1), p.slice(0, -1)) && q[q.length - 1] < p[p.length - 1]).length;
    return [...p.slice(0, -1), p[p.length - 1] + before + 1];
  });
  return [doc, out];
}

/** `where` = "forward", "backward", "front" or "back" among the element's siblings. */
export function reorder(doc: VectorDoc, path: Path, where: "forward" | "backward" | "front" | "back"): [VectorDoc, Path] {
  if (!path.length) throw new VectorError("The drawing itself can't be moved.");
  const parent = get(doc, path.slice(0, -1));
  const els = [...parent.elements];
  const k = path[path.length - 1];
  const n = els.length;
  const target = { forward: Math.min(k + 1, n - 1), backward: Math.max(k - 1, 0), front: n - 1, back: 0 }[where];
  if (target === k) return [doc, path];
  const [el] = els.splice(k, 1);
  els.splice(target, 0, el);
  const kids = [...parent.children];
  let e = 0;
  kids.forEach((c, i) => {
    if (c instanceof Element) kids[i] = els[e++];
  });
  return [replaceAt(doc, path.slice(0, -1), parent.withChildren(kids)), [...path.slice(0, -1), target]];
}

/** Put the elements into a new `<g>` where the topmost of them was. */
export function group(doc: VectorDoc, pathsIn: Path[]): [VectorDoc, Path] {
  const paths = check(doc, pathsIn);
  const parentPath = siblings(paths);
  const parent = get(doc, parentPath);
  let kids = [...parent.children];
  const slots = paths.map((p) => slot(parent, p[p.length - 1]));
  const members = slots.map((s) => kids[s]);
  const ws = indentOf(parent, slots[slots.length - 1]);
  const inner: Node[] = [];
  for (const mbr of members) inner.push(...(ws ? [new Text(ws + "  "), mbr] : [mbr]));
  if (ws) inner.push(new Text(ws));
  const g = xmlsrc.element("g", [["id", uniqueId(ids(doc), "group")]], inner);
  kids[slots[slots.length - 1]] = g;
  kids = removeSlots(kids, slots.slice(0, -1));
  const newParent = parent.withChildren(kids);
  const k = newParent.elements.indexOf(g);
  return [replaceAt(doc, parentPath, newParent), [...parentPath, k]];
}

/** Put a group's parts in its place: its transform and inherited style go down to them. */
export function ungroup(doc: VectorDoc, path: Path): [VectorDoc, Path[]] {
  const g = get(doc, path);
  if (!CONTAINERS.includes(kind(g)) || !path.length) throw new VectorError("Only a group can be ungrouped.");
  const m = shapes.ownTransform(g);
  const passed = shapes.INHERITED.filter((n) => shapes.styleGet(g, n)).map((n) => [n, shapes.styleGet(g, n)!]);
  const opacity = shapes.length(shapes.styleGet(g, "opacity"));
  const members: Node[] = [];
  for (const c of g.children) {
    if (c instanceof Element && editable(c)) {
      let el = shapes.transformed(c, m);
      for (const [name, value] of passed) if (shapes.styleGet(el, name) === null) el = shapes.styleSet(el, name, value);
      if (opacity !== null && opacity !== 1) {
        const own = shapes.length(shapes.styleGet(el, "opacity"));
        el = shapes.styleSet(el, "opacity", num(opacity * (own === null ? 1 : own)));
      }
      members.push(el);
    } else if (c instanceof Element && !HIDDEN.includes(kind(c))) members.push(shapes.transformed(c, m));
    else if (c instanceof Element || !(c instanceof Text)) members.push(c);
  }
  const parent = get(doc, path.slice(0, -1));
  const kids = [...parent.children];
  const i = slot(parent, path[path.length - 1]);
  const indent = indentOf(parent, i);
  const spaced: Node[] = [];
  members.forEach((mbr, j) => {
    if (j && indent) spaced.push(new Text(indent));
    spaced.push(mbr);
  });
  kids.splice(i, 1, ...spaced);
  const newParent = parent.withChildren(kids);
  const firsts: Path[] = [];
  newParent.elements.forEach((c, j) => {
    if (members.includes(c)) firsts.push([...path.slice(0, -1), j]);
  });
  return [replaceAt(doc, path.slice(0, -1), newParent), firsts];
}

/** Append `el` on top of `parent`'s children (indented like the last one). */
export function add(doc: VectorDoc, el: Element, parent: Path = []): [VectorDoc, Path] {
  let host = get(doc, parent);
  let kids = [...host.children];
  let last = -1;
  kids.forEach((c, i) => {
    if (c instanceof Element) last = i;
  });
  if (last >= 0) {
    const indent = indentOf(host, last);
    kids.splice(last + 1, 0, ...(indent ? [new Text(indent)] : []), el);
  } else {
    const tail = kids.length ? kids[kids.length - 1] : undefined;
    const ws = blank(tail) ? tail.raw : "";
    kids = ws ? [...kids.slice(0, -1), new Text(ws + "  "), el, new Text(ws)] : [...kids, el];
  }
  host = host.withChildren(kids);
  const k = host.elements.indexOf(el);
  return [replaceAt(doc, parent, host), [...parent, k]];
}

export function setText(doc: VectorDoc, path: Path, text: string): VectorDoc {
  const el = get(doc, path);
  if (kind(el) !== "text") throw new VectorError("Only text can be typed into.");
  if (el.text() === text) return doc;
  return replaceAt(doc, path, el.withChildren([new Text(xmlsrc.escapeText(text))]));
}

/** A plain attribute change (the Node tool's `d`, a rectangle's corner radius …). */
export const setAttr = (doc: VectorDoc, path: Path, name: string, value: string | null) =>
  update(doc, path, (el) => el.set(name, value));

/** A rectangle / ellipse / line drawn from `box` (a line goes corner to corner). */
export function newShape(kindIn: "rect" | "ellipse" | "line", box: Box, style: shapes.StyleItem[], taken: Set<string>): Element {
  const ident = uniqueId(taken, kindIn);
  let geo: [string, number][];
  if (kindIn === "rect") geo = [["x", box.x0], ["y", box.y0], ["width", box.width], ["height", box.height]];
  else if (kindIn === "ellipse") {
    geo = [["cx", (box.x0 + box.x1) / 2], ["cy", (box.y0 + box.y1) / 2], ["rx", box.width / 2], ["ry", box.height / 2]];
  } else geo = [["x1", box.x0], ["y1", box.y0], ["x2", box.x1], ["y2", box.y1]];
  const attrs: xmlsrc.Attr[] = [["id", ident], ...geo.map(([k, v]): xmlsrc.Attr => [k, num(v)])];
  if (style.length) attrs.push(["style", shapes.writeStyle(style)]);
  return xmlsrc.element(kindIn, attrs);
}
