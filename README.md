# Universal Media Converter

## ▶ [Open the converter in your browser](https://darkbepo.github.io/Universal-Media-Converter/)

**https://darkbepo.github.io/Universal-Media-Converter/** — nothing to install, works on Windows,
Mac, Linux and phones. Your files **never leave your device**: everything is converted right in
your browser, nothing is uploaded.

Drop files in, pick an output format, (optionally) tweak settings, press **Convert**.

| From | To |
|---|---|
| **Images** (PNG, JPG, WEBP, AVIF, HEIC, GIF, BMP, TIFF, ICO, …) | any other image format · a slideshow video or animated GIF · a PDF |
| **Videos** (MP4, MKV, MOV, AVI, WEBM, …) | other video formats · GIF · image frames (choose how many per 30 frames) · audio only (MP3, M4A, WAV, FLAC, OGG, OPUS) |
| **Documents** (PDF, DOCX, ODT, RTF, TXT, Markdown, HTML, EPUB, CSV, XLSX, PPTX, …) | other document formats · images (one per page) · split into several PDFs |

## Desktop app (for the heavy stuff)

Prefer a program on your PC, or need something the browser can't do? The **Windows desktop app**
adds old Office formats (DOC, XLS, PPT and OpenDocument spreadsheets/presentations), VP9 / H.265 /
AV1 video and very large files. It installs LibreOffice and Pandoc for you with one click when a
conversion needs them.

Get it from **Actions → Build Windows executable** → latest successful run → download
`UniversalMediaConverter-windows`, extract, and run `UniversalMediaConverter.exe`.

The browser version's source is in [`web/`](web/README.md).

## What works where

✅ = works now · ❌ = not yet · 🔜 = planned (on the roadmap) · — = not possible there (reason below)

| Feature | Desktop app | Website |
|---|:---:|:---:|
| **Images** ↔ each other (PNG, JPG, WEBP, AVIF, HEIC, GIF, BMP, TIFF, ICO…) | ✅ | ✅ |
| Save as HEIC | ✅ | ❌ |
| Save as JPEG XL (JXL) | ❌ | ✅ |
| Images → slideshow video / animated GIF / PDF | ✅ | ✅ |
| Camera RAW (CR2, CR3, NEF, ARW, DNG, RAF, ORF, RW2…) and Photoshop PSD as input | ✅ | ✅ |
| **Video** ↔ each other (MP4, MKV, MOV, WEBM, AVI…) | ✅ | ✅ |
| Video codecs H.264 · VP8 · MPEG-4 | ✅ | ✅ |
| Video codecs H.265 · VP9 · AV1 | ✅ | 🔜 |
| Video → GIF · frames (N per 30) · audio only | ✅ | ✅ |
| Very large videos (several GB) | ✅ | ❌ |
| **Audio** ↔ each other (MP3, WAV, FLAC, M4A, OGG, AIFF, WMA…) | ✅ | ✅ |
| **Documents** ↔ each other (DOCX, ODT, RTF, TXT, Markdown, HTML, EPUB, PDF) | ✅ | ✅ |
| Old Office formats (DOC, XLS, PPT) and ODS / ODP | ✅ | 🔜 |
| Spreadsheets & slides as input (XLSX, CSV, PPTX) | ✅ | ✅ |
| Save as PowerPoint (PPTX) | ❌ | ✅ |
| Documents → images (one per page) | ✅ | ✅ |
| PDF: page range, paper size, margins, split | ✅ | ✅ |
| PDF: merge, compress, rotate, add / remove password, reorder / delete pages | ✅ | ✅ |
| PDF → Word keeping the layout | 🔜 | 🔜 |
| OCR (scanned PDF / photo → text) | 🔜 | 🔜 |
| Compress to a target size (video, image, PDF, audio) | ✅ | ✅ |
| Data: JSON ↔ CSV ↔ TSV ↔ XML ↔ YAML ↔ XLSX | ✅ | ✅ |
| **3D printing**: STL, OBJ, 3MF, PLY, GLB, STEP / IGES | ✅ | 🔜 |
| Archives: ZIP, 7Z, RAR, TAR | 🔜 | 🔜 |
| Subtitles: SRT, VTT, ASS · extract / burn into video | 🔜 | 🔜 |
| Fonts: TTF, OTF, WOFF, WOFF2 | 🔜 | 🔜 |
| Vector: SVG, EPS, AI · image → SVG tracing | 🔜 | 🔜 |
| eBooks: save as MOBI / AZW3 (Kindle) | 🔜 | — |
| CAD drawings: DXF (DWG if feasible) | 🔜 | 🔜 |
| Files never leave your computer | ✅ | ✅ |
| No install needed | ❌ | ✅ |
| Save straight into a folder | ✅ | ✅ Chrome/Edge · ❌ Firefox/Safari (zip instead) |

**Why some things stay desktop-only:** the website runs inside the browser, which limits memory
to a few GB per tab (so huge videos can fail) and can't run desktop programs. Kindle formats
need Calibre, a large desktop program with no browser version.

## Running from source

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python scripts/fetch_ffmpeg.py   # Windows; elsewhere install ffmpeg with brew/apt
python -m umc
```

Office formats (DOC/DOCX/ODT/XLSX/PPTX…) need [LibreOffice](https://www.libreoffice.org/download/download/)
and Markdown/EPUB/HTML need [Pandoc](https://pandoc.org/installing.html). If one is missing, the
app offers to install it for you with one click — no restart needed.

## Building the Windows executable

On GitHub: **Actions → Build Windows executable → Run workflow**, then download the zip from
the finished run. Locally on Windows: `pyinstaller packaging/umc.spec` → `dist/UniversalMediaConverter/`.

## License

Copyright (C) 2026 darkbepo

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The bundled FFmpeg (GPL build) and PyMuPDF (AGPL-3.0) are why the app uses this licence.

See [CLAUDE.md](CLAUDE.md) for architecture and contributor notes.
