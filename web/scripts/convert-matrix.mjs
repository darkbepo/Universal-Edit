// Try every conversion the website offers for a folder of samples, with default settings.
//
//   node scripts/convert-matrix.mjs <samples-dir> [--url http://localhost:4173/] [--channel msedge]
//                                   [--only name,...] [--timeout 180]
//
// One sample per file in <samples-dir> (the desktop's scripts/convert_matrix.py writes a good set);
// a few multi-file sets are added (images → slideshow, video + subtitles, mixed files). Each
// conversion runs on a freshly loaded page so a crash or hang only loses that one. Writes
// web-matrix.json and web-matrix.md next to the samples folder.
import { readdirSync, statSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { chromium } from "@playwright/test";

const args = process.argv.slice(2);
const dir = args[0];
const flag = (name, dflt) => { const i = args.indexOf(`--${name}`); return i >= 0 ? args[i + 1] : dflt; };
const url = flag("url", "http://localhost:4173/");
const channel = flag("channel", undefined);
const only = (flag("only", "") || "").split(",").filter(Boolean);
const timeout = Number(flag("timeout", "180")) * 1000;

const files = readdirSync(dir).filter((f) => statSync(join(dir, f)).isFile()).sort();
const sets = files.filter((f) => !/^slide\d/.test(f)).map((f) => [f, [join(dir, f)]]);
const slides = files.filter((f) => /^slide\d/.test(f)).map((f) => join(dir, f));
if (slides.length) sets.push(["5 images", slides]);
if (files.includes("video.mp4") && files.includes("subs.srt")) sets.push(["video.mp4 + subs.srt", [join(dir, "video.mp4"), join(dir, "subs.srt")]]);
sets.push(["mixed: image + pdf + txt", ["image.png", "three.pdf", "notes.txt"].map((f) => join(dir, f))]);
const chosen = only.length ? sets.filter(([n]) => only.some((o) => n.includes(o))) : sets;

const browser = await chromium.launch({ channel, headless: true });
const results = [];

async function open(paths) {
  const ctx = await browser.newContext({ acceptDownloads: true });
  const page = await ctx.newPage();
  await page.goto(url);
  await page.waitForFunction(() => crossOriginIsolated, null, { timeout: 30000 }).catch(() => {});
  await page.locator('input[type="file"]').first().setInputFiles(paths);
  await page.waitForTimeout(300);
  return { ctx, page };
}

for (const [name, paths] of chosen) {
  const probe = await open(paths);
  const targets = await probe.page.locator(".pill").evaluateAll((els) => els.map((e) => e.dataset.target));
  const note = (await probe.page.locator(".note").textContent().catch(() => ""))?.trim();
  await probe.ctx.close();
  if (!targets.length) {
    results.push({ sample: name, target: "(none)", status: "no-targets", seconds: 0, message: note || "no formats offered" });
    console.log(`X ${name.padEnd(28)} no targets  ${note}`);
    continue;
  }
  for (const target of targets) {
    const { ctx, page } = await open(paths);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const t0 = Date.now();
    let status = "timeout";
    let message = "";
    try {
      await page.locator(`.pill[data-target="${target}"]`).click();
      await page.getByRole("button", { name: "Convert", exact: true }).click();
      await page.locator(".status.success, .status.error").waitFor({ timeout });
      message = (await page.locator(".status").textContent())?.trim() ?? "";
      status = (await page.locator(".status.success").count()) ? "ok" : /desktop app|needs the desktop|isn.t available in the browser/i.test(message) ? "desktop-only" : "fail";
      if (status === "ok") {
        const sizes = await page.locator(".results .fsize").allTextContents();
        if (!sizes.length) { status = "bad-output"; message = "no result files listed"; }
        else if (sizes.some((s) => s.trim() === "0 B")) { status = "bad-output"; message = `empty output (${sizes.join(", ")})`; }
      }
    } catch (e) {
      message = `${(await page.locator(".status").textContent().catch(() => ""))?.trim()} ${errors.join(" ")}`.trim();
    }
    const secs = (Date.now() - t0) / 1000;
    results.push({ sample: name, target, status, seconds: Math.round(secs * 10) / 10, message });
    console.log(`${status === "ok" ? "." : "X"} ${name.padEnd(28)} → ${target.padEnd(22)} ${status.padEnd(12)} ${secs.toFixed(1).padStart(6)}s ${status === "ok" ? "" : message.slice(0, 120)}`);
    await ctx.close();
  }
}
await browser.close();

const out = dirname(dir.replace(/[\\/]+$/, ""));
writeFileSync(join(out, "web-matrix.json"), JSON.stringify(results, null, 1));
const counts = {};
for (const r of results) counts[r.status] = (counts[r.status] ?? 0) + 1;
const summary = Object.entries(counts).sort().map(([k, v]) => `${k}: ${v}`).join(", ");
const rows = results.filter((r) => r.status !== "ok").map((r) => `| ${r.sample} | ${r.target} | ${r.status} | ${r.seconds} s | ${r.message.replace(/\|/g, "/")} |`);
writeFileSync(join(out, "web-matrix.md"), ["# Website conversion matrix", "", summary, "", "| Sample | Target | Result | Time | Message |", "|---|---|---|---|---|", ...rows, ""].join("\n"));
console.log(`\n${summary}`);
