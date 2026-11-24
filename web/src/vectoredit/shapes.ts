// What the vector editor understands of an SVG element — a line-by-line port of
// src/umc/vectoredit/shapes.py: bounds, styles and the geometry of the simple shapes.

import { Box, IDENTITY, Matrix, formatTransform, num, parseTransform } from "./geom";
import * as pathdata from "./pathdata";
import type { Element } from "./xmlsrc";

export const SHAPES = ["rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "image", "use"];
export const CONTAINERS = ["g", "a", "switch"];
export const HIDDEN = [
  "defs", "metadata", "title", "desc", "style", "script", "namedview", "clipPath", "mask",
  "pattern", "marker", "symbol", "linearGradient", "radialGradient", "filter", "font",
];
export const INHERITED = [
  "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
  "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "font-family", "font-size",
  "font-weight", "font-style", "text-anchor", "color",
];
export const DEFAULT_FONT_SIZE = 16;

const LENGTH = /^[ \t\r\n]*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(px)?[ \t\r\n]*$/;
const NUMBERS = /[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/g;

/** Python's `str.strip(" \t\r\n")`. */
export const trim = (s: string) => s.replace(/^[ \t\r\n]+|[ \t\r\n]+$/g, "");

/** `svg:rect` → `rect`. */
export const local = (name: string) => name.slice(name.lastIndexOf(":") + 1);

export const kind = (el: Element) => local(el.name);

/** A plain number or `px` length; anything else (%, em, mm …) → null. */
export function length(value: string | null): number | null {
  if (value === null) return null;
  const m = LENGTH.exec(value);
  return m ? Number(m[1]) : null;
}

export const numbers = (value: string | null): number[] => (value ?? "").match(NUMBERS)?.map(Number) ?? [];

// ------------------------------------------------------------------------------- style

export type StyleItem = [string, string];

/** `style="a:b; c:d"` → `[["a", "b"], ["c", "d"]]` (order kept, names trimmed). */
export function parseStyle(text: string | null): StyleItem[] {
  const out: StyleItem[] = [];
  for (const part of (text ?? "").split(";")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const k = trim(part.slice(0, i));
    const v = trim(part.slice(i + 1));
    if (k) out.push([k, v]);
  }
  return out;
}

export const writeStyle = (items: StyleItem[]) => items.map(([k, v]) => `${k}:${v}`).join(";");

/** The element's own value (the `style` attribute wins over the attribute). */
export function styleGet(el: Element, name: string): string | null {
  for (const [k, v] of parseStyle(el.get("style"))) if (k === name) return v;
  return el.get(name);
}

/** Change `name` where it was written; a new one goes into `style` if the element has one,
 * else into an attribute. null removes it from both places. */
export function styleSet(elIn: Element, name: string, value: string | null): Element {
  let el = elIn;
  let items = parseStyle(el.get("style"));
  const inStyle = items.some(([k]) => k === name);
  if (value === null) {
    if (inStyle) {
      items = items.filter(([k]) => k !== name);
      el = el.set("style", items.length ? writeStyle(items) : null);
    }
    return el.set(name, null);
  }
  if (inStyle) {
    el = el.set("style", writeStyle(items.map(([k, v]) => [k, k === name ? value : v])));
    return el.get(name) !== null ? el.set(name, null) : el;
  }
  if (el.get(name) !== null || el.get("style") === null) return el.set(name, value);
  return el.set("style", writeStyle([...items, [name, value]]));
}

export const hidden = (el: Element) => styleGet(el, "display") === "none";

// ------------------------------------------------------------------------------- geometry

export function ownTransform(el: Element): Matrix {
  try {
    return parseTransform(el.get("transform") ?? "");
  } catch {
    return IDENTITY;
  }
}

function points(el: Element): [number, number][] {
  const v = numbers(el.get("points"));
  const out: [number, number][] = [];
  for (let i = 0; i < v.length - 1; i += 2) out.push([v[i], v[i + 1]]);
  return out;
}

export const textOf = (el: Element) => el.text();

/** Python's `len(str)`: code points, not UTF-16 units. */
const chars = (s: string) => [...s].length;

export type Find = (id: string) => Element | undefined;
export const MAX_USE_DEPTH = 8;

function href(el: Element): string | null {
  const ref = el.get("href") || el.get("xlink:href");
  return ref && ref.startsWith("#") ? ref.slice(1) : null;
}

/** The element's box in its own coordinates (before its `transform`); null if empty or
 * unknown. Text is estimated (0.6 em per character). `find` looks an id up, so a `<use>`
 * without a size takes the box of what it shows. */
export function localBounds(el: Element, find: Find | null = null, depth = 0): Box | null {
  const k = kind(el);
  if (k === "use" && el.get("width") === null && find !== null && depth < MAX_USE_DEPTH) {
    const ref = href(el);
    const target = ref ? find(ref) : undefined;
    const b = target === undefined ? null : bounds(target, find, depth + 1);
    if (b === null) return null;
    const x = length(el.get("x", "0")) || 0;
    const y = length(el.get("y", "0")) || 0;
    return new Box(b.x0 + x, b.y0 + y, b.x1 + x, b.y1 + y);
  }
  if (k === "rect" || k === "image" || k === "use") {
    const x = length(el.get("x", "0"));
    const y = length(el.get("y", "0"));
    const w = length(el.get("width"));
    const h = length(el.get("height"));
    if (x === null || y === null || w === null || h === null || w <= 0 || h <= 0) return null;
    return new Box(x, y, x + w, y + h);
  }
  if (k === "circle" || k === "ellipse") {
    const cx = length(el.get("cx", "0"));
    const cy = length(el.get("cy", "0"));
    let rx: number | null;
    let ry: number | null;
    if (k === "circle") rx = ry = length(el.get("r"));
    else {
      rx = length(el.get("rx"));
      ry = length(el.get("ry"));
    }
    if (cx === null || cy === null || rx === null || ry === null || rx <= 0 || ry <= 0) return null;
    return new Box(cx - rx, cy - ry, cx + rx, cy + ry);
  }
  if (k === "line") {
    const vals = ["x1", "y1", "x2", "y2"].map((a) => length(el.get(a, "0")));
    if (vals.includes(null)) return null;
    const [x1, y1, x2, y2] = vals as number[];
    return new Box(Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2));
  }
  if (k === "polyline" || k === "polygon") {
    const pts = points(el);
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    return new Box(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys));
  }
  if (k === "path") return pathdata.bounds(pathdata.parse(el.get("d") ?? ""));
  if (k === "text") {
    const xs = numbers(el.get("x", "0"));
    const ys = numbers(el.get("y", "0"));
    let x = xs.length ? xs[0] : 0;
    const y = ys.length ? ys[0] : 0;
    const size = length(styleGet(el, "font-size")) || DEFAULT_FONT_SIZE;
    const n = chars(textOf(el));
    if (n === 0) return null;
    const w = n * 0.6 * size;
    const anchor = styleGet(el, "text-anchor");
    if (anchor === "middle") x -= w / 2;
    else if (anchor === "end") x -= w;
    return new Box(x, y - 0.8 * size, x + w, y + 0.2 * size);
  }
  if (CONTAINERS.includes(k) || k === "svg") {
    let box: Box | null = null;
    for (const child of el.elements) {
      if (HIDDEN.includes(kind(child)) || hidden(child)) continue;
      const b = bounds(child, find, depth);
      if (b !== null) box = box === null ? b : box.union(b);
    }
    return box;
  }
  return null;
}

/** The element's box in its parent's coordinates (its own `transform` applied). */
export function bounds(el: Element, find: Find | null = null, depth = 0): Box | null {
  if (kind(el) === "svg") return localBounds(el, find, depth);
  const b = localBounds(el, find, depth);
  return b === null ? null : b.transformed(ownTransform(el));
}

// ------------------------------------------------------------------------------- changing

function setNums(elIn: Element, values: [string, number][]): Element {
  let el = elIn;
  for (const [name, v] of values) el = el.set(name, num(v));
  return el;
}

const LEADING_TRANSLATE = /^[ \t\r\n]*translate[ \t\r\n]*\(([^)]*)\)[ \t\r\n]*,?[ \t\r\n]*/;

/** Put `m` in front of the element's own transform. A move keeps the rest readable: it becomes
 * (or adds to) a leading `translate(…)`; anything else is written as one matrix. */
function fold(el: Element, m: Matrix): Element {
  const text = el.get("transform") ?? "";
  const own = ownTransform(el);
  if (m.isTranslate() && trim(text) && !own.isTranslate()) {
    const lead = LEADING_TRANSLATE.exec(text);
    let rest = lead ? text.slice(lead[0].length) : trim(text);
    let tx = m.e;
    let ty = m.f;
    if (lead) {
      const v = numbers(lead[1]);
      if (v.length === 1 || v.length === 2) {
        tx += v[0];
        ty += v.length === 2 ? v[1] : 0;
      } else rest = trim(text);
    }
    const move = formatTransform(new Matrix(1, 0, 0, 1, tx, ty));
    return el.set("transform", move ? `${move} ${rest}`.trim() : rest);
  }
  return el.set("transform", formatTransform(m.times(own)) || null);
}

/** `el` moved / scaled by `m` (given in the parent's coordinates); see shapes.py. */
export function transformed(el: Element, m: Matrix): Element {
  if (m.isIdentity()) return el;
  const k = kind(el);
  const plain = ownTransform(el).isIdentity();
  const aligned = m.b === 0 && m.c === 0;
  if (plain && k === "path") {
    return el.set("d", pathdata.write(pathdata.transform(pathdata.parse(el.get("d") ?? ""), m)));
  }
  if (plain && (k === "polyline" || k === "polygon" || k === "line")) {
    if (k === "line") {
      const vals = ["x1", "y1", "x2", "y2"].map((a) => length(el.get(a, "0")));
      if (!vals.includes(null)) {
        const v = vals as number[];
        const [x1, y1] = m.apply(v[0], v[1]);
        const [x2, y2] = m.apply(v[2], v[3]);
        return setNums(el, [["x1", x1], ["y1", y1], ["x2", x2], ["y2", y2]]);
      }
    } else {
      const pts = points(el).map(([x, y]) => m.apply(x, y));
      return el.set("points", pts.map(([x, y]) => `${num(x)},${num(y)}`).join(" "));
    }
  }
  if (plain && aligned && m.a > 0 && m.d > 0) {
    const x = length(el.get("x", "0"));
    const y = length(el.get("y", "0"));
    const w = length(el.get("width"));
    const h = length(el.get("height"));
    const sized = w !== null && h !== null;
    if ((k === "rect" || k === "image" || k === "use") && x !== null && y !== null && (sized || m.isTranslate())) {
      const [x0, y0] = m.apply(x, y);
      let out = setNums(el, [["x", x0], ["y", y0]]);
      if (sized) out = setNums(out, [["width", w! * m.a], ["height", h! * m.d]]);
      if (k === "rect" && (m.a !== 1 || m.d !== 1)) {
        for (const [attr, s] of [["rx", m.a], ["ry", m.d]] as [string, number][]) {
          const r = length(el.get(attr));
          if (r !== null) out = setNums(out, [[attr, r * s]]);
        }
      }
      return out;
    }
    if (k === "ellipse" || (k === "circle" && m.a === m.d)) {
      const b = localBounds(el);
      if (b !== null) {
        const [cx, cy] = m.apply((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2);
        const out = setNums(el, [["cx", cx], ["cy", cy]]);
        if (k === "circle") return setNums(out, [["r", (b.width / 2) * m.a]]);
        return setNums(out, [["rx", (b.width / 2) * m.a], ["ry", (b.height / 2) * m.d]]);
      }
    }
    const positioned = el.elements.some((c) => c.get("x") || c.get("y"));
    if (k === "text" && m.isTranslate() && !positioned) {
      const xs = numbers(el.get("x", "0"));
      const ys = numbers(el.get("y", "0"));
      if (ys.length <= 1) {
        const [nx, ny] = m.apply(xs.length ? xs[0] : 0, ys.length ? ys[0] : 0);
        return setNums(el, [["x", nx], ["y", ny]]);
      }
    }
  }
  return fold(el, m);
}
