// Fonts: TTF / OTF ↔ WOFF ↔ WOFF2, with subsetting — same target ids and options as the desktop
// app (converters/fonts.py). Changing TrueType ↔ CFF outlines (TTF ↔ OTF) is desktop-only.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type OutputFile } from "../core/types";
import { baseName } from "./util";

const FORMATS: Record<string, string> = { ttf: "TTF", otf: "OTF", woff: "WOFF", woff2: "WOFF2" };
const MIME: Record<string, string> = { ttf: "font/ttf", otf: "font/otf", woff: "font/woff", woff2: "font/woff2" };

// Must match LATIN / LATIN_EXT / CYRILLIC / GREEK in src/umc/converters/fonts.py (tests check).
export const LATIN = "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";
export const SUBSETS: Record<string, string> = {
  latin: LATIN,
  "latin-ext": LATIN + ",U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF",
  cyrillic: LATIN + ",U+0301,U+0400-045F,U+0490-0491,U+04B0-04B1,U+2116",
  greek: LATIN + ",U+0370-0377,U+037A-037F,U+0384-038A,U+038C,U+038E-03A1,U+03A3-03FF",
};

export const fontConverter: Converter = {
  id: "fonts",
  family: "font",
  options: [
    opt.choice("subset", "Characters to keep", "all", [
      ["all", "All (no subsetting)"], ["latin", "Latin (English, Western Europe)"], ["latin-ext", "Latin extended (all European Latin)"],
      ["cyrillic", "Latin + Cyrillic"], ["greek", "Latin + Greek"], ["custom", "Only the characters I type…"],
    ], { help: "Smaller files for the web: drop the characters you don't need." }),
    opt.text("custom_text", "Characters", "", { help: "Type or paste every character to keep, e.g. a heading or a logo text.", showIf: (v) => v.subset === "custom" }),
    opt.bool("keep_hinting", "Keep hinting", true, { help: "Hinting makes small text crisper on Windows. Off = smaller file." }),
    opt.bool("all_features", "Keep all OpenType features", true, { help: "Ligatures, stylistic sets, small caps… Off keeps only the common ones (smaller).", showIf: (v) => v.subset !== "all" }),
  ],
  targets(files) {
    const exts = new Set(files.map((f) => f.ext));
    return Object.entries(FORMATS)
      // a .ttf can't become .otf (or the other way) in the browser: that needs the desktop app
      .filter(([e]) => !(files.length === 1 && exts.has(e)) && !(e === "otf" && exts.has("ttf")) && !(e === "ttf" && exts.has("otf")))
      .map(([e, label]) => ({ id: e, label, ext: e, group: "Font", converterId: "fonts" }));
  },
  async run(files, target, o: Values, ctx) {
    const f = await import("../engines/fonts");
    const out: OutputFile[] = [];
    for (const [i, file] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${file.name}`);
      let sfnt = await f.toSfnt(new Uint8Array(await file.file.arrayBuffer()), file.name);
      const outline = f.outlinesOf(sfnt);
      if ((target.ext === "ttf" && outline === "otf") || (target.ext === "otf" && outline === "ttf")) {
        throw new ConversionError(`“${file.name}” has ${outline === "otf" ? "CFF (OTF)" : "TrueType"} outlines. Changing them to ${target.ext.toUpperCase()} needs the desktop app — WOFF / WOFF2 work here.`);
      }
      if (o.subset !== "all" || !o.keep_hinting) {
        let codepoints: number[] | null = null;
        if (o.subset === "custom") {
          const text = String(o.custom_text ?? "");
          if (!text.trim()) throw new ConversionError("Type the characters to keep (or choose another “Characters to keep”).");
          codepoints = [...new Set([...(text + " ")].map((c) => c.codePointAt(0)!))];
        } else if (o.subset !== "all") {
          codepoints = f.parseUnicodes(SUBSETS[String(o.subset)]);
        }
        sfnt = await f.subset(sfnt, codepoints, { hinting: Boolean(o.keep_hinting), allFeatures: Boolean(o.all_features) || o.subset === "all" });
      }
      out.push({ name: `${baseName(file.name)}.${target.ext}`, data: await f.fromSfnt(sfnt, target.ext as never), type: MIME[target.ext] });
    }
    ctx.progress(1, "Done");
    return out;
  },
};
