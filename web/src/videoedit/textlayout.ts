// Where text items sit on a frame (port of src/umc/videoedit/text_ass.py). Pure: a later step measures and draws.
import type { Project, TextItem } from "./project";

export const FALLBACK_FONT = "DejaVu Sans";

// anchor → [column, row]
const ANCHORS: Record<string, [0 | 1 | 2, 0 | 1 | 2]> = {
  "top-left": [0, 0],
  top: [1, 0],
  "top-right": [2, 0],
  left: [0, 1],
  center: [1, 1],
  right: [2, 1],
  "bottom-left": [0, 2],
  bottom: [1, 2],
  "bottom-right": [2, 2],
};
const BASE = [0.05, 0.5, 0.95];

export interface TextBox {
  itemId: string;
  lines: string[];
  family: string;
  bold: boolean;
  italic: boolean;
  fontPx: number;
  x: number;
  y: number;
  column: 0 | 1 | 2;
  row: 0 | 1 | 2;
  colour: string;
  outlineColour: string;
  outlineWidth: number;
  box: { colour: string; opacity: number; padding: number } | null;
  alpha: number;
}

export function fontFamily(t: TextItem, has: (family: string) => boolean): string {
  return t.font && (has(t.font) || t.font === FALLBACK_FONT) ? t.font : FALLBACK_FONT;
}

/** Fade-in / fade-out, scaled down together when they don't fit in the item (text_ass._fades). */
export function textFades(t: TextItem): [number, number] {
  let fin = Math.max(t.fade_in, 0);
  let fout = Math.max(t.fade_out, 0);
  const span = t.end - t.start;
  if (fin + fout > span && span > 0) {
    const scale = span / (fin + fout);
    fin *= scale;
    fout *= scale;
  }
  return [fin, fout];
}

function alphaAt(t: TextItem, time: number): number {
  const [fin, fout] = textFades(t);
  let a = 1;
  if (fin > 0 && time < t.start + fin) a = Math.min(a, (time - t.start) / fin);
  if (fout > 0 && time > t.end - fout) a = Math.min(a, (t.end - time) / fout);
  return Math.max(0, Math.min(1, a));
}

export function textAt(p: Project, t: number, size: [number, number], has: (family: string) => boolean): TextBox[] {
  const [w, h] = size;
  const out: TextBox[] = [];
  for (const row of p.text_rows) {
    for (const it of row) {
      if (!(it.start <= t && t < it.end) || !it.text) continue;
      const [column, r] = ANCHORS[it.anchor] ?? ANCHORS.center;
      out.push({
        itemId: it.id,
        lines: it.text.split(/\r\n|\r|\n/),
        family: fontFamily(it, has),
        bold: it.bold,
        italic: it.italic,
        fontPx: Math.round((it.size / 100) * h),
        x: Math.round(w * (BASE[column] + it.x_offset / 100)),
        y: Math.round(h * (BASE[r] + it.y_offset / 100)),
        column,
        row: r,
        colour: it.colour,
        outlineColour: it.outline_colour,
        outlineWidth: it.outline,
        box: it.box ? { colour: it.box_colour, opacity: it.box_opacity / 100, padding: it.outline } : null,
        alpha: alphaAt(it, t),
      });
    }
  }
  return out;
}
