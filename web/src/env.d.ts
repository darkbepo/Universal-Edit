// Aliases defined in vite.config.ts (pandoc-wasm only exports its browser entry, which
// imports the .wasm in a way Vite can't bundle, so we wire core.js + the binary ourselves).
declare module "pandoc-wasm-core" {
  export function createPandocInstance(wasm: ArrayBuffer): Promise<{
    convert(
      options: Record<string, unknown>,
      stdin: string | null,
      files: Record<string, string | Blob>,
    ): Promise<{ stdout: string; stderr: string; files: Record<string, string | Blob>; mediaFiles: Record<string, Blob> }>;
    query(q: Record<string, unknown>): Promise<unknown>;
  }>;
}
declare module "pandoc-wasm-binary?url" {
  const url: string;
  export default url;
}
declare module "occt-import-js" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const init: (opts?: { locateFile?: (path: string) => string }) => Promise<any>;
  export default init;
}
declare module "vectortracer-bg" {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  export function __wbg_set_wasm(exports: any): void;
  export class BinaryImageConverter {
    constructor(image: ImageData, params: Record<string, unknown>, options: Record<string, unknown>);
    init(): void;
    tick(): boolean;
    getResult(): string;
    free(): void;
  }
}
declare module "vectortracer-wasm?url" {
  const url: string;
  export default url;
}
