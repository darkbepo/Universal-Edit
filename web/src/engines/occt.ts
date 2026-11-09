// OpenCASCADE (occt-import-js, LGPL-2.1) for STEP / IGES → triangle meshes. Loaded on first use.
import occtimportjs from "occt-import-js";
import wasmURL from "occt-import-js/dist/occt-import-js.wasm?url";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let ready: Promise<any> | null = null;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function getOcct(onStatus?: (msg: string) => void): Promise<any> {
  if (!ready) {
    onStatus?.("Loading CAD engine (first time only)…");
    ready = occtimportjs({ locateFile: () => wasmURL });
    ready.catch(() => (ready = null));
  }
  return ready;
}
