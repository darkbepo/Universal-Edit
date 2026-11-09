import { describe, expect, it } from "vitest";
import { ocrImageConverter, ocrPdfConverter } from "../src/converters/ocr";
import type { InputFile } from "../src/core/types";
import { LANGUAGES } from "../src/engines/tesseract";
import py from "../../src/umc/tools/tessdata.py?raw";

const file = (name: string, family: InputFile["family"]): InputFile => ({ file: new File([], name), name, ext: name.split(".").pop()!, family });

describe("OCR", () => {
  it("uses the same pinned language files as the desktop app", () => {
    const desktop = Object.fromEntries([...py.matchAll(/"(\w+)": \("([0-9a-f]{64})", \d+\)/g)].map((m) => [m[1], m[2]]));
    expect(Object.keys(desktop)).toHaveLength(LANGUAGES.length);
    for (const [code, , sha] of LANGUAGES) expect(desktop[code], code).toBe(sha);
  });

  it("offers searchable PDF and text for images and PDFs only", () => {
    expect(ocrImageConverter.targets([file("a.jpg", "image")]).map((t) => t.id)).toEqual(["ocr:pdf", "ocr:txt"]);
    expect(ocrPdfConverter.targets([file("a.pdf", "document")]).map((t) => t.id)).toEqual(["ocr:pdf", "ocr:txt"]);
    expect(ocrPdfConverter.targets([file("a.docx", "document")])).toEqual([]);
  });
});
