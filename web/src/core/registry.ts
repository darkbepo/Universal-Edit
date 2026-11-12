// All converters, and which targets a set of input files can be turned into.
// Converter modules only *declare* engines; the heavy WebAssembly loads lazily in the worker.
import { imageConverter, imagesToPdfConverter } from "../converters/image";
import { dataConverter } from "../converters/data";
import { model3dViewConverter, modelConverter } from "../converters/model3d";
import { ocrImageConverter, ocrPdfConverter } from "../converters/ocr";
import { archiveConverter, packConverter } from "../converters/archive";
import { fontConverter } from "../converters/fonts";
import { cadConverter } from "../converters/cad";
import { bgremoveConverter } from "../converters/bgremove";
import { audioEditOpenConverter } from "../converters/audioedit";
import { pdfToSvgConverter, traceConverter, vectorConverter } from "../converters/vector";
import { subtitleConverter, subtitleExtractConverter, subtitleVideoConverter } from "../converters/subtitles";
import { documentConverter, pagesConverter, splitConverter } from "../converters/pdf";
import { pdfToolsConverter } from "../converters/pdftools";
import { propertiesConverter } from "../converters/properties";
import { slideshowConverter } from "../converters/slideshow";
import { audioConverter, audioFileConverter, framesConverter, gifConverter, videoConverter } from "../converters/video";
import { DESKTOP_ONLY_DOCUMENT_EXTS } from "./detect";
import type { Converter, InputFile, Target } from "./types";

export const CONVERTERS: Converter[] = [
  imageConverter,
  slideshowConverter,
  imagesToPdfConverter,
  videoConverter,
  gifConverter,
  framesConverter,
  audioConverter,
  audioFileConverter,
  dataConverter,
  documentConverter,
  pagesConverter,
  splitConverter,
  pdfToolsConverter,
  modelConverter,
  ocrImageConverter,
  ocrPdfConverter,
  archiveConverter,
  fontConverter,
  vectorConverter,
  cadConverter,
  pdfToSvgConverter,
  traceConverter,
  subtitleConverter,
  subtitleExtractConverter,
  subtitleVideoConverter,
  packConverter,
  bgremoveConverter, // image editor only (no targets)
  model3dViewConverter, // 3D viewer only (no targets)
  propertiesConverter, // Properties mode only (no targets)
  audioEditOpenConverter, // audio editor only (no targets)
];

export const converterById = (id: string) => CONVERTERS.find((c) => c.id === id)!;

export interface TargetResult {
  targets: Target[];
  /** Plain-language reason when nothing (or not everything) can be offered. */
  note: string;
}

export function targetsFor(files: InputFile[]): TargetResult {
  if (!files.length) return { targets: [], note: "" };
  const families = new Set(files.map((f) => f.family));
  const desktopOnly = files.filter((f) => DESKTOP_ONLY_DOCUMENT_EXTS.has(f.ext));
  if (desktopOnly.length) {
    const kinds = [...new Set(desktopOnly.map((f) => f.ext.toUpperCase()))].join(", ");
    return {
      targets: [],
      note: `${kinds} files can't be converted in the browser — they need LibreOffice, which only the desktop app can install. Newer formats (DOCX, XLSX, PPTX) work here.`,
    };
  }
  const postscript = files.filter((f) => f.ext === "eps" || f.ext === "ps");
  if (postscript.length && postscript.length === files.length) {
    return { targets: [], note: "EPS and PostScript files need Ghostscript, which only the desktop app can install. SVG and Illustrator (.ai) files work here." };
  }
  if (files.some((f) => f.ext === "dwg") && files.every((f) => f.family === "cad")) {
    return { targets: [], note: "DWG drawings need LibreDWG, which only the desktop app can install. DXF files work here." };
  }
  const family = families.size === 1 ? [...families][0] : null; // null = mixed: only "pack" targets
  const seen = new Set<string>();
  const targets: Target[] = [];
  for (const c of CONVERTERS) {
    if (!c.anyFamily && c.family !== family) continue;
    for (const t of c.targets(files)) {
      if (!seen.has(t.id)) {
        seen.add(t.id);
        targets.push(t);
      }
    }
  }
  return { targets, note: "" };
}
