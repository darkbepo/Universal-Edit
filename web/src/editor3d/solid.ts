// Logos joined into the model as one watertight body (boolean union) or cut into it
// (difference) with the manifold-3d wasm build (Apache-2.0); port of the desktop's
// umc/editor3d/solid.py. The engine is handed in (engines/manifold.ts loads it in a worker;
// tests load it in node). Meshes are triangle soups like ViewModel.positions. No DOM.
import type { ManifoldToplevel } from "manifold-3d";
import { weld } from "./meshcheck";

export class SolidError extends Error {}

type Engine = ManifoldToplevel;
type Solid = InstanceType<Engine["Manifold"]>;

function manifold(m: Engine, soup: ArrayLike<number>, what: string, keep: Solid[]): Solid {
  const w = weld(soup);
  if (!w.faces.length) throw new SolidError(`${what} has no triangles.`);
  const mesh = new m.Mesh({ numProp: 3, vertProperties: Float32Array.from(w.vertices), triVerts: Uint32Array.from(w.faces.flat()) });
  let solid: Solid;
  try {
    solid = new m.Manifold(mesh); // throws a ManifoldError ("Not manifold") on an open mesh
  } catch {
    throw new SolidError(`${what} isn't a closed solid.`);
  }
  keep.push(solid);
  if (solid.status() !== "NoError") throw new SolidError(`${what} isn't a closed solid.`);
  return solid;
}

/** The model with the raised parts joined on and the engraved ones cut out, as one closed
 *  triangle soup. Throws SolidError in plain words. */
export function combine(m: Engine, base: ArrayLike<number> | null, raised: ArrayLike<number>[], engraved: ArrayLike<number>[]): Float32Array {
  const keep: Solid[] = []; // wasm objects to free
  try {
    const solids: Solid[] = [];
    if (base) solids.push(manifold(m, base, "The model", keep));
    for (const p of raised) solids.push(manifold(m, p, "A logo", keep));
    if (!solids.length) throw new SolidError("There is nothing to cut the engraving into.");
    let body = solids.length > 1 ? m.Manifold.union(solids) : solids[0];
    if (body !== solids[0]) keep.push(body);
    if (engraved.length) {
      if (!base) throw new SolidError("There is nothing to cut the engraving into.");
      const cutters = engraved.map((p) => manifold(m, p, "An engraved logo", keep));
      body = m.Manifold.difference([body, ...cutters]);
      keep.push(body);
    }
    if (body.status() !== "NoError" || body.isEmpty()) throw new SolidError("Joining the parts failed.");
    const out = body.getMesh();
    const np = out.numProp, v = out.vertProperties, t = out.triVerts;
    const soup = new Float32Array(t.length * 3);
    for (let i = 0; i < t.length; i++) for (let k = 0; k < 3; k++) soup[i * 3 + k] = v[t[i] * np + k];
    return soup;
  } finally {
    for (const s of keep) s.delete();
  }
}
