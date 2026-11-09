import * as THREE from "three";
import { STLExporter } from "three/examples/jsm/exporters/STLExporter.js";
import { describe, expect, it } from "vitest";
import { modelConverter } from "../src/converters/model3d";
import { defaults, type Values } from "../src/core/options";
import type { InputFile } from "../src/core/types";

/** A 20 × 10 × 5 mm box as binary STL, offset away from the origin. */
function boxStl(): Uint8Array {
  const g = new THREE.BoxGeometry(20, 10, 5).toNonIndexed();
  g.translate(50, 50, 50);
  const out = new STLExporter().parse(new THREE.Mesh(g), { binary: true }) as DataView;
  return new Uint8Array(out.buffer, out.byteOffset, out.byteLength).slice();
}

async function convert(name: string, data: Uint8Array, to: string, opts: Values = {}) {
  const ext = name.split(".").pop()!;
  const input: InputFile = { file: new File([data as BlobPart], name), name, ext, family: "model" };
  const target = modelConverter.targets([input]).find((t) => t.id === to)!;
  const [out] = await modelConverter.run([input], target, { ...defaults(modelConverter.options), ...opts }, { progress() {} });
  return out;
}

/** Bounding box size + min of a model, read back through STL. */
async function measure(name: string, data: Uint8Array) {
  const stl = name.endsWith(".stl") ? data : (await convert(name, data, "stl", { center_on_bed: false, repair: false })).data;
  const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
  const g = new STLLoader().parse(stl.buffer.slice(stl.byteOffset, stl.byteOffset + stl.byteLength) as ArrayBuffer);
  g.computeBoundingBox();
  const size = g.boundingBox!.getSize(new THREE.Vector3());
  return { size: size.toArray().map((v) => +v.toFixed(3)), min: g.boundingBox!.min.toArray().map((v) => +v.toFixed(3)), triangles: g.getAttribute("position").count / 3 };
}

describe("3D models", () => {
  it.each(["3mf", "obj", "ply", "glb"])("STL → %s → STL keeps the size in mm", async (fmt) => {
    const out = await convert("box.stl", boxStl(), fmt);
    expect(out.name).toBe(`box.${fmt}`);
    const m = await measure(out.name, out.data);
    expect(m.size).toEqual([20, 10, 5]);
    expect(m.triangles).toBe(12);
    expect(m.min).toEqual([-10, -5, 0]); // centred on the bed
  });

  it("3MF is a zip with a millimetre model", async () => {
    const out = await convert("box.stl", boxStl(), "3mf");
    const { unzipSync, strFromU8 } = await import("fflate");
    const model = strFromU8(unzipSync(out.data)["3D/3dmodel.model"]);
    expect(model).toContain('unit="millimeter"');
    expect(model.match(/<triangle /g)).toHaveLength(12);
  });

  it("inches and scale", async () => {
    const out = await convert("box.stl", boxStl(), "obj", { units: "inch", scale_percent: 50 });
    const m = await measure("box.obj", out.data);
    expect(m.size).toEqual([254, 127, 63.5]);
  });

  it("ASCII STL", async () => {
    const out = await convert("box.obj", (await convert("box.stl", boxStl(), "obj")).data, "stl", { stl_format: "ascii" });
    expect(new TextDecoder().decode(out.data.slice(0, 5))).toBe("solid");
  });

  it("repair removes duplicate and degenerate triangles", async () => {
    const tri = "v 0 0 0\nv 10 0 0\nv 0 10 0\nv 5 5 0\nf 1 2 3\nf 1 2 3\nf 1 1 2\n";
    const out = await convert("t.obj", new TextEncoder().encode(tri), "stl");
    expect((await measure("t.stl", out.data)).triangles).toBe(1);
  });

  it("offers all formats except the source", () => {
    const f: InputFile = { file: new File([], "a.step"), name: "a.step", ext: "step", family: "model" };
    expect(modelConverter.targets([f]).map((t) => t.id)).toEqual(["stl", "3mf", "obj", "ply", "glb"]);
  });
});
