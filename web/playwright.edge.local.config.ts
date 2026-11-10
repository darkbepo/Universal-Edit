// Local-only: run the e2e suite in the installed Microsoft Edge (no Playwright browser download).
import base from "./playwright.config";

export default {
  ...base,
  projects: [{ name: "msedge", use: { ...base.projects![0].use, channel: "msedge" } }],
};
