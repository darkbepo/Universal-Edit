// The editors shown as tiles on the empty start screen (same list in src/umc/core/editors.py;
// tests/test_editor_hub.py checks they match). available false = greyed out, "Coming soon".
import type { IconName } from "../ui/icon_data";

export interface EditorInfo {
  id: string;
  title: string;
  blurb: string;
  icon: IconName; // Tabler icon (ui/icon_data.ts)
  available: boolean;
}

export const EDITORS: EditorInfo[] = [
  { id: "image", title: "Image editor", blurb: "Paint, layers, retouch and effects", icon: "photo", available: true },
  { id: "video", title: "Video editor", blurb: "Trim, join and mix clips", icon: "movie", available: true },
  { id: "audio", title: "Audio editor", blurb: "Cut, fade and clean up sound", icon: "music", available: true },
  { id: "document", title: "Document editor", blurb: "Write and format documents", icon: "document", available: true },
  { id: "code", title: "Code editor", blurb: "Quick edits to code and text files", icon: "code", available: true },
  { id: "model", title: "3D viewer", blurb: "Look at and measure 3D models", icon: "cube", available: true },
  { id: "vector", title: "Vector editor", blurb: "Edit shapes and paths in SVG drawings", icon: "vector", available: false },
  { id: "font", title: "Font editor", blurb: "Change letters, spacing and font names", icon: "font", available: false },
  { id: "subtitle", title: "Subtitle editor", blurb: "Fix subtitle text and timing", icon: "subtitle", available: true },
  { id: "data", title: "Data editor", blurb: "Edit CSV, JSON, XML and spreadsheets", icon: "table", available: false },
];

export const COMING_SOON = "Coming soon";

// Start-screen tile rows (owner, Nov 2026): as many tiles per row as fit, spread evenly over the
// rows so no tile is left alone, each row centred; the tiles stretch to fill the width.
export const MIN_TILE_W = 260;
export const TILE_GAP = 10;

/** How many tiles go in each row for `count` tiles in `width` px (same as core/editors.py). */
export function hubRows(count: number, width: number, minTile = MIN_TILE_W, gap = TILE_GAP): number[] {
  if (count <= 0) return [];
  const fit = Math.max(1, Math.floor((width + gap) / (minTile + gap)));
  const rows = Math.ceil(count / fit);
  const base = Math.floor(count / rows), extra = count % rows;
  return [...Array<number>(extra).fill(base + 1), ...Array<number>(rows - extra).fill(base)];
}

/** Every tile's width: the longest row fills `width`. */
export function hubTileWidth(rows: number[], width: number, gap = TILE_GAP): number {
  const most = Math.max(1, ...rows);
  return Math.max(1, (width - gap * (most - 1)) / most);
}
