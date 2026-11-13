// Screenshots of the website for the README, each framed as a promo card.
//
//   python scripts/readme/make_samples.py SAMPLES packaging/bin/ffmpeg.exe
//   cd web && npm run build && npm run preview          (serves http://localhost:4173)
//   node ../scripts/readme/shoot.mjs SAMPLES [BASE_URL] [only-scene]
//
// Writes docs/images/<scene>.jpg. Uses the installed Microsoft Edge (no Playwright download).
// The video editor is desktop-only: scripts/readme/shoot_desktop.py makes its raw shot.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const require = createRequire(join(repo, "web", "package.json"));
const { chromium } = require("@playwright/test");

const [samples, base = "http://localhost:4173", only] = process.argv.slice(2);
const out = join(repo, "docs", "images");
const raw = join(out, "raw");
mkdirSync(raw, { recursive: true });
const s = (n) => join(samples, n);

const VIEW = { width: 1440, height: 860 };

const SCENES = {
  async start(page) {
    await page.goto(`${base}/?e2e`);
    await page.waitForTimeout(2500); // let the badges spread out
  },
  async convert(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles(
      ["lake-sunrise.jpg", "alpine-noon.jpg", "desert-dusk.jpg", "teal-evening.jpg"].map(s));
    await page.locator(".queue li").nth(3).waitFor();
    await page.locator('.pill[data-target="webp"]').click();
    await page.waitForTimeout(1500); // thumbnails
  },
  async image(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles([s("lake-sunrise.jpg")]);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".editor").waitFor();
    await page.waitForFunction(() => (window.__editor?.doc.layers.length ?? 0) === 1);
    await page.evaluate(() => window.__editor.floating.minimise("tool"));
    await page.locator(".tool-rail").getByRole("button", { name: /^Quick fix/ }).click();
    await page.evaluate(() => { for (const el of document.querySelectorAll(".tool-rail, .tool-rail *")) el.scrollTop = 0; });
    await page.waitForTimeout(800);
  },
  async document(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles([s("lake-trip.docx")]);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".doc-editor .ProseMirror").waitFor({ timeout: 120_000 });
    await page.waitForTimeout(1000);
  },
  async audio(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles([s("summer-tune.wav")]);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".audio-info").filter({ hasText: "kHz" }).waitFor({ timeout: 120_000 });
    await page.evaluate(() => {
      const a = window.__audio;
      a.doc.setSelection(Math.round(a.doc.frames * 0.38), Math.round(a.doc.frames * 0.56));
      a.update();
      a.draw();
    });
    await page.waitForTimeout(600);
  },
  async model(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles([s("torus-knot.stl")]);
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page.locator(".viewer3d-info").filter({ hasText: "Width (X)" }).waitFor({ timeout: 120_000 });
    await page.waitForTimeout(800);
  },
  async properties(page) {
    await page.goto(`${base}/?e2e`);
    await page.locator('input[type="file"]').first().setInputFiles([s("IMG_2048.jpg")]);
    await page.getByRole("button", { name: "Properties", exact: true }).click();
    await page.locator(".prop-card").first().waitFor({ timeout: 60_000 });
    await page.waitForTimeout(1500);
  },
};

/** Promo card text: [file, headline, subline, background colours]. */
export const CARDS = {
  start: ["Drop. Pick. Done.", "Hundreds of formats and five editors — and your files never leave your device.", ["#5b4bdb", "#1fa2c7"]],
  convert: ["Convert anything", "Drop files in, pick a format, press Convert. Hundreds of formats, sensible defaults.", ["#3a7bd5", "#00d2a8"]],
  image: ["Image editor", "Layers, selections, adjustments, filters, text, retouching and background removal.", ["#ee6b4d", "#c53d8c"]],
  document: ["Document editor", "Open Word, ODT, RTF, Markdown or HTML, edit, and save back or as PDF.", ["#2f6fdb", "#7a5af8"]],
  audio: ["Audio editor", "Cut, join, fade, amplify and normalise — then save as MP3, FLAC and more.", ["#0f9b8e", "#2c7be5"]],
  model: ["3D viewer", "STL, OBJ, 3MF, STEP and more: measure, check it's watertight, convert for printing.", ["#f2994a", "#d9466f"]],
  properties: ["Properties", "Everything about a file at a glance: size, codecs, camera, tags, pages.", ["#6a5acd", "#3aa0c9"]],
  video: ["Video editor", "Cut, join, add pictures, music, titles and transitions — export to MP4 and more.", ["#8e44ad", "#e2557a"]],
};

async function frame(browser, name) {
  const [title, sub, [c1, c2]] = CARDS[name];
  const shot = readFileSync(join(raw, `${name}.png`)).toString("base64");
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  await page.setContent(`<!doctype html><html><head><style>
    *{box-sizing:border-box;margin:0}
    body{width:1600px;font-family:"Segoe UI Variable Display","Segoe UI",system-ui,sans-serif;
      background:linear-gradient(135deg,${c1},${c2});display:flex;flex-direction:column;align-items:center;
      padding:56px 80px 72px;overflow:hidden;position:relative;color:#fff}
    body::before{content:"";position:absolute;inset:0;background:
      radial-gradient(circle at 12% 18%,rgba(255,255,255,.22),transparent 40%),
      radial-gradient(circle at 92% 85%,rgba(0,0,0,.18),transparent 45%)}
    h1{font-size:64px;font-weight:700;letter-spacing:-1px;position:relative}
    p{font-size:27px;opacity:.92;margin-top:10px;position:relative;text-align:center;max-width:1250px}
    .win{margin-top:44px;width:1440px;border-radius:14px;overflow:hidden;position:relative;
      box-shadow:0 30px 80px rgba(0,0,0,.35),0 0 0 1px rgba(255,255,255,.18)}
    .bar{height:34px;background:#1f2228;display:flex;align-items:center;gap:8px;padding:0 14px}
    .bar i{width:12px;height:12px;border-radius:50%;display:block}
    img{display:block;width:1440px}
  </style></head><body><h1>${title}</h1><p>${sub}</p>
  <div class="win"><div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i></div>
  <img src="data:image/png;base64,${shot}"></div></body></html>`);
  await page.waitForTimeout(300);
  await page.screenshot({ path: join(out, `${name}.jpg`), fullPage: true, type: "jpeg", quality: 88 });
  await page.close();
}

const browser = await chromium.launch({ channel: "msedge" });
for (const name of Object.keys(CARDS)) {
  if (only && name !== only) continue;
  if (SCENES[name]) {
    const ctx = await browser.newContext({ viewport: VIEW, colorScheme: "dark", deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    try {
      await SCENES[name](page);
      await page.screenshot({ path: join(raw, `${name}.png`) });
    } catch (e) {
      console.error(`${name}: ${e.message}`);
      await page.screenshot({ path: join(raw, `${name}-failed.png`) });
      await ctx.close();
      continue;
    }
    await ctx.close();
  }
  if (!existsSync(join(raw, `${name}.png`))) { console.warn(`${name}: no raw shot`); continue; }
  await frame(browser, name);
  console.log("framed", name);
}
await browser.close();
writeFileSync(join(raw, ".gitignore"), "*\n");
