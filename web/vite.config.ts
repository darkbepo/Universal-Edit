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

// Cross-origin isolation enables SharedArrayBuffer, which multi-threaded FFmpeg needs.
// The same headers are set for production in public/_headers (Netlify / Cloudflare Pages).
const isolation = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  base: "./",
  plugins: [coiServiceWorker()],
  resolve: {
    alias: [
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
