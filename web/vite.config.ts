/// <reference types="vitest/config" />
import { defineConfig } from "vite";

// Cross-origin isolation enables SharedArrayBuffer, which multi-threaded FFmpeg needs.
// The same headers are set for production in public/_headers (Netlify / Cloudflare Pages).
const isolation = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
};

export default defineConfig({
  base: "./",
  server: { headers: isolation },
  preview: { headers: isolation },
  optimizeDeps: { exclude: ["@ffmpeg/ffmpeg", "@ffmpeg/util"] },
  worker: { format: "es" },
  // Never inline assets as data: URLs — FFmpeg's thread workers loaded from a data: URL have an
  // opaque origin and can't fetch the engine, which crashes every video conversion.
  build: { target: "es2022", chunkSizeWarningLimit: 4000, assetsInlineLimit: 0 },
  test: { include: ["tests/**/*.test.ts"], environment: "node" },
});
