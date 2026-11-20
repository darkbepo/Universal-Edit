// Fonts for the video editor's text: the bundled DejaVu Sans (what the desktop's libass falls back
// to), and a check whether a typed family is really there. document.fonts.check() says yes in
// Chrome for fonts that aren't installed, so the check measures text instead.
import { FALLBACK_FONT } from "../../videoedit/textlayout";

const SAMPLE = "mmmmmmmmmmlli WQ@#0 1.234 ÄÖ";

/** A family is present when text in it measures differently from both generic fallbacks. */
export function fontAvailable(family: string, measure: (font: string) => number): boolean {
  const q = `"${family.replace(/"/g, "")}"`;
  return measure(`${q}, monospace`) !== measure("monospace") || measure(`${q}, serif`) !== measure("serif");
}

let loading: Promise<void> | null = null;
let loaded = false;
const known = new Map<string, boolean>();

function fontSet(): FontFaceSet | null {
  if (typeof document !== "undefined") return document.fonts;
  const scope = globalThis as unknown as { fonts?: FontFaceSet };
  return scope.fonts ?? null;
}

/** DejaVu Sans regular / Bold / Oblique / BoldOblique as FontFaces, loaded once. */
export function loadBundledFonts(): Promise<void> {
  loading ??= (async () => {
    const faces: Array<[Promise<{ default: string }>, string, string]> = [
      [import("dejavu-fonts-ttf/ttf/DejaVuSans.ttf?url"), "normal", "normal"],
      [import("dejavu-fonts-ttf/ttf/DejaVuSans-Bold.ttf?url"), "bold", "normal"],
      [import("dejavu-fonts-ttf/ttf/DejaVuSans-Oblique.ttf?url"), "normal", "italic"],
      [import("dejavu-fonts-ttf/ttf/DejaVuSans-BoldOblique.ttf?url"), "bold", "italic"],
    ];
    const set = fontSet();
    await Promise.all(
      faces.map(async ([mod, weight, style]) => {
        const { default: url } = await mod;
        const face = new FontFace(FALLBACK_FONT, `url(${url})`, { weight, style });
        await face.load();
        set?.add(face);
      }),
    );
    loaded = true;
    known.delete(FALLBACK_FONT);
  })();
  return loading;
}

let measurer: { font: string; measureText(s: string): { width: number } } | null = null;

function measureWith(font: string): number {
  if (!measurer) {
    measurer =
      typeof OffscreenCanvas !== "undefined"
        ? new OffscreenCanvas(1, 1).getContext("2d")
        : document.createElement("canvas").getContext("2d");
  }
  if (!measurer) return 0;
  measurer.font = `72px ${font}`;
  return measurer.measureText(SAMPLE).width;
}

/** Is `family` usable for drawing here? Cached per family. */
export function hasFont(family: string): boolean {
  if (family === FALLBACK_FONT && loaded) return true;
  let v = known.get(family);
  if (v === undefined) {
    v = fontAvailable(family, measureWith);
    known.set(family, v);
  }
  return v;
}
