// What the 3D viewer shows and saves once logos are added (port of the desktop's
// umc/editor3d/scene.py): the opened model plus logos, immutable, with snapshot undo.
// No DOM, no three.js.
import { applyIndexed, applyNormal, applyPoint, applySoup, bounds, determinant3, type Matrix } from "./arrange";
import type { Vec3 } from "./camera";
import {
  badge, frame, LogoError, plateOutline, shapeArea, placedLogo, placement, positions, shapeFromPicture, transform, footprint, SINK_MM, volume,
  type Detail, type MaskMode, type Mesh, type PlateKind, type Shape,
} from "./logo";
import type { ModelStats, ViewModel } from "./mesh";
import { overhang, type Face } from "./pick";

export const BADGE_GAP_MM = 10;
export const SAVE_EXTS = ["3mf", "stl", "obj", "ply", "glb"] as const;
export type SaveExt = (typeof SAVE_EXTS)[number];

export interface Picture {
  name: string;
  rgba: Uint8ClampedArray | Uint8Array; // at most MAX_SIDE on its longer side
  w: number;
  h: number;
}

export interface LogoSettings {
  mode: MaskMode;
  threshold: number;
  invert: boolean;
  detail: Detail;
  width: number; // mm
  thickness: number;
  where: "face" | "badge";
  rotation: number; // degrees
  sink: number;
  plate: PlateKind;
  margin: number;
  plate_thickness: number;
  hole: boolean;
}

export const DEFAULT_SETTINGS: LogoSettings = {
  mode: "alpha", threshold: 128, invert: false, detail: "normal", width: 40, thickness: 2, where: "face",
  rotation: 0, sink: SINK_MM, plate: "none", margin: 3, plate_thickness: 2, hole: false,
};

export const sameSettings = (a: LogoSettings, b: LogoSettings) =>
  (Object.keys(a) as (keyof LogoSettings)[]).every((k) => a[k] === b[k]);

export interface Logo {
  picture: Picture;
  settings: LogoSettings;
  point: Vec3 | null;
  normal: Vec3 | null;
  meshes: Mesh[];
  width: number;
  height: number;
  dropped: number;
  overhang: number;
  overlap: number; // mm³ inside the model / plate (sunk in), so the volume counts it once
}

export const shapeOf = (pic: Picture, s: LogoSettings) =>
  shapeFromPicture(pic.rgba, pic.w, pic.h, s.width, s.mode, s.threshold, s.invert, s.detail);

/** Trace (unless ``shp`` is given) + extrude + place. Throws LogoError in plain words. */
export function buildLogo(
  pic: Picture, s: LogoSettings, point: Vec3 | null, normal: Vec3 | null,
  base: ViewModel | null, faceTris: number[] | null, shp?: Shape,
): Logo {
  shp ??= shapeOf(pic, s);
  let over = 0;
  let sunk = 0;
  let meshes: Mesh[];
  if (s.where === "face") {
    sunk = s.sink;
    if (!point || !normal) throw new LogoError("Click a flat face of the model to place the logo.");
    meshes = [placedLogo(shp, s.thickness, point, normal, s.rotation, s.sink)];
    if (base && faceTris?.length) {
      const face: Face = { triangles: faceTris, normal, point };
      over = overhang(footprint(shp, point, normal, s.rotation), base.positions, face);
    }
  } else {
    const parts = badge(shp, s.thickness, s.plate, s.margin, s.plate_thickness, s.hole);
    if (s.plate !== "none") sunk = Math.min(SINK_MM, s.plate_thickness / 2);
    let x = 0;
    if (base) {
      let hi = -Infinity;
      for (let i = 0; i < base.positions.length; i += 3) hi = Math.max(hi, base.positions[i]);
      let left = shp.width / 2; // how far the badge reaches left of its centre
      if (s.plate !== "none") left = -Math.min(...plateOutline(shp, s.margin, s.plate, s.hole)[0].map(([px]) => px));
      x = hi + BADGE_GAP_MM + left;
    }
    const m = placement([x, 0, 0], [0, 0, 1], s.rotation, 0);
    meshes = parts.map((p) => transform(p, m));
    point = normal = null;
  }
  return { picture: pic, settings: s, point, normal, meshes, width: shp.width, height: shp.height, dropped: shp.dropped, overhang: over, overlap: shapeArea(shp) * sunk };
}

export interface Scene {
  readonly base: ViewModel | null;
  readonly logos: readonly Logo[];
}

export const emptyScene = (base: ViewModel | null = null): Scene => ({ base, logos: [] });

export function withLogo(sc: Scene, logo: Logo, index: number | null = null): Scene {
  const logos = [...sc.logos];
  if (index === null) logos.push(logo);
  else logos[index] = logo;
  return { base: sc.base, logos };
}

export const without = (sc: Scene, index: number): Scene => ({ base: sc.base, logos: sc.logos.filter((_, i) => i !== index) });

/** The model replaced (Repair), logos kept. */
export const withBase = (sc: Scene, base: ViewModel | null): Scene => ({ base, logos: sc.logos });

/** Everything moved / turned / scaled by ``m`` (see scene.py ``arranged``): logos keep what
 * Change… needs (point, direction, turn on the face, sizes scaled); when ``m`` mirrors they are
 * made again the right way round. */
export function arranged(sc: Scene, m: Matrix): Scene {
  const det = determinant3(m);
  const k = Math.cbrt(Math.abs(det));
  let base = sc.base;
  if (base) {
    const positions = applySoup(base.positions, m);
    base = { ...base, positions, size: sizeOf(positions), volumeMm3: base.volumeMm3 !== null ? base.volumeMm3 * Math.abs(det) : null };
  }
  const logos = sc.logos.map((lg): Logo => {
    const point = lg.point ? applyPoint(m, lg.point) : null;
    const normal = lg.normal ? applyNormal(m, lg.normal) : null;
    const s: LogoSettings = {
      ...lg.settings,
      width: lg.settings.width * k,
      thickness: lg.settings.thickness * k,
      sink: lg.settings.sink * k,
      rotation: turned(m, lg.normal ?? [0, 0, 1], normal ?? [0, 0, 1], lg.settings),
    };
    if (det < 0) {
      try {
        return buildLogo(lg.picture, s, point, normal, base, null);
      } catch (err) {
        if (!(err instanceof LogoError)) throw err;
      }
    }
    return {
      ...lg, settings: s, point, normal, meshes: lg.meshes.map((x) => applyIndexed(x.vertices, x.faces, m)),
      width: lg.width * k, height: lg.height * k, overlap: lg.overlap * k ** 3,
    };
  });
  return { base, logos };
}

/** The logo's turn on its face after ``m`` (see scene.py ``_turned``). */
function turned(m: Matrix, oldNormal: Vec3, newNormal: Vec3, s: LogoSettings): number {
  const xo = frame(oldNormal, s.rotation)[0];
  let x = [0, 1, 2].map((r) => m[r][0] * xo[0] + m[r][1] * xo[1] + m[r][2] * xo[2]);
  const ln = Math.hypot(...newNormal);
  const n = newNormal.map((c) => c / ln);
  const dn = x[0] * n[0] + x[1] * n[1] + x[2] * n[2];
  x = x.map((c, i) => c - n[i] * dn);
  if (Math.hypot(...x) < 1e-12) return s.rotation;
  const [bx, by] = frame(n as Vec3, 0);
  return (Math.atan2(x[0] * by[0] + x[1] * by[1] + x[2] * by[2], x[0] * bx[0] + x[1] * bx[1] + x[2] * bx[2]) * 180) / Math.PI;
}

/** Memory the model's own soup takes (for the undo limit). */
export const sceneBytes = (sc: Scene) => sc.base?.positions.byteLength ?? 0;
export let UNDO_BYTES = 300 * 1024 * 1024;
/** Tests only. */
export function setUndoBytes(n: number): void {
  UNDO_BYTES = n;
}

function sizeOf(pos: Float32Array): [number, number, number] {
  const [lo, hi] = bounds(pos);
  return [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
}

/** Every corner of the model and the logos, as one soup (for bounds). */
export function scenePositions(sc: Scene): Float32Array {
  const extra = logoPositions(sc.logos);
  const out = new Float32Array((sc.base?.positions.length ?? 0) + extra.length);
  if (sc.base) out.set(sc.base.positions);
  out.set(extra, sc.base?.positions.length ?? 0);
  return out;
}

/** Every logo piece as one triangle soup (like ViewModel.positions). */
export function logoPositions(logos: readonly Logo[]): Float32Array {
  const parts = logos.flatMap((lg) => lg.meshes.map(positions));
  const out = new Float32Array(parts.reduce((t, p) => t + p.length, 0));
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function size(pos: Float32Array): [number, number, number] {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < pos.length; i++) {
    const a = i % 3;
    if (pos[i] < lo[a]) lo[a] = pos[i];
    if (pos[i] > hi[a]) hi[a] = pos[i];
  }
  return [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
}

/** One ViewModel of everything (parts = the model's + one per logo piece). */
export function combined(sc: Scene, name = ""): ViewModel | null {
  if (!sc.base && !sc.logos.length) return null;
  if (!sc.logos.length) return sc.base;
  const extra = logoPositions(sc.logos);
  const base = sc.base;
  const pos = new Float32Array((base?.positions.length ?? 0) + extra.length);
  if (base) pos.set(base.positions);
  pos.set(extra, base?.positions.length ?? 0);
  const pieces = sc.logos.reduce((t, lg) => t + lg.meshes.length, 0);
  const watertight = base ? base.watertight : true;
  let total: number | null = null;
  if (watertight) {
    total = base?.volumeMm3 ?? 0;
    for (const lg of sc.logos) {
      for (const m of lg.meshes) total += Math.abs(volume(m));
      total -= lg.overlap;
    }
  }
  const stats: ModelStats = {
    triangles: pos.length / 9,
    parts: (base?.parts ?? 0) + pieces,
    size: size(pos),
    watertight,
    volumeMm3: total,
  };
  return { ...stats, name: name || base?.name || "Logo", positions: pos };
}

/** Binary STL of the whole scene (the worker turns it into the chosen format). */
export function sceneStl(sc: Scene): Uint8Array {
  const parts = [sc.base?.positions ?? new Float32Array(), logoPositions(sc.logos)];
  const tris = parts.reduce((t, p) => t + p.length / 9, 0);
  if (!tris) throw new LogoError("There is nothing to save yet.");
  const out = new Uint8Array(84 + tris * 50);
  const dv = new DataView(out.buffer);
  dv.setUint32(80, tris, true);
  let o = 84;
  for (const p of parts)
    for (let t = 0; t < p.length; t += 9) {
      o += 12; // normal 0,0,0: readers work it out
      for (let k = 0; k < 9; k++, o += 4) dv.setFloat32(o, p[t + k], true);
      o += 2;
    }
  return out;
}

/** "<name> with logo.3mf" / "<name> edited.3mf"; "logo.3mf" in the empty viewer. */
export function defaultSaveName(source: string | null, ext: string, logos = true): string {
  return source ? `${source.replace(/\.[^.]+$/, "")} ${logos ? "with logo" : "edited"}.${ext}` : `logo.${ext}`;
}

/** Undo / redo of whole scenes. */
export class History {
  undoStack: Scene[] = [];
  redoStack: Scene[] = [];
  saved: Scene;
  constructor(public scene: Scene = emptyScene()) {
    this.saved = scene;
  }
  push(sc: Scene): void {
    if (sc === this.scene) return;
    this.undoStack.push(this.scene);
    this.redoStack = [];
    this.scene = sc;
    this.trim();
  }

  /** An arranged model is a new copy: drop the oldest steps once the models kept pass
   * ``UNDO_BYTES``. */
  private trim(): void {
    const seen = new Set<unknown>();
    let total = 0;
    const all = [...this.undoStack, this.scene].reverse();
    for (let i = 0; i < all.length; i++) {
      const key = all[i].base;
      if (!seen.has(key)) {
        seen.add(key);
        total += sceneBytes(all[i]);
      }
      if (total > UNDO_BYTES && i > 0) {
        this.undoStack.splice(0, this.undoStack.length - i + 1);
        return;
      }
    }
  }
  undo(): boolean {
    const prev = this.undoStack.pop();
    if (!prev) return false;
    this.redoStack.push(this.scene);
    this.scene = prev;
    return true;
  }
  redo(): boolean {
    const next = this.redoStack.pop();
    if (!next) return false;
    this.undoStack.push(this.scene);
    this.scene = next;
    return true;
  }
  get dirty(): boolean {
    return this.scene !== this.saved;
  }
  markSaved(): void {
    this.saved = this.scene;
  }
  /** Back to what was last saved (or opened), forgetting undo / redo. */
  revert(): void {
    this.scene = this.saved;
    this.undoStack = [];
    this.redoStack = [];
  }
}
