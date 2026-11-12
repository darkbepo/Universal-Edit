// How Properties shows values: a line-by-line port of the desktop's properties/format.py. Both
// test suites check tests/fixtures/properties_format_cases.json.

const PAPER_MM: Record<string, [number, number]> = { // portrait sizes, matched within 2 mm either way round
  A3: [297, 420],
  A4: [210, 297],
  A5: [148, 210],
  Letter: [215.9, 279.4],
  Legal: [215.9, 355.6],
};

// Python rounds an exact tie to the even neighbour (round(2.5) = 2, f"{1.125:.2f}" = "1.12");
// JS rounds it up. These copy Python, so both apps show the same text (ties are common: sizes
// divided by 1024).
/** Python's ``round(v)``. */
export function roundEven(v: number): number {
  const f = Math.floor(v);
  const diff = v - f;
  if (diff !== 0.5) return Math.round(v);
  return f % 2 === 0 ? f : f + 1;
}

/** Python's ``f"{v:.{d}f}"``. A real tie (the double's exact value ends in 5 right after the
 * kept digits) has at most d + 1 decimals, so ``toFixed(100)`` (exact digits) shows it. */
export function fixed(v: number, d: number): string {
  const exact = Math.abs(v).toFixed(100).split(".")[1];
  if (exact[d] === "5" && /^0*$/.test(exact.slice(d + 1))) {
    const scale = 10 ** d;
    return (roundEven(v * scale) / scale).toFixed(d);
  }
  return v.toFixed(d);
}

/** Fixed decimals, trailing zeros (and a bare point) dropped: 2.50 → "2.5", 8.0 → "8". */
function num(v: number, decimals: number): string {
  const text = fixed(v, decimals);
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

export const count = (n: number): string => Math.round(n).toLocaleString("en-US");

/** Like Windows: 1 KB = 1024 bytes, about three significant digits. */
export function size(n: number): string {
  if (n < 1024) return n === 1 ? "1 byte" : `${n} bytes`;
  let v = n;
  const units = ["KB", "MB", "GB", "TB"];
  for (const unit of units) {
    v /= 1024;
    if (v < 1024 || unit === "TB") {
      const text = v < 10 ? fixed(v, 2) : v < 100 ? fixed(v, 1) : fixed(v, 0);
      return `${text} ${unit}`;
    }
  }
  throw new Error("unreachable");
}

/** "2.30 MB (2,412,345 bytes)"; small files just "512 bytes". */
export const sizeExact = (n: number): string => (n < 1024 ? size(n) : `${size(n)} (${count(n)} bytes)`);

export function duration(seconds: number): string {
  if (seconds < 60) return `${num(seconds, 1)} s`;
  const total = roundEven(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const two = (x: number) => String(x).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`;
}

/** A moment → local "2026-11-12 14:03". */
export function date(d: Date): string {
  const two = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

export function exposure(t: number): string {
  if (t <= 0) return "";
  if (t >= 1) return `${num(t, 1)} s`;
  return `1/${roundEven(1 / t)} s`;
}

export const aperture = (f: number): string => `f/${num(f, 1)}`;
export const focal = (mm: number): string => `${num(mm, 1)} mm`;
export const location = (lat: number, lon: number): string => `${fixed(lat, 5)}, ${fixed(lon, 5)}`;
export const pixels = (w: number, h: number): string => `${count(w)} × ${count(h)} px`;
export const megapixels = (w: number, h: number): string => `${num((w * h) / 1e6, 1)} MP`;
export const fps = (rate: number): string => `${num(rate, 2)} fps`;

export function bitrate(bitsPerS: number): string {
  if (bitsPerS >= 1e6) return `${num(bitsPerS / 1e6, 1)} Mbit/s`;
  return `${roundEven(bitsPerS / 1e3)} kbit/s`;
}

export const sampleRate = (hz: number): string => `${num(hz / 1000, 1)} kHz`;

export function channels(n: number): string {
  return ({ 1: "Mono", 2: "Stereo", 6: "5.1 surround", 8: "7.1 surround" } as Record<number, string>)[n] ?? `${n} channels`;
}

/** A PDF page size in points → "A4 (210 × 297 mm)", "Letter, landscape (…)" or "… mm". */
export function paper(widthPt: number, heightPt: number): string {
  const w = (widthPt * 25.4) / 72;
  const h = (heightPt * 25.4) / 72;
  const dims = `${num(w, 0)} × ${num(h, 0)} mm`;
  for (const [name, [pw, ph]] of Object.entries(PAPER_MM)) {
    if (Math.abs(w - pw) <= 2 && Math.abs(h - ph) <= 2) return `${name} (${dims})`;
    if (Math.abs(w - ph) <= 2 && Math.abs(h - pw) <= 2) return `${name}, landscape (${dims})`;
  }
  return dims;
}
