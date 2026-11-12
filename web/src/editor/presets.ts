// New-picture sizes and backgrounds (no other imports: the start page loads this without the
// editor). Same as PRESETS / BACKGROUNDS in src/umc/editor/blank.py.

/** [label, width, height]; the first is the default. */
export const PRESETS: [string, number, number][] = [
  ["Full HD (1920 × 1080)", 1920, 1080],
  ["Square (1080 × 1080)", 1080, 1080],
  ["Phone story (1080 × 1920)", 1080, 1920],
  ["HD (1280 × 720)", 1280, 720],
  ["4K (3840 × 2160)", 3840, 2160],
  ["A4 print, 300 dpi (2480 × 3508)", 2480, 3508],
];

export type Background = "white" | "transparent" | "black";
export const BACKGROUNDS: Record<Background, [string, [number, number, number, number]]> = {
  white: ["White", [255, 255, 255, 255]],
  transparent: ["Transparent", [0, 0, 0, 0]],
  black: ["Black", [0, 0, 0, 255]],
};
