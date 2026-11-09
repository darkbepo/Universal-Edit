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
