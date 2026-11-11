// A 3D model as the viewer shows it (port of the desktop's umc/editor3d/mesh.py): one
// triangle soup in millimetres, X/Y centred on 0 and standing on Z = 0. No DOM, no three.js.

export interface ModelStats {
  triangles: number;
  parts: number;
  size: [number, number, number]; // mm
  watertight: boolean;
  volumeMm3: number | null; // only when watertight
}

export interface ViewModel extends ModelStats {
  name: string;
  /** Non-indexed triangle corners, xyz per corner (9 numbers per triangle), mm. */
  positions: Float32Array;
}

/** Move the model like "Centre on the print bed" (in place); returns its size. */
export function standOnBed(pos: Float32Array): [number, number, number] {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i++) {
    const a = i % 3;
    if (pos[i] < min[a]) min[a] = pos[i];
    if (pos[i] > max[a]) max[a] = pos[i];
  }
  const shift = [-(min[0] + max[0]) / 2, -(min[1] + max[1]) / 2, -min[2]];
  for (let i = 0; i < pos.length; i++) pos[i] += shift[i % 3];
  return [max[0] - min[0], max[1] - min[1], max[2] - min[2]];
}

/**
 * Welds corners that share a position (exact, like an STL's shared corners) and checks that
 * every edge belongs to exactly two triangles of one part. Volume = signed tetrahedra sum.
 * ``partStarts`` = the first triangle of each part.
 */
export function meshStats(pos: Float32Array, partStarts: number[] = [0]): ModelStats {
  const n = Math.floor(pos.length / 9);
  const ids = new Map<string, number>();
  const id = (k: number) => {
    const key = `${pos[k]},${pos[k + 1]},${pos[k + 2]}`;
    let v = ids.get(key);
    if (v === undefined) { v = ids.size; ids.set(key, v); }
    return v;
  };
  const starts = [...partStarts, n];
  let watertight = n > 0;
  let volume = 0;
  for (let p = 0; p + 1 < starts.length && watertight; p++) {
    const edges = new Map<number, number>();
    let partVolume = 0;
    for (let t = starts[p]; t < starts[p + 1]; t++) {
      const k = t * 9;
      const v = [id(k), id(k + 3), id(k + 6)];
      for (let e = 0; e < 3; e++) {
        const a = v[e], b = v[(e + 1) % 3];
        const key = a < b ? a * 0x200000 + b : b * 0x200000 + a; // < 2M distinct corners stay exact
        edges.set(key, (edges.get(key) ?? 0) + 1);
      }
      const [ax, ay, az, bx, by, bz, cx, cy, cz] = pos.subarray(k, k + 9);
      partVolume += (ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)) / 6;
    }
    for (const c of edges.values()) if (c !== 2) { watertight = false; break; }
    volume += Math.abs(partVolume);
  }
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i++) {
    const a = i % 3;
    min[a] = Math.min(min[a], pos[i]);
    max[a] = Math.max(max[a], pos[i]);
  }
  const size: [number, number, number] = n ? [max[0] - min[0], max[1] - min[1], max[2] - min[2]] : [0, 0, 0];
  return { triangles: n, parts: partStarts.length, size, watertight, volumeMm3: watertight ? volume : null };
}

export const fmtMm = (v: number) => (Math.abs(v) < 1000 ? `${v.toFixed(1)} mm` : `${(v / 1000).toFixed(2)} m`);

/** The info panel rows (label, value), same as the desktop's ``describe``. */
export function describe(m: ModelStats): [string, string][] {
  const rows: [string, string][] = [
    ["Width (X)", fmtMm(m.size[0])],
    ["Depth (Y)", fmtMm(m.size[1])],
    ["Height (Z)", fmtMm(m.size[2])],
    ["Triangles", m.triangles.toLocaleString("en-US")],
    ["Parts", String(m.parts)],
    ["Watertight", m.watertight ? "Yes" : "No — may not print cleanly"],
  ];
  if (m.volumeMm3 !== null) rows.push(["Volume", `${(m.volumeMm3 / 1000).toFixed(2)} cm³`]);
  return rows;
}

/** Bed grid on Z = 0 (port of the desktop's ``grid_lines``): spacing and half size, mm. */
export function gridSpec(size: [number, number, number]): { step: number; half: number } {
  const extent = Math.max(size[0], size[1], 1);
  const step = Math.max(10 ** Math.floor(Math.log10(extent / 2)), 1);
  const half = Math.max(Math.ceil((extent * 0.75) / step) * step, step * 5);
  return { step, half };
}
