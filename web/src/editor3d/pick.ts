// Clicking on a model: the ray under the pointer, the triangle it hits and the flat face around
// it (port of the desktop's umc/editor3d/pick.py). Meshes here are triangle soups like
// ViewModel.positions (9 numbers per triangle). No DOM.
import { FOV, type OrbitCamera, type Vec3 } from "./camera";

export const FLAT_DEGREES = 1;
export const FLAT_MM = 0.01;

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: Vec3, b: Vec3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec3, b: Vec3): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const corner = (pos: ArrayLike<number>, tri: number, k: number): Vec3 => [pos[tri * 9 + k * 3], pos[tri * 9 + k * 3 + 1], pos[tri * 9 + k * 3 + 2]];

/** (origin, unit direction) through pixel (x, y) of a w × h view, y down. */
export function ray(cam: OrbitCamera, x: number, y: number, w: number, h: number): [Vec3, Vec3] {
  const t = Math.tan((FOV * Math.PI) / 180 / 2);
  const nx = (2 * x / Math.max(w, 1) - 1) * t * (w / Math.max(h, 1));
  const ny = (1 - 2 * y / Math.max(h, 1)) * t;
  const f = cam.direction(), r = cam.right(), u = cam.up();
  const d: Vec3 = [-f[0] + r[0] * nx + u[0] * ny, -f[1] + r[1] * nx + u[1] * ny, -f[2] + r[2] * nx + u[2] * ny];
  const l = Math.sqrt(dot(d, d));
  return [cam.eye(), [d[0] / l, d[1] / l, d[2] / l]];
}

/** The nearest triangle the ray hits (either side) and where (Möller–Trumbore). */
export function hit(pos: ArrayLike<number>, origin: Vec3, dir: Vec3): [number, Vec3] | null {
  let best = Infinity, which = -1;
  const n = Math.floor(pos.length / 9);
  for (let i = 0; i < n; i++) {
    const t0 = corner(pos, i, 0);
    const e1 = sub(corner(pos, i, 1), t0), e2 = sub(corner(pos, i, 2), t0);
    const p = cross(dir, e2);
    const det = dot(e1, p);
    if (Math.abs(det) <= 1e-12) continue;
    const inv = 1 / det;
    const s = sub(origin, t0);
    const u = dot(s, p) * inv;
    if (u < 0) continue;
    const q = cross(s, e1);
    const v = dot(q, dir) * inv;
    if (v < 0 || u + v > 1) continue;
    const t = dot(e2, q) * inv;
    if (t > 1e-9 && t < best) {
      best = t;
      which = i;
    }
  }
  if (which < 0) return null;
  return [which, [origin[0] + dir[0] * best, origin[1] + dir[1] * best, origin[2] + dir[2] * best]];
}

export function normalOf(pos: ArrayLike<number>, tri: number): Vec3 {
  const a = corner(pos, tri, 0);
  const n = cross(sub(corner(pos, tri, 1), a), sub(corner(pos, tri, 2), a));
  const l = Math.sqrt(dot(n, n));
  return l > 0 ? [n[0] / l, n[1] / l, n[2] / l] : [0, 0, 0];
}

export interface Face {
  triangles: number[]; // the clicked one first
  normal: Vec3;
  point: Vec3;
}

export function faceArea(pos: ArrayLike<number>, face: Face): number {
  let s = 0;
  for (const t of face.triangles) {
    const a = corner(pos, t, 0);
    const c = cross(sub(corner(pos, t, 1), a), sub(corner(pos, t, 2), a));
    s += Math.sqrt(dot(c, c));
  }
  return s / 2;
}

/** The connected triangles around ``tri`` in its plane (corners matched by position). Only
 * triangles in the plane are looked at, so a click on a big model stays quick. */
export function flatFace(pos: ArrayLike<number>, tri: number): Face {
  const n = Math.floor(pos.length / 9);
  const n0 = normalOf(pos, tri), p0 = corner(pos, tri, 0);
  const cosLimit = Math.cos((FLAT_DEGREES * Math.PI) / 180);
  const cand: number[] = [];
  for (let j = 0; j < n; j++) {
    if (j !== tri) {
      if (dot(normalOf(pos, j), n0) < cosLimit) continue;
      let off = 0;
      for (let m = 0; m < 3; m++) off = Math.max(off, Math.abs(dot(sub(corner(pos, j, m), p0), n0)));
      if (off > FLAT_MM) continue;
    }
    cand.push(j);
  }
  const ids = new Map<string, number>();
  const welded = new Map<number, [number, number, number]>();
  for (const t of cand) {
    const w = [0, 1, 2].map((k) => {
      const key = `${pos[t * 9 + k * 3]},${pos[t * 9 + k * 3 + 1]},${pos[t * 9 + k * 3 + 2]}`;
      let id = ids.get(key);
      if (id === undefined) ids.set(key, (id = ids.size));
      return id;
    }) as [number, number, number];
    welded.set(t, w);
  }
  const edgeMap = new Map<string, number[]>();
  const ek = (a: number, b: number) => `${Math.min(a, b)},${Math.max(a, b)}`;
  for (const t of cand) {
    const [a, b, c] = welded.get(t)!;
    for (const [p, q] of [[a, b], [b, c], [c, a]]) {
      const key = ek(p, q);
      const list = edgeMap.get(key);
      if (list) list.push(t);
      else edgeMap.set(key, [t]);
    }
  }
  const seen = new Set([tri]);
  const order = [tri];
  for (let k = 0; k < order.length; k++) {
    const [a, b, c] = welded.get(order[k])!;
    for (const [p, q] of [[a, b], [b, c], [c, a]])
      for (const j of edgeMap.get(ek(p, q))!)
        if (!seen.has(j)) {
          seen.add(j);
          order.push(j);
        }
  }
  return { triangles: order, normal: n0, point: p0 };
}

/** Per point: does it lie on the face (projected onto its plane)? */
export function onFace(points: Vec3[], pos: ArrayLike<number>, face: Face): boolean[] {
  const n = face.normal;
  let a = cross(n, Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]);
  const l = Math.sqrt(dot(a, a));
  a = [a[0] / l, a[1] / l, a[2] / l];
  const b = cross(n, a);
  const p2 = points.map((p) => [dot(p, a), dot(p, b)]);
  const out = points.map(() => false);
  const eps = 1e-6;
  for (const t of face.triangles) {
    const [x0, x1, x2] = [0, 1, 2].map((k) => {
      const c = corner(pos, t, k);
      return [dot(c, a), dot(c, b)];
    });
    const d = (x1[1] - x2[1]) * (x0[0] - x2[0]) + (x2[0] - x1[0]) * (x0[1] - x2[1]);
    if (Math.abs(d) < 1e-15) continue;
    p2.forEach(([px, py], i) => {
      if (out[i]) return;
      const l1 = ((x1[1] - x2[1]) * (px - x2[0]) + (x2[0] - x1[0]) * (py - x2[1])) / d;
      const l2 = ((x2[1] - x0[1]) * (px - x2[0]) + (x0[0] - x2[0]) * (py - x2[1])) / d;
      if (l1 >= -eps && l2 >= -eps && 1 - l1 - l2 >= -eps) out[i] = true;
    });
  }
  return out;
}

/** How many of the logo's outline points lie off the face. */
export const overhang = (points: Vec3[], pos: ArrayLike<number>, face: Face) => onFace(points, pos, face).filter((x) => !x).length;

// ----------------------------------------------------------------------------- curved surfaces
export const AREA_COS = 0.5; // triangles facing within 60° of the click count towards the direction
export const CELL_TRIS = 4; // the surface grid aims at this many triangles per cell
export const MAX_CELLS = 512; // ... with at most this many cells along a side
export const SURFACE_MARGIN_MM = 1; // triangles this far past the logo's reach are kept too

/** The logo's "out" direction on a curved model: the area-weighted normal of the triangles
 *  with a corner within ``radius`` of ``point`` that face roughly the clicked way (within
 *  60°). A flat face (or big triangles with no corner that close) gives ``normal`` back. */
export function areaNormal(pos: ArrayLike<number>, point: Vec3, normal: Vec3, radius: number): Vec3 {
  const l0 = Math.sqrt(dot(normal, normal));
  const n0: Vec3 = [normal[0] / l0, normal[1] / l0, normal[2] / l0];
  const r2 = radius * radius;
  let tx = 0, ty = 0, tz = 0;
  const n = Math.floor(pos.length / 9);
  for (let i = 0; i < n; i++) {
    let close = false;
    for (let k = 0; k < 3 && !close; k++) {
      const dx = pos[i * 9 + k * 3] - point[0], dy = pos[i * 9 + k * 3 + 1] - point[1], dz = pos[i * 9 + k * 3 + 2] - point[2];
      close = dx * dx + dy * dy + dz * dz <= r2;
    }
    if (!close) continue;
    const t0 = corner(pos, i, 0);
    const c = cross(sub(corner(pos, i, 1), t0), sub(corner(pos, i, 2), t0));
    const length = Math.sqrt(dot(c, c));
    if (length > 0 && dot(c, n0) >= AREA_COS * length) {
      tx += c[0];
      ty += c[1];
      tz += c[2];
    }
  }
  const size = Math.sqrt(tx * tx + ty * ty + tz * tz);
  return size > 1e-12 ? [tx / size, ty / size, tz / size] : [n0[0], n0[1], n0[2]];
}

export interface Samples {
  h: Float64Array; // height along n (0 where missed)
  normals: Float64Array; // unit world normal, 3 per point
  hit: Uint8Array;
}

/** The model's triangles under a logo, seen along −n from the logo's plane: (u, v) in the
 *  logo's x / y, h = height along n. Every projection ray of a logo runs along −n, so where
 *  it meets the model is the triangle a point lies in, in 2D — a uniform grid. */
export class Surface {
  constructor(
    readonly origin: Vec3,
    readonly axes: [Vec3, Vec3, Vec3], // x, y, n
    readonly tris: Float64Array, // 9 per triangle: (u, v, h) corners, front-facing only
    readonly normals: Float64Array, // 3 per triangle, unit, world
    readonly lo: [number, number],
    readonly cell: number,
    readonly size: number,
    readonly starts: Int32Array, // size² + 1 offsets into items
    readonly items: Int32Array,
  ) {}

  get count(): number {
    return this.tris.length / 9;
  }

  /** (h, unit world normal, hit) per (u, v) point (2 numbers each): of the front-facing
   *  triangles the point lies in, the one nearest the logo's plane (|h| smallest). */
  sample(uv: ArrayLike<number>): Samples {
    const p = Math.floor(uv.length / 2);
    const out: Samples = { h: new Float64Array(p), normals: new Float64Array(p * 3), hit: new Uint8Array(p) };
    if (!p || !this.count) return out;
    const t = this.tris, eps = 1e-9;
    for (let i = 0; i < p; i++) {
      const qx = uv[i * 2], qy = uv[i * 2 + 1];
      const gx = Math.floor((qx - this.lo[0]) / this.cell), gy = Math.floor((qy - this.lo[1]) / this.cell);
      if (gx < 0 || gy < 0 || gx >= this.size || gy >= this.size) continue;
      const cid = gy * this.size + gx;
      let best = Infinity, which = -1, bestH = 0;
      for (let j = this.starts[cid]; j < this.starts[cid + 1]; j++) {
        const k = this.items[j] * 9;
        const x0 = t[k], y0 = t[k + 1], x1 = t[k + 3], y1 = t[k + 4], x2 = t[k + 6], y2 = t[k + 7];
        const d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
        const l0 = ((y1 - y2) * (qx - x2) + (x2 - x1) * (qy - y2)) / d;
        const l1 = ((y2 - y0) * (qx - x2) + (x0 - x2) * (qy - y2)) / d;
        const l2 = 1 - l0 - l1;
        if (!(l0 >= -eps && l1 >= -eps && l2 >= -eps)) continue;
        const hh = l0 * t[k + 2] + l1 * t[k + 5] + l2 * t[k + 8];
        if (Math.abs(hh) < best) {
          best = Math.abs(hh);
          which = this.items[j];
          bestH = hh;
        }
      }
      if (which < 0) continue;
      out.h[i] = bestH;
      out.normals.set(this.normals.subarray(which * 3, which * 3 + 3), i * 3);
      out.hit[i] = 1;
    }
    return out;
  }
}

/** The ``Surface`` around ``origin``: triangles whose (u, v) box comes within ``reach`` (+
 *  SURFACE_MARGIN_MM) of it, within 2 · reach along n, and that face the logo. One pass over
 *  all triangles, so a big model costs one sweep per placement, not per ray. */
export function surface(pos: ArrayLike<number>, origin: Vec3, axes: [Vec3, Vec3, Vec3], reach: number): Surface {
  const r = reach + SURFACE_MARGIN_MM;
  const depth = 2 * reach + SURFACE_MARGIN_MM;
  const limits = [r, r, depth];
  const offs = axes.map((a) => dot(origin, a));
  const n = Math.floor(pos.length / 9);
  const kept: number[] = [];
  const nrm: number[] = [];
  const local = new Float64Array(9);
  const front = 1e-12 * Math.max(r * r, 1e-9);
  for (let i = 0; i < n; i++) {
    for (let k = 0; k < 3; k++) {
      const vx = pos[i * 9 + k * 3], vy = pos[i * 9 + k * 3 + 1], vz = pos[i * 9 + k * 3 + 2];
      for (let a = 0; a < 3; a++) local[k * 3 + a] = vx * axes[a][0] + vy * axes[a][1] + vz * axes[a][2] - offs[a];
    }
    let out = false;
    for (let a = 0; a < 3 && !out; a++) {
      const lim = limits[a], c0 = local[a], c1 = local[3 + a], c2 = local[6 + a];
      out = (c0 < -lim && c1 < -lim && c2 < -lim) || (c0 > lim && c1 > lim && c2 > lim);
    }
    if (out) continue;
    const area2 = (local[3] - local[0]) * (local[7] - local[1]) - (local[4] - local[1]) * (local[6] - local[0]);
    if (!(area2 > front)) continue;
    for (let j = 0; j < 9; j++) kept.push(local[j]);
    const nn = normalOf(pos, i);
    nrm.push(nn[0], nn[1], nn[2]);
  }
  const count = kept.length / 9;
  const size = Math.min(MAX_CELLS, Math.max(1, Math.ceil(Math.sqrt(count / CELL_TRIS))));
  const lo: [number, number] = [-r, -r];
  const cell = (2 * r) / size;
  const clampCell = (v: number) => Math.min(size - 1, Math.max(0, Math.floor((v - -r) / cell)));
  const box = new Int32Array(count * 4);
  const counts = new Int32Array(size * size + 1);
  for (let i = 0; i < count; i++) {
    const k = i * 9;
    const gx0 = clampCell(Math.min(kept[k], kept[k + 3], kept[k + 6])), gx1 = clampCell(Math.max(kept[k], kept[k + 3], kept[k + 6]));
    const gy0 = clampCell(Math.min(kept[k + 1], kept[k + 4], kept[k + 7])), gy1 = clampCell(Math.max(kept[k + 1], kept[k + 4], kept[k + 7]));
    box.set([gx0, gx1, gy0, gy1], i * 4);
    for (let cy = gy0; cy <= gy1; cy++) for (let cx = gx0; cx <= gx1; cx++) counts[cy * size + cx + 1]++;
  }
  for (let c = 0; c < size * size; c++) counts[c + 1] += counts[c];
  const starts = Int32Array.from(counts);
  const fill = Int32Array.from(counts);
  const items = new Int32Array(starts[size * size]);
  for (let i = 0; i < count; i++) {
    const [gx0, gx1, gy0, gy1] = box.subarray(i * 4, i * 4 + 4);
    for (let cy = gy0; cy <= gy1; cy++) for (let cx = gx0; cx <= gx1; cx++) items[fill[cy * size + cx]++] = i;
  }
  return new Surface(origin, axes, Float64Array.from(kept), Float64Array.from(nrm), lo, cell, size, starts, items);
}
