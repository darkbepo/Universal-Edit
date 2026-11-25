// 3D logo model (mirrors tests/test_logo3d.py): the shared cases made by the desktop's
// scripts/make_logo3d_cases.py, plus watertightness checks of our own meshes.
import { describe, expect, it } from "vitest";
import data from "../../tests/fixtures/logo3d_cases.json";
import { OrbitCamera, type Vec3 } from "../src/editor3d/camera";
import {
  area, badge, defaultMode, footprint, logoMesh, LogoError, mask, placement, shape, shapeArea, trace,
  volume, type Detail, type MaskMode, type Mesh, type PlateKind, type Ring,
} from "../src/editor3d/logo";
import { faceArea, flatFace, hit, overhang, ray } from "../src/editor3d/pick";

const grid = (rows: string[]) => ({ m: Uint8Array.from(rows.join("").split(""), (c) => (c === "#" ? 1 : 0)), w: rows[0].length, h: rows.length });
const close = (a: number, b: number, tol = 1e-6) => expect(Math.abs(a - b)).toBeLessThanOrEqual(tol * Math.max(1, Math.abs(b)));
const closeAll = (a: number[], b: number[], tol = 1e-6) => {
  expect(a.length).toBe(b.length);
  a.forEach((v, i) => close(v, b[i], tol));
};
const rings6 = (rs: Ring[]) => rs.map((r) => r.flat());
const traced = (rows: string[], detail: Detail = "normal") => {
  const { m, w, h } = grid(rows);
  return trace(m, w, h, detail);
};

/** Every edge used exactly twice, once in each direction (closed, consistently wound). */
function watertight(mesh: Mesh): boolean {
  const key = (i: number) => `${mesh.vertices[i * 3]},${mesh.vertices[i * 3 + 1]},${mesh.vertices[i * 3 + 2]}`;
  const count = new Map<string, number>();
  for (let t = 0; t < mesh.faces.length; t += 3)
    for (let k = 0; k < 3; k++) {
      const e = `${key(mesh.faces[t + k])}>${key(mesh.faces[t + ((k + 1) % 3)])}`;
      count.set(e, (count.get(e) ?? 0) + 1);
    }
  for (const [e, n] of count) {
    const [a, b] = e.split(">");
    if (n !== 1 || count.get(`${b}>${a}`) !== 1) return false;
  }
  return true;
}

describe("shared cases", () => {
  it("mask", () => {
    for (const c of data.mask) {
      expect(Array.from(mask(c.rgba, c.w, c.h, c.mode as MaskMode, c.threshold, c.invert))).toEqual(c.mask);
      expect(defaultMode(c.rgba, c.w, c.h)).toBe(c.default);
    }
  });

  it("trace", () => {
    for (const c of data.trace) {
      const got = rings6(traced(c.rows, c.detail as Detail));
      expect(got.length, `${c.name} ${c.detail}`).toBe(c.rings.length);
      got.forEach((r, i) => closeAll(r, c.rings[i].flat()));
    }
  });

  it("shape", () => {
    for (const c of data.shape) {
      if ("error" in c) {
        expect(() => shape(traced(c.rows), c.width)).toThrow(new LogoError(c.error));
        continue;
      }
      const s = shape(traced(c.rows), c.width);
      expect(s.polygons.length, c.name).toBe(c.polygons!.length);
      s.polygons.forEach((p, i) => {
        closeAll(p.outer.flat(), c.polygons![i].outer.flat());
        expect(p.holes.length).toBe(c.polygons![i].holes.length);
        p.holes.forEach((hole, j) => closeAll(hole.flat(), c.polygons![i].holes[j].flat()));
      });
      close(s.width, c.width_mm!);
      close(s.height, c.height_mm!);
      expect(s.dropped).toBe(c.dropped);
      close(shapeArea(s), c.area!);
    }
  });

  it("badge", () => {
    for (const c of data.badge) {
      const parts = badge(shape(traced(c.rows), 30), 2.5, c.plate as PlateKind, 3, 1.5, c.hole);
      expect(parts.length).toBe(c.parts.length);
      parts.forEach((p, i) => {
        const want = c.parts[i];
        expect(p.faces.length / 3, `${c.name} ${c.plate}`).toBe(want.triangles);
        close(volume(p), want.volume);
        const lo = [0, 1, 2].map((a) => Math.min(...p.vertices.filter((_, k) => k % 3 === a)));
        const hi = [0, 1, 2].map((a) => Math.max(...p.vertices.filter((_, k) => k % 3 === a)));
        closeAll(lo, want.min);
        closeAll(hi, want.max);
        expect(watertight(p)).toBe(true);
      });
    }
  });

  it("frame", () => {
    for (const c of data.frame) closeAll(placement([1, 2, 3], c.normal as Vec3, c.rotation).flat(), c.matrix.flat());
  });

  it("ray", () => {
    for (const c of data.ray) {
      const cam = new OrbitCamera();
      cam.target = c.target as Vec3;
      cam.distance = c.distance;
      cam.yaw = c.yaw;
      cam.pitch = c.pitch;
      const [o, d] = ray(cam, c.x, c.y, c.w, c.h);
      closeAll(o, c.origin);
      closeAll(d, c.direction);
    }
  });

  it("face", () => {
    for (const c of data.face) {
      const pos = Float64Array.from(c.vertices.flat());
      const l = Math.hypot(...c.direction);
      const found = hit(pos, c.origin as Vec3, c.direction.map((v) => v / l) as Vec3);
      if (c.hit === null) {
        expect(found).toBeNull();
        continue;
      }
      const [tri, point] = found!;
      expect(tri).toBe(c.hit);
      closeAll(point, c.point!);
      const face = flatFace(pos, tri);
      expect([...face.triangles].sort((a, b) => a - b)).toEqual(c.triangles);
      closeAll(face.normal, c.normal!);
      close(faceArea(pos, face), c.area!);
      const s = shape(traced(data.trace.find((t) => t.name === "ring")!.rows), c.width);
      expect(overhang(footprint(s, point, face.normal, 15), pos, face)).toBe(c.overhang);
    }
  });
});

describe("meshes", () => {
  it("a logo is a closed solid whose volume is area × thickness", () => {
    const s = shape(traced(data.trace.find((t) => t.name === "letters")!.rows), 50);
    const m = logoMesh(s, 3);
    expect(watertight(m)).toBe(true);
    close(volume(m), shapeArea(s) * 3);
  });

  it("outer rings run counter-clockwise, holes clockwise", () => {
    const rings = traced(data.trace.find((t) => t.name === "ring")!.rows);
    expect(rings.map((r) => Math.sign(area(r)))).toEqual([1, -1, 1, -1]);
  });
});
