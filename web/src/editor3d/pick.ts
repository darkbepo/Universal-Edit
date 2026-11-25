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
