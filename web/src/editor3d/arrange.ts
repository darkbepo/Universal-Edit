// Arranging a model on the print bed (port of the desktop's umc/editor3d/arrange.py): 4×4
// row-major matrices for column vectors, the same numbers in both apps. No DOM.
import type { Vec3 } from "./camera";

export type Matrix = number[][];
export type Bed = [string, number, number, number];

export const BEDS: Bed[] = [
  ["Prusa MK4", 250, 210, 220],
  ["Prusa MINI", 180, 180, 180],
  ["Bambu Lab A1 / P1 / X1", 256, 256, 256],
  ["Bambu Lab A1 mini", 180, 180, 180],
  ["Creality Ender-3", 220, 220, 250],
  ["Creality K1", 220, 220, 250],
  ["Elegoo Neptune 4", 225, 225, 265],
  ["Anycubic Kobra 2", 220, 220, 250],
];
export const DEFAULT_BED = BEDS[2];
export const UNIT_FIXES: Record<string, number> = { inch: 25.4, cm: 10, to_inch: 1 / 25.4, to_cm: 0.1 };

export const identity = (): Matrix => [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => (r === c ? 1 : 0)));

/** a @ b (b first, then a). */
export function multiply(a: Matrix, b: Matrix): Matrix {
  return [0, 1, 2, 3].map((r) => [0, 1, 2, 3].map((c) => {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[r][k] * b[k][c];
    return s;
  }));
}

export function translate(x: number, y: number, z: number): Matrix {
  const m = identity();
  m[0][3] = x;
  m[1][3] = y;
  m[2][3] = z;
  return m;
}

const about = (m: Matrix, [cx, cy, cz]: Vec3) => multiply(translate(cx, cy, cz), multiply(m, translate(-cx, -cy, -cz)));

/** Rotation about an axis through ``centre``; multiples of 90° are exact. */
export function rotate(axis: "x" | "y" | "z", degrees: number, centre: Vec3 = [0, 0, 0]): Matrix {
  const q = ((degrees % 360) + 360) % 360;
  let c: number, s: number;
  if (q === 0 || q === 90 || q === 180 || q === 270) [c, s] = ({ 0: [1, 0], 90: [0, 1], 180: [-1, 0], 270: [0, -1] } as Record<number, [number, number]>)[q];
  else {
    const t = (degrees * Math.PI) / 180;
    c = Math.cos(t);
    s = Math.sin(t);
  }
  const m = identity();
  const [i, j] = { x: [1, 2], y: [2, 0], z: [0, 1] }[axis];
  m[i][i] = c;
  m[i][j] = -s;
  m[j][i] = s;
  m[j][j] = c;
  return about(m, centre);
}

export function scale(sx: number, sy: number, sz: number, centre: Vec3 = [0, 0, 0]): Matrix {
  const m = identity();
  m[0][0] = sx;
  m[1][1] = sy;
  m[2][2] = sz;
  return about(m, centre);
}

export function mirror(axis: "x" | "y" | "z", centre: Vec3 = [0, 0, 0]): Matrix {
  const f = { x: [-1, 1, 1], y: [1, -1, 1], z: [1, 1, -1] }[axis] as Vec3;
  return scale(...f, centre);
}

/** Rotation turning a face with this outward normal to face straight down. */
export function layFlat(normal: Vec3, centre: Vec3 = [0, 0, 0]): Matrix {
  const l = Math.hypot(...normal);
  const n = normal.map((x) => x / l) as Vec3;
  const c = -n[2]; // n · (0, 0, -1)
  if (c > 1 - 1e-12) return identity();
  if (c < -1 + 1e-12) return rotate("x", 180, centre);
  const v: Vec3 = [n[1] * -1 - n[2] * 0, n[2] * 0 - n[0] * -1, n[0] * 0 - n[1] * 0]; // n × (0,0,-1)
  const s = Math.hypot(...v);
  const [kx, ky, kz] = v.map((x) => x / s);
  const t = 1 - c;
  const r = [
    [c + kx * kx * t, kx * ky * t - kz * s, kx * kz * t + ky * s],
    [ky * kx * t + kz * s, c + ky * ky * t, ky * kz * t - kx * s],
    [kz * kx * t - ky * s, kz * ky * t + kx * s, c + kz * kz * t],
  ];
  const m = identity();
  for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) m[i][j] = r[i][j];
  return about(m, centre);
}

/** Would ``m`` leave everything where it is? */
export const isIdentity = (m: Matrix, eps = 1e-9) => m.every((row, r) => row.every((x, c) => Math.abs(x - (r === c ? 1 : 0)) <= eps));

export const determinant3 = (m: Matrix) =>
  m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);

/** A triangle soup (9 numbers per triangle) under ``m``; a mirroring matrix reverses each
 * triangle so it still faces out. */
export function applySoup(pos: ArrayLike<number>, m: Matrix): Float32Array {
  const out = new Float32Array(pos.length);
  const flipped = determinant3(m) < 0;
  for (let t = 0; t < pos.length; t += 9)
    for (let k = 0; k < 3; k++) {
      const src = t + k * 3, dst = t + (flipped ? [0, 2, 1][k] : k) * 3;
      const x = pos[src], y = pos[src + 1], z = pos[src + 2];
      for (let r = 0; r < 3; r++) out[dst + r] = m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3];
    }
  return out;
}

/** Indexed vertices (xyz flat) under ``m``, and the faces (reversed when it mirrors). */
export function applyIndexed(vertices: ArrayLike<number>, faces: ArrayLike<number>, m: Matrix): { vertices: Float64Array; faces: Uint32Array } {
  const v = new Float64Array(vertices.length);
  for (let i = 0; i < vertices.length; i += 3)
    for (let r = 0; r < 3; r++) v[i + r] = m[r][0] * vertices[i] + m[r][1] * vertices[i + 1] + m[r][2] * vertices[i + 2] + m[r][3];
  if (determinant3(m) >= 0) return { vertices: v, faces: faces as Uint32Array }; // shared: never changed
  const f = Uint32Array.from(faces);
  for (let i = 0; i < f.length; i += 3) [f[i + 1], f[i + 2]] = [f[i + 2], f[i + 1]];
  return { vertices: v, faces: f };
}

export const applyPoint = (m: Matrix, [x, y, z]: Vec3): Vec3 =>
  [0, 1, 2].map((r) => m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3]) as Vec3;

/** A surface normal under ``m`` (inverse transpose of the 3×3 part), unit length. */
export function applyNormal(m: Matrix, n: Vec3): Vec3 {
  const a = m;
  const det = determinant3(m);
  // inverse transpose = cofactor matrix / det
  const cof = [
    [a[1][1] * a[2][2] - a[1][2] * a[2][1], a[1][2] * a[2][0] - a[1][0] * a[2][2], a[1][0] * a[2][1] - a[1][1] * a[2][0]],
    [a[0][2] * a[2][1] - a[0][1] * a[2][2], a[0][0] * a[2][2] - a[0][2] * a[2][0], a[0][1] * a[2][0] - a[0][0] * a[2][1]],
    [a[0][1] * a[1][2] - a[0][2] * a[1][1], a[0][2] * a[1][0] - a[0][0] * a[1][2], a[0][0] * a[1][1] - a[0][1] * a[1][0]],
  ];
  const v = [0, 1, 2].map((r) => (cof[r][0] * n[0] + cof[r][1] * n[1] + cof[r][2] * n[2]) / det);
  const l = Math.hypot(...v);
  return v.map((x) => x / l) as Vec3;
}

export function bounds(pos: ArrayLike<number>): [Vec3, Vec3] {
  const lo: Vec3 = [Infinity, Infinity, Infinity], hi: Vec3 = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i++) {
    const a = i % 3;
    if (pos[i] < lo[a]) lo[a] = pos[i];
    if (pos[i] > hi[a]) hi[a] = pos[i];
  }
  return [lo, hi];
}

export function centreOf(pos: ArrayLike<number>): Vec3 {
  const [lo, hi] = bounds(pos);
  return [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
}

/** ``m`` followed by the move back onto the bed, keeping the X / Y centre where it was. */
export function settle(pos: ArrayLike<number>, m: Matrix): Matrix {
  const [cx, cy] = centreOf(pos);
  const [lo, hi] = bounds(applySoupPoints(pos, m));
  return multiply(translate(cx - (lo[0] + hi[0]) / 2, cy - (lo[1] + hi[1]) / 2, -lo[2]), m);
}

function applySoupPoints(pos: ArrayLike<number>, m: Matrix): Float64Array {
  const out = new Float64Array(pos.length);
  for (let i = 0; i < pos.length; i += 3)
    for (let r = 0; r < 3; r++) out[i + r] = m[r][0] * pos[i] + m[r][1] * pos[i + 1] + m[r][2] * pos[i + 2] + m[r][3];
  return out;
}

export function centred(pos: ArrayLike<number>): Matrix {
  const [lo, hi] = bounds(pos);
  return translate(-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]);
}

export const fits = (size: Vec3, bed: Vec3) => size.map((s, i) => Math.max(0, s - bed[i]));

/** Does the model (its bounds) fit on the bed (centred on X / Y = 0, from Z = 0 up)? */
export function fitNote(lo: Vec3, hi: Vec3, bed: Vec3): string {
  const size: Vec3 = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
  const over = fits(size, bed);
  const parts = ["wide", "deep", "tall"].flatMap((w, i) => (over[i] > 0 ? [`${over[i].toFixed(1)} mm too ${w}`] : []));
  if (parts.length) return `Too big for the bed: ${parts.join(", ")}.`;
  const [w, d] = bed;
  const off = Math.max(0, -w / 2 - lo[0], hi[0] - w / 2, -d / 2 - lo[1], hi[1] - d / 2);
  if (off > 0.05) return `Hangs ${off.toFixed(1)} mm off the edge of the bed — Centre moves it back.`;
  return "Fits the printer's bed.";
}
