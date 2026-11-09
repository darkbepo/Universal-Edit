import { existsSync, readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { FIXTURES } from "./global-setup";

const fx = (name: string) => `${FIXTURES}${name}`;
const hasVideo = () => existsSync(fx("clip.mp4"));

async function load(page: Page, files: string[]) {
  await page.goto("./");
  await page.locator('input[type="file"]').first().setInputFiles(files);
}

async function convert(page: Page, targetId: string, timeout = 150_000) {
  await page.locator(`.pill[data-target="${targetId}"]`).click();
  await page.getByRole("button", { name: "Convert" }).click();
  await expect(page.locator(".status")).toHaveClass(/success|error/, { timeout });
  await expect(page.locator(".status")).toHaveClass(/success/);
}

async function downloadSingle(page: Page, targetId: string): Promise<Buffer> {
  const download = page.waitForEvent("download", { timeout: 150_000 });
  await convert(page, targetId);
  return readFileSync(await (await download).path());
}

test("page is cross-origin isolated (multi-threaded engines available)", async ({ page }) => {
  await page.goto("./");
  // On hosts without COOP/COEP headers (GitHub Pages) coi-serviceworker reloads the page once.
  await expect
    .poll(() => page.evaluate(() => crossOriginIsolated).catch(() => false), { timeout: 15_000 })
    .toBe(true);
  await expect(page.getByText("Files never leave your device")).toBeVisible();
});

test("png → jpg", async ({ page }) => {
  await load(page, [fx("img0.png")]);
  const data = await downloadSingle(page, "jpg");
  expect([...data.subarray(0, 3)]).toEqual([0xff, 0xd8, 0xff]);
});

test("png → avif with resize", async ({ page }) => {
  await load(page, [fx("img0.png")]);
  await page.locator(`.pill[data-target="avif"]`).click();
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.locator("#opt-resize").selectOption({ label: "Scale by percentage" });
  const data = await downloadSingle(page, "avif");
  expect(data.subarray(4, 12).toString("latin1")).toContain("ftypavif");
});

test.describe("camera RAW and Photoshop", () => {
  test.skip(!existsSync(fx("photo.dng")), "needs python3 to create the samples");

  test("DNG → JPG keeps the colours", async ({ page }) => {
    await load(page, [fx("photo.dng")]);
    const data = await downloadSingle(page, "jpg");
    const mupdf = await import("mupdf");
    const img = new mupdf.Image(new Uint8Array(data)).toPixmap();
    const px = (x: number, y: number) => [...img.getPixels().slice((y * img.getWidth() + x) * img.getNumberOfComponents(), (y * img.getWidth() + x) * img.getNumberOfComponents() + 3)];
    expect(img.getWidth()).toBe(64);
    const [r1, , b1] = px(5, 24);
    const [r2, , b2] = px(60, 24);
    expect(r1).toBeGreaterThan(200); expect(b1).toBeLessThan(80);
    expect(b2).toBeGreaterThan(200); expect(r2).toBeLessThan(80);
  });

  test("PSD → PNG uses the flattened image (not layers as frames)", async ({ page }) => {
    await load(page, [fx("design.psd")]);
    const data = await downloadSingle(page, "png");
    expect([...data.subarray(1, 4)].map((c) => String.fromCharCode(c)).join("")).toBe("PNG");
    expect(data.toString("latin1")).not.toContain("acTL"); // not an animated PNG
  });
});

test("images → PDF", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "images:pdf");
  expect(data.subarray(0, 4).toString()).toBe("%PDF");
  expect((data.toString("latin1").match(/\/Type\s*\/Page\b/g) ?? []).length).toBe(3);
});

test("images → animated GIF slideshow", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "slideshow:gif");
  expect(data.subarray(0, 6).toString()).toBe("GIF89a");
});

test("images → MP4 slideshow", async ({ page }) => {
  await load(page, [fx("img0.png"), fx("img1.png"), fx("img2.png")]);
  const data = await downloadSingle(page, "slideshow:mp4");
  expect(data.subarray(4, 8).toString()).toBe("ftyp");
});

test("PDF split into three documents", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await convert(page, "split:pdf");
  await expect(page.locator(".results li")).toHaveCount(3);
  await expect(page.locator(".results a").first()).toHaveText("three_split/three_page_1.pdf");
});

test("PDF pages → PNG images", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await convert(page, "pages:png");
  await expect(page.locator(".results li")).toHaveCount(3);
});

test("PDF → A4 with page range", async ({ page }) => {
  await load(page, [fx("three.pdf")]);
  await page.locator(`.pill[data-target="doc:pdf"]`).click();
  await page.getByRole("button", { name: /Settings/ }).click();
  await page.locator("#opt-page_range").fill("2-3");
  const data = await downloadSingle(page, "doc:pdf");
  expect((data.toString("latin1").match(/\/Type\s*\/Page\b/g) ?? []).length).toBe(2);
});

test("old .doc files explain they need the desktop app", async ({ page }) => {
  await load(page, [fx("letter.doc")]);
  await expect(page.locator(".note")).toContainText("desktop app");
  await expect(page.locator(".pill")).toHaveCount(0);
});

const pdfText = async (data: Buffer) => {
  const mupdf = await import("mupdf");
  const doc = mupdf.Document.openDocument(new Uint8Array(data), "application/pdf");
  return [...Array(doc.countPages()).keys()].map((i) => doc.loadPage(i).toStructuredText("").asText()).join("\n");
};

test.describe("data formats", () => {
  test("CSV → JSON with real numbers", async ({ page }) => {
    await load(page, [fx("data.csv")]);
    const json = JSON.parse((await downloadSingle(page, "data:json")).toString());
    expect(json).toEqual([{ name: "Ada", score: 10 }, { name: "Lin", score: 7 }]);
  });

  test("CSV → XLSX", async ({ page }) => {
    await load(page, [fx("data.csv")]);
    await expect(page.locator('.pill[data-target="data:xlsx"]')).toBeVisible();
    const data = await downloadSingle(page, "data:xlsx");
    expect(data.subarray(0, 2).toString()).toBe("PK");
  });
});

test.describe("target file size", () => {
  const MB = 1024 * 1024;
  const setTarget = async (page: Page, target: string, value: string) => {
    await page.locator(`.pill[data-target="${target}"]`).click();
    if (!(await page.locator(".form").isVisible())) await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator(target.startsWith("jpg") ? "#opt-target_size_kb" : "#opt-target_size_mb").fill(value);
  };

  test("image → JPG under 100 KB", async ({ page }) => {
    await load(page, [fx("noise.png")]);
    await setTarget(page, "jpg", "100");
    expect((await downloadSingle(page, "jpg")).length).toBeLessThanOrEqual(100 * 1024);
  });

  test("photo PDF → under 0.3 MB", async ({ page }) => {
    await load(page, [fx("photo.pdf")]);
    await setTarget(page, "pdf:compress", "0.3");
    expect((await downloadSingle(page, "pdf:compress")).length).toBeLessThanOrEqual(0.3 * MB);
  });

  test("video → MP4 under 1 MB (two-pass)", async ({ page }) => {
    test.skip(!existsSync(fx("noisy.mp4")), "needs ffmpeg");
    await load(page, [fx("noisy.mp4")]);
    await setTarget(page, "mp4", "1");
    await page.locator("#opt-preset").selectOption("ultrafast");
    const size = (await downloadSingle(page, "mp4")).length;
    expect(size).toBeLessThanOrEqual(1 * MB);
    expect(size).toBeGreaterThan(0.6 * MB);
  });

  test("audio → MP3 under its target", async ({ page }) => {
    test.skip(!existsSync(fx("tone.wav")), "needs ffmpeg");
    await load(page, [fx("tone.wav")]);
    await setTarget(page, "mp3", "0.03");
    expect((await downloadSingle(page, "mp3")).length).toBeLessThanOrEqual(0.03 * MB);
  });
});

test.describe("PDF tools", () => {
  const openPdf = async (data: Buffer) => (await import("mupdf")).Document.openDocument(new Uint8Array(data), "application/pdf").asPDF()!;
  const settings = async (page: Page, target: string) => {
    await page.locator(`.pill[data-target="${target}"]`).click();
    if (!(await page.locator(".form").isVisible())) await page.getByRole("button", { name: /Settings/ }).click();
  };

  test("merge a PDF and a Word file, with bookmarks", async ({ page }) => {
    await load(page, [fx("three.pdf"), fx("report.docx")]);
    const pdf = await openPdf(await downloadSingle(page, "pdf:merge"));
    expect(pdf.countPages()).toBe(4);
    expect(pdf.loadOutline()?.map((o) => o.title)).toEqual(["three", "report"]);
  });

  test("compress shrinks a photo PDF", async ({ page }) => {
    await load(page, [fx("photo.pdf")]);
    await settings(page, "pdf:compress");
    await page.locator("#opt-level").selectOption({ label: "Strong (images 110 DPI)" });
    const data = await downloadSingle(page, "pdf:compress");
    const before = (await import("node:fs")).statSync(fx("photo.pdf")).size;
    expect(data.length).toBeLessThan(before / 3);
    expect((await openPdf(data)).countPages()).toBe(1);
  });

  test("rotate pages 2 onwards", async ({ page }) => {
    await load(page, [fx("three.pdf")]);
    await settings(page, "pdf:rotate");
    await page.locator("#opt-rotate_pages").fill("2-");
    const pdf = await openPdf(await downloadSingle(page, "pdf:rotate"));
    const rot = (i: number) => { const r = pdf.loadPage(i).getObject().getInheritable("Rotate"); return r.isNumber() ? r.asNumber() : 0; };
    expect([rot(0), rot(1), rot(2)]).toEqual([0, 90, 90]);
  });

  test("reorder and delete pages", async ({ page }) => {
    await load(page, [fx("three.pdf")]);
    await settings(page, "pdf:organize");
    await page.locator("#opt-keep").fill("3, 1");
    const pdf = await openPdf(await downloadSingle(page, "pdf:organize"));
    expect([0, 1].map((i) => pdf.loadPage(i).toStructuredText("").asText().trim())).toEqual(["Page 3", "Page 1"]);
  });

  test("add a password, then remove it", async ({ page }, testInfo) => {
    await load(page, [fx("three.pdf")]);
    await settings(page, "pdf:protect");
    await page.locator("#opt-password").fill("s3cret");
    await page.locator("#opt-allow_copy").uncheck();
    const locked = await downloadSingle(page, "pdf:protect");
    const pdf = await openPdf(locked);
    expect(pdf.needsPassword()).toBe(true);
    expect(pdf.authenticatePassword("s3cret")).not.toBe(0);
    expect(pdf.hasPermission("copy")).toBe(false);

    const lockedPath = testInfo.outputPath("locked.pdf");
    (await import("node:fs")).writeFileSync(lockedPath, locked);
    await load(page, [lockedPath]);
    await settings(page, "pdf:unlock");
    await page.locator("#opt-password").fill("wrong");
    await page.locator('.pill[data-target="pdf:unlock"]').click();
    await page.getByRole("button", { name: "Convert" }).click();
    await expect(page.locator(".status")).toHaveClass(/error/, { timeout: 60_000 });
    await expect(page.locator(".status")).toContainText("password");

    await page.locator("#opt-password").fill("s3cret");
    const opened = await downloadSingle(page, "pdf:unlock");
    expect((await openPdf(opened)).needsPassword()).toBe(false);
  });
});

test.describe("documents", () => {
  test("tiny TXT → PDF (the owner's “eye effect.txt”)", async ({ page }) => {
    await load(page, [fx("eye effect.txt")]);
    const data = await downloadSingle(page, "doc:pdf");
    expect(data.subarray(0, 4).toString()).toBe("%PDF");
    expect(await pdfText(data)).toContain("Hi");
  });

  test("TXT → DOCX keeps special characters", async ({ page }) => {
    await load(page, [fx("notes.txt")]);
    const data = await downloadSingle(page, "doc:docx");
    expect(data.subarray(0, 2).toString()).toBe("PK");
  });

  test("Markdown → PDF with a table", async ({ page }) => {
    await load(page, [fx("doc.md")]);
    const text = await pdfText(await downloadSingle(page, "doc:pdf"));
    expect(text).toContain("Title ÆØÅ");
    expect(text).toContain("world");
  });

  test("DOCX → PDF", async ({ page }) => {
    await load(page, [fx("report.docx")]);
    const text = await pdfText(await downloadSingle(page, "doc:pdf"));
    expect(text).toContain("First paragraph with bold");
  });

  test("DOCX → Markdown", async ({ page }) => {
    await load(page, [fx("report.docx")]);
    const md = (await downloadSingle(page, "doc:md")).toString();
    expect(md).toContain("# Report");
    expect(md).toContain("**bold**");
  });

  test("Markdown → PowerPoint", async ({ page }) => {
    await load(page, [fx("doc.md")]);
    const data = await downloadSingle(page, "doc:pptx");
    expect(data.subarray(0, 2).toString()).toBe("PK");
  });

  test("CSV → Word table", async ({ page }) => {
    await load(page, [fx("data.csv")]);
    const data = await downloadSingle(page, "doc:docx");
    expect(data.subarray(0, 2).toString()).toBe("PK");
  });

  test("DOCX → page images", async ({ page }) => {
    await load(page, [fx("report.docx")]);
    await convert(page, "pages:png");
    await expect(page.locator(".results li")).toHaveCount(1);
  });

  test("PDF → DOCX", async ({ page }) => {
    await load(page, [fx("three.pdf")]);
    const data = await downloadSingle(page, "doc:docx");
    expect(data.subarray(0, 2).toString()).toBe("PK");
  });
});

test.describe("video", () => {
  test.skip(!hasVideo(), "needs ffmpeg to create the sample video");

  test("mp4 → webm", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "webm");
    expect([...data.subarray(0, 4)]).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
  });

  test("mp4 → mkv (H.264)", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "mkv");
    expect([...data.subarray(0, 4)]).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
  });

  test("mp4 → frames keeps 10 of every 30", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    await convert(page, "frames:jpg");
    await expect(page.locator(".results li")).toHaveCount(20); // 2 s × 30 fps × 10/30
  });

  test("mp4 → gif", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "video:gif");
    expect(data.subarray(0, 6).toString()).toBe("GIF89a");
  });

  test("mp4 → mp3", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    const data = await downloadSingle(page, "audio:mp3");
    expect(data.length).toBeGreaterThan(1000);
  });

  for (const [fmt, magic] of [["mp3", null], ["flac", "fLaC"], ["ogg", "OggS"], ["m4a", "ftyp"], ["aiff", "FORM"], ["wma", null]] as const) {
    test(`WAV → ${fmt.toUpperCase()}`, async ({ page }) => {
      await load(page, [fx("tone.wav")]);
      await expect(page.locator(".files-head")).toContainText("1 audio file");
      const data = await downloadSingle(page, fmt);
      expect(data.length).toBeGreaterThan(1000);
      if (magic) expect(data.toString("latin1", 0, 12)).toContain(magic);
    });
  }

  test("audio settings: mono, trimmed", async ({ page }) => {
    await load(page, [fx("tone.wav")]);
    await page.locator('.pill[data-target="wav"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-channels").selectOption({ label: "Mono" });
    await page.locator("#opt-end").fill("1");
    const data = await downloadSingle(page, "wav");
    expect(data.readUInt16LE(22)).toBe(1); // WAV header: channel count
    expect(data.length).toBeLessThan(150_000); // ~1 s of 16-bit mono 44.1/48 kHz
  });

  test("cancel stops a conversion", async ({ page }) => {
    await load(page, [fx("clip.mp4")]);
    await page.locator(`.pill[data-target="mkv"]`).click();
    await page.getByRole("button", { name: "Convert" }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator(".status")).toHaveText("Cancelled.");
    await expect(page.getByRole("button", { name: "Convert" })).toBeEnabled();
  });
});

test.describe("3D models", () => {
  const stlSize = (data: Buffer) => {
    // binary STL: 80-byte header, uint32 count, then 50 bytes per triangle
    const n = data.readUInt32LE(80);
    const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
    for (let t = 0; t < n; t++) for (let v = 0; v < 3; v++) for (let a = 0; a < 3; a++) {
      const x = data.readFloatLE(84 + t * 50 + 12 + v * 12 + a * 4);
      min[a] = Math.min(min[a], x); max[a] = Math.max(max[a], x);
    }
    return { triangles: n, size: max.map((m, a) => +(m - min[a]).toFixed(3)), minZ: +min[2].toFixed(3) };
  };

  for (const f of ["cube-10mm.stp", "cube-10mm.igs"]) {
    test(`${f.split(".")[1].toUpperCase()} → STL is 10 mm on the bed`, async ({ page }) => {
      await load(page, [fx(f)]);
      const m = stlSize(await downloadSingle(page, "stl"));
      expect(m.size).toEqual([10, 10, 10]);
      expect(m.minZ).toBe(0);
      expect(m.triangles).toBe(12);
    });
  }

  test("STEP → 3MF → GLB", async ({ page }) => {
    await load(page, [fx("cube-10mm.stp")]);
    const tmf = await downloadSingle(page, "3mf");
    expect(tmf.subarray(0, 2).toString()).toBe("PK");
    const { writeFileSync } = await import("node:fs");
    writeFileSync(fx("cube.3mf"), tmf);
    await load(page, [fx("cube.3mf")]);
    const glb = await downloadSingle(page, "glb");
    expect(glb.subarray(0, 4).toString()).toBe("glTF");
  });
});

test.describe("OCR", () => {
  test.beforeEach(async ({ context }) => {
    if (process.env.E2E_BASE_URL) return; // the live-site run uses the real download
    await context.route("https://raw.githubusercontent.com/tesseract-ocr/**", (route) =>
      route.fulfill({
        body: readFileSync(fx(route.request().url().split("/").pop()!)),
        headers: { "access-control-allow-origin": "*", "content-type": "application/octet-stream" },
      }));
  });
  const words = (s: string) => s.replace(/\s+/g, " ");

  test("scan PNG → text", async ({ page }) => {
    await load(page, [fx("scan.png")]);
    const text = words((await downloadSingle(page, "ocr:txt")).toString("utf8"));
    expect(text).toContain("INVOICE 2026");
    expect(text).toContain("Total due 42.50 EUR");
  });

  test("scanned PDF → searchable PDF keeps the text page", async ({ page }) => {
    await load(page, [fx("scanned.pdf")]);
    const data = await downloadSingle(page, "ocr:pdf");
    const mupdf = await import("mupdf");
    const doc = mupdf.Document.openDocument(new Uint8Array(data), "application/pdf");
    expect(doc.countPages()).toBe(2);
    const text = (i: number) => words(doc.loadPage(i).toStructuredText("").asText());
    expect(text(0)).toContain("INVOICE 2026");
    expect(text(1)).toContain("Real text page");
    const size = doc.loadPage(0).getBounds();
    expect(Math.round(size[2] - size[0])).toBe(420); // page size kept
  });

  test("two scans → one searchable PDF (Danish data downloaded and verified)", async ({ page }) => {
    await load(page, [fx("scan.png"), fx("img0.png")]);
    await page.locator('.pill[data-target="ocr:pdf"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-language").selectOption({ label: "Danish" });
    const data = await downloadSingle(page, "ocr:pdf");
    const mupdf = await import("mupdf");
    const doc = mupdf.Document.openDocument(new Uint8Array(data), "application/pdf");
    expect(doc.countPages()).toBe(2);
    expect(words(doc.loadPage(0).toStructuredText("").asText())).toContain("2026");
  });
});

test.describe("archives", () => {
  const unzip = async (data: Buffer) => {
    const { unzipSync, strFromU8 } = await import("fflate");
    const files = unzipSync(new Uint8Array(data));
    return { names: Object.keys(files).filter((n) => !n.endsWith("/")).sort(), text: (n: string) => strFromU8(files[n]) };
  };
  const extracted = async (page: Page) => {
    await convert(page, "archive:extract");
    return (await page.locator(".results a.fname").allTextContents()).sort();
  };

  test("ZIP → TAR.XZ → ZIP keeps every file", async ({ page }) => {
    await load(page, [fx("bundle.zip")]);
    const xz = await downloadSingle(page, "tar.xz");
    expect([...xz.subarray(0, 6)]).toEqual([0xfd, 0x37, 0x7a, 0x58, 0x5a, 0x00]);
    const { writeFileSync } = await import("node:fs");
    writeFileSync(fx("bundle.tar.xz"), xz);
    await load(page, [fx("bundle.tar.xz")]);
    const z = await unzip(await downloadSingle(page, "zip"));
    expect(z.names).toEqual(["readme.txt", "sub/deep/data.bin"]);
    expect(z.text("readme.txt")).toBe("hello ÆØÅ\n");
  });

  test("RAR → ZIP", async ({ page }) => {
    await load(page, [fx("sample-rar5.rar")]);
    await expect(page.locator('.pill[data-target="rar"]')).toHaveCount(0); // RAR can't be written
    expect((await unzip(await downloadSingle(page, "zip"))).names).toEqual(["test.bin"]);
  });

  test("7Z → extract", async ({ page }) => {
    await load(page, [fx("sample.7z")]);
    expect(await extracted(page)).toEqual(["sample/notes/hello.txt", "sample/second.txt"]);
  });

  test("password-protected ZIP: asks, then opens with the password", async ({ page }) => {
    await load(page, [fx("sample-aes.zip")]);
    await page.locator('.pill[data-target="archive:extract"]').click();
    await page.getByRole("button", { name: "Convert" }).click();
    await expect(page.locator(".status")).toHaveClass(/error/, { timeout: 60_000 });
    await expect(page.locator(".status")).toContainText("password");
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-source_password").fill("s3cret");
    const data = await downloadSingle(page, "archive:extract");
    expect(data.toString("utf8")).toBe("top secret ÆØÅ\n");
  });

  test("mixed files → one ZIP; incompressible data → TAR.GZ", async ({ page }) => {
    await load(page, [fx("img0.png"), fx("notes.txt"), fx("scan.png")]);
    await expect(page.locator(".pill")).toHaveCount(2); // pack:zip, pack:tar.gz only
    expect((await unzip(await downloadSingle(page, "pack:zip"))).names).toEqual(["img0.png", "notes.txt", "scan.png"]);
    await load(page, [fx("noise.png")]);
    const gz = await downloadSingle(page, "pack:tar.gz");
    expect([...gz.subarray(0, 2)]).toEqual([0x1f, 0x8b]);
    expect(gz.length).toBeGreaterThan(readFileSync(fx("noise.png")).length);
  });
});

test.describe("subtitles", () => {
  test("old Windows-1252 SRT → VTT, shifted", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg"); // the fixtures' generator also needs it for video
    await load(page, [fx("old.srt")]);
    await page.locator('.pill[data-target="vtt"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-shift").fill("2.5");
    const vtt = (await downloadSingle(page, "vtt")).toString("utf8");
    expect(vtt.startsWith("WEBVTT")).toBe(true);
    expect(vtt).toContain("Æble");
    expect(vtt).toContain("02.700 --> 00:04.000");
  });

  test("video + SRT → MKV track → extract it again", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg");
    await load(page, [fx("clip.mp4"), fx("movie.da.srt")]);
    await expect(page.locator('.pill[data-target="burn:mp4"]')).toHaveCount(1);
    const mkv = await downloadSingle(page, "subtrack:mkv");
    expect([...mkv.subarray(0, 4)]).toEqual([0x1a, 0x45, 0xdf, 0xa3]);
    const { writeFileSync } = await import("node:fs");
    writeFileSync(fx("with-subs.mkv"), mkv);
    await load(page, [fx("with-subs.mkv")]);
    const srt = await downloadSingle(page, "subs:srt");
    expect(srt.toString("utf8")).toContain("Hej Æble ØL Å");
    await expect(page.locator(".results a.fname")).toHaveText("with-subs.dan.srt");
  });

  test("burn into video draws on the picture", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg");
    await load(page, [fx("clip.mp4"), fx("movie.da.srt")]);
    await page.locator('.pill[data-target="burn:mp4"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-font_size").fill("80");
    const mp4 = await downloadSingle(page, "burn:mp4");
    const { writeFileSync } = await import("node:fs");
    const { execFileSync } = await import("node:child_process");
    writeFileSync(fx("burned.mp4"), mp4);
    // one grey frame at 1 s from each, compared pixel by pixel (320×240)
    const grab = (file: string) => execFileSync("ffmpeg", ["-loglevel", "error", "-ss", "1", "-i", fx(file), "-frames:v", "1", "-s", "320x240", "-pix_fmt", "gray", "-f", "rawvideo", "-"]);
    const [a, b] = [grab("clip.mp4"), grab("burned.mp4")];
    let top = 0, bottom = 0;
    for (let i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > 60) (i < 320 * 80 ? top++ : i >= 320 * 120 ? bottom++ : 0);
    expect(bottom).toBeGreaterThan(300);
    expect(top).toBeLessThan(50);
  });
});

test.describe("fonts", () => {
  /** Load the font in the page: Chrome runs every web font through its strict OTS sanitizer. */
  const loads = (page: Page, data: Buffer) =>
    page.evaluate(async (b64) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      const face = new FontFace("check", bytes);
      await face.load();
      return face.status;
    }, data.toString("base64"));

  test("TTF → WOFF2 → WOFF → TTF", async ({ page }) => {
    const { writeFileSync } = await import("node:fs");
    await load(page, [fx("DejaVuSans.ttf")]);
    await expect(page.locator('.pill[data-target="otf"]')).toHaveCount(0); // outline change: desktop only
    const woff2 = await downloadSingle(page, "woff2");
    expect(woff2.subarray(0, 4).toString()).toBe("wOF2");
    expect(woff2.length).toBeLessThan(300_000);
    expect(await loads(page, woff2)).toBe("loaded");
    writeFileSync(fx("DejaVuSans.woff2"), woff2);
    await load(page, [fx("DejaVuSans.woff2")]);
    const woff = await downloadSingle(page, "woff");
    expect(woff.subarray(0, 4).toString()).toBe("wOFF");
    writeFileSync(fx("DejaVuSans.woff"), woff);
    await load(page, [fx("DejaVuSans.woff")]);
    const ttf = await downloadSingle(page, "ttf");
    expect([...ttf.subarray(0, 4)]).toEqual([0, 1, 0, 0]);
    expect(Math.abs(ttf.length - readFileSync(fx("DejaVuSans.ttf")).length)).toBeLessThan(2000);
    expect(await loads(page, ttf)).toBe("loaded");
  });

  test("subset to Latin and to typed characters", async ({ page }) => {
    await load(page, [fx("DejaVuSans.ttf")]);
    await page.locator('.pill[data-target="woff2"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-subset").selectOption({ label: "Latin (English, Western Europe)" });
    const latin = await downloadSingle(page, "woff2");
    expect(latin.length).toBeLessThan(40_000);
    expect(await loads(page, latin)).toBe("loaded");
    await page.locator("#opt-subset").selectOption({ label: "Only the characters I type…" });
    await page.locator("#opt-custom_text").fill("Hej ÆØÅ");
    const tiny = await downloadSingle(page, "woff2");
    expect(tiny.length).toBeLessThan(latin.length);
    expect(await loads(page, tiny)).toBe("loaded");
  });

  test("WOFF2 with TrueType outlines → OTF explains it needs the desktop app", async ({ page }) => {
    await load(page, [fx("DejaVuSans.woff2")]);
    await page.locator('.pill[data-target="otf"]').click();
    await page.getByRole("button", { name: "Convert" }).click();
    await expect(page.locator(".status")).toHaveClass(/error/, { timeout: 60_000 });
    await expect(page.locator(".status")).toContainText("desktop app");
  });
});

test.describe("vector", () => {
  /** Decode an image (PNG/JPG/SVG…) in the page; return its size and the RGBA at relative points. */
  const inspect = (page: Page, data: Buffer, type: string, points: [number, number][]) =>
    page.evaluate(async ({ b64, type, points }) => {
      const blob = new Blob([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], { type });
      const img = new Image();
      img.src = URL.createObjectURL(blob);
      await img.decode();
      const w = img.naturalWidth || 300, h = img.naturalHeight || 200;
      const canvas = new OffscreenCanvas(w, h);
      const g = canvas.getContext("2d")!;
      g.drawImage(img, 0, 0, w, h);
      return { w, h, px: points.map(([x, y]) => [...g.getImageData(Math.floor(x * w), Math.floor(y * h), 1, 1).data]) };
    }, { b64: data.toString("base64"), type, points });

  test("SVG → PNG keeps size, colours and transparency", async ({ page }) => {
    await load(page, [fx("logo.svg")]);
    const png = await downloadSingle(page, "vec:png");
    const r = await inspect(page, png, "image/png", [[0.25, 0.5], [0.75, 0.5], [0.98, 0.02]]);
    expect([r.w, r.h]).toEqual([200, 100]);
    expect(r.px[0].slice(0, 3)).toEqual([255, 0, 0]);
    expect(r.px[1].slice(0, 3)).toEqual([0, 0, 255]);
    expect(r.px[2][3]).toBe(0);
    await expect(page.locator('.pill[data-target="vec:pdf"]')).toHaveCount(0); // desktop only
  });

  test("Illustrator → PDF stays vector", async ({ page }) => {
    await load(page, [fx("poster.ai")]);
    const pdf = await downloadSingle(page, "vec:pdf");
    const mupdf = await import("mupdf");
    const doc = mupdf.Document.openDocument(new Uint8Array(pdf), "application/pdf");
    expect(Math.round(doc.loadPage(0).getBounds()[2])).toBe(300);
    expect(doc.loadPage(0).toStructuredText("").asText()).toContain("Illustrator");
  });

  test("Illustrator → SVG and JPG at a chosen width", async ({ page }) => {
    await load(page, [fx("poster.ai")]);
    const svg = (await downloadSingle(page, "svg")).toString("utf8");
    expect(svg).toContain("<svg");
    expect(svg).toContain("<path");
    await page.locator('.pill[data-target="vec:jpg"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-width_px").fill("600");
    const jpg = await downloadSingle(page, "vec:jpg");
    const r = await inspect(page, jpg, "image/jpeg", [[0.2, 0.5]]);
    expect([r.w, r.h]).toEqual([600, 300]);
    expect(r.px[0][1]).toBeGreaterThan(120); // green square
  });

  test("PDF pages → SVG", async ({ page }) => {
    await load(page, [fx("three.pdf")]);
    await convert(page, "pages:svg");
    expect(await page.locator(".results a.fname").allTextContents()).toEqual(["three_1.svg", "three_2.svg", "three_3.svg"]);
  });

  for (const mode of ["color", "binary"]) {
    test(`trace a picture to SVG (${mode})`, async ({ page }) => {
      await load(page, [fx("shapes.png")]);
      await page.locator('.pill[data-target="trace:svg"]').click();
      await page.getByRole("button", { name: /Settings/ }).click();
      await page.locator("#opt-colour_mode").selectOption({ label: mode === "color" ? "Full colour" : "Black & white" });
      const svg = await downloadSingle(page, "trace:svg");
      expect(svg.toString("utf8")).toContain("<path");
      const r = await inspect(page, svg, "image/svg+xml", [[70 / 300, 0.5], [215 / 300, 105 / 200], [0.02, 0.02]]);
      expect([r.w, r.h]).toEqual([300, 200]);
      if (mode === "color") {
        expect(r.px[0].slice(0, 3)).toEqual([255, 0, 0]);
        expect(r.px[1].slice(0, 3)).toEqual([0, 0, 128]);
      } else {
        expect(r.px[0].slice(0, 3)).toEqual([0, 0, 0]); // red is dark enough to become black
      }
    });
  }

  test("EPS explains it needs the desktop app", async ({ page }) => {
    await load(page, [fx("art.eps")]);
    await expect(page.getByText("need Ghostscript")).toBeVisible();
  });
});

test.describe("CAD", () => {
  test("DXF → SVG keeps layer colours; → PNG on a dark background", async ({ page }) => {
    await load(page, [fx("plan.dxf")]);
    await page.locator('.pill[data-target="cad:svg"]').click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-colours").selectOption({ label: "Layer colours on white" });
    const svg = (await downloadSingle(page, "cad:svg")).toString("utf8");
    expect(svg).toContain("rgb(255, 0, 0)");
    expect(svg).toContain("rgb(0, 0, 255)");
    await page.locator('.pill[data-target="cad:png"]').click();
    await page.locator("#opt-colours").selectOption({ label: "Layer colours on black (like the CAD screen)" });
    await page.locator("#opt-width_px").fill("800");
    const png = await downloadSingle(page, "cad:png");
    const r = await page.evaluate(async (b64) => {
      const img = await createImageBitmap(new Blob([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))]));
      const g = new OffscreenCanvas(img.width, img.height).getContext("2d")!;
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, img.width, img.height).data;
      let red = 0, blue = 0;
      for (let i = 0; i < d.length; i += 4) {
        if (d[i] > 180 && d[i + 1] < 80 && d[i + 2] < 80) red++;
        if (d[i + 2] > 180 && d[i] < 80) blue++;
      }
      return { w: img.width, corner: [...g.getImageData(1, 1, 1, 1).data], red, blue };
    }, png.toString("base64"));
    expect(r.w).toBe(800);
    expect(r.corner.slice(0, 3)).toEqual([0, 0, 0]);
    expect(r.red).toBeGreaterThan(100);
    expect(r.blue).toBeGreaterThan(100);
  });

  test("DWG explains it needs the desktop app", async ({ page }) => {
    await load(page, [fx("house.dwg")]);
    await expect(page.getByText("need LibreDWG")).toBeVisible();
  });
});

test.describe("WebCodecs video (VP9 / AV1)", () => {
  const probe = async (data: Buffer, name: string) => {
    const { writeFileSync } = await import("node:fs");
    const { execFileSync } = await import("node:child_process");
    writeFileSync(fx(name), data);
    return JSON.parse(execFileSync("ffprobe", ["-v", "error", "-print_format", "json", "-show_streams", fx(name)]).toString()).streams as { codec_type: string; codec_name: string; height?: number }[];
  };
  const pickCodec = async (page: Page, target: string, label: string) => {
    await page.locator(`.pill[data-target="${target}"]`).click();
    await page.getByRole("button", { name: /Settings/ }).click();
    await page.locator("#opt-video_codec").selectOption({ label });
  };

  test("MP4 → WebM with VP9", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg");
    await load(page, [fx("clip.mp4")]);
    await pickCodec(page, "webm", "VP9 (browser encoder)");
    const streams = await probe(await downloadSingle(page, "webm"), "out-vp9.webm");
    expect(streams.find((s) => s.codec_type === "video")?.codec_name).toBe("vp9");
    expect(streams.find((s) => s.codec_type === "audio")?.codec_name).toBe("opus");
  });

  test("MP4 → MKV with AV1 at 360p", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg");
    await load(page, [fx("clip.mp4")]);
    await pickCodec(page, "mkv", "AV1 (browser encoder, slow)");
    await page.locator("#opt-resolution").selectOption({ label: "360p" });
    const video = (await probe(await downloadSingle(page, "mkv"), "out-av1.mkv")).find((s) => s.codec_type === "video");
    expect(video?.codec_name).toBe("av1");
    expect(video?.height).toBe(360);
  });

  test("AVI (not readable by WebCodecs) → WebM VP9 via the FFmpeg fallback", async ({ page }) => {
    test.skip(!hasVideo(), "needs ffmpeg");
    const { execFileSync } = await import("node:child_process");
    execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", fx("clip.mp4"), "-c:v", "mpeg4", "-c:a", "mp3", fx("clip.avi")]);
    await load(page, [fx("clip.avi")]);
    await pickCodec(page, "webm", "VP9 (browser encoder)");
    const streams = await probe(await downloadSingle(page, "webm"), "out-avi-vp9.webm");
    expect(streams.find((s) => s.codec_type === "video")?.codec_name).toBe("vp9");
  });
});
