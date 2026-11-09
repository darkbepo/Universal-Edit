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
