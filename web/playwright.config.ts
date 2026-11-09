import { defineConfig, devices } from "@playwright/test";

// E2E_BASE_URL runs the suite against an already running server (e.g. the local launcher).
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 180_000,
  fullyParallel: false,
  workers: 1,
  // Against a live site the 10–30 MB engines come over the network (GitHub Pages' CDN can stall
  // right after a deploy), so allow one retry there. Local/CI runs never retry.
  retries: external?.startsWith("https://") ? 1 : 0,
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: external ?? "http://localhost:4173", acceptDownloads: true, trace: "off" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: external ? undefined : {
    command: "npm run build && npm run preview",
    url: "http://localhost:4173",
    reuseExistingServer: true,
    timeout: 180_000,
  },
});
