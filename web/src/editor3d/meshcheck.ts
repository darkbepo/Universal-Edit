// Checking and repairing a model for printing (port of the desktop's umc/editor3d/meshcheck.py,
// same steps in the same order; shared cases tests/fixtures/print3d_cases.json). No DOM.
import earcut from "earcut";

export const OVERHANG_DEG = 45;
export const BED_EPS = 0.05;
export const TINY_MM3 = 1;
export const TINY_SHARE = 0.01;
export const MAX_HOLE_EDGES = 5000;

type Tri = [number, number, number];

export interface Indexed {
  vertices: Float64Array; // xyz per vertex
  faces: Tri[];
}

/** Corners at exactly the same position become one vertex, numbered in first-appearance order.
 * ``pos`` = a triangle soup (9 numbers per triangle), as ViewModel.positions. */
export function weld(pos: ArrayLike<number>): Indexed {
  const ids = new Map<string, number>();
  const v: number[] = [];
  const faces: Tri[] = [];
  const n = Math.floor(pos.length / 9);
  for (let t = 0; t < n; t++) {
    const tri = [0, 1, 2].map((k) => {
      const x = pos[t * 9 + k * 3] + 0, y = pos[t * 9 + k * 3 + 1] + 0, z = pos[t * 9 + k * 3 + 2] + 0;
      const key = `${x},${y},${z}`;
      let id = ids.get(key);
      if (id === undefined) {
        ids.set(key, (id = ids.size));
        v.push(x, y, z);
      }
      return id;
    }) as Tri;
    faces.push(tri);
  }
  return { vertices: Float64Array.from(v), faces };
}

/** Back to a triangle soup (Float32, like the viewer's models). */
export function soup(m: Indexed): Float32Array {
  const out = new Float32Array(m.faces.length * 9);
  m.faces.forEach((t, i) => t.forEach((x, k) => out.set(m.vertices.subarray(x * 3, x * 3 + 3), i * 9 + k * 3)));
  return out;
}

const P = (v: Float64Array, i: number): [number, number, number] => [v[i * 3], v[i * 3 + 1], v[i * 3 + 2]];
const cross = (a: number[], b: number[]) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const sub = (a: number[], b: number[]) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];

function area2(v: Float64Array, [a, b, c]: Tri): number {
  const A = P(v, a);
  return Math.hypot(...cross(sub(P(v, b), A), sub(P(v, c), A)));
}

function scaleOf(v: Float64Array): number {
  let s = 0;
  for (const x of v) s = Math.max(s, Math.abs(x));
  return v.length ? s : 1;
}

function clean(v: Float64Array, f: Tri[]): { keep: number[]; zero: number; dup: number } {
  const eps = (scaleOf(v) * 1e-9) ** 2;
  const seen = new Set<string>();
  const keep: number[] = [];
  let zero = 0, dup = 0;
  f.forEach((t, i) => {
    const [a, b, c] = t;
    if (area2(v, t) <= eps || a === b || a === c || b === c) {
      zero++;
      return;
    }
    const key = [a, b, c].sort((x, y) => x - y).join(",");
    if (seen.has(key)) {
      dup++;
      return;
    }
    seen.add(key);
    keep.push(i);
  });
  return { keep, zero, dup };
}

const ekey = (p: number, q: number) => (p < q ? `${p},${q}` : `${q},${p}`);

function edgesOf(f: Tri[]): Map<string, number[]> {
  const out = new Map<string, number[]>();
  f.forEach(([a, b, c], i) => {
    for (const [p, q] of [[a, b], [b, c], [c, a]]) {
      const k = ekey(p, q);
      const list = out.get(k);
      if (list) list.push(i);
      else out.set(k, [i]);
    }
  });
  return out;
}

function partsOf(n: number, edges: Map<string, number[]>): number[] {
  const parent = Array.from({ length: n }, (_, i) => i);
  const find = (x: number) => {
    while (parent[x] !== x) {
      parent[x] = parent[parent[x]];
      x = parent[x];
    }
    return x;
  };
  for (const tris of edges.values()) {
    let r0 = find(tris[0]);
    for (const t of tris.slice(1)) {
      let r = find(t);
      if (r !== r0) {
        if (r < r0) [r, r0] = [r0, r];
        parent[r] = r0;
      }
    }
  }
  const label = new Map<number, number>();
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const r = find(i);
    if (!label.has(r)) label.set(r, label.size);
    out.push(label.get(r)!);
  }
  return out;
}

function volumesOf(v: Float64Array, f: Tri[], part: number[], parts: number): number[] {
  const vol = new Array<number>(parts).fill(0);
  f.forEach(([a, b, c], i) => {
    const A = P(v, a), B = P(v, b), C = P(v, c);
    const bc = cross(B, C);
    vol[part[i]] += (A[0] * bc[0] + A[1] * bc[1] + A[2] * bc[2]) / 6;
  });
  return vol;
}

export interface Report {
  triangles: number;
  zero_area: number;
  duplicates: number;
  open_edges: number;
  shared_edges: number;
  flipped_edges: number;
  parts: number;
  inside_out: number;
  tiny_parts: number;
  holes: number;
  volume: number;
  area: number;
  overhang_faces: number[];
  overhang_area: number;
}

export const isClosed = (r: Report) => r.open_edges === 0 && r.shared_edges === 0;
export const hasProblems = (r: Report) =>
  Boolean(r.zero_area || r.duplicates || r.open_edges || r.shared_edges || r.flipped_edges || r.inside_out || r.tiny_parts);

export function check(m: Indexed, overhangDeg = OVERHANG_DEG): Report {
  const { vertices: v, faces: f } = m;
  const r: Report = {
    triangles: f.length, zero_area: 0, duplicates: 0, open_edges: 0, shared_edges: 0, flipped_edges: 0, parts: 0,
    inside_out: 0, tiny_parts: 0, holes: 0, volume: 0, area: 0, overhang_faces: [], overhang_area: 0,
  };
  const { keep, zero, dup } = clean(v, f);
  r.zero_area = zero;
  r.duplicates = dup;
  const good = keep.map((i) => f[i]);
  const edges = edgesOf(good);
  const directed = new Map<string, number>();
  for (const [a, b, c] of good) for (const [p, q] of [[a, b], [b, c], [c, a]]) directed.set(`${p},${q}`, (directed.get(`${p},${q}`) ?? 0) + 1);
  for (const [k, tris] of edges) {
    if (tris.length === 1) r.open_edges++;
    else if (tris.length > 2) r.shared_edges++;
    else if ((directed.get(k) ?? 0) !== 1) r.flipped_edges++;
  }
  const part = partsOf(good.length, edges);
  r.parts = part.length ? Math.max(...part) + 1 : 0;
  const vols = volumesOf(v, good, part, r.parts);
  const openPart = new Array<boolean>(r.parts).fill(false);
  for (const tris of edges.values()) if (tris.length !== 2) for (const t of tris) openPart[part[t]] = true;
  const biggest = vols.reduce((m2, x) => Math.max(m2, Math.abs(x)), 0);
  for (let p = 0; p < r.parts; p++) {
    if (!openPart[p] && vols[p] < 0) r.inside_out++;
    if (Math.abs(vols[p]) < TINY_MM3 && Math.abs(vols[p]) < TINY_SHARE * biggest) r.tiny_parts++;
  }
  r.holes = holeLoops(good, edges).loops.length;
  r.volume = isClosed(r) && !r.flipped_edges ? Math.abs(vols.reduce((s, x) => s + x, 0)) : 0;
  if (good.length) {
    r.area = good.reduce((s, t) => s + area2(v, t), 0) / 2;
    [r.overhang_faces, r.overhang_area] = overhangs(v, f, keep, overhangDeg);
  }
  return r;
}

function overhangs(v: Float64Array, f: Tri[], keep: number[], overhangDeg: number): [number[], number] {
  let floor = Infinity;
  for (let i = 2; i < v.length; i += 3) floor = Math.min(floor, v[i]);
  const limit = -Math.sin((overhangDeg * Math.PI) / 180);
  const hit: number[] = [];
  let total = 0;
  for (const i of keep) {
    const [a, b, c] = f[i];
    const A = P(v, a), B = P(v, b), C = P(v, c);
    const n = cross(sub(B, A), sub(C, A));
    const length = Math.hypot(...n);
    const nz = n[2] / (length > 0 ? length : 1);
    const onBed = A[2] <= floor + BED_EPS && B[2] <= floor + BED_EPS && C[2] <= floor + BED_EPS;
    if (nz < limit && !onBed) {
      hit.push(i);
      total += length;
    }
  }
  return [hit, total / 2];
}

function holeLoops(f: Tri[], edges: Map<string, number[]>): { loops: number[][]; stray: number } {
  const nxt = new Map<number, number[]>();
  const order: [number, number][] = [];
  for (const [a, b, c] of f)
    for (const [p, q] of [[a, b], [b, c], [c, a]])
      if (edges.get(ekey(p, q))!.length === 1) {
        if (!nxt.has(p)) nxt.set(p, []);
        nxt.get(p)!.push(q);
        order.push([p, q]);
      }
  const used = new Set<string>();
  const loops: number[][] = [];
  let stray = 0;
  for (const [s0, s1] of order) {
    if (used.has(`${s0},${s1}`)) continue;
    const loop = [s0];
    used.add(`${s0},${s1}`);
    let cur = s1;
    let ok = true;
    while (cur !== s0) {
      if (loop.includes(cur)) {
        ok = false;
        break;
      }
      loop.push(cur);
      const step = (nxt.get(cur) ?? []).find((q) => !used.has(`${cur},${q}`));
      if (step === undefined) {
        ok = false;
        break;
      }
      used.add(`${cur},${step}`);
      cur = step;
    }
    if (ok && loop.length >= 3) loops.push(loop);
    else stray += loop.length;
  }
  return { loops, stray };
}

export interface Repaired extends Indexed {
  zero_area: number;
  duplicates: number;
  turned: number;
  holes_filled: number;
  holes_left: number;
  tiny_removed: number;
}

export const repairChanged = (x: Repaired) => Boolean(x.zero_area || x.duplicates || x.turned || x.holes_filled || x.tiny_removed);

export function repair(m: Indexed): Repaired {
  const { vertices: v, faces: f } = m;
  const { keep, zero, dup } = clean(v, f);
  let good: Tri[] = keep.map((i) => [...f[i]] as Tri);
  const out: Repaired = { vertices: v, faces: f, zero_area: zero, duplicates: dup, turned: 0, holes_filled: 0, holes_left: 0, tiny_removed: 0 };
  const original = good.length;
  // 1. every part facing one way round
  let edges = edgesOf(good);
  const flip = orient(good, edges);
  good = good.map(([a, b, c], i) => (flip[i] ? [a, c, b] : [a, b, c]));
  // 2. fill holes flat
  for (const loop of holeLoops(good, edges).loops) {
    if (loop.length > MAX_HOLE_EDGES) {
      out.holes_left++;
      continue;
    }
    good.push(...cap(v, loop));
    out.holes_filled++;
  }
  // 3. closed parts facing inwards: turn them all
  edges = edgesOf(good);
  let part = partsOf(good.length, edges);
  let parts = part.length ? Math.max(...part) + 1 : 0;
  const openPart = new Array<boolean>(parts).fill(false);
  for (const tris of edges.values()) if (tris.length !== 2) for (const t of tris) openPart[part[t]] = true;
  let vols = volumesOf(v, good, part, parts);
  const inward = vols.map((x, p) => !openPart[p] && x < 0);
  good = good.map(([a, b, c], i) => (inward[part[i]] ? [a, c, b] : [a, b, c]));
  for (let i = 0; i < original; i++) if (flip[i] !== inward[part[i]]) out.turned++;
  // 4. tiny loose bits
  edges = edgesOf(good);
  part = partsOf(good.length, edges);
  parts = part.length ? Math.max(...part) + 1 : 0;
  vols = volumesOf(v, good, part, parts);
  const biggest = vols.reduce((s, x) => Math.max(s, Math.abs(x)), 0);
  const tiny = vols.map((x) => Math.abs(x) < TINY_MM3 && Math.abs(x) < TINY_SHARE * biggest);
  out.tiny_removed = tiny.filter(Boolean).length;
  good = good.filter((_, i) => !tiny[part[i]]);
  // 5. only the vertices still used, in first-use order
  const remap = new Map<number, number>();
  for (const t of good) for (const x of t) if (!remap.has(x)) remap.set(x, remap.size);
  const nv = new Float64Array(remap.size * 3);
  for (const [old, i] of remap) nv.set(v.subarray(old * 3, old * 3 + 3), i * 3);
  out.vertices = nv;
  out.faces = good.map((t) => t.map((x) => remap.get(x)!) as Tri);
  return out;
}

function orient(f: Tri[], edges: Map<string, number[]>): boolean[] {
  const flip: (boolean | null)[] = new Array(f.length).fill(null);
  for (let seed = 0; seed < f.length; seed++) {
    if (flip[seed] !== null) continue;
    flip[seed] = false;
    const todo = [seed];
    while (todo.length) {
      const t = todo.pop()!;
      let [a, b, c] = f[t];
      if (flip[t]) [b, c] = [c, b];
      for (const [p, q] of [[a, b], [b, c], [c, a]]) {
        const tris = edges.get(ekey(p, q))!;
        if (tris.length !== 2) continue;
        const g = tris[0] === t ? tris[1] : tris[0];
        if (flip[g] !== null) continue;
        const [x, y, z] = f[g];
        flip[g] = (x === p && y === q) || (y === p && z === q) || (z === p && x === q);
        todo.push(g);
      }
    }
  }
  return flip.map(Boolean);
}

function cap(v: Float64Array, loop: number[]): Tri[] {
  const ring = [...loop].reverse();
  const p = ring.map((i) => P(v, i));
  let nx = 0, ny = 0, nz = 0;
  const k = ring.length;
  for (let i = 0; i < k; i++) {
    const [x1, y1, z1] = p[i], [x2, y2, z2] = p[(i + 1) % k];
    nx += (y1 - y2) * (z1 + z2);
    ny += (z1 - z2) * (x1 + x2);
    nz += (x1 - x2) * (y1 + y2);
  }
  const length = Math.hypot(nx, ny, nz);
  if (length === 0) return [];
  const n = [nx / length, ny / length, nz / length];
  let u = cross(n, Math.abs(n[0]) < 0.9 ? [1, 0, 0] : [0, 1, 0]);
  const lu = Math.hypot(...u);
  u = u.map((x) => x / lu);
  const w = cross(n, u);
  const xy = p.map((q) => [q[0] * u[0] + q[1] * u[1] + q[2] * u[2], q[0] * w[0] + q[1] * w[1] + q[2] * w[2]]);
  const idx = earcut(xy.flat(), null, 2);
  const out: Tri[] = [];
  for (let i = 0; i < idx.length; i += 3) {
    let a = idx[i], b = idx[i + 1], c = idx[i + 2];
    const [ax, ay] = xy[a], [bx, by] = xy[b], [cx, cy] = xy[c];
    if ((bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0) [b, c] = [c, b];
    out.push([ring[a], ring[b], ring[c]]);
  }
  return out;
}

const n_ = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** The check in plain words (see meshcheck.py ``notes``). */
export function notes(r: Report): string[] {
  const out: string[] = [];
  if (r.open_edges)
    out.push(`${r.holes || "Some"} hole${r.holes !== 1 ? "s" : ""} in the surface (${n_(r.open_edges, "open edge")}) — a slicer may print it hollow or skip parts.`);
  if (r.shared_edges) out.push(`${n_(r.shared_edges, "edge")} shared by more than two triangles — the surface folds into itself there.`);
  if (r.flipped_edges) out.push(`Some triangles face the wrong way (${n_(r.flipped_edges, "edge")}).`);
  if (r.inside_out) out.push(`${r.inside_out} part${r.inside_out !== 1 ? "s are" : " is"} inside out.`);
  if (r.zero_area) out.push(`${n_(r.zero_area, "triangle")} with no area.`);
  if (r.duplicates) out.push(`${n_(r.duplicates, "triangle")} appear${r.duplicates === 1 ? "s" : ""} twice.`);
  if (r.tiny_parts) out.push(`${r.tiny_parts} tiny loose bit${r.tiny_parts !== 1 ? "s" : ""} (too small to print).`);
  if (!out.length) out.push("No problems found — ready for the slicer.");
  return out;
}

/** What Repair did; with the check made afterwards, also whether anything is left. */
export function repairedNote(x: Repaired, after: Report | null = null): string {
  const left = after !== null && hasProblems(after);
  if (!repairChanged(x)) return left ? "Nothing could be repaired automatically — see the check for what's left." : "Nothing needed repairing.";
  const done: string[] = [];
  if (x.holes_filled) done.push(`closed ${x.holes_filled} hole${x.holes_filled !== 1 ? "s" : ""}`);
  if (x.turned) done.push(`turned ${x.turned} triangle${x.turned !== 1 ? "s" : ""} to face outwards`);
  if (x.zero_area || x.duplicates) done.push(`removed ${n_(x.zero_area + x.duplicates, "empty or repeated triangle")}`);
  if (x.tiny_removed) done.push(`removed ${x.tiny_removed} tiny loose bit${x.tiny_removed !== 1 ? "s" : ""}`);
  let note = `Repaired: ${done.join(", ")}.`;
  if (x.holes_left) note += ` ${x.holes_left} very large opening${x.holes_left !== 1 ? "s were" : " was"} left as is.`;
  if (left) note += " Some problems couldn't be repaired here — see the check.";
  return note;
}
