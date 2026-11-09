// Generates small sample files (no binaries in git). Video needs a system ffmpeg; those tests
// skip without it.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const FIXTURES = fileURLToPath(new URL("./fixtures/", import.meta.url));

export default async function globalSetup() {
  mkdirSync(FIXTURES, { recursive: true });
  const mupdf = await import("mupdf");
  const colors: [number, number, number][] = [[255, 0, 0], [0, 160, 0], [0, 0, 255]];
  colors.forEach(([r, g, b], i) => {
    const pix = new mupdf.Pixmap(mupdf.ColorSpace.DeviceRGB, [0, 0, 321, 241], false);
    const samples = pix.getPixels();
    for (let k = 0; k < samples.length; k += 3) [samples[k], samples[k + 1], samples[k + 2]] = [r, g, b];
    writeFileSync(`${FIXTURES}img${i}.png`, pix.asPNG());
  });
  const doc = new mupdf.PDFDocument();
  for (let n = 1; n <= 3; n++) {
    const font = doc.addSimpleFont(new mupdf.Font("Helvetica"));
    const res = doc.addObject({ Font: { F1: font } });
    const page = doc.addPage([0, 0, 595, 842], 0, res, `BT /F1 24 Tf 72 720 Td (Page ${n}) Tj ET`);
    doc.insertPage(-1, page);
  }
  writeFileSync(`${FIXTURES}three.pdf`, doc.saveToBuffer("compress").asUint8Array());
  // One page holding a large, noisy (hard to compress) photo, for "Compress".
  if (!existsSync(`${FIXTURES}photo.pdf`)) {
    const photo = new mupdf.PDFDocument();
    const pix = new mupdf.Pixmap(mupdf.ColorSpace.DeviceRGB, [0, 0, 1600, 1200], false);
    const px = pix.getPixels();
    for (let k = 0; k < px.length; k++) px[k] = (Math.random() * 256) | 0;
    const img = photo.addImage(new mupdf.Image(pix));
    const res = photo.addObject({ XObject: { I0: img } });
    photo.insertPage(-1, photo.addPage([0, 0, 595, 842], 0, res, "q 595 0 0 842 0 0 cm /I0 Do Q"));
    writeFileSync(`${FIXTURES}photo.pdf`, photo.saveToBuffer("compress").asUint8Array());
  }
  // OCR samples: text drawn into a PDF, rendered at 300 DPI to a "scan" PNG; then a PDF with that
  // scan (image only, no text) as page 1 and a page with real text as page 2.
  {
    const src = new mupdf.PDFDocument();
    const font = src.addSimpleFont(new mupdf.Font("Helvetica"));
    const res = src.addObject({ Font: { F1: font } });
    src.insertPage(-1, src.addPage([0, 0, 420, 140], 0, res, "BT /F1 28 Tf 30 85 Td (INVOICE 2026) Tj 0 -45 Td (Total due 42.50 EUR) Tj ET"));
    const scan = src.loadPage(0).toPixmap(mupdf.Matrix.scale(300 / 72, 300 / 72), mupdf.ColorSpace.DeviceRGB, false);
    scan.setResolution(300, 300);
    writeFileSync(`${FIXTURES}scan.png`, scan.asPNG());
    const mixed = new mupdf.PDFDocument();
    const img = mixed.addImage(new mupdf.Image(scan));
    mixed.insertPage(-1, mixed.addPage([0, 0, 420, 140], 0, mixed.addObject({ XObject: { I0: img } }), "q 420 0 0 140 0 0 cm /I0 Do Q"));
    const f2 = mixed.addSimpleFont(new mupdf.Font("Helvetica"));
    mixed.insertPage(-1, mixed.addPage([0, 0, 420, 140], 0, mixed.addObject({ Font: { F1: f2 } }), "BT /F1 18 Tf 30 70 Td (Real text page) Tj ET"));
    writeFileSync(`${FIXTURES}scanned.pdf`, mixed.saveToBuffer("compress").asUint8Array());
  }
  // OCR language data (served to the page by a route in the tests, unless run against the live
  // site). Taken from packaging/tessdata (scripts/fetch_tessdata.py eng dan) or downloaded.
  for (const code of ["eng", "dan"]) {
    const name = `${code}.traineddata`;
    if (existsSync(`${FIXTURES}${name}`)) continue;
    const local = fileURLToPath(new URL(`../../packaging/tessdata/${name}`, import.meta.url));
    if (existsSync(local)) copyFileSync(local, `${FIXTURES}${name}`);
    else {
      const r = await fetch(`https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/${name}`);
      if (r.ok) writeFileSync(`${FIXTURES}${name}`, Buffer.from(await r.arrayBuffer()));
      else console.warn(`couldn't fetch ${name}: OCR e2e tests will fail`);
    }
  }
  writeFileSync(`${FIXTURES}letter.doc`, "old binary Word file");
  writeFileSync(`${FIXTURES}eye effect.txt`, "Hi\n"); // the 3-byte file from the owner's test
  writeFileSync(`${FIXTURES}notes.txt`, "Plain text line 1\nLine 2 with ÆØÅ\n");
  writeFileSync(`${FIXTURES}doc.md`, "# Title ÆØÅ\n\nHello **world**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n");
  writeFileSync(`${FIXTURES}data.csv`, "name,score\nAda,10\nLin,7\n");
  // A real .docx, made with the same pandoc build the app uses.
  const { createPandocInstance } = await import("../node_modules/pandoc-wasm/src/core.js");
  const { readFileSync } = await import("node:fs");
  const pandoc = await createPandocInstance(readFileSync(new URL("../node_modules/pandoc-wasm/src/pandoc.wasm", import.meta.url)));
  const r = await pandoc.convert({ from: "markdown", to: "docx", "output-file": "out.docx" },
    "# Report\n\nFirst paragraph with **bold**.\n\n- one\n- two\n", {});
  writeFileSync(`${FIXTURES}report.docx`, Buffer.from(await (r.files["out.docx"] as Blob).arrayBuffer()));
  // Camera RAW (DNG) and Photoshop samples, from the desktop app's pure-Python writer.
  try {
    execFileSync("python3", ["-c",
      "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; " +
      "from umc.core.samples import write_test_dng, write_test_psd; " +
      "write_test_dng(Path(sys.argv[2]) / 'photo.dng'); write_test_psd(Path(sys.argv[2]) / 'design.psd')",
      fileURLToPath(new URL("../../src", import.meta.url)), FIXTURES]);
  } catch {
    console.warn("python3 not found: RAW/PSD e2e tests will be skipped");
  }
  // STEP / IGES cubes (10 mm) shared with the desktop tests.
  for (const f of ["cube-10mm.stp", "cube-10mm.igs"]) copyFileSync(fileURLToPath(new URL(`../../tests/fixtures/${f}`, import.meta.url)), `${FIXTURES}${f}`);
  if (!existsSync(`${FIXTURES}noise.png`)) {
    const pix = new mupdf.Pixmap(mupdf.ColorSpace.DeviceRGB, [0, 0, 1200, 900], false);
    const px = pix.getPixels();
    for (let k = 0; k < px.length; k++) px[k] = (Math.random() * 256) | 0;
    writeFileSync(`${FIXTURES}noise.png`, pix.asPNG());
  }
  if (!existsSync(`${FIXTURES}noisy.mp4`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=640x360:rate=30:duration=10,noise=alls=40:allf=t", "-f", "lavfi", "-i", "sine=duration=10",
        "-shortest", "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", `${FIXTURES}noisy.mp4`]);
    } catch {
      /* skipped like the other video tests */
    }
  }
  if (!existsSync(`${FIXTURES}tone.wav`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-ac", "2", `${FIXTURES}tone.wav`]);
    } catch {
      /* skipped like the video tests */
    }
  }
  if (!existsSync(`${FIXTURES}clip.mp4`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", `${FIXTURES}clip.mp4`]);
    } catch {
      console.warn("ffmpeg not found: video and audio e2e tests will be skipped");
    }
  }
}
