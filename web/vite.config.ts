/// <reference types="vitest/config" />
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

// coi-serviceworker (MIT) adds the isolation headers itself on hosts that can't set them
// (GitHub Pages, plain static servers). It does nothing when the server already sends them.
const coiSource = () =>
  readFileSync(fileURLToPath(new URL("./node_modules/coi-serviceworker/coi-serviceworker.min.js", import.meta.url)), "utf8");

function coiServiceWorker(): Plugin {
  return {
    name: "coi-serviceworker",
    configureServer(server) {
      server.middlewares.use("/coi-serviceworker.js", (_req, res) => {
        res.setHeader("Content-Type", "text/javascript");
        res.end(coiSource());
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "coi-serviceworker.js", source: coiSource() });
    },
  };
}

// libarchive.js (MIT) runs in its own worker, which loads libarchive.wasm from next to itself — so
// both are emitted under fixed names in assets/libarchive/. Its writer sizes the output buffer as
// "input + 128 bytes", which fails for files that don't compress; give it real headroom instead.
const LIBARCHIVE = "./node_modules/libarchive.js/dist/";
const BUFFER_SIZE = "e+r.size+128),0)+128;";
function libarchiveFile(name: string): string | Buffer {
  const path = fileURLToPath(new URL(LIBARCHIVE + name, import.meta.url));
  if (!name.endsWith(".js")) return readFileSync(path);
  const src = readFileSync(path, "utf8");
  if (!src.includes(BUFFER_SIZE)) throw new Error("libarchive.js changed: re-check the output buffer patch in vite.config.ts");
  return src.replace(BUFFER_SIZE, "e+r.size+r.size/8+1024),0)+65536;");
}
function libarchiveAssets(): Plugin {
  const files = ["worker-bundle.js", "libarchive.wasm"];
  return {
    name: "libarchive-assets",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const name = files.find((f) => req.url?.split("?")[0].endsWith(`/libarchive/${f}`));
        if (!name) return next();
        res.setHeader("Content-Type", name.endsWith(".js") ? "text/javascript" : "application/wasm");
        res.end(libarchiveFile(name));
      });
    },
    generateBundle() {
      for (const f of files) this.emitFile({ type: "asset", fileName: `assets/libarchive/${f}`, source: libarchiveFile(f) });
    },
  };
}

// Cross-origin isolation enables SharedArrayBuffer, which multi-threaded FFmpeg needs.
// The same headers are set for production in public/_headers (Netlify / Cloudflare Pages).
const isolation = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  base: "./",
  plugins: [coiServiceWorker(), libarchiveAssets()],
  resolve: {
    alias: [
      // vectortracer (MIT) is built for bundlers that import .wasm as ES modules; we instantiate it ourselves
      { find: /^vectortracer-bg$/, replacement: fileURLToPath(new URL("./node_modules/vectortracer/pkg/vectortracer_bg.js", import.meta.url)) },
      { find: /^vectortracer-wasm/, replacement: fileURLToPath(new URL("./node_modules/vectortracer/pkg/vectortracer_bg.wasm", import.meta.url)) },
      { find: /^pandoc-wasm-core$/, replacement: fileURLToPath(new URL("./node_modules/pandoc-wasm/src/core.js", import.meta.url)) },
      { find: /^pandoc-wasm-binary/, replacement: fileURLToPath(new URL("./node_modules/pandoc-wasm/src/pandoc.wasm", import.meta.url)) },
    ],
  },
  server: { headers: isolation },
  preview: { headers: isolation },
  optimizeDeps: { exclude: ["@ffmpeg/ffmpeg", "@ffmpeg/util"] },
  worker: { format: "es" },
  // Never inline assets as data: URLs — FFmpeg's thread workers loaded from a data: URL have an
  // opaque origin and can't fetch the engine, which crashes every video conversion.
  build: { target: "es2022", chunkSizeWarningLimit: 4000, assetsInlineLimit: 0 },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
