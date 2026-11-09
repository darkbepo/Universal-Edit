// All converters, and which targets a set of input files can be turned into.
// Converter modules only *declare* engines; the heavy WebAssembly loads lazily in the worker.
import { imageConverter, imagesToPdfConverter } from "../converters/image";
import { dataConverter } from "../converters/data";
import { modelConverter } from "../converters/model3d";
import { ocrImageConverter, ocrPdfConverter } from "../converters/ocr";
import { documentConverter, pagesConverter, splitConverter } from "../converters/pdf";
import { pdfToolsConverter } from "../converters/pdftools";
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
  if (families.size > 1) {
    return { targets: [], note: "Please add files of one kind at a time (only images, only videos, only audio, only documents or only 3D models)." };
  }
  const desktopOnly = files.filter((f) => DESKTOP_ONLY_DOCUMENT_EXTS.has(f.ext));
  if (desktopOnly.length) {
    const kinds = [...new Set(desktopOnly.map((f) => f.ext.toUpperCase()))].join(", ");
    return {
      targets: [],
      note: `${kinds} files can't be converted in the browser — they need LibreOffice, which only the desktop app can install. Newer formats (DOCX, XLSX, PPTX) work here.`,
    };
  }
  const family = [...families][0];
  const seen = new Set<string>();
  const targets: Target[] = [];
  for (const c of CONVERTERS) {
    if (c.family !== family) continue;
    for (const t of c.targets(files)) {
      if (!seen.has(t.id)) {
        seen.add(t.id);
        targets.push(t);
      }
    }
  }
  return { targets, note: "" };
}
