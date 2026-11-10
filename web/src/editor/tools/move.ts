// Move tool: drag the selected layers (Shift keeps the move horizontal or vertical).
// Port of src/umc/editor/tools/move.py.
import { selectedIndices, type EditorDoc } from "../document";
import { EMPTY, type Rect } from "../geometry";
import { LayerListCommand, type Command } from "../history";
import { layerBounds, withProps, type Layer } from "../layer";
import { Tool, type ToolContext } from "./base";

function bounds(layers: Layer[], indices: number[]): Rect {
  let r = EMPTY;
  for (const i of indices) r = r.empty ? layerBounds(layers[i]) : r.union(layerBounds(layers[i]));
  return r;
}

export class MoveTool extends Tool {
  readonly id = "move";
  readonly label = "Move";
  readonly shortcut = "V";
  override readonly cursor = "move" as const;
  private start: [number, number] | null = null;
  private before: Layer[] = [];
  private indices: number[] = [];
  private shown: Rect = EMPTY;

  private offset(x: number, y: number, ctx: ToolContext): [number, number] {
    const [sx, sy] = this.start as [number, number];
    let dx = Math.round(x - sx);
    let dy = Math.round(y - sy);
    if (ctx.shift) {
      if (Math.abs(dx) >= Math.abs(dy)) dy = 0;
      else dx = 0;
    }
    return [dx, dy];
  }

  private apply(doc: EditorDoc, dx: number, dy: number): Rect {
    const layers = [...this.before];
    for (const i of this.indices) layers[i] = withProps(this.before[i], { x: this.before[i].x + dx, y: this.before[i].y + dy });
    doc.layers = layers;
    const now = bounds(layers, this.indices);
    const dirty = this.shown.empty ? now : this.shown.union(now);
    this.shown = now;
    return dirty;
  }

  override press(doc: EditorDoc, x: number, y: number): Rect | null {
    this.start = [x, y];
    this.before = [...doc.layers];
    this.indices = selectedIndices(doc);
    this.shown = bounds(this.before, this.indices);
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    if (!this.start) return null;
    return this.apply(doc, ...this.offset(x, y, ctx));
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    if (!this.start) return null;
    const [dx, dy] = this.offset(x, y, ctx);
    this.apply(doc, dx, dy);
    this.start = null;
    if (dx === 0 && dy === 0) {
      doc.layers = this.before;
      return null;
    }
    const selected = [...(doc.selected ?? [])];
    return new LayerListCommand("Move", this.before, [...doc.layers], doc.active, doc.active, selected, selected);
  }

  override cancel(doc: EditorDoc): Rect | null {
    if (!this.start) return null;
    this.start = null;
    const dirty = this.apply(doc, 0, 0);
    doc.layers = this.before;
    return dirty;
  }
}
