// Generates small sample files (no binaries in git). Video needs a system ffmpeg; those tests
// skip without it.
import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export const FIXTURES = fileURLToPath(new URL("./fixtures/", import.meta.url));

/** A Python that can run the desktop code (python-docx + lxml), or null: `UMC_PYTHON`, then
 * `python3` (CI), then the desktop venv next to `web/` (local Windows runs). */
export function findPython(check = "import docx, lxml"): string | null {
  const venv = fileURLToPath(new URL(`../../.venv/${process.platform === "win32" ? "Scripts/python.exe" : "bin/python"}`, import.meta.url));
  for (const exe of [process.env.UMC_PYTHON, "python3", venv]) {
    if (!exe) continue;
    try {
      execFileSync(exe, ["-c", check], { stdio: "ignore" });
      return exe;
    } catch {
      /* next one */
    }
  }
  return null;
}

/** A Python that can run the desktop's Qt-free video editor model (`umc.videoedit`, taken from
 * the repo's `src/`, so no install is needed), or null. */
export function videoEditPython(): string | null {
  const src = fileURLToPath(new URL("../../src", import.meta.url));
  return findPython(`import sys; sys.path.insert(0, ${JSON.stringify(src)}); import umc.videoedit.projectfile`);
}

/** `e2e/videoedit_cross.py`'s answer (spans = [start, end] in seconds). */
export interface DesktopProject {
  sources: { id: string; name: string; kind: string; size: number }[];
  main: { id: string; source: string; span: [number, number] }[];
  music: { id: string; source: string; span: [number, number] }[];
  texts: { id: string; text: string; span: [number, number] }[];
  missing: string[];
}

/** Runs `e2e/videoedit_cross.py` (`save SPEC OUT` / `load PATH`) → the project as the desktop
 * loaded it. */
export function videoEditCross(python: string, args: string[]): DesktopProject {
  const script = fileURLToPath(new URL("./videoedit_cross.py", import.meta.url));
  return JSON.parse(execFileSync(python, [script, ...args]).toString("utf8")) as DesktopProject;
}

/** clip.mp4, img0.png and tone.wav as `videoedit_cross.py` sources (ids s1–s3). */
export const MIXED_SOURCES = [
  { id: "s1", name: "clip.mp4", kind: "video", duration: 2.0, width: 320, height: 240, fps: 30.0, has_audio: true },
  { id: "s2", name: "img0.png", kind: "image", duration: 0.0, width: 321, height: 241, fps: 0.0, has_audio: false },
  { id: "s3", name: "tone.wav", kind: "audio", duration: 3.0, width: 0, height: 0, fps: 0.0, has_audio: true },
];

/** A 20 × 10 × 5 mm box as binary STL (12 triangles), away from the origin. */
export function boxStl(path: string) {
  const [w, d, h] = [20, 10, 5];
  const c = (x: number, y: number, z: number) => [x * w + 40, y * d + 40, z * h + 40];
  const quads = [
    [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]], [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], [[0, 1, 0], [0, 1, 1], [1, 1, 1], [1, 1, 0]],
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]], [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 1]],
  ];
  const buf = Buffer.alloc(84 + 12 * 50);
  buf.writeUInt32LE(12, 80);
  let o = 84;
  for (const q of quads) for (const tri of [[q[0], q[1], q[2]], [q[0], q[2], q[3]]]) {
    o += 12; // normal (slicers recompute it)
    for (const v of tri) for (const x of c(v[0], v[1], v[2])) { buf.writeFloatLE(x, o); o += 4; }
    o += 2;
  }
  writeFileSync(path, buf);
}

export default async function globalSetup() {
  mkdirSync(FIXTURES, { recursive: true });
  const mupdf = await import("mupdf");
  // img0–img2 for most tests; img3–img7 only for the 8-picture crossfade slideshow.
  const colors: [number, number, number][] = [
    [255, 0, 0], [0, 160, 0], [0, 0, 255],
    [255, 200, 0], [0, 200, 200], [200, 0, 200], [40, 40, 40], [230, 230, 230],
  ];
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
  // Properties: a PDF with a title, locked with the password "pw".
  const info = doc.getTrailer().get("Info");
  const meta = info.isNull() ? doc.addObject(doc.newDictionary()) : info;
  if (info.isNull()) doc.getTrailer().put("Info", meta);
  meta.put("Title", doc.newString("Three pages"));
  writeFileSync(`${FIXTURES}locked.pdf`,
    doc.saveToBuffer("compress,encrypt=aes-256,user-password=pw,owner-password=own").asUint8Array());
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
  // Archives: a ZIP made here, plus the small 7Z / AES-ZIP / RAR samples from the desktop tests.
  {
    const { zipSync, strToU8 } = await import("fflate");
    writeFileSync(`${FIXTURES}bundle.zip`, zipSync({ "readme.txt": strToU8("hello ÆØÅ\n"), "sub/deep/data.bin": new Uint8Array(10240).map((_, i) => i % 256) }));
    for (const f of ["sample.7z", "sample-aes.zip", "sample-rar5.rar"]) copyFileSync(fileURLToPath(new URL(`../../tests/fixtures/${f}`, import.meta.url)), `${FIXTURES}${f}`);
  }
  // Subtitles: a UTF-8 SRT with a language in its name, and the same text as an old Windows-1252 file.
  {
    const srt = "1\n00:00:00,200 --> 00:00:01,500\nHej Æble ØL Å\n\n2\n00:00:01,600 --> 00:00:01,900\nSecond line\n";
    writeFileSync(`${FIXTURES}movie.da.srt`, srt, "utf8");
    writeFileSync(`${FIXTURES}old.srt`, Buffer.from(srt, "latin1")); // ÆØÅ are the same bytes in Windows-1252
  }
  // Fonts: DejaVu Sans (also the subtitle font) as a real-world TTF with thousands of glyphs.
  copyFileSync(fileURLToPath(new URL("../node_modules/dejavu-fonts-ttf/ttf/DejaVuSans.ttf", import.meta.url)), `${FIXTURES}DejaVuSans.ttf`);
  // Vector: an SVG, an Illustrator file (modern .ai = a PDF inside), a PostScript EPS and a flat
  // two-colour picture to trace.
  {
    writeFileSync(`${FIXTURES}logo.svg`, '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">' +
      '<rect x="10" y="10" width="80" height="80" fill="#ff0000"/><circle cx="150" cy="50" r="40" fill="#0000ff"/></svg>');
    const ai = new mupdf.PDFDocument();
    const font = ai.addSimpleFont(new mupdf.Font("Helvetica"));
    ai.insertPage(-1, ai.addPage([0, 0, 300, 150], 0, ai.addObject({ Font: { F1: font } }),
      "0 0.6 0 rg 10 10 130 130 re f BT /F1 20 Tf 0 g 160 70 Td (Illustrator) Tj ET"));
    writeFileSync(`${FIXTURES}poster.ai`, ai.saveToBuffer("compress").asUint8Array());
    writeFileSync(`${FIXTURES}art.eps`, "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 200 100\n1 0 0 setrgbcolor 10 10 80 80 rectfill showpage\n");
    const shapes = new mupdf.Pixmap(mupdf.ColorSpace.DeviceRGB, [0, 0, 300, 200], false);
    const px = shapes.getPixels();
    for (let y = 0; y < 200; y++) for (let x = 0; x < 300; x++) {
      const i = (y * 300 + x) * 3;
      const c = x >= 20 && x < 120 && y >= 20 && y < 180 ? [255, 0, 0] : (x - 215) ** 2 + (y - 105) ** 2 < 65 ** 2 ? [0, 0, 128] : [255, 255, 255];
      px.set(c, i);
    }
    writeFileSync(`${FIXTURES}shapes.png`, shapes.asPNG());
  }
  // CAD: a hand-written R12 DXF — red WALLS layer (a 200 × 100 rectangle) and a blue circle.
  {
    const pairs: (string | number)[] = [0, "SECTION", 2, "TABLES", 0, "TABLE", 2, "LAYER", 70, 2,
      0, "LAYER", 2, "WALLS", 70, 0, 62, 1, 6, "CONTINUOUS", 0, "LAYER", 2, "FURN", 70, 0, 62, 5, 6, "CONTINUOUS",
      0, "ENDTAB", 0, "ENDSEC", 0, "SECTION", 2, "ENTITIES"];
    for (const [x1, y1, x2, y2] of [[0, 0, 200, 0], [200, 0, 200, 100], [200, 100, 0, 100], [0, 100, 0, 0]]) {
      pairs.push(0, "LINE", 8, "WALLS", 10, x1, 20, y1, 30, 0, 11, x2, 21, y2, 31, 0);
    }
    pairs.push(0, "CIRCLE", 8, "FURN", 10, 150, 20, 50, 30, 0, 40, 30, 0, "ENDSEC", 0, "EOF");
    writeFileSync(`${FIXTURES}plan.dxf`, pairs.map((v) => String(v)).join("\n") + "\n");
    writeFileSync(`${FIXTURES}house.dwg`, "AC1032" + "\0".repeat(100));
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
  // The document editor's samples: a DOCX and an ODT from the same pandoc build (heading, bold, a
  // list, a table with a header row and one more), indented TXT and a README-like Markdown file.
  const editable = "# Editable report\n\nSome **bold words** here.\n\n- first\n- second\n\n| Name | Score |\n|------|-------|\n| Ada  | 10    |\n";
  for (const ext of ["docx", "odt"]) {
    const out = await pandoc.convert({ from: "markdown", to: ext, "output-file": `out.${ext}` }, editable, {});
    writeFileSync(`${FIXTURES}editable.${ext}`, Buffer.from(await (out.files[`out.${ext}`] as Blob).arrayBuffer()));
  }
  writeFileSync(`${FIXTURES}aligned.txt`, "def f():\n    return 1\n\n\tTabbed\tcolumns\there\n  two  spaces  inside\n");
  writeFileSync(`${FIXTURES}readme.md`, "---\ntitle: Readme\n---\n\n# Project\n\nIntro with `code` and *emphasis*.\n\n" +
    "```python\ndef main():\n    print(\"hi\")\n```\n\n> A quote\n> on two lines\n\n- one\n  - nested\n- two\n\n" +
    "| Col | Other |\n| --- | --- |\n| a |  |\n");
  const python = findPython();
  // A DOCX written by the desktop editor, and what the desktop editor reads back from it. Old
  // copies go first, so a failed write skips the cross-app test instead of reusing them.
  for (const f of ["desktop.docx", "desktop.html"]) rmSync(`${FIXTURES}${f}`, { force: true });
  if (python) {
    try {
      // the desktop's writer, then a run in Word's own "Strong" character style (python-docx)
      execFileSync(python, ["-c", [
        "import sys, docx",
        "sys.path.insert(0, sys.argv[1])",
        "from pathlib import Path",
        "from umc.doceditor import docx_reader, docx_writer",
        "p = Path(sys.argv[2]) / 'desktop.docx'",
        "docx_writer.write('<h1>From the desktop</h1><p>Plain and <b>bold</b> text.</p><p>Name\\tScore</p>'",
        "  '<table><tr><td><p>Ada</p></td><td><p>10</p></td></tr><tr><td><p>Lin</p></td><td><p></p></td></tr></table>', {}, p)",
        "d = docx.Document(str(p))",
        "d.add_paragraph('Word style: ').add_run('Strong', style='Strong')",
        "d.save(str(p))",
        "(Path(sys.argv[2]) / 'desktop.html').write_text(docx_reader.read(p).html, encoding='utf-8')",
      ].join("\n"), fileURLToPath(new URL("../../src", import.meta.url)), FIXTURES]);
    } catch (e) {
      console.warn("couldn't write desktop.docx: the cross-app DOCX test will be skipped", e);
    }
  } else console.warn("no python with python-docx: the cross-app DOCX test will be skipped");
  // Camera RAW (DNG) and Photoshop samples, from the desktop app's pure-Python writer.
  try {
    execFileSync(python ?? "python3", ["-c",
      "import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; " +
      "from umc.core.samples import write_test_dng, write_test_photo, write_test_psd; " +
      "write_test_dng(Path(sys.argv[2]) / 'photo.dng'); write_test_psd(Path(sys.argv[2]) / 'design.psd'); " +
      "write_test_photo(Path(sys.argv[2]) / 'camera.jpg')",
      fileURLToPath(new URL("../../src", import.meta.url)), FIXTURES]);
  } catch {
    console.warn("python3 not found: RAW/PSD e2e tests will be skipped");
  }
  // STEP / IGES cubes (10 mm) shared with the desktop tests.
  for (const f of ["cube-10mm.stp", "cube-10mm.igs"]) copyFileSync(fileURLToPath(new URL(`../../tests/fixtures/${f}`, import.meta.url)), `${FIXTURES}${f}`);
  boxStl(`${FIXTURES}box.stl`); // 3D viewer and Properties tests
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
  if (!existsSync(`${FIXTURES}song.mp3`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=3", "-ac", "2",
        "-metadata", "title=Song", "-metadata", "artist=Band", "-metadata", "date=2019-05-01", "-id3v2_version", "3", `${FIXTURES}song.mp3`]);
    } catch {
      // no ffmpeg: the Properties music test is skipped
    }
  }
  if (!existsSync(`${FIXTURES}song.flac`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
        "-metadata", "title=Other", "-metadata", "artist=Band", "-metadata", "track=2", `${FIXTURES}song.flac`]);
    } catch {
      // no ffmpeg: the music tags tests are skipped
    }
  }
  if (!existsSync(`${FIXTURES}beep.mp3`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        `${FIXTURES}beep.mp3`]); // mono, 1 s: the audio editor joins it after tone.wav
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
  // Video editor: one solid colour each, so the preview's pixels can be checked.
  if (!existsSync(`${FIXTURES}blue.png`)) {
    const pix = new mupdf.Pixmap(mupdf.ColorSpace.DeviceRGB, [0, 0, 64, 36], false);
    const px = pix.getPixels();
    for (let k = 0; k < px.length; k += 3) [px[k], px[k + 1], px[k + 2]] = [0x20, 0x40, 0xd0];
    writeFileSync(`${FIXTURES}blue.png`, pix.asPNG());
  }
  if (!existsSync(`${FIXTURES}red.mp4`)) {
    try {
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0xd02020:s=320x180:r=30:d=1",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", `${FIXTURES}red.mp4`]);
    } catch {
      // skipped like the other video tests
    }
  }
  if (!existsSync(`${FIXTURES}clip.avi`)) {
    try { // the browser can't read AVI: the video editor copies it with FFmpeg first
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=duration=1", "-shortest", "-c:v", "mpeg4", "-c:a", "libmp3lame", `${FIXTURES}clip.avi`]);
    } catch {
      // skipped like the other video tests
    }
  }
  if (!existsSync(`${FIXTURES}odd.avi`)) {
    try { // an odd size: the copy (yuv420p) is one pixel smaller each way
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=321x241:rate=25", "-c:v", "mpeg4", `${FIXTURES}odd.avi`]);
    } catch {
      // skipped like the other video tests
    }
  }
  if (!existsSync(`${FIXTURES}turned.mp4`) && existsSync(`${FIXTURES}clip.mp4`)) {
    try { // shown turned 90° anticlockwise (= 270° clockwise), like a phone clip
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-display_rotation", "90", "-i", `${FIXTURES}clip.mp4`, "-c", "copy", `${FIXTURES}turned.mp4`]);
    } catch {
      // older ffmpeg without -display_rotation: the rotation check is skipped
    }
  }
  if (!existsSync(`${FIXTURES}tracks.mkv`) && existsSync(`${FIXTURES}clip.mp4`)) {
    try { // two sound tracks (English default, the other unset) and Danish subtitles: the details card
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", `${FIXTURES}clip.mp4`, "-i", `${FIXTURES}movie.da.srt`,
        "-map", "0:v", "-map", "0:a", "-map", "0:a", "-map", "1:s", "-c", "copy",
        "-metadata", "title=Holiday", "-metadata:s:a:0", "language=eng", "-metadata:s:s:0", "language=dan",
        "-disposition:a:0", "default", "-disposition:a:1", "0", "-disposition:s:0", "0", `${FIXTURES}tracks.mkv`]);
    } catch {
      // the track test is skipped
    }
  }
  // A video project saved by the desktop app's own code (absolute paths + relpath, its field
  // order): the website must open it and keep those paths when it saves it again.
  rmSync(`${FIXTURES}desktop.umcvideo`, { force: true });
  const crossPython = videoEditPython();
  if (crossPython && existsSync(`${FIXTURES}clip.mp4`) && existsSync(`${FIXTURES}tone.wav`)) {
    try {
      const spec = `${FIXTURES}desktop-spec.json`;
      writeFileSync(spec, JSON.stringify({
        sources: MIXED_SOURCES,
        main: [{ source: "s1" }, { source: "s2" }],
        music: [{ source: "s3", start: 0 }],
        texts: [{ start: 0.5 }],
      }));
      videoEditCross(crossPython, ["save", spec, `${FIXTURES}desktop.umcvideo`]);
    } catch (e) {
      console.warn("couldn't write desktop.umcvideo: the cross-app video project test will be skipped", e);
    }
  }
}
