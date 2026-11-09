import { describe, expect, it } from "vitest";
import { canonicalExt, familyOfExt, sniff } from "../src/core/detect";
import { defaults, parsePageGroups, parsePageRange, parseTime, visible } from "../src/core/options";
import { CONVERTERS, targetsFor } from "../src/core/registry";
import type { InputFile } from "../src/core/types";
import { groupName, pageGroups } from "../src/converters/pdf";
import { orderFiles, outputSize } from "../src/converters/slideshow";
import { atempoChain, trimArgs } from "../src/converters/video";
import { selectExpr, uniqueNamer } from "../src/converters/util";
import { resolveVideoCodec } from "../src/converters/codecs";
import { plan } from "../src/converters/docroute";

describe("document routes", () => {
  const route = (a: string, b: string) => plan(a, b)?.map((e) => `${e.engine}:${e.dst}`);
  it("picks sensible chains", () => {
    expect(route("txt", "pdf")).toEqual(["mupdf:pdf"]);
    expect(route("md", "pdf")).toEqual(["pandoc:html", "mupdf:pdf"]);
    expect(route("docx", "pdf")).toEqual(["mammoth:html", "mupdf:pdf"]); // no Pandoc download
    expect(route("md", "docx")).toEqual(["pandoc:docx"]);
    expect(route("pdf", "txt")).toEqual(["mupdf:txt"]);
    expect(route("pdf", "docx")).toEqual(["mupdf:html", "pandoc:docx"]);
    expect(route("epub", "pdf")).toEqual(["mupdf:pdf"]);
    expect(plan("doc", "pdf")).toBeNull();
  });
});

const input = (name: string, family: InputFile["family"], ext = canonicalExt(name), lastModified = 0): InputFile =>
  ({ file: { name, size: 1, lastModified } as File, name, ext, family });

describe("detect", () => {
  it("normalises extensions", () => {
    expect(canonicalExt("Photo.JPEG")).toBe("jpg");
    expect(canonicalExt("a.tif")).toBe("tiff");
    expect(familyOfExt("mkv")).toBe("video");
    expect(familyOfExt("docx")).toBe("document");
    expect(familyOfExt("zzz")).toBeNull();
  });
  it("sniffs magic bytes", () => {
    const png = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    expect(sniff(png)).toBe("png");
    expect(sniff(new TextEncoder().encode("%PDF-1.7"))).toBe("pdf");
    expect(sniff(new TextEncoder().encode("\0\0\0\x18ftypheic"))).toBe("heic");
  });
});

describe("options", () => {
  it("parses times", () => {
    expect(parseTime("90")).toBe(90);
    expect(parseTime("1:30")).toBe(90);
    expect(parseTime("01:00:01.5")).toBe(3601.5);
    expect(parseTime("")).toBe(0);
    expect(() => parseTime("abc")).toThrow();
  });
  it("parses page ranges", () => {
    expect(parsePageRange("all", 3)).toEqual([0, 1, 2]);
    expect(parsePageRange("1-2, 4", 5)).toEqual([0, 1, 3]);
    expect(parsePageRange("4-", 5)).toEqual([3, 4]);
    expect(() => parsePageRange("x", 5)).toThrow();
    expect(parsePageGroups("1, 2-", 3)).toEqual([[0], [1, 2]]);
  });
  it("hides options for other targets and by condition", () => {
    const quality = CONVERTERS[0].options.find((o) => o.name === "quality")!;
    expect(visible(quality, {}, "jpg")).toBe(true);
    expect(visible(quality, {}, "png")).toBe(false);
    const scale = CONVERTERS[0].options.find((o) => o.name === "scale_percent")!;
    expect(visible(scale, { resize: "none" }, "png")).toBe(false);
    expect(visible(scale, { resize: "percent" }, "png")).toBe(true);
  });
  it("every converter has defaults for every option", () => {
    for (const c of CONVERTERS) expect(Object.keys(defaults(c.options))).toHaveLength(c.options.length);
  });
});

describe("registry", () => {
  it("offers image, slideshow and pdf targets for images", () => {
    const ids = targetsFor([input("a.png", "image"), input("b.jpg", "image")]).targets.map((t) => t.id);
    expect(ids).toEqual(expect.arrayContaining(["jpg", "webp", "avif", "slideshow:mp4", "slideshow:gif", "images:pdf"]));
    expect(targetsFor([input("a.png", "image")]).targets.map((t) => t.id)).not.toContain("slideshow:gif");
  });
  it("offers audio targets for audio files", () => {
    expect(familyOfExt("flac")).toBe("audio");
    const ids = targetsFor([input("song.wav", "audio")]).targets.map((t) => t.id);
    expect(ids).toEqual(expect.arrayContaining(["mp3", "m4a", "flac", "ogg", "opus", "aiff", "wma", "ac3"]));
    expect(sniff(new TextEncoder().encode("RIFF\0\0\0\0WAVEfmt "))).toBe("wav");
    expect(sniff(new TextEncoder().encode("ID3\x04"))).toBe("mp3");
    expect(sniff(new TextEncoder().encode("fLaC"))).toBe("flac");
  });
  it("offers video targets", () => {
    const ids = targetsFor([input("a.mp4", "video")]).targets.map((t) => t.id);
    expect(ids).toEqual(expect.arrayContaining(["webm", "mkv", "video:gif", "frames:png", "audio:mp3"]));
  });
  it("offers pdf targets and explains desktop-only formats", () => {
    expect(targetsFor([input("a.pdf", "document")]).targets.map((t) => t.id)).toEqual(
      expect.arrayContaining(["doc:pdf", "doc:txt", "pages:png", "split:pdf"]),
    );
    const doc = targetsFor([input("a.doc", "document")]);
    expect(doc.targets).toEqual([]);
    expect(doc.note).toContain("desktop app");
  });
  it("offers document targets for text, Word, Markdown and spreadsheets", () => {
    const txt = targetsFor([input("eye effect.txt", "document")]).targets.map((t) => t.id);
    expect(txt).toEqual(expect.arrayContaining(["doc:pdf", "doc:docx", "doc:md", "doc:html", "pages:png", "split:pdf"]));
    expect(txt).not.toContain("doc:txt"); // same format as the input
    const docx = targetsFor([input("a.docx", "document")]).targets.map((t) => t.id);
    expect(docx).toEqual(expect.arrayContaining(["doc:pdf", "doc:odt", "doc:md", "doc:epub", "doc:pptx"]));
    expect(targetsFor([input("a.xlsx", "document")]).targets.map((t) => t.id)).toEqual(expect.arrayContaining(["doc:pdf", "doc:html", "doc:docx"]));
  });
  it("mixed families can only be packed into an archive", () => {
    const r = targetsFor([input("a.png", "image"), input("b.mp4", "video")]);
    expect(r.targets.map((t) => t.group)).toEqual(["Archive", "Archive"]);
    expect(r.note).toBe("");
  });
  it("target ids are unique per input set", () => {
    const ids = targetsFor([input("a.png", "image"), input("b.png", "image")]).targets.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("video helpers", () => {
  it("keeps N evenly spaced frames out of 30", () => {
    expect(selectExpr(30)).toBe("");
    for (const n of [1, 3, 10, 15, 29]) {
      const kept = [...Array(30).keys()].filter((m) => Math.floor(((m + 1) * n) / 30) - Math.floor((m * n) / 30) === 1);
      expect(kept).toHaveLength(n);
      expect(selectExpr(n)).toContain(`*${n}/30`);
    }
  });
  it("computes trims", () => {
    expect(trimArgs("1", "", 10)).toEqual([["-ss", "1.000"], 9]);
    expect(trimArgs("", "3", 10)).toEqual([["-t", "3.000"], 3]);
    expect(() => trimArgs("20", "", 10)).toThrow();
  });
  it("chains atempo within 0.5–2", () => {
    expect(atempoChain(4)).toEqual(["atempo=2.0", "atempo=2.0000"]);
    expect(atempoChain(0.25)).toEqual(["atempo=0.5", "atempo=0.5000"]);
  });
  it("rejects codecs a container can't hold", () => {
    expect(resolveVideoCodec("mp4", "auto")).toBe("h264");
    expect(resolveVideoCodec("webm", "auto")).toBe("vp8");
    expect(() => resolveVideoCodec("webm", "h264")).toThrow(/can't be saved/);
  });
});

describe("slideshow helpers", () => {
  it("orders and sizes", () => {
    const files = [input("b10.png", "image", "png", 2), input("b2.png", "image", "png", 1)];
    expect(orderFiles(files, "name").map((f) => f.name)).toEqual(["b2.png", "b10.png"]);
    expect(orderFiles(files, "date").map((f) => f.name)).toEqual(["b2.png", "b10.png"]);
    expect(outputSize({ resolution: "720p", orientation: "landscape" }, [])).toEqual([1280, 720]);
    expect(outputSize({ resolution: "720p", orientation: "portrait" }, [])).toEqual([720, 1280]);
    expect(outputSize({ resolution: "first" }, [[641, 481]])).toEqual([640, 480]);
  });
});

describe("split helpers", () => {
  it("groups and names", () => {
    expect(pageGroups({ mode: "every" }, 3)).toEqual([[0], [1], [2]]);
    expect(pageGroups({ mode: "chunks", pages_per_document: 2 }, 3)).toEqual([[0, 1], [2]]);
    expect(groupName("doc", 0, 2, [0, 1, 2], "pages")).toBe("doc_pages_1-3");
    expect(groupName("doc", 1, 2, [3], "pages")).toBe("doc_page_4");
    expect(groupName("doc", 0, 12, [0], "part")).toBe("doc_part_01");
  });
  it("dedupes output names", () => {
    const u = uniqueNamer();
    expect([u("a.png"), u("a.png"), u("a.png")]).toEqual(["a.png", "a (1).png", "a (2).png"]);
  });
});
