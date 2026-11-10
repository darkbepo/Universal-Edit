// Floating bar next to a crop rectangle or a selection: size, Apply / Cancel. Port of
// src/umc/gui/editor/crop_bar.py — the canvas shows it only once a rectangle exists and the
// pointer is up, next to the corner where the drag ended, and moves it with zoom / pan.
import { h } from "./panels";

export const GAP = 8; // between the rectangle and the bar
export const MARGIN = 4; // keep this far from the canvas edges

export type BarMode = "crop" | "select";
type Box = [number, number, number, number]; // x0, y0, x1, y1

/** [right, bottom]: the corner of `rect` nearest to (x, y). */
export function nearestCorner([x0, y0, x1, y1]: Box, x: number, y: number): [boolean, boolean] {
  return [Math.abs(x - x1) < Math.abs(x - x0), Math.abs(y - y1) < Math.abs(y - y0)];
}

/** Top-left position for a bar of `size` next to `corner` of `rect`, inside `area`: outside the
 *  rectangle (below a bottom corner, above a top one), the other side if there's no room, inside
 *  as a last resort; lined up with the corner's side; clamped to `area`. */
export function placeBar([x0, y0, x1, y1]: Box, [right, bottom]: [boolean, boolean], [w, h]: [number, number], [aw, ah]: [number, number]): [number, number] {
  let x = right ? x1 - w : x0;
  const below = y1 + GAP;
  const above = y0 - GAP - h;
  const fitsBelow = below + h <= ah - MARGIN;
  const fitsAbove = above >= MARGIN;
  let y = bottom
    ? fitsBelow ? below : fitsAbove ? above : y1 - GAP - h
    : fitsAbove ? above : fitsBelow ? below : y0 + GAP;
  x = Math.max(MARGIN, Math.min(x, aw - w - MARGIN));
  y = Math.max(MARGIN, Math.min(y, ah - h - MARGIN));
  return [Math.round(x), Math.round(y)];
}

const LABELS: Record<BarMode, [string, string, string, string]> = {
  crop: ["Apply crop", "Cancel", "Crop to the rectangle (Enter)", "Discard the rectangle (Esc)"],
  select: ["Crop to selection", "Deselect", "Crop the image to the selection", "Remove the selection (Ctrl+D)"],
};

export class CropBar {
  readonly el = h("div", { class: "crop-bar", hidden: true, role: "toolbar", ariaLabel: "Crop" });
  readonly size = h("span", { class: "size" });
  readonly apply = h("button", { class: "btn small primary-small" });
  readonly cancel = h("button", { class: "btn small" });
  mode: BarMode = "crop";

  constructor(onApply: (mode: BarMode) => void, onCancel: (mode: BarMode) => void) {
    this.apply.addEventListener("click", () => onApply(this.mode));
    this.cancel.addEventListener("click", () => onCancel(this.mode));
    // The canvas must not see presses on the bar (they'd start a new rectangle).
    this.el.addEventListener("pointerdown", (e) => e.stopPropagation());
    this.el.append(this.size, this.apply, this.cancel);
  }

  showFor(mode: BarMode, width: number, height: number): void {
    if (mode !== this.mode || !this.apply.textContent) {
      this.mode = mode;
      const [a, c, ta, tc] = LABELS[mode];
      this.apply.textContent = a;
      this.cancel.textContent = c;
      this.apply.title = ta;
      this.cancel.title = tc;
    }
    const text = `${width} × ${height} px`;
    if (this.size.textContent !== text) this.size.textContent = text;
    this.el.hidden = false;
  }

  hide(): void {
    this.el.hidden = true;
  }
}
