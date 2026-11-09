// Generates small sample files (no binaries in git). Video needs a system ffmpeg; those tests
// skip without it.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
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
