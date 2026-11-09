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
  writeFileSync(`${FIXTURES}letter.docx`, "not really a docx");
  if (!existsSync(`${FIXTURES}clip.mp4`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-f", "lavfi", "-i", "sine=duration=2", "-shortest", "-pix_fmt", "yuv420p", `${FIXTURES}clip.mp4`]);
    } catch {
      console.warn("ffmpeg not found: video e2e tests will be skipped");
    }
  }
}
