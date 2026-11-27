// 3D logos on curved surfaces (mirrors tests/test_logo3d_curved.py): the shared cases made by
// scripts/make_logo3d_cases.py ("refine", "surface", "fit"), a logo on a sphere sitting flush,
// and merging / engraving with manifold-3d (run in node here).
import { describe, expect, it } from "vitest";
import Module from "manifold-3d";
import data from "../../tests/fixtures/logo3d_cases.json";
import type { Vec3 } from "../src/editor3d/camera";
import { fittedLogo, frame, LogoError, outlineEdges, prepare, refine, shape, trace, volume, ENGRAVE_LIFT_MM, type Mesh } from "../src/editor3d/logo";
import type { ViewModel } from "../src/editor3d/mesh";
import { check, isClosed, weld } from "../src/editor3d/meshcheck";
import { areaNormal, surface } from "../src/editor3d/pick";
import { combine, SolidError } from "../src/editor3d/solid";
import { buildLogo, combined, DEFAULT_SETTINGS, needsSolid, reach, sceneSolids, type Scene } from "../src/editor3d/scene";

const R = 30;
const grid = (rows: string[]) => ({ m: Uint8Array.from(rows.join("").split(""), (c) => (c === "#" ? 1 : 0)), w: rows[0].length, h: rows.length });
const rowsOf = (name: string) => data.trace.find((c) => c.name === name)!.rows;
const traced = (rows: string[]) => {
  const { m, w, h } = grid(rows);
  return trace(m, w, h, "normal");
};
const near = (a: number, b: number, tol: number) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol);

/** The shared icosphere as a soup (ViewModel.positions). */
function ballSoup(): Float64Array {
  const { vertices, faces } = data.ball;
  const out = new Float64Array(faces.length * 9);
  faces.forEach((f, t) => f.forEach((v, k) => out.set(vertices[v], t * 9 + k * 3)));
  return out;
}

/** Every edge used exactly twice, once each way. */
function closed(mesh: Mesh): boolean {
  const count = new Map<string, number>();
  for (let t = 0; t < mesh.faces.length; t += 3)
    for (let k = 0; k < 3; k++) {
      const e = `${mesh.faces[t + k]}>${mesh.faces[t + ((k + 1) % 3)]}`;
      count.set(e, (count.get(e) ?? 0) + 1);
    }
  for (const [e, n] of count) {
    const [a, b] = e.split(">");
    if (n !== 1 || count.get(`${b}>${a}`) !== 1) return false;
  }
  return true;
}

const LOGO = ["##########..########", "##########..#....###", "##########..########"];

describe("shared cases", () => {
  it("refine", () => {
    for (const c of data.refine) {
      const s = prepare(shape(traced(rowsOf(c.name)), 20));
      const poly = s.polygons[c.polygon];
      const xy = [poly.outer, ...poly.holes].flat();
      const [pts, tris] = refine(xy, poly.caps!, c.max_edge);
      // the two earcut builds may cut a polygon with holes differently: counts must match only
      // for the plain square; the outline (split the same way) and the area always
      expect([c.name, outlineEdges(tris).length]).toEqual([c.name, c.outline]);
      if (c.name === "square") expect([pts.length, tris.length]).toEqual([c.points, c.triangles]);
      for (const [a, b, d] of tris)
        for (const [u, v] of [[a, b], [b, d], [d, a]]) expect(Math.hypot(pts[v][0] - pts[u][0], pts[v][1] - pts[u][1])).toBeLessThanOrEqual(c.max_edge + 1e-9);
      let a2 = 0;
      for (const [a, b, d] of tris) a2 += (pts[b][0] - pts[a][0]) * (pts[d][1] - pts[a][1]) - (pts[b][1] - pts[a][1]) * (pts[d][0] - pts[a][0]);
      near(a2 / 2, c.area, 1e-6);
    }
  });

  it("surface", () => {
    const soup = ballSoup();
    for (const c of data.surface) {
      const d = c.direction as Vec3;
      const point = c.point as Vec3;
      const n = areaNormal(soup, point, d[2] > 0.5 * Math.hypot(...d) ? [0, 0, 1] : d, c.reach);
      n.forEach((v, i) => near(v, c.normal[i], 1e-8));
      const surf = surface(soup, point, frame(n, c.rotation), c.reach);
      expect([surf.count, surf.size]).toEqual([c.kept, c.size]);
      const s = surf.sample(c.uv.flat());
      expect(Array.from(s.hit, Boolean)).toEqual(c.hit);
      s.h.forEach((v, i) => near(v, c.h[i], 1e-8));
      c.normals.forEach((row, i) => row.forEach((v, k) => near(s.normals[i * 3 + k], v, 1e-8)));
    }
  });

  it("fit", () => {
    const soup = ballSoup();
    const s = prepare(shape(traced(rowsOf("ring")), 16));
    for (const c of data.fit) {
      const point = c.point as Vec3, n = c.normal as Vec3;
      const surf = surface(soup, point, frame(n, 15), reach(s));
      const r = fittedLogo(s, 2, point, n, 15, 0.2, c.fit as "flat" | "wrap", surf, c.style as "raised" | "engraved");
      // the ring has a hole: earcut may cut it differently here, so the triangle count can differ
      expect([c.fit, c.style, r.missed, r.folded]).toEqual([c.fit, c.style, c.missed, c.folded]);
      near(volume(r.mesh), c.volume, 1e-3 * c.volume);
      expect(closed(r.mesh)).toBe(true);
    }
  });
});

function fit(style: "raised" | "engraved", how: "flat" | "wrap", width = 24) {
  const soup = ballSoup();
  const s = prepare(shape(traced(LOGO), width));
  const len = Math.hypot(0.3, 0.2, 1);
  const d: Vec3 = [0.3 / len, 0.2 / len, 1 / len];
  const point: Vec3 = [d[0] * R, d[1] * R, d[2] * R];
  const n = areaNormal(soup, point, d, reach(s));
  return fittedLogo(s, 2, point, n, 15, 0.2, how, surface(soup, point, frame(n, 15), reach(s)), style);
}

describe("on a sphere", () => {
  it.each(["flat", "wrap"] as const)("%s sits flush", (how) => {
    const r = fit("raised", how);
    expect([r.missed, r.folded]).toEqual([0, 0]);
    expect(closed(r.mesh)).toBe(true);
    const v = r.mesh.vertices;
    const rad = Array.from({ length: v.length / 3 }, (_, i) => Math.hypot(v[i * 3], v[i * 3 + 1], v[i * 3 + 2]));
    const bottom = rad.filter((x) => x < R), top = rad.filter((x) => x >= R);
    expect(bottom.length).toBe(top.length);
    // the shared ball is coarse (1280 triangles): its flat facets sit up to 0.11 mm inside the
    // sphere (the desktop test checks 0.05 mm on a fine one)
    for (const x of bottom) near(x, R - 0.2, 0.15);
    if (how === "wrap") for (const x of top) near(x, R + 2, 0.15);
  });

  it("wrap refuses a logo that misses the model; flat warns", () => {
    expect(() => fit("raised", "wrap", 80)).toThrow(LogoError);
    expect(fit("raised", "flat", 80).missed).toBeGreaterThan(0);
  });

  it("engraved cutters reach below and above the surface", () => {
    for (const how of ["flat", "wrap"] as const) {
      const r = fit("engraved", how);
      const v = r.mesh.vertices;
      const rad = Array.from({ length: v.length / 3 }, (_, i) => Math.hypot(v[i * 3], v[i * 3 + 1], v[i * 3 + 2]));
      expect(Math.min(...rad)).toBeLessThan(R - 1.9);
      expect(Math.max(...rad)).toBeGreaterThan(R + ENGRAVE_LIFT_MM - 0.1);
      expect(closed(r.mesh)).toBe(true);
    }
  });
});

function ballModel(open = false): ViewModel {
  const soup = ballSoup();
  const positions = Float32Array.from(open ? soup.subarray(9) : soup);
  return { name: "ball", positions, triangles: positions.length / 9, parts: 1, size: [60, 60, 60], watertight: !open, volumeMm3: (4 / 3) * Math.PI * R ** 3 };
}

const picture = () => ({ name: "logo.png", rgba: new Uint8Array(Array(16).fill([0, 0, 0, 255]).flat()), w: 4, h: 4 });

function scene(style: "raised" | "engraved", open = false): Scene {
  const base = ballModel(open);
  const lg = buildLogo(picture(), { ...DEFAULT_SETTINGS, width: 12, fit: "wrap", style }, [0, 0, R], [0, 0, 1], base, null);
  return { base, logos: [lg] };
}

describe("merging", () => {
  it.each(["raised", "engraved"] as const)("%s: one closed body", async (style) => {
    const m = await Module();
    m.setup();
    const sc = scene(style);
    expect(needsSolid(sc)).toBe(style === "engraved");
    const { base, raised, engraved } = sceneSolids(sc);
    const out = combine(m, base, raised, engraved);
    const w = weld(out);
    const report = check(w);
    expect(isClosed(report)).toBe(true);
    const bw = weld(sc.base!.positions);
    const ball = volume({ vertices: bw.vertices, faces: Uint32Array.from(bw.faces.flat()) }); // faceted
    const v = volume({ vertices: w.vertices, faces: Uint32Array.from(w.faces.flat()) });
    if (style === "raised") expect(v).toBeGreaterThan(ball + 50);
    else expect(v).toBeLessThan(ball - 50);
    const info = combined({ ...sc, base: { ...sc.base!, volumeMm3: ball } })!.volumeMm3!;
    expect(Math.abs(info - v) / v).toBeLessThan(0.02);
  });

  it("an open model can't be merged", async () => {
    const m = await Module();
    m.setup();
    const { base, raised, engraved } = sceneSolids(scene("raised", true));
    expect(() => combine(m, base, raised, engraved)).toThrow(SolidError);
  });
});
