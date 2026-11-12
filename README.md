# Universal Edit

Convert and edit pictures, video, audio, documents, 3D models and more, on your own device.
(Formerly *Universal Media Converter*.)

## ▶ [Open the converter in your browser](https://darkbepo.github.io/Universal-Edit/)

**https://darkbepo.github.io/Universal-Edit/** — nothing to install, works on Windows,
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
AV1 video and very large files, and it converts video much faster because it uses your whole
processor (the browser version is limited to a few threads). It installs LibreOffice and Pandoc for you with one click when a
conversion needs them.

Get it from **Actions → Build Windows executable** → latest successful run → download
**`UniversalEdit-setup`** and run `UniversalEdit-setup.exe` (installs for your user only, no
administrator rights needed). Setup lets you choose which editors to install (they can also be
installed later from the app). During setup you can add **"Convert with Universal Edit"** to the
right-click menu and put Universal Edit in **"Open with"**; both can be switched later in the
app (cog, top left). To make the viewer your default for pictures or videos, pick it once in
Windows' *Default apps* (setup offers a shortcut there). Prefer no installer? Download
`UniversalEdit-windows`, extract, and run `UniversalEdit.exe`.

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
| Video codecs VP9 · AV1 | ✅ | ✅ (browser encoder) |
| Video codec H.265 / HEVC | ✅ | ✅ where the device has an encoder (most Windows/Mac/phones; not Linux) |
| Video → GIF · frames (N per 30) · audio only | ✅ | ✅ |
| Very large videos (several GB) | ✅ | ❌ |
| **Audio** ↔ each other (MP3, WAV, FLAC, M4A, OGG, AIFF, WMA…) | ✅ | ✅ |
| **Documents** ↔ each other (DOCX, ODT, RTF, TXT, Markdown, HTML, EPUB, PDF) | ✅ | ✅ |
| Old Office formats (DOC, XLS, PPT) and ODS / ODP | ✅ | ❌ (needs LibreOffice: ~250 MB, too big for the website for now) |
| Spreadsheets & slides as input (XLSX, CSV, PPTX) | ✅ | ✅ |
| Save as PowerPoint (PPTX) | ❌ | ✅ |
| Documents → images (one per page) | ✅ | ✅ |
| PDF: page range, paper size, margins, split | ✅ | ✅ |
| PDF: merge, compress, rotate, add / remove password, reorder / delete pages | ✅ | ✅ |
| PDF → Word (DOCX / ODT / RTF) keeping the layout: tables, columns, pictures | ✅ | ❌ (text only) |
| OCR (scanned PDF / photo → searchable PDF or text, 30 languages) | ✅ | ✅ |
| Compress to a target size (video, image, PDF, audio) | ✅ | ✅ |
| Data: JSON ↔ CSV ↔ TSV ↔ XML ↔ YAML ↔ XLSX | ✅ | ✅ |
| **3D printing**: STL, OBJ, 3MF, PLY, GLB, STEP / IGES | ✅ | ✅ |
| Archives: open ZIP, 7Z, RAR, TAR(.GZ/.XZ/.BZ2) · extract · repack · pack any files | ✅ | ✅ |
| Archives: create 7Z, password-protect (AES-256) | ✅ | ❌ |
| Subtitles: SRT ↔ VTT ↔ ASS ↔ TTML ↔ LRC, shift timing, fix old encodings · extract from video · burn in · add as track | ✅ | ✅ |
| Fonts: TTF / OTF ↔ WOFF ↔ WOFF2 · subset (Latin, Cyrillic, Greek or your own characters) | ✅ | ✅ |
| Fonts: TTF ↔ OTF (changes the outline type) | ✅ | ❌ |
| Vector: SVG / AI → PNG, JPG, WEBP, TIFF · AI → SVG / PDF · PDF pages → SVG | ✅ | ✅ |
| Vector: trace images into SVG (colour or black & white) | ✅ | ✅ |
| Vector: SVG → PDF, EPS / PostScript in and out | ✅ | ❌ |
| eBooks: save as MOBI / AZW3 (Kindle), read Kindle books (one-click Calibre install) | ✅ | — |
| CAD drawings: DXF → SVG, PNG, JPG (layer colours or print black-on-white) | ✅ | ✅ |
| CAD drawings: DWG in (one-click LibreDWG install), → PDF on paper sizes, save as another DXF version | ✅ | ❌ |
| **Image editor**: Convert \| Edit switch, layers (opacity, hide, reorder, merge), brush, eraser, fill, colour picker, shapes, text, crop, resize, rotate / flip, undo | ✅ | ✅ |
| Image editor layers: select several (Ctrl / Shift+click), drag to reorder, ▲▼ arrows on each layer, right-click menu (duplicate, delete, merge, move to top / bottom, show / hide) | ✅ | ✅ |
| Image editor crop: after you draw a crop rectangle (or a rectangle selection), a small bar next to it shows the size with Apply crop / Cancel (Crop to selection / Deselect) | ✅ | ✅ |
| Image editor clipboard: copy (Ctrl+C), copy what you see (Ctrl+Shift+C), cut, paste as a new layer — back in place, or centred for pictures from other programs | ✅ | ✅ (copying into other programs needs a browser that allows it) |
| Image editor Move tool (V): drag the selected layers, arrow keys nudge · layer shortcuts (duplicate, merge, new, up / down / top / bottom) · a "?" list of every shortcut | ✅ | ✅ |
| Image editor Adjust group: Quick fix (exposure, brightness, contrast, temperature, tint, saturation, vibrance, auto tone), Levels (histogram, per channel, auto), Curves (per channel) — live preview, "hold to see the original" | ✅ | ✅ |
| Image editor Filters group: Blur, Sharpen (amount, radius, threshold), 11 one-click Looks (warm, cool, vivid, black & white, noir, sepia, faded, vintage, dramatic, matte, bright & airy) with strength — live preview | ✅ | ✅ |
| Image editor selections: rectangle / ellipse (M), lasso / polygon lasso (Q), magic wand (W); Shift adds, Alt subtracts; select all, deselect, invert, feather — any shape with soft edges, and every tool, adjustment, filter, copy and cut follows it | ✅ | ✅ |
| Image editor masks and gradients: layer masks (add, from selection, invert, apply, delete) with a Mask brush (K), Gradient tool (colour, layer mask or soft selection — e.g. brighten only the sky), Background blur for portraits | ✅ | ✅ |
| Image editor text: text layers stay editable (click them with the Text tool, or Edit text… in the layer menu; kept in .ora), alignment, outline, drop shadow, background box, watermark across the picture | ✅ | ✅ |
| Image editor annotation: arrow tool (one or both heads; L switches line / arrow), highlighter (H; see-through, doesn't darken where it crosses itself), 11 built-in stickers (check mark, cross, star, speech bubble, map pin…; free to use, CC0) | ✅ | ✅ |
| Image editor pasteboard: parts of layers moved past the picture's edge stay visible (faded) around it and can be moved back; they're kept in .ora but never saved into the picture | ✅ | ✅ |
| Image editor retouching: clone stamp (S; Alt+click the source, aligned or not, this layer or all layers) and content-aware fill (paints the selection over from what surrounds it — blemishes, wires, small objects) | ✅ | ✅ |
| Image editor background removal (Retouch group): hides the background with a layer mask, so it can be touched up with the mask brush; the model is downloaded once (desktop: IS-Net, ~179 MB, SHA-256 checked; website: IMG.LY's IS-Net, ~80 MB, cached by the browser) | ✅ | ✅ |
| Image editor: show only one layer (Alt+click its eye, or the layer menu; again to show all), colour-picker magnifier while the button is held, the mask brush adds a mask by itself | ✅ | ✅ |
| Image editor: move a selection by dragging inside it (or with the arrow keys) — the outline moves, the picture stays | ✅ | ✅ |
| Image editor tool rail: every tool and action grouped (Select, Move & crop, Paint, Retouch, Shapes & text, Adjust, Filters, Image) with names shown, or narrowed to icons only (remembered) | ✅ | ✅ |
| Image editor windows: every tool's options and every feature with settings (adjustments, filters, resize, stickers, feather, shortcuts) open in a window over the picture — drag it anywhere, minimise it back into its icon, keep several open; positions are remembered | ✅ | ✅ |
| Image editor live text: clicking with the Text tool makes the text layer at once; it is drawn in its real style while you type, can be dragged by its handle while typing, and stays editable text | ✅ | ✅ |
| Image editor shape layers: each line, arrow, rectangle and ellipse is its own layer with handles on its corners / ends to reshape it any time; Rasterize layer (or merging, rotating, resizing) makes it plain pixels; kept in .ora | ✅ | ✅ |
| Image editor layer effects: drop shadow, glow, outline and colour overlay on any layer, live and editable in their own windows (fx badge in the layers list); merging or Rasterize layer draws them in; kept in .ora | ✅ | ✅ |
| Layered projects: save and open OpenRaster `.ora` (GIMP and Krita open it too); `.ora` → any image format | ✅ | ✅ |
| **Document editor**: Convert \| Edit for one DOCX, ODT, RTF, DOC, Markdown, TXT or HTML file · headings, fonts, colours, highlight, lists, tables, pictures, links, page breaks · find & replace · undo · save back or as PDF and every document format | ✅ | 🔜 |
| **3D viewer**: Convert \| Edit for 3D models (STL, OBJ, 3MF, PLY, GLB, OFF, STEP, IGES) · rotate / pan / zoom, front / top / side / 3D views, shaded / edges / wireframe, print-bed grid · size in mm, triangles, parts, watertight check, volume | ✅ | ✅ (no OFF) |
| **Start screen editor tiles**: open the image editor (with a new picture: size presets, white / transparent / black) or the 3D viewer without a file · video, audio and document editor tiles show "Coming soon" | ✅ | ✅ |
| **Viewer** (`UniversalEdit.exe --view FILE`, for "Open with"): pictures (zoom / pan, animated GIFs), video and music with sound, PDF / EPUB / SVG pages, Word / Excel / PowerPoint / EPS / DXF as pages (converted in the background), text, 3D models · the folder's other files beside it (← / →) · **Convert** / **Edit** move the file into the converter or editor · one window: a second "Open with" reuses it | ✅ | — |
| **Windows installer** (per user, no admin) · right-click **"Convert with Universal Edit"** (several files → one window) · **"Open with"** → viewer · switches in the app's Settings (cog) | ✅ | — |
| **Editors as add-ons**: the app is the converter + viewer; the image editor (≈ 24 MB, incl. background removal) and the 3D viewer are installed when first used (tile shows **Install**), picked in the installer, or managed in Settings · downloads are checked against fingerprints built into the app | ✅ | — |
| **Properties** (Convert \| Edit \| Properties, and the viewer's Properties button): about this file — size and dates · pictures: dimensions, colour, frames, dpi, orientation, camera, lens, exposure, date taken, author, location · video / music: length, codecs, frame rate, channels, track languages, tags (title, artist, album, year…), cover picture, recording location · PDF / Word / Excel / PowerPoint / ODF: pages, words, page size, title, author, dates · fonts: family, style, version, glyphs, variable axes · 3D models: size, triangles, watertight · read-only for now; changing details is next | ✅ | ✅ (no folder; subtitle tracks and AVI / WMV / FLV details desktop only) |
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
the finished run. Locally on Windows: `pyinstaller packaging/umc.spec` → `dist/UniversalEdit/`.

## License

Copyright (C) 2026 darkbepo

This program is free software: you can redistribute it and/or modify it under the terms of the
GNU Affero General Public License as published by the Free Software Foundation, either version 3
of the License, or (at your option) any later version. See [LICENSE](LICENSE).

The bundled FFmpeg (GPL build) and PyMuPDF (AGPL-3.0) are why the app uses this licence.

See [CLAUDE.md](CLAUDE.md) for architecture and contributor notes.
