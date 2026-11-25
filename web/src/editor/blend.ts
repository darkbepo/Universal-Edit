// Layer blend modes — a line-by-line port of src/umc/editor/blend.py (shared cases
// tests/fixtures/blend_cases.json). Separable W3C modes mixed with source-over:
// co = αs·(1−αb)·Cs + αs·αb·B(Cb, Cs) + (1−αs)·αb·Cb, αo = αs + αb·(1−αs).

export const MODES: [string, string][] = [
  ["normal", "Normal"],
  ["multiply", "Multiply"],
  ["screen", "Screen"],
  ["overlay", "Overlay"],
  ["darken", "Darken"],
  ["lighten", "Lighten"],
  ["color-dodge", "Colour dodge"],
  ["color-burn", "Colour burn"],
  ["hard-light", "Hard light"],
  ["soft-light", "Soft light"],
  ["difference", "Difference"],
  ["exclusion", "Exclusion"],
];
export const IDS = MODES.map(([m]) => m);

export const oraOp = (mode: string) => (mode === "normal" ? "svg:src-over" : `svg:${mode}`);

/** `composite-op` → a mode; anything we don't do (or none) is normal. */
export function fromOra(op: string | null | undefined): string {
  const name = (op ?? "").replace(/^svg:/, "");
  return IDS.includes(name) && name !== "normal" ? name : "normal";
}

/** B(Cb, Cs) for colour values in 0…1. */
export function mix(mode: string, cb: number, cs: number): number {
  switch (mode) {
    case "multiply": return cb * cs;
    case "screen": return cb + cs - cb * cs;
    case "overlay": return mix("hard-light", cs, cb);
    case "darken": return Math.min(cb, cs);
    case "lighten": return Math.max(cb, cs);
    case "color-dodge":
      if (cb === 0) return 0;
      if (cs >= 1) return 1;
      return Math.min(1, cb / (1 - cs));
    case "color-burn":
      if (cb >= 1) return 1;
      if (cs <= 0) return 0;
      return 1 - Math.min(1, (1 - cb) / cs);
    case "hard-light": return cs <= 0.5 ? cb * (2 * cs) : mix("screen", cb, 2 * cs - 1);
    case "soft-light": {
      const d = cb <= 0.25 ? ((16 * cb - 12) * cb + 4) * cb : Math.sqrt(cb);
      return cs <= 0.5 ? cb - (1 - 2 * cs) * cb * (1 - cb) : cb + (2 * cs - 1) * (d - cb);
    }
    case "difference": return Math.abs(cb - cs);
    case "exclusion": return cb + cs - 2 * cb * cs;
    default: throw new RangeError(mode);
  }
}

const byte = (v: number) => Math.min(255, Math.max(0, Math.floor(v * 255 + 0.5)));

/** One source pixel (alpha already × opacity × mask, as 0…1) onto `dst` at `i`, mixed with `mode`
 * (not "normal": callers keep their own source-over for that). */
export function blendPixel(dst: Uint8ClampedArray, i: number, r: number, g: number, b: number, sa: number, mode: string): void {
  if (sa <= 0) return;
  const ba = dst[i + 3] / 255;
  const ao = sa + ba * (1 - sa);
  const src = [r / 255, g / 255, b / 255];
  for (let c = 0; c < 3; c++) {
    const cs = src[c];
    const cb = dst[i + c] / 255;
    const co = sa * (1 - ba) * cs + sa * ba * mix(mode, cb, cs) + (1 - sa) * ba * cb;
    dst[i + c] = ao > 0 ? byte(co / ao) : 0;
  }
  dst[i + 3] = byte(ao);
}
