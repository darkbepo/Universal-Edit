// 3D models for printing: STL ↔ OBJ ↔ 3MF ↔ PLY ↔ GLB, and STEP / IGES (CAD) → mesh.
// Same rules as the desktop app's model3d.py: everything is normalised to millimetres.
// three.js loaders that need the DOM (3MF, GLB) are replaced by small readers here, because
// conversions run in a Web Worker.
import { defaults, opt, type Values } from "../core/options";
import { ConversionError, type Converter, type InputFile, type OutputFile } from "../core/types";
import { baseName, uniqueNamer } from "./util";

type THREE = typeof import("three");
type Geometry = import("three").BufferGeometry;
interface Part {
  name: string;
  geometry: Geometry; // world space, millimetres, position attribute only
}

const TARGETS: Record<string, string> = { stl: "STL", "3mf": "3MF", obj: "OBJ", ply: "PLY", glb: "GLB" };
const MIME: Record<string, string> = { stl: "model/stl", "3mf": "model/3mf", obj: "model/obj", ply: "application/octet-stream", glb: "model/gltf-binary" };
const UNIT_MM: Record<string, number> = { mm: 1, cm: 10, m: 1000, inch: 25.4, ft: 304.8 };
const THREEMF_UNIT_MM: Record<string, number> = { micron: 0.001, millimeter: 1, centimeter: 10, inch: 25.4, foot: 304.8, meter: 1000 };
const DETAIL: Record<string, [number, number]> = { coarse: [0.1, 1.0], normal: [0.02, 0.5], fine: [0.005, 0.2] };

// ------------------------------------------------------------------------- readers

function partFrom(T: THREE, name: string, positions: ArrayLike<number>, index?: ArrayLike<number> | null): Part {
  const g = new T.BufferGeometry();
  g.setAttribute("position", new T.Float32BufferAttribute(Array.from(positions), 3));
  if (index && index.length) g.setIndex(Array.from(index));
  return { name, geometry: g };
}

/** Bake every mesh under ``root`` into world-space parts (position only). */
function partsFromObject(T: THREE, root: import("three").Object3D): Part[] {
  root.updateMatrixWorld(true);
  const parts: Part[] = [];
  root.traverse((o) => {
    const mesh = o as import("three").Mesh;
    if (!mesh.isMesh || !mesh.geometry) return;
    const g = new T.BufferGeometry();
    g.setAttribute("position", mesh.geometry.getAttribute("position").clone());
    if (mesh.geometry.index) g.setIndex(mesh.geometry.index.clone());
    g.applyMatrix4(mesh.matrixWorld);
    parts.push({ name: mesh.name || `part${parts.length + 1}`, geometry: g });
  });
  return parts;
}

/** Minimal GLB reader: triangle meshes + node transforms (no textures, no Draco). */
function readGlb(T: THREE, buf: ArrayBuffer): Part[] {
  const dv = new DataView(buf);
  if (dv.getUint32(0, true) !== 0x46546c67) throw new ConversionError("This isn't a binary glTF (GLB) file.");
  let offset = 12;
  let json: Record<string, any> | null = null; // eslint-disable-line @typescript-eslint/no-explicit-any
  let bin: Uint8Array | null = null;
  while (offset < dv.byteLength) {
    const len = dv.getUint32(offset, true);
    const type = dv.getUint32(offset + 4, true);
    const chunk = new Uint8Array(buf, offset + 8, len);
    if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(chunk));
    else if (type === 0x004e4942) bin = chunk;
    offset += 8 + len;
  }
  if (!json || !bin) throw new ConversionError("The GLB file is incomplete.");
  if ((json.extensionsUsed ?? []).includes("KHR_draco_mesh_compression")) {
    throw new ConversionError("This GLB uses Draco compression, which the browser version can't read yet. Use the desktop app.");
  }
  const SIZE: Record<number, number> = { 5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4 };
  const COUNT: Record<string, number> = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
  const accessor = (i: number): number[] => {
    const a = json!.accessors[i];
    const view = json!.bufferViews[a.bufferView];
    const comps = COUNT[a.type];
    const size = SIZE[a.componentType];
    const stride = view.byteStride || comps * size;
    const base = bin!.byteOffset + (view.byteOffset ?? 0) + (a.byteOffset ?? 0);
    const d = new DataView(bin!.buffer);
    const read = (p: number) => a.componentType === 5126 ? d.getFloat32(p, true) : a.componentType === 5125 ? d.getUint32(p, true) : a.componentType === 5123 ? d.getUint16(p, true) : d.getUint8(p);
    const out: number[] = [];
    for (let k = 0; k < a.count; k++) for (let c = 0; c < comps; c++) out.push(read(base + k * stride + c * size));
    return out;
  };
  const parts: Part[] = [];
  const visit = (nodeIndex: number, parent: import("three").Matrix4) => {
    const n = json!.nodes[nodeIndex];
    const local = new T.Matrix4();
    if (n.matrix) local.fromArray(n.matrix);
    else local.compose(new T.Vector3(...(n.translation ?? [0, 0, 0])), new T.Quaternion(...(n.rotation ?? [0, 0, 0, 1])), new T.Vector3(...(n.scale ?? [1, 1, 1])));
    const world = parent.clone().multiply(local);
    if (n.mesh !== undefined) {
      for (const prim of json!.meshes[n.mesh].primitives) {
        if ((prim.mode ?? 4) !== 4 || prim.attributes.POSITION === undefined) continue;
        const part = partFrom(T, n.name ?? json!.meshes[n.mesh].name ?? `part${parts.length + 1}`, accessor(prim.attributes.POSITION), prim.indices !== undefined ? accessor(prim.indices) : null);
        part.geometry.applyMatrix4(world);
        parts.push(part);
      }
    }
    for (const c of n.children ?? []) visit(c, world);
  };
  const scene = json.scenes?.[json.scene ?? 0];
  for (const root of scene?.nodes ?? json.nodes?.map((_: unknown, i: number) => i) ?? []) visit(root, new T.Matrix4());
  return parts;
}

/** Minimal 3MF reader: meshes, components and build items with transforms. */
async function read3mf(T: THREE, bytes: Uint8Array): Promise<{ parts: Part[]; unitMm: number }> {
  const { unzipSync, strFromU8 } = await import("fflate");
  const { XMLParser } = await import("fast-xml-parser");
  const zip = unzipSync(bytes);
  const modelPath = Object.keys(zip).find((p) => /^3D\/.*\.model$/i.test(p));
  if (!modelPath) throw new ConversionError("This 3MF file has no 3D model inside.");
  const arrays = new Set(["object", "vertex", "triangle", "item", "component"]);
  const doc = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "", removeNSPrefix: true, isArray: (n) => arrays.has(n) }).parse(strFromU8(zip[modelPath]));
  const model = doc.model;
  const unitMm = THREEMF_UNIT_MM[model.unit ?? "millimeter"] ?? 1;
  const objects = new Map<string, Record<string, any>>(); // eslint-disable-line @typescript-eslint/no-explicit-any
  for (const o of model.resources?.object ?? []) objects.set(String(o.id), o);
  const matrix = (t?: string) => {
    const m = new T.Matrix4();
    if (!t) return m;
    const v = t.trim().split(/\s+/).map(Number);
    // 3MF: row vectors, 4x3 — x' = x*m00 + y*m10 + z*m20 + m30
    m.set(v[0], v[3], v[6], v[9], v[1], v[4], v[7], v[10], v[2], v[5], v[8], v[11], 0, 0, 0, 1);
    return m;
  };
  const parts: Part[] = [];
  const emit = (id: string, world: import("three").Matrix4, depth = 0) => {
    const o = objects.get(id);
    if (!o || depth > 16) return;
    if (o.mesh) {
      const pos = (o.mesh.vertices?.vertex ?? []).flatMap((v: Record<string, string>) => [Number(v.x), Number(v.y), Number(v.z)]);
      const idx = (o.mesh.triangles?.triangle ?? []).flatMap((t: Record<string, string>) => [Number(t.v1), Number(t.v2), Number(t.v3)]);
      const part = partFrom(T, o.name ?? `object${id}`, pos, idx);
      part.geometry.applyMatrix4(world);
      parts.push(part);
    }
    for (const c of o.components?.component ?? []) emit(String(c.objectid), world.clone().multiply(matrix(c.transform)), depth + 1);
  };
  for (const item of model.build?.item ?? []) emit(String(item.objectid), matrix(item.transform));
  return { parts, unitMm };
}

async function loadParts(T: THREE, f: InputFile, o: Values, onStatus: (m: string) => void): Promise<Part[]> {
  const buf = await f.file.arrayBuffer();
  let parts: Part[];
  let toMm = 1;
  if (f.ext === "step" || f.ext === "iges") {
    const { getOcct } = await import("../engines/occt");
    const occt = await getOcct(onStatus);
    const [lin, ang] = DETAIL[String(o.detail)] ?? DETAIL.normal;
    const params = { linearUnit: "millimeter", linearDeflectionType: "absolute_value", linearDeflection: lin, angularDeflection: ang };
    const r = (f.ext === "step" ? occt.ReadStepFile : occt.ReadIgesFile)(new Uint8Array(buf), params);
    if (!r.success || !r.meshes.length) throw new ConversionError(`Couldn't read “${f.name}” as a ${f.ext.toUpperCase()} CAD file.`);
    parts = r.meshes.map((m: { name?: string; attributes: { position: { array: number[] } }; index: { array: number[] } }, i: number) =>
      partFrom(T, m.name || `part${i + 1}`, m.attributes.position.array, m.index.array));
  } else if (f.ext === "stl") {
    const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
    parts = [{ name: baseName(f.name), geometry: new STLLoader().parse(buf) }];
  } else if (f.ext === "obj") {
    const { OBJLoader } = await import("three/examples/jsm/loaders/OBJLoader.js");
    parts = partsFromObject(T, new OBJLoader().parse(new TextDecoder().decode(buf)));
  } else if (f.ext === "ply") {
    const { PLYLoader } = await import("three/examples/jsm/loaders/PLYLoader.js");
    parts = [{ name: baseName(f.name), geometry: new PLYLoader().parse(buf) }];
  } else if (f.ext === "glb") {
    parts = readGlb(T, buf);
    toMm = 1000; // glTF is metres
  } else if (f.ext === "3mf") {
    const r = await read3mf(T, new Uint8Array(buf));
    parts = r.parts;
    toMm = r.unitMm;
  } else {
    throw new ConversionError(`${f.ext.toUpperCase()} models can't be opened in the browser yet.`);
  }
  if (!parts.length || parts.every((p) => !p.geometry.getAttribute("position")?.count)) {
    throw new ConversionError(`“${f.name}” doesn't contain any 3D geometry.`);
  }
  if (o.units !== "auto") toMm = UNIT_MM[String(o.units)];
  const s = (toMm * Number(o.scale_percent)) / 100;
  for (const p of parts) {
    const g = new T.BufferGeometry();
    g.setAttribute("position", p.geometry.getAttribute("position"));
    if (p.geometry.index) g.setIndex(p.geometry.index);
    g.scale(s, s, s);
    p.geometry = g;
  }
  return parts;
}

// ------------------------------------------------------------------------- processing

async function repair(T: THREE, g: Geometry): Promise<Geometry> {
  const { mergeVertices } = await import("three/examples/jsm/utils/BufferGeometryUtils.js");
  const merged = mergeVertices(g, 1e-5);
  const pos = merged.getAttribute("position");
  const idx = merged.index!;
  const keep: number[] = [];
  const seen = new Set<string>();
  const a = new T.Vector3(), b = new T.Vector3(), c = new T.Vector3();
  for (let i = 0; i < idx.count; i += 3) {
    const t = [idx.getX(i), idx.getX(i + 1), idx.getX(i + 2)];
    if (t[0] === t[1] || t[1] === t[2] || t[0] === t[2]) continue; // degenerate
    a.fromBufferAttribute(pos, t[0]); b.fromBufferAttribute(pos, t[1]); c.fromBufferAttribute(pos, t[2]);
    if (b.clone().sub(a).cross(c.clone().sub(a)).lengthSq() < 1e-18) continue; // zero area
    const key = [...t].sort((x, y) => x - y).join(",");
    if (seen.has(key)) continue; // duplicate
    seen.add(key);
    keep.push(...t);
  }
  merged.setIndex(keep);
  return merged;
}

async function mergeParts(parts: Part[]): Promise<Part[]> {
  const { mergeGeometries } = await import("three/examples/jsm/utils/BufferGeometryUtils.js");
  const flat = parts.map((p) => (p.geometry.index ? p.geometry.toNonIndexed() : p.geometry));
  return [{ name: parts[0].name, geometry: mergeGeometries(flat)! }];
}

// ------------------------------------------------------------------------- writers

function write3mf(T: THREE, parts: Part[], strToU8: (s: string) => Uint8Array, zipSync: (f: Record<string, Uint8Array>) => Uint8Array): Uint8Array {
  const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);
  const objects: string[] = [];
  const items: string[] = [];
  parts.forEach((p, i) => {
    const g = p.geometry.index ? p.geometry : p.geometry.toNonIndexed();
    const pos = g.getAttribute("position");
    const index = g.index ? Array.from(g.index.array) : [...Array(pos.count).keys()];
    const v = new T.Vector3();
    const verts: string[] = [];
    for (let k = 0; k < pos.count; k++) {
      v.fromBufferAttribute(pos, k);
      verts.push(`<vertex x="${+v.x.toFixed(6)}" y="${+v.y.toFixed(6)}" z="${+v.z.toFixed(6)}"/>`);
    }
    const tris: string[] = [];
    for (let k = 0; k + 2 < index.length; k += 3) tris.push(`<triangle v1="${index[k]}" v2="${index[k + 1]}" v3="${index[k + 2]}"/>`);
    objects.push(`<object id="${i + 1}" type="model" name="${esc(p.name)}"><mesh><vertices>${verts.join("")}</vertices><triangles>${tris.join("")}</triangles></mesh></object>`);
    items.push(`<item objectid="${i + 1}"/>`);
  });
  return zipSync({
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>`),
    "_rels/.rels": strToU8(`<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>`),
    "3D/3dmodel.model": strToU8(`<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>${objects.join("")}</resources><build>${items.join("")}</build></model>`),
  });
}

/** Indexed float positions + uint32 triangles for a part. */
function indexed(T: THREE, g: Geometry): { pos: Float32Array; idx: Uint32Array } {
  const pos = g.getAttribute("position");
  const p = new Float32Array(pos.count * 3);
  const v = new T.Vector3();
  for (let k = 0; k < pos.count; k++) { v.fromBufferAttribute(pos, k); p.set([v.x, v.y, v.z], k * 3); }
  const idx = g.index ? Uint32Array.from(g.index.array) : Uint32Array.from({ length: pos.count }, (_, k) => k);
  return { pos: p, idx };
}

/** Binary little-endian PLY (vertices + triangles). */
function writePly(T: THREE, parts: Part[]): Uint8Array {
  const { pos, idx } = indexed(T, parts[0].geometry);
  const nv = pos.length / 3, nf = idx.length / 3;
  const header = new TextEncoder().encode(`ply\nformat binary_little_endian 1.0\ncomment Universal Edit (units: mm)\nelement vertex ${nv}\nproperty float x\nproperty float y\nproperty float z\nelement face ${nf}\nproperty list uchar uint vertex_indices\nend_header\n`);
  const out = new Uint8Array(header.length + nv * 12 + nf * 13);
  out.set(header);
  const dv = new DataView(out.buffer);
  let o = header.length;
  for (let k = 0; k < pos.length; k++, o += 4) dv.setFloat32(o, pos[k], true);
  for (let k = 0; k < idx.length; k += 3) {
    dv.setUint8(o, 3);
    dv.setUint32(o + 1, idx[k], true); dv.setUint32(o + 5, idx[k + 1], true); dv.setUint32(o + 9, idx[k + 2], true);
    o += 13;
  }
  return out;
}

/** Binary glTF 2.0 (one node + mesh per part), positions converted to metres. */
function writeGlb(T: THREE, parts: Part[]): Uint8Array {
  const chunks: Uint8Array[] = [];
  let length = 0;
  const views: object[] = [], accessors: object[] = [], meshes: object[] = [], nodes: object[] = [];
  const addView = (bytes: Uint8Array, target: number) => {
    views.push({ buffer: 0, byteOffset: length, byteLength: bytes.byteLength, target });
    chunks.push(bytes);
    length += bytes.byteLength; // both float32 and uint32 keep 4-byte alignment
    return views.length - 1;
  };
  parts.forEach((part, i) => {
    const { pos, idx } = indexed(T, part.geometry);
    for (let k = 0; k < pos.length; k++) pos[k] *= 0.001;
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let k = 0; k < pos.length; k++) { min[k % 3] = Math.min(min[k % 3], pos[k]); max[k % 3] = Math.max(max[k % 3], pos[k]); }
    accessors.push({ bufferView: addView(new Uint8Array(pos.buffer), 34962), componentType: 5126, count: pos.length / 3, type: "VEC3", min, max });
    accessors.push({ bufferView: addView(new Uint8Array(idx.buffer), 34963), componentType: 5125, count: idx.length, type: "SCALAR" });
    meshes.push({ name: part.name, primitives: [{ attributes: { POSITION: 2 * i }, indices: 2 * i + 1, mode: 4 }] });
    nodes.push({ name: part.name, mesh: i });
  });
  const json = { asset: { version: "2.0", generator: "Universal Edit" }, scene: 0, scenes: [{ nodes: nodes.map((_, i) => i) }], nodes, meshes, accessors, bufferViews: views, buffers: [{ byteLength: length }] };
  let jsonBytes = new TextEncoder().encode(JSON.stringify(json));
  const pad = (4 - (jsonBytes.length % 4)) % 4;
  if (pad) { const padded = new Uint8Array(jsonBytes.length + pad).fill(0x20); padded.set(jsonBytes); jsonBytes = padded; }
  const total = 12 + 8 + jsonBytes.length + 8 + length;
  const out = new Uint8Array(total);
  const dv = new DataView(out.buffer);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, jsonBytes.length, true); dv.setUint32(16, 0x4e4f534a, true);
  out.set(jsonBytes, 20);
  let o = 20 + jsonBytes.length;
  dv.setUint32(o, length, true); dv.setUint32(o + 4, 0x004e4942, true);
  o += 8;
  for (const c of chunks) { out.set(c, o); o += c.byteLength; }
  return out;
}

async function writeParts(T: THREE, parts: Part[], ext: string, o: Values): Promise<Uint8Array> {
  const group = new T.Group();
  for (const p of parts) {
    const mesh = new T.Mesh(p.geometry, new T.MeshStandardMaterial());
    mesh.name = p.name;
    group.add(mesh);
  }
  if (ext === "stl") {
    const { STLExporter } = await import("three/examples/jsm/exporters/STLExporter.js");
    const out = new STLExporter().parse(group, { binary: o.stl_format !== "ascii" });
    return typeof out === "string" ? new TextEncoder().encode(out) : new Uint8Array(out.buffer, out.byteOffset, out.byteLength).slice();
  }
  if (ext === "obj") {
    const { OBJExporter } = await import("three/examples/jsm/exporters/OBJExporter.js");
    return new TextEncoder().encode(new OBJExporter().parse(group));
  }
  if (ext === "ply") return writePly(T, parts);
  if (ext === "glb") return writeGlb(T, parts);
  const { strToU8, zipSync } = await import("fflate");
  return write3mf(T, parts, strToU8, zipSync);
}

export const modelConverter: Converter = {
  id: "model3d",
  family: "model",
  options: [
    opt.choice("units", "Source units", "auto", [["auto", "Automatic (mm for STL/OBJ/PLY/3MF)"], ["mm", "Millimetres"], ["cm", "Centimetres"], ["m", "Metres"], ["inch", "Inches"], ["ft", "Feet"]], { help: "STL, OBJ and PLY files don't store a unit. If your print comes out 25× too small, the model was made in inches." }),
    opt.float("scale_percent", "Scale", 100, { min: 0.1, max: 100000, decimals: 1, unit: "%" }),
    opt.bool("center_on_bed", "Centre on the print bed", true, { help: "Moves the model to the middle (X/Y = 0) and onto the bed (Z = 0)." }),
    opt.bool("repair", "Repair mesh", true, { help: "Merges duplicate points and removes broken or duplicate triangles." }),
    opt.bool("merge_parts", "Merge all parts into one object", false, { targets: ["3mf", "glb"] }),
    opt.choice("stl_format", "STL format", "binary", [["binary", "Binary (smaller)"], ["ascii", "ASCII (text)"]], { targets: ["stl"] }),
    opt.choice("detail", "Detail for STEP / IGES", "normal", [["coarse", "Coarse (small file)"], ["normal", "Normal"], ["fine", "Fine (smooth curves)"]], { help: "How finely curved CAD surfaces are turned into triangles." }),
  ],
  targets(files) {
    const single = files.length === 1 ? files[0].ext : "";
    return Object.entries(TARGETS).filter(([ext]) => ext !== single).map(([ext, label]) => ({ id: ext, label, ext, group: "3D model", converterId: "model3d" }));
  },
  async run(files, target, o, ctx) {
    const T = await import("three");
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    const ext = target.ext;
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name} (${i + 1}/${files.length})`);
      let parts = await loadParts(T, f, o, (m) => ctx.progress(i / files.length, m));
      if (["stl", "obj", "ply"].includes(ext) || o.merge_parts) parts = await mergeParts(parts);
      if (o.repair) for (const p of parts) p.geometry = await repair(T, p.geometry);
      if (o.center_on_bed) {
        const box = new T.Box3();
        for (const p of parts) { p.geometry.computeBoundingBox(); box.union(p.geometry.boundingBox!); }
        const dx = -(box.min.x + box.max.x) / 2, dy = -(box.min.y + box.max.y) / 2, dz = -box.min.z;
        for (const p of parts) p.geometry.translate(dx, dy, dz);
      }
      out.push({ name: unique(`${baseName(f.name)}.${ext}`), data: await writeParts(T, parts, ext, o), type: MIME[ext] });
    }
    ctx.progress(1, "Done");
    return out;
  },
};

// ------------------------------------------------------------------------- 3D viewer

/** Hidden target: the 3D viewer asks the worker for "mesh.bin" + "info.json" (never offered). */
export const VIEW_TARGET = { id: "view", label: "3D view", ext: "bin", group: "", converterId: "model3d-view" };

export const model3dViewConverter: Converter = {
  id: "model3d-view",
  family: "model",
  options: [],
  targets: () => [],
  async run(files, _target, _o, ctx) {
    const T = await import("three");
    const { meshStats, standOnBed } = await import("../editor3d/mesh");
    const f = files[0];
    const parts = await loadParts(T, f, defaults(modelConverter.options), (m) => ctx.progress(0.2, m));
    // triangles only: skip point clouds and parts without positions (desktop does the same)
    const flat = parts
      .map((p) => (p.geometry.index ? p.geometry.toNonIndexed() : p.geometry).getAttribute("position"))
      .filter((a) => a && a.count >= 3 && a.count % 3 === 0)
      .map((a) => a.array as ArrayLike<number>);
    const total = flat.reduce((n, a) => n + a.length, 0);
    const positions = new Float32Array(total);
    const starts: number[] = [];
    let at = 0;
    for (const a of flat) {
      if (!a.length) continue;
      starts.push(at / 9);
      positions.set(a, at);
      at += a.length;
    }
    if (!starts.length) throw new ConversionError(`“${f.name}” doesn't contain any triangles to show.`);
    ctx.progress(0.8, "Measuring…");
    standOnBed(positions);
    const stats = meshStats(positions, starts);
    return [
      { name: "mesh.bin", data: new Uint8Array(positions.buffer), type: "application/octet-stream" },
      { name: "info.json", data: new TextEncoder().encode(JSON.stringify(stats)), type: "application/json" },
    ];
  },
};
