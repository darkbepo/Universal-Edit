# Media Converter — web companion

The browser version of Universal Media Converter. Everything runs **on the visitor's own
device** (WebAssembly in a Web Worker) — files are never uploaded.

| From | To |
|---|---|
| Images (PNG, JPG, WEBP, AVIF, HEIC*, GIF, BMP, TIFF, ICO, TGA, QOI, JXL…) | any image format · slideshow video (MP4/WebM/MKV/MOV) or GIF · PDF |
| Videos (MP4, MKV, MOV, AVI, WebM…) | MP4, WebM, MKV, MOV, AVI · GIF · frames (N per 30) · audio (MP3, M4A, WAV, FLAC, OGG, OPUS) |
| PDF, EPUB, XPS, MOBI, FB2, CBZ | PDF (page range, paper size) · TXT · images per page · split into several PDFs |

\* HEIC can be read but not written.

**Not in the browser (yet) — use the desktop app:** Word / Markdown / spreadsheet / presentation
files, VP9 / H.265 / AV1 video, and very large videos (browsers cap memory at a few GB).

## Develop

```bash
cd web
npm install
npm run dev        # http://localhost:5173
npm test           # unit tests (vitest)
npm run e2e        # real conversions in headless Chromium (Playwright); video tests need ffmpeg on PATH
npm run build      # static site in dist/
```

## Deploy

`dist/` is a static site. It must be served with these headers so FFmpeg can use threads
(`public/_headers` sets them automatically on Netlify and Cloudflare Pages):

```
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
```

Engines (downloaded on first use, then cached): FFmpeg ~10 MB, ImageMagick ~5.5 MB, MuPDF ~4.8 MB (gzipped).

Licensed AGPL-3.0-or-later (same as the desktop app); a public deployment must link to the source.
