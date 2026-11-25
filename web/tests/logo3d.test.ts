// 3D logo model (mirrors tests/test_logo3d.py): the shared cases made by the desktop's
// scripts/make_logo3d_cases.py, plus watertightness checks of our own meshes.
import { describe, expect, it } from "vitest";
import data from "../../tests/fixtures/logo3d_cases.json";
import { OrbitCamera, type Vec3 } from "../src/editor3d/camera";
import {
  area, badge, defaultMode, MIN_WALL_MM, placedLogo, plateOutline, prepare, ringsMeet, shapeOfMask, footprint, logoMesh, LogoError, mask, placement, shape, shapeArea, trace,
  volume, type Detail, type MaskMode, type Mesh, type PlateKind, type Ring,
} from "../src/editor3d/logo";
import type { ViewModel } from "../src/editor3d/mesh";
import { faceArea, flatFace, hit, overhang, ray } from "../src/editor3d/pick";
import { BADGE_GAP_MM, buildLogo, combined, DEFAULT_SETTINGS, defaultSaveName, emptyScene, History, sceneStl, without, withLogo } from "../src/editor3d/scene";

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

describe("rings that touch or cross", () => {
  it("ringsMeet: the shared cases", () => {
    for (const c of data.meet) expect(ringsMeet(c.rings as Ring[]), c.name).toBe(c.meets);
  });

  it("shape falls back to the unsimplified rings where they would touch", () => {
    const f = data.fallback;
    const s = shape(f.rings as Ring[], 10, null, f.raw as Ring[]);
    expect(s.polygons[0].holes.length).toBe(f.holes.length);
    s.polygons[0].holes.forEach((h, i) => closeAll(h.flat(), f.holes[i].flat()));
  });

  it("a busy picture: a sealed mesh, and placing it again is quick", () => {
    const n = 400;
    const m = new Uint8Array(n * n);
    let seed = 7;
    const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    const cells = Array.from({ length: 40 * 40 }, () => rnd() < 0.5);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) m[y * n + x] = cells[Math.floor(y / 10) * 40 + Math.floor(x / 10)] ? 1 : 0;
    const s = prepare(shapeOfMask(m, n, n, "normal", 80));
    const t = performance.now();
    const mesh = placedLogo(s, 2, [0, 0, 10], [0, 0, 1], 30);
    expect(performance.now() - t).toBeLessThan(200);
    expect(watertight(mesh)).toBe(true);
  });
});

describe("meshes", () => {
  it("busy shapes: closed, same triangles and volume as the desktop", () => {
    for (const c of data.busy) {
      const s = shape(traced(c.rows, c.detail as Detail), 30);
      const m = logoMesh(s, 2);
      expect(watertight(m), `${c.seed} ${c.detail}`).toBe(true);
      expect(m.faces.length / 3).toBe(c.triangles);
      close(volume(m), c.volume);
      close(shapeArea(s), c.area);
    }
  });

  it("the keychain hole keeps a wall, however thin the logo and margin", () => {
    const s = shape(traced(["#".repeat(60), "#".repeat(60)]), 40); // 40 × 1.3 mm
    for (const plate of ["rectangle", "rounded"] as const)
      for (const margin of [0, 0.5, 1, 3, 10]) {
        const [outer, hole] = plateOutline(s, margin, plate, true);
        const ox = outer.map((q) => q[0]), oy = outer.map((q) => q[1]);
        const hx = hole!.map((q) => q[0]), hy = hole!.map((q) => q[1]);
        expect(Math.min(...hx) - Math.min(...ox)).toBeGreaterThanOrEqual(MIN_WALL_MM - 1e-9);
        expect(Math.min(...hy) - Math.min(...oy)).toBeGreaterThanOrEqual(MIN_WALL_MM - 1e-9);
        expect(Math.max(...oy) - Math.max(...hy)).toBeGreaterThanOrEqual(MIN_WALL_MM - 1e-9);
        const [plateMesh, logoPart] = badge(s, 2, plate, margin, 2, true);
        expect(watertight(plateMesh) && watertight(logoPart), `${plate} ${margin}`).toBe(true);
      }
  });

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

describe("scene", () => {
  const ringRows = data.trace.find((t) => t.name === "ring")!.rows;
  const picture = () => {
    const { m, w, h } = grid(ringRows);
    const rgba = new Uint8Array(w * h * 4);
    m.forEach((v, i) => (rgba[i * 4 + 3] = v ? 255 : 0));
    return { name: "ring.png", rgba, w, h };
  };
  const box = (): ViewModel => {
    // 40 × 40 × 10 box standing on the bed, as a triangle soup
    const c = (x: number, y: number, z: number) => [x ? 20 : -20, y ? 20 : -20, z ? 10 : 0];
    const quads = [[[0,0,0],[0,1,0],[1,1,0],[1,0,0]], [[0,0,1],[1,0,1],[1,1,1],[0,1,1]], [[0,0,0],[1,0,0],[1,0,1],[0,0,1]],
      [[0,1,0],[0,1,1],[1,1,1],[1,1,0]], [[0,0,0],[0,0,1],[0,1,1],[0,1,0]], [[1,0,0],[1,1,0],[1,1,1],[1,0,1]]];
    const pos: number[] = [];
    for (const q of quads) for (const t of [[0, 1, 2], [0, 2, 3]]) for (const k of t) pos.push(...c(q[k][0], q[k][1], q[k][2]));
    return { name: "box.stl", positions: Float32Array.from(pos), triangles: 12, parts: 1, size: [40, 40, 10], watertight: true, volumeMm3: 16000 };
  };

  it("a logo on the top face stands on it and sinks in", () => {
    const base = box();
    const s = { ...DEFAULT_SETTINGS, width: 20 };
    const lg = buildLogo(picture(), s, [2, 3, 10], [0, 0, 1], base, [2, 3]);
    expect(lg.overhang).toBe(0);
    const z = lg.meshes[0].vertices.filter((_, i) => i % 3 === 2);
    close(Math.min(...z), 9.8);
    close(Math.max(...z), 12);
    const far = buildLogo(picture(), { ...s, width: 60 }, [0, 0, 10], [0, 0, 1], base, [2, 3]);
    expect(far.overhang).toBeGreaterThan(0);
    expect(() => buildLogo(picture(), s, null, null, base, null)).toThrow(LogoError);
  });

  it("a badge stands right of the model; combined counts every piece", () => {
    const base = box();
    const s = { ...DEFAULT_SETTINGS, where: "badge" as const, width: 20, plate: "rounded" as const, hole: true };
    const lg = buildLogo(picture(), s, null, null, base, null);
    expect(lg.meshes.length).toBe(2);
    const x = lg.meshes[0].vertices.filter((_, i) => i % 3 === 0);
    close(Math.min(...x), 20 + BADGE_GAP_MM);
    const sc = withLogo(emptyScene(base), lg);
    const m = combined(sc)!;
    expect(m.parts).toBe(3);
    expect(m.triangles).toBe(12 + lg.meshes.reduce((t, mm) => t + mm.faces.length / 3, 0));
    close(m.size[2], 10);
    expect(m.volumeMm3!).toBeGreaterThan(16000);
    const onTop = buildLogo(picture(), { ...DEFAULT_SETTINGS, width: 20, thickness: 2 }, [0, 0, 10], [0, 0, 1], base, [2, 3]);
    const sq = shape(traced(ringRows), 20);
    close(combined(withLogo(emptyScene(base), onTop))!.volumeMm3!, 16000 + shapeArea(sq) * 2); // the sunk part once
    expect(combined(without(sc, 0))).toBe(base);
  });

  it("STL of the scene holds every triangle", () => {
    const base = box();
    const lg = buildLogo(picture(), { ...DEFAULT_SETTINGS, width: 20 }, [0, 0, 10], [0, 0, 1], base, [2, 3]);
    const stl = sceneStl(withLogo(emptyScene(base), lg));
    const n = new DataView(stl.buffer).getUint32(80, true);
    expect(n).toBe(12 + lg.meshes[0].faces.length / 3);
    expect(stl.length).toBe(84 + 50 * n);
    expect(() => sceneStl(emptyScene())).toThrow(LogoError);
    expect(defaultSaveName("box.stl", "3mf")).toBe("box with logo.3mf");
    expect(defaultSaveName(null, "stl")).toBe("logo.stl");
  });

  it("history: undo, redo, dirty", () => {
    const hs = new History(emptyScene(box()));
    expect(hs.dirty).toBe(false);
    const lg = buildLogo(picture(), { ...DEFAULT_SETTINGS, width: 20 }, [0, 0, 10], [0, 0, 1], null, null);
    hs.push(withLogo(hs.scene, lg));
    expect(hs.dirty).toBe(true);
    expect(hs.undo()).toBe(true);
    expect(hs.dirty).toBe(false);
    expect(hs.redo()).toBe(true);
    hs.markSaved();
    expect(hs.dirty).toBe(false);
    expect(hs.redo()).toBe(false);
  });
});
