// A picture turned into a 3D logo for printing (port of the desktop's umc/editor3d/logo.py,
// line by line: same arithmetic in the same order, checked by tests/fixtures/logo3d_cases.json).
// mask → trace (pixel-edge corners, simplified) → shape (mm, holes nested, specks left out) →
// extrude / badge (closed meshes); placement puts a logo onto a flat face (see pick.ts).
// No DOM, no three.js.
import earcut from "earcut";
import type { Vec3 } from "./camera";

export const MAX_SIDE = 1024;
export const MAX_EDGES = 2_000_000;
export const DETAIL = { smooth: 1.5, normal: 0.8, fine: 0.5 } as const;
export type Detail = keyof typeof DETAIL;
export type MaskMode = "alpha" | "dark" | "light";
export const SPECK_FRACTION = 0.005;
export const MIN_FEATURE_MM = 0.4;
export const SADDLE_NUDGE = 0.05; // px: corners where two parts touch are moved apart
export const SINK_MM = 0.2;
export const HOLE_RADIUS_MM = 2.5;
export const MIN_WALL_MM = 1; // plate left all round the keychain hole
export const CORNER_SEGMENTS = 8;
export const HOLE_SEGMENTS = 32;

export type Point = [number, number];
export type Ring = Point[];

export class LogoError extends Error {}

const NO_SHAPE = "The picture has no clear shape — try another “Shape from” setting.";

// ------------------------------------------------------------------------------ mask
export const luma = (r: number, g: number, b: number) => Math.floor((r * 299 + g * 587 + b * 114) / 1000);

export function hasTransparency(rgba: ArrayLike<number>, w: number, h: number): boolean {
  for (let i = 3; i < w * h * 4; i += 4) if (rgba[i] < 255) return true;
  return false;
}

export const defaultMode = (rgba: ArrayLike<number>, w: number, h: number): MaskMode => (hasTransparency(rgba, w, h) ? "alpha" : "dark");

/** 0 / 1 per pixel, row by row (see logo.py ``mask``). */
export function mask(rgba: ArrayLike<number>, w: number, h: number, mode: MaskMode = "alpha", threshold = 128, invert = false): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const r = rgba[i * 4], g = rgba[i * 4 + 1], b = rgba[i * 4 + 2], a = rgba[i * 4 + 3];
    let on = a >= 128;
    if (mode !== "alpha") {
      const y = luma(r, g, b);
      on = on && (mode === "dark" ? y < threshold : y > threshold);
    }
    out[i] = (invert ? !on : on) ? 1 : 0;
  }
  return out;
}

// ------------------------------------------------------------------------------ trace
const DX = [1, 0, -1, 0];
const DY = [0, 1, 0, -1];

/** Boundary edges as key (corner · 4 + direction) → order, in the desktop's order. */
function edges(m: ArrayLike<number>, w: number, h: number): Map<number, number> {
  const at = (x: number, y: number) => (x < 0 || y < 0 || x >= w || y >= h ? 0 : m[y * w + x]);
  let total = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++)
      if (at(x, y) === 1) total += +(at(x, y - 1) === 0) + +(at(x + 1, y) === 0) + +(at(x, y + 1) === 0) + +(at(x - 1, y) === 0);
  if (total > MAX_EDGES) throw new LogoError("The picture is too busy to make a logo from — try a simpler one.");
  const start: Point[] = [[0, 0], [1, 0], [1, 1], [0, 1]];
  const out = new Map<number, number>();
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      if (at(x, y) !== 1) continue;
      const empty = [at(x, y - 1) === 0, at(x + 1, y) === 0, at(x, y + 1) === 0, at(x - 1, y) === 0];
      for (let side = 0; side < 4; side++) {
        if (!empty[side]) continue;
        const [sx, sy] = start[side];
        out.set(((y + sy) * (w + 1) + x + sx) * 4 + side, out.size);
      }
    }
  return out;
}

function loops(m: ArrayLike<number>, w: number, h: number): [[number, number][][], Set<number>] {
  const all = edges(m, w, h);
  const starts = new Map<number, number>();
  for (const key of all.keys()) starts.set(Math.floor(key / 4), (starts.get(Math.floor(key / 4)) ?? 0) + 1);
  const saddles = new Set<number>();
  for (const [v, n] of starts) if (n > 1) saddles.add(v);
  const used = new Set<number>();
  const result: [number, number][][] = [];
  for (const key of all.keys()) {
    if (used.has(key)) continue;
    const loop: [number, number][] = [];
    let v = Math.floor(key / 4), d = key % 4;
    while (!used.has(v * 4 + d)) {
      used.add(v * 4 + d);
      const x = v % (w + 1), y = Math.floor(v / (w + 1));
      loop.push([x, y]);
      const nv = (y + DY[d]) * (w + 1) + x + DX[d];
      let found = false;
      for (const nd of [(d + 1) % 4, d, (d + 3) % 4]) {
        if (all.has(nv * 4 + nd)) {
          v = nv;
          d = nd;
          found = true;
          break;
        }
      }
      if (!found) break;
    }
    result.push(loop);
  }
  return [result, saddles];
}

function simplify(points: Ring, tol: number): Ring {
  const n = points.length;
  if (n < 4) return points;
  const [x0, y0] = points[0];
  let far = 0, best = -1;
  for (let i = 1; i < n; i++) {
    const dx = points[i][0] - x0, dy = points[i][1] - y0;
    const dd = dx * dx + dy * dy;
    if (dd > best) {
      far = i;
      best = dd;
    }
  }
  const keep = new Array<boolean>(n).fill(false);
  keep[0] = keep[far] = true;
  const stack: [number, number][] = [[0, far], [far, n]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    const [ax, ay] = points[a];
    const [bx, by] = points[b % n];
    const dx = bx - ax, dy = by - ay;
    const length = Math.sqrt(dx * dx + dy * dy);
    let idx = -1, worst = tol;
    for (let i = a + 1; i < b; i++) {
      const [px, py] = points[i];
      const dist = length === 0 ? Math.sqrt((px - ax) ** 2 + (py - ay) ** 2) : Math.abs(dx * (ay - py) - dy * (ax - px)) / length;
      if (dist > worst) {
        idx = i;
        worst = dist;
      }
    }
    if (idx >= 0) {
      keep[idx] = true;
      stack.push([a, idx]);
      stack.push([idx, b]);
    }
  }
  return points.filter((_, i) => keep[i]);
}

function dropFlat(points: Ring): Ring {
  let pts = points.slice();
  let changed = true;
  while (changed && pts.length >= 3) {
    changed = false;
    const out: Ring = [];
    const n = pts.length;
    for (let i = 0; i < n; i++) {
      const [ax, ay] = out.length ? out[out.length - 1] : pts[(i - 1 + n) % n];
      const [px, py] = pts[i];
      const [bx, by] = pts[(i + 1) % n];
      if ((px === ax && py === ay) || (bx - ax) * (py - ay) - (by - ay) * (px - ax) === 0) {
        changed = true;
        continue;
      }
      out.push([px, py]);
    }
    pts = out;
  }
  return pts;
}

/** Signed area (shoelace): positive = counter-clockwise. */
export function area(ring: Ring): number {
  let s = 0;
  const n = ring.length;
  for (let i = 0; i < n; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[(i + 1) % n];
    s += x1 * y2 - x2 * y1;
  }
  return s / 2;
}

/** Closed outlines in pixel units, y up (outer rings counter-clockwise, holes clockwise). */
export function trace(m: ArrayLike<number>, w: number, h: number, detail: Detail = "normal"): Ring[] {
  return traceBoth(m, w, h, detail).map(([ring]) => ring);
}

/** ``trace``, with each ring's unsimplified corners next to it (``shape`` falls back to them
 * where simplifying made rings touch or cross). */
export function traceBoth(m: ArrayLike<number>, w: number, h: number, detail: Detail = "normal"): [Ring, Ring][] {
  const tol = DETAIL[detail];
  const rings: [Ring, Ring][] = [];
  const [all, saddles] = loops(m, w, h);
  for (const loop of all) {
    const n = loop.length;
    const corners: Ring = [];
    for (let i = n - 1; i >= 0; i--) {
      const [px, py] = loop[(i - 1 + n) % n];
      const [x, y] = loop[i];
      const [nx, ny] = loop[(i + 1) % n];
      const ix = x - px, iy = y - py, ox = nx - x, oy = ny - y;
      if (ix === ox && iy === oy) continue;
      if (saddles.has(y * (w + 1) + x)) {
        // into this part's own pixel: the parts no longer touch
        const cx = x + SADDLE_NUDGE * (-iy - oy);
        const cy = y + SADDLE_NUDGE * (ix + ox);
        corners.push([cx, h - cy]);
      } else corners.push([x, h - y]);
    }
    const raw = dropFlat(corners);
    let ring = dropFlat(simplify(corners, tol));
    if (ring.length < 3 || area(ring) === 0) ring = raw;
    if (ring.length >= 3 && area(ring) !== 0) rings.push([ring, raw]);
  }
  return rings;
}

// ------------------------------------------------------------------------------ shape
export function inside([x, y]: Point, ring: Ring): boolean {
  let hit = false;
  const n = ring.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
}

export interface Polygon {
  outer: Ring;
  holes: Ring[];
  /** Cap triangles, made once (``prepare``): moving / turning a logo only transforms it. */
  caps?: [number, number, number][];
}

export interface Shape {
  polygons: Polygon[];
  width: number; // mm
  height: number;
  dropped: number; // tiny bits left out
}

export const shapeArea = (s: Shape) => s.polygons.reduce((t, p) => t + area(p.outer) + p.holes.reduce((u, h) => u + area(h), 0), 0);

const min = (v: number[]) => v.reduce((a, b) => (b < a ? b : a), Infinity);
const max = (v: number[]) => v.reduce((a, b) => (b > a ? b : a), -Infinity);

/** See logo.py ``shape``; with ``raw`` (unsimplified rings, same order) a polygon whose rings
 * touch or cross uses those instead. */
export function shape(rings: Ring[], widthMm: number, heightMm: number | null = null, raw: Ring[] | null = null): Shape {
  if (!rings.length) throw new LogoError(NO_SHAPE);
  const xs = rings.flatMap((r) => r.map((p) => p[0]));
  const ys = rings.flatMap((r) => r.map((p) => p[1]));
  const x0 = min(xs), x1 = max(xs), y0 = min(ys), y1 = max(ys);
  if (x1 - x0 <= 0 || y1 - y0 <= 0) throw new LogoError(NO_SHAPE);
  const sx = widthMm / (x1 - x0);
  const sy = heightMm === null ? sx : heightMm / (y1 - y0);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const scaled: Ring[] = rings.map((r) => r.map(([x, y]) => [(x - cx) * sx, (y - cy) * sy] as Point));
  const unsimplified = raw ? raw.map((r) => r.map(([x, y]) => [(x - cx) * sx, (y - cy) * sy] as Point)) : null;
  const plain = new Map(scaled.map((r, i) => [r, i]));
  let total = 0;
  for (const r of scaled) if (area(r) > 0) total += area(r);
  const limit = Math.max(total * SPECK_FRACTION, MIN_FEATURE_MM * MIN_FEATURE_MM);
  const outers: Ring[] = [], holes: Ring[] = [];
  let dropped = 0;
  for (const r of scaled) {
    const a = area(r);
    if (Math.abs(a) < limit) dropped++;
    else if (a > 0) outers.push(r);
    else holes.push(r);
  }
  if (!outers.length) throw new LogoError("The logo would be too small to print — make it wider.");
  const polygons: Polygon[] = outers.map((o) => ({ outer: o, holes: [] }));
  const sizes = outers.map(area);
  for (const hole of holes) {
    let best = -1;
    outers.forEach((o, i) => {
      if (inside(hole[0], o) && (best < 0 || sizes[i] < sizes[best])) best = i;
    });
    if (best < 0) dropped++;
    else polygons[best].holes.push(hole);
  }
  if (unsimplified)
    for (const p of polygons)
      if (ringsMeet([p.outer, ...p.holes])) {
        p.outer = unsimplified[plain.get(p.outer)!];
        p.holes = p.holes.map((h) => unsimplified[plain.get(h)!]);
      }
  const px = polygons.flatMap((p) => p.outer.map((q) => q[0]));
  const py = polygons.flatMap((p) => p.outer.map((q) => q[1]));
  return { polygons, width: max(px) - min(px), height: max(py) - min(py), dropped };
}

const orient = (ox: number, oy: number, ax: number, ay: number, bx: number, by: number) =>
  Math.sign((ax - ox) * (by - oy) - (ay - oy) * (bx - ox));

/** Does any ring cross or touch itself (edges that aren't neighbours), or another ring? Only
 * edges whose x ranges overlap are compared (sorted by their left end). */
export function ringsMeet(rings: Ring[]): boolean {
  const e: { ax: number; ay: number; bx: number; by: number; lx: number; hx: number; ly: number; hy: number; ring: number; local: number; size: number }[] = [];
  rings.forEach((r, k) => r.forEach(([ax, ay], i) => {
    const [bx, by] = r[(i + 1) % r.length];
    e.push({ ax, ay, bx, by, lx: Math.min(ax, bx), hx: Math.max(ax, bx), ly: Math.min(ay, by), hy: Math.max(ay, by), ring: k, local: i, size: r.length });
  }));
  const order = e.map((_, i) => i).sort((a, b) => e[a].lx - e[b].lx || a - b);
  const left = order.map((i) => e[i].lx);
  const firstAtLeast = (v: number) => {
    let lo = 0, hi = left.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (left[mid] < v) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  for (let i = 0; i < e.length; i++) {
    const a = e[i];
    for (let k = firstAtLeast(a.lx); k < order.length && left[k] <= a.hx; k++) {
      const j = order[k];
      if (j === i) continue;
      const b = e[j];
      const d = Math.abs(a.local - b.local);
      if (a.ring === b.ring && (d === 1 || d === a.size - 1)) continue;
      if (a.hy < b.ly || a.ly > b.hy) continue;
      if (orient(a.ax, a.ay, a.bx, a.by, b.ax, b.ay) * orient(a.ax, a.ay, a.bx, a.by, b.bx, b.by) <= 0 &&
        orient(b.ax, b.ay, b.bx, b.by, a.ax, a.ay) * orient(b.ax, b.ay, b.bx, b.by, a.bx, a.by) <= 0) return true;
    }
  }
  return false;
}

export function shapeFromPicture(
  rgba: ArrayLike<number>, w: number, h: number, widthMm: number,
  mode: MaskMode = "alpha", threshold = 128, invert = false, detail: Detail = "normal",
): Shape {
  return shapeOfMask(mask(rgba, w, h, mode, threshold, invert), w, h, detail, widthMm);
}

export function shapeOfMask(m: ArrayLike<number>, w: number, h: number, detail: Detail, widthMm: number): Shape {
  const both = traceBoth(m, w, h, detail);
  return shape(both.map(([r]) => r), widthMm, null, both.map(([, raw]) => raw));
}

/** Make the cap triangles now (the trace worker), so placing / dragging is quick. */
export function prepare(s: Shape): Shape {
  for (const p of s.polygons) caps(p);
  return s;
}

const caps = (p: Polygon) => (p.caps ??= triangulate(p));

// ------------------------------------------------------------------------------ meshes
export interface Mesh {
  vertices: Float64Array; // xyz per corner, mm
  faces: Uint32Array; // 3 corner indices per triangle, counter-clockwise from outside
}

export function volume(m: Mesh): number {
  const v = m.vertices, f = m.faces;
  let s = 0;
  for (let i = 0; i < f.length; i += 3) {
    const a = f[i] * 3, b = f[i + 1] * 3, c = f[i + 2] * 3;
    const cx = v[b + 1] * v[c + 2] - v[b + 2] * v[c + 1];
    const cy = v[b + 2] * v[c] - v[b] * v[c + 2];
    const cz = v[b] * v[c + 1] - v[b + 1] * v[c];
    s += v[a] * cx + v[a + 1] * cy + v[a + 2] * cz;
  }
  return s / 6;
}

/** Cap triangles, counter-clockwise, meeting the walls exactly (see logo.py ``_triangulate``):
 * zero-area triangles are dropped and triangles spanning a corner point are split there. */
function triangulate(poly: Polygon): [number, number, number][] {
  const rings = [poly.outer, ...poly.holes];
  const pts = rings.flat();
  const holeStarts: number[] = [];
  let k = poly.outer.length;
  for (const h of poly.holes) {
    holeStarts.push(k);
    k += h.length;
  }
  const idx = earcut(pts.flat(), holeStarts.length ? holeStarts : null, 2);
  let scale = 0;
  for (const [x, y] of pts) scale = Math.max(scale, Math.abs(x), Math.abs(y));
  scale ||= 1;
  const eps = scale * 1e-9;
  const tris: [number, number, number][] = [];
  for (let i = 0; i < idx.length; i += 3) {
    const a = idx[i], b = idx[i + 1], c = idx[i + 2];
    const [ax, ay] = pts[a], [bx, by] = pts[b], [cx, cy] = pts[c];
    const area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    if (Math.abs(area2) <= eps * scale) continue; // zero area: nothing to cover
    tris.push(area2 < 0 ? [a, c, b] : [a, b, c]);
  }
  return splitAt(tris, pts, eps);
}

/** Split every triangle whose edge passes over a corner point (a fan from the opposite corner)
 * until none does; level / upright edges look corners up by their exact row / column. */
function splitAt(tris: [number, number, number][], xy: Point[], eps: number): [number, number, number][] {
  const rows = new Map<number, [number, number][]>(), cols = new Map<number, [number, number][]>();
  xy.forEach(([x, y], i) => {
    if (!rows.has(y)) rows.set(y, []);
    rows.get(y)!.push([x, i]);
    if (!cols.has(x)) cols.set(x, []);
    cols.get(x)!.push([y, i]);
  });
  const byKey = (p: [number, number], q: [number, number]) => p[0] - q[0] || p[1] - q[1];
  for (const m of [rows, cols]) for (const v of m.values()) v.sort(byKey);
  const byX = xy.map((_, i) => i).sort((a, b) => xy[a][0] - xy[b][0] || a - b);
  const xs = byX.map((i) => xy[i][0]);
  const firstAtLeast = (v: number) => {
    let lo = 0, hi = xs.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] < v) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  const seen = new Map<string, number[]>();
  const inside = (u: number, v: number): number[] => {
    const back = seen.get(`${v},${u}`);
    if (back) return [...back].reverse();
    const key = `${u},${v}`;
    let found = seen.get(key);
    if (!found) seen.set(key, (found = inside0(u, v)));
    return found;
  };
  const inside0 = (u: number, v: number): number[] => {
    const [ax, ay] = xy[u], [bx, by] = xy[v];
    if (ay === by || ax === bx) {
      const [line, k0, k1] = ay === by ? [rows.get(ay)!, ax, bx] : [cols.get(ax)!, ay, by];
      const lo = Math.min(k0, k1), hi = Math.max(k0, k1);
      const found = line.filter(([k]) => lo < k && k < hi);
      if (k0 > k1) found.sort((p, q) => byKey(q, p));
      return found.map(([, i]) => i);
    }
    const dx = bx - ax, dy = by - ay;
    const length = Math.sqrt(dx * dx + dy * dy);
    const hits: [number, number][] = [];
    const hiX = Math.max(ax, bx) + eps;
    for (let k = firstAtLeast(Math.min(ax, bx) - eps); k < xs.length && xs[k] <= hiX; k++) {
      const i = byX[k];
      const rx = xy[i][0] - ax, ry = xy[i][1] - ay;
      const t = (rx * dx + ry * dy) / (length * length);
      if (Math.abs(dx * ry - dy * rx) <= eps * length && t * length > eps && t * length < length - eps) hits.push([t, i]);
    }
    hits.sort((p, q) => p[0] - q[0]);
    return hits.map(([, i]) => i);
  };
  const todo = [...tris];
  const out: [number, number, number][] = [];
  while (todo.length) {
    const tri = todo.pop()!;
    let split = false;
    for (let e = 0; e < 3 && !split; e++) {
      const on = inside(tri[e], tri[(e + 1) % 3]);
      if (!on.length) continue;
      const seq = [tri[e], ...on, tri[(e + 1) % 3]];
      const w = tri[(e + 2) % 3];
      for (let i = 0; i < seq.length - 1; i++) todo.push([seq[i], seq[i + 1], w]);
      split = true;
    }
    if (!split) out.push(tri);
  }
  return out.reverse();
}

/** A closed solid: each polygon from z = ``bottom`` to ``top``. */
export function extrude(polygons: Polygon[], bottom: number, top: number): Mesh {
  const verts: number[] = [];
  const faces: number[] = [];
  for (const poly of polygons) {
    const rings = [poly.outer, ...poly.holes];
    const n = rings.reduce((t, r) => t + r.length, 0);
    const base = verts.length / 3;
    for (const z of [bottom, top]) for (const r of rings) for (const [x, y] of r) verts.push(x, y, z);
    for (const [a, b, c] of caps(poly)) {
      faces.push(base + n + a, base + n + b, base + n + c);
      faces.push(base + a, base + c, base + b);
    }
    let start = 0;
    for (const r of rings) {
      const k = r.length;
      for (let i = 0; i < k; i++) {
        const a = base + start + i, b = base + start + ((i + 1) % k);
        faces.push(a, b, b + n);
        faces.push(a, b + n, a + n);
      }
      start += k;
    }
  }
  return { vertices: Float64Array.from(verts), faces: Uint32Array.from(faces) };
}

export const logoMesh = (s: Shape, thickness: number, bottom = 0) => extrude(s.polygons, bottom, bottom + thickness);

function circle(cx: number, cy: number, r: number, segments: number, clockwise: boolean): Ring {
  const pts: Ring = [];
  for (let i = 0; i < segments; i++) pts.push([cx + r * Math.cos((2 * Math.PI * i) / segments), cy + r * Math.sin((2 * Math.PI * i) / segments)]);
  return clockwise ? pts.reverse() : pts;
}

export type PlateKind = "none" | "rectangle" | "rounded";

export function plateOutline(s: Shape, margin: number, kind: PlateKind = "rectangle", hole = false): [Ring, Ring | null] {
  let x0 = -s.width / 2 - margin;
  const x1 = s.width / 2 + margin;
  let y0 = -s.height / 2 - margin, y1 = s.height / 2 + margin;
  let ringHole: Ring | null = null;
  if (hole) {
    // at least MIN_WALL_MM of plate all round the hole, however thin the logo / margin
    const wall = Math.max(margin, MIN_WALL_MM);
    x0 -= 2 * HOLE_RADIUS_MM + wall;
    const grow = (2 * HOLE_RADIUS_MM + 2 * MIN_WALL_MM - (y1 - y0)) / 2;
    if (grow > 0) {
      y0 -= grow;
      y1 += grow;
    }
    ringHole = circle(x0 + wall + HOLE_RADIUS_MM, 0, HOLE_RADIUS_MM, HOLE_SEGMENTS, true);
  }
  let outer: Ring;
  if (kind === "rounded") {
    const r = Math.min(Math.max(margin, 1), (x1 - x0) / 2, (y1 - y0) / 2);
    outer = [];
    const corners: [number, number, number][] = [[x1 - r, y0 + r, -90], [x1 - r, y1 - r, 0], [x0 + r, y1 - r, 90], [x0 + r, y0 + r, 180]];
    for (const [cx, cy, start] of corners)
      for (let i = 0; i <= CORNER_SEGMENTS; i++) {
        const t = ((start + (90 * i) / CORNER_SEGMENTS) * Math.PI) / 180;
        outer.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
      }
  } else {
    outer = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  }
  return [outer, ringHole];
}

/** A stand-alone logo, alone or on a base plate; one mesh per part. */
export function badge(s: Shape, thickness: number, plate: PlateKind = "none", margin = 3, plateThickness = 2, hole = false): Mesh[] {
  if (plate === "none") return [logoMesh(s, thickness)];
  const [outer, ringHole] = plateOutline(s, margin, plate, hole);
  const plateMesh = extrude([{ outer, holes: ringHole ? [ringHole] : [] }], 0, plateThickness);
  const bottom = plateThickness - Math.min(SINK_MM, plateThickness / 2);
  return [plateMesh, logoMesh(s, thickness + (plateThickness - bottom), bottom)];
}

// ------------------------------------------------------------------------------ placing
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: Vec3): Vec3 => {
  const l = Math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
  return [a[0] / l, a[1] / l, a[2] / l];
};

/** Logo axes on a face: x (its right), y (its up), n (out of the face). */
export function frame(normal: Vec3, rotationDeg = 0): [Vec3, Vec3, Vec3] {
  const n = norm(normal);
  const x = Math.abs(n[2]) > 0.999 ? ([1, 0, 0] as Vec3) : norm(cross([0, 0, 1], n));
  const y = cross(n, x);
  const t = (rotationDeg * Math.PI) / 180;
  const c = Math.cos(t), s = Math.sin(t);
  return [[x[0] * c + y[0] * s, x[1] * c + y[1] * s, x[2] * c + y[2] * s], [y[0] * c - x[0] * s, y[1] * c - x[1] * s, y[2] * c - x[2] * s], n];
}

/** 4×4 row-major matrix taking logo coordinates (z = 0 = its bottom) onto the face. */
export function placement(point: Vec3, normal: Vec3, rotationDeg = 0, sink = SINK_MM): number[][] {
  const [x, y, n] = frame(normal, rotationDeg);
  return [
    [x[0], y[0], n[0], point[0] - n[0] * sink],
    [x[1], y[1], n[1], point[1] - n[1] * sink],
    [x[2], y[2], n[2], point[2] - n[2] * sink],
    [0, 0, 0, 1],
  ];
}

export function transform(mesh: Mesh, m: number[][]): Mesh {
  const v = mesh.vertices, out = new Float64Array(v.length);
  for (let i = 0; i < v.length; i += 3)
    for (let r = 0; r < 3; r++) out[i + r] = v[i] * m[r][0] + v[i + 1] * m[r][1] + v[i + 2] * m[r][2] + m[r][3];
  return { vertices: out, faces: mesh.faces.slice() };
}

export const placedLogo = (s: Shape, thickness: number, point: Vec3, normal: Vec3, rotationDeg = 0, sink = SINK_MM) =>
  transform(logoMesh(s, thickness + sink), placement(point, normal, rotationDeg, sink));

/** The outer rings' points on the face (for the "hangs over the edge" check). */
export function footprint(s: Shape, point: Vec3, normal: Vec3, rotationDeg = 0): Vec3[] {
  const [x, y] = frame(normal, rotationDeg);
  return s.polygons.flatMap((p) => p.outer.map(([u, v]) => [point[0] + u * x[0] + v * y[0], point[1] + u * x[1] + v * y[1], point[2] + u * x[2] + v * y[2]] as Vec3));
}

/** Non-indexed triangle corners (9 numbers per triangle), like ViewModel.positions. */
export function positions(m: Mesh): Float32Array {
  const out = new Float32Array(m.faces.length * 3);
  for (let i = 0; i < m.faces.length; i++) out.set(m.vertices.subarray(m.faces[i] * 3, m.faces[i] * 3 + 3), i * 3);
  return out;
}
