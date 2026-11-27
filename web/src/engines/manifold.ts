// manifold-3d (Apache-2.0): boolean union / difference for merging 3D logos into a model.
// Loaded on first use (a worker only: the page never needs it).
import Module, { type ManifoldToplevel } from "manifold-3d";
import wasmURL from "manifold-3d/manifold.wasm?url";

let ready: Promise<ManifoldToplevel> | null = null;

export function getManifold(): Promise<ManifoldToplevel> {
  if (!ready) {
    ready = Module({ locateFile: () => wasmURL }).then((m) => {
      m.setup();
      return m;
    });
    ready.catch(() => (ready = null));
  }
  return ready;
}
