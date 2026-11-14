# Universal Edit — web companion

The browser version of Universal Edit. Everything runs **on the visitor's own
device** (WebAssembly in a Web Worker) — files are never uploaded.

| From | To |
|---|---|
| Images (PNG, JPG, WEBP, AVIF, HEIC*, GIF, BMP, TIFF, ICO, TGA, QOI, JXL…) | any image format · slideshow video (MP4/WebM/MKV/MOV) or GIF · PDF |
| Audio (MP3, WAV, FLAC, M4A, OGG, OPUS, AIFF, WMA…) | any other audio format, with bitrate, sample rate, channels, volume, normalise and trim |
| Videos (MP4, MKV, MOV, AVI, WebM…) | MP4, WebM, MKV, MOV, AVI · GIF · frames (N per 30) · audio (MP3, M4A, WAV, FLAC, OGG, OPUS) |
| Documents: PDF, DOCX, ODT, RTF, TXT, Markdown, HTML, EPUB, CSV, XLSX, PPTX, XPS… | PDF (page range, paper size) · DOCX · ODT · RTF · TXT · Markdown · HTML · EPUB · PPTX · images per page · split into several PDFs |

\* HEIC can be read but not written.

**Not in the browser — use the [desktop app](https://github.com/darkbepo/Universal-Edit/releases)
(not released yet):** old Office formats (DOC, XLS, PPT) and
OpenDocument spreadsheets/presentations (they need LibreOffice), VP9 / H.265 / AV1 video, and
very large videos (browsers cap memory at a few GB).

## Develop

```bash
cd web
npm install
npm run dev        # http://localhost:5173
npm test           # unit tests (vitest)
npm run e2e        # real conversions in headless Chromium (Playwright); video tests need ffmpeg on PATH
npm run build      # static site in dist/
```

## Local test version (no install)

`npm run package:local` builds `release/UniversalEdit-web-local.zip`: the site in `app/` plus
`Start Universal Edit.bat` (Windows, PowerShell server) and `serve.py` (macOS/Linux). Both serve
on `localhost` only, with the isolation headers. CI publishes it as the `UniversalEdit-web-local`
artifact of the "Web companion" workflow, and tests the Windows launcher on windows-latest.

## Deploy

`dist/` is a static site. FFmpeg needs these headers to use threads. `public/_headers` sets them on
Netlify / Cloudflare Pages; on hosts that can't (GitHub Pages), the bundled `coi-serviceworker.js`
adds them itself (the first visit reloads once):

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

Engines (downloaded on first use, then cached): FFmpeg ~10 MB, ImageMagick ~5.5 MB, MuPDF ~4.8 MB,
Pandoc ~16 MB, OpenCASCADE for STEP/IGES ~3 MB (gzipped). three.js (MIT) and occt-import-js (LGPL-2.1) handle 3D models; libarchive.js (MIT, ~0.5 MB) opens archives; DejaVu Sans (~0.7 MB, free licence) is the font for burned-in subtitles; HarfBuzz hb-subset (MIT, ~0.3 MB) and woff2-encoder (MIT) handle fonts; resvg (MPL-2.0) renders SVG and vectortracer (MIT) traces images; tesseract.js (Apache-2.0, ~4 MB) does OCR, with language data (1–8 MB each) downloaded once from tessdata_fast and checked against SHA-256.

Licensed AGPL-3.0-or-later (same as the desktop app); a public deployment must link to the source.
