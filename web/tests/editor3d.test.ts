// 3D viewer model + camera (mirrors tests/test_editor3d.py).
import * as THREE from "three";
import { STLExporter } from "three/examples/jsm/exporters/STLExporter.js";
import { describe as group, expect, it } from "vitest";
import { model3dViewConverter, VIEW_TARGET } from "../src/converters/model3d";
import type { InputFile } from "../src/core/types";
import { OrbitCamera, VIEWS, type Vec3 } from "../src/editor3d/camera";
import { describe, gridSpec, meshStats, standOnBed } from "../src/editor3d/mesh";

function box(w: number, d: number, hgt: number, at: Vec3 = [0, 0, 0]): Float32Array {
  const g = new THREE.BoxGeometry(w, d, hgt).toNonIndexed();
  g.translate(...at);
  return Float32Array.from(g.getAttribute("position").array);
}

group("mesh", () => {
  it("stands the model on the bed and measures it", () => {
    const pos = box(20, 10, 5, [100, 50, 30]);
    expect(standOnBed(pos)).toEqual([20, 10, 5]);
    const s = meshStats(pos);
    expect(s).toMatchObject({ triangles: 12, parts: 1, watertight: true });
    expect(s.volumeMm3).toBeCloseTo(1000);
    const rows = Object.fromEntries(describe(s));
    expect(rows).toMatchObject({ "Width (X)": "20.0 mm", "Height (Z)": "5.0 mm", Triangles: "12", Watertight: "Yes", Volume: "1.00 cm³" });
    let min = Infinity;
    for (let i = 2; i < pos.length; i += 3) min = Math.min(min, pos[i]);
    expect(min).toBe(0);
  });

  it("a hole means not watertight and no volume", () => {
    const s = meshStats(box(10, 10, 10).slice(9));
    expect(s.watertight).toBe(false);
    expect(s.volumeMm3).toBeNull();
    expect(Object.fromEntries(describe(s)).Volume).toBeUndefined();
  });

  it("parts are checked one by one", () => {
    const a = box(10, 10, 10), b = box(10, 10, 10, [30, 0, 0]);
    const pos = new Float32Array(a.length + b.length);
    pos.set(a);
    pos.set(b, a.length);
    const s = meshStats(pos, [0, 12]);
    expect(s.parts).toBe(2);
    expect(s.volumeMm3).toBeCloseTo(2000);
  });

  it("grid spacing matches the desktop", () => {
    expect(gridSpec([120, 20, 5])).toEqual({ step: 10, half: 90 });
    expect(gridSpec([3, 3, 3]).step).toBe(1);
  });
});

group("hidden viewer converter", () => {
  it("reads an STL into mesh.bin + info.json", async () => {
    const g = new THREE.BoxGeometry(20, 10, 5).toNonIndexed();
    g.translate(50, 50, 50);
    const stl = new STLExporter().parse(new THREE.Mesh(g), { binary: true }) as DataView;
    const data = new Uint8Array(stl.buffer, stl.byteOffset, stl.byteLength).slice();
    const input: InputFile = { file: new File([data as BlobPart], "box.stl"), name: "box.stl", ext: "stl", family: "model" };
    expect(model3dViewConverter.targets([input])).toEqual([]);
    const out = await model3dViewConverter.run([input], VIEW_TARGET, {}, { progress() {} });
    const pos = new Float32Array(out.find((o) => o.name === "mesh.bin")!.data.slice().buffer);
    expect(pos.length).toBe(12 * 9);
    const info = JSON.parse(new TextDecoder().decode(out.find((o) => o.name === "info.json")!.data));
    expect(info.size.map((v: number) => +v.toFixed(3))).toEqual([20, 10, 5]);
    expect(info.watertight).toBe(true);
  });
});

group("hidden viewer converter: no triangles", () => {
  it("a point cloud is refused in plain words", async () => {
    const ply = "ply\nformat ascii 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nend_header\n0 0 0\n1 0 0\n0 1 0\n0 0 1\n";
    const input: InputFile = { file: new File([ply], "dots.ply"), name: "dots.ply", ext: "ply", family: "model" };
    await expect(model3dViewConverter.run([input], VIEW_TARGET, {}, { progress() {} })).rejects.toThrow(/triangles/);
  });
});

group("camera", () => {
  const project = (cam: OrbitCamera, p: Vec3, aspect = 1) => {
    const c = new THREE.PerspectiveCamera(35, aspect, ...cam.clipPlanes());
    c.position.set(...cam.eye());
    c.up.set(...cam.up());
    c.lookAt(...cam.target);
    c.updateMatrixWorld();
    c.updateProjectionMatrix();
    return new THREE.Vector3(...p).project(c);
  };

  it("presets look the right way (same as desktop)", () => {
    const cam = new OrbitCamera();
    cam.setView("front");
    expect(cam.direction().map((v) => +v.toFixed(9) + 0)).toEqual([0, -1, 0]);
    expect(cam.up().map((v) => +v.toFixed(9) + 0)).toEqual([0, 0, 1]);
    cam.setView("top");
    expect(cam.direction().map((v) => +v.toFixed(9) + 0)).toEqual([0, 0, 1]);
    expect(cam.up().map((v) => +v.toFixed(9) + 0)).toEqual([0, 1, 0]);
  });

  for (const view of Object.keys(VIEWS)) {
    it(`fit keeps the box on screen (${view})`, () => {
      const cam = new OrbitCamera();
      cam.setView(view);
      const lo: Vec3 = [-30, -5, 0], hi: Vec3 = [30, 5, 12];
      for (const aspect of [0.5, 1, 2]) {
        cam.fit(lo, hi, aspect);
        for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) {
          const p = project(cam, [x, y, z], aspect);
          for (const v of [p.x, p.y, p.z]) expect(Math.abs(v)).toBeLessThanOrEqual(1);
        }
      }
    });
  }

  it("orbit, pan and zoom", () => {
    const cam = new OrbitCamera();
    cam.fit([-10, -10, -10], [10, 10, 10]);
    const d = cam.distance;
    cam.zoom(0.5);
    expect(cam.distance).toBeCloseTo(d / 2);
    cam.zoom(1e-9);
    expect(cam.distance).toBeCloseTo(cam.radius * 0.05);
    cam.orbit(0, 500);
    expect(cam.pitch).toBe(90);
    cam.orbit(-400, 0);
    expect(cam.yaw).toBeGreaterThanOrEqual(-180);
    expect(cam.yaw).toBeLessThan(180);
    cam.setView("front");
    cam.fit([-10, -10, -10], [10, 10, 10]);
    const before = project(cam, [0, 0, 0]).x;
    cam.pan(50, 0, 500);
    expect(project(cam, [0, 0, 0]).x).toBeGreaterThan(before);
  });
});
