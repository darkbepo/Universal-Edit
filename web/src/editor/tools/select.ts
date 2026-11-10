// Rectangle selection, crop rectangle and selection commands (port of tools/select.py).
import { docBounds, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, SelectionCommand, type Command } from "../history";
import { cropData } from "../layer";
import { clipRect, Tool, type ToolContext } from "./base";

abstract class DragRectTool extends Tool {
  protected start: [number, number] | null = null;

  protected rect(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    const [sx, sy] = this.start as [number, number];
    if (Math.hypot(x - sx, y - sy) < 1) return null;
    // snap to the nearest pixel edge (the pointer is usually between two pixels)
    const r = Rect.fromPoints(Math.round(sx), Math.round(sy), Math.round(x), Math.round(y)).intersect(docBounds(doc));
    return r.empty ? null : r;
  }
}

export class SelectTool extends DragRectTool {
  readonly id = "select";
  readonly label = "Rectangle select";
  readonly shortcut = "M";
  private before: Rect | null = null;

  override press(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.start = [x, y];
    this.before = doc.selection;
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    if (this.start) doc.selection = this.rect(doc, x, y);
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Command | null {
    if (!this.start) return null;
    doc.selection = this.rect(doc, x, y);
    this.start = null;
    const same = doc.selection === this.before || (doc.selection && doc.selection.equals(this.before));
    if (same) return null;
    return new SelectionCommand(this.before, doc.selection, doc.selection ? "Select" : "Deselect");
  }

  override cancel(doc: EditorDoc): Rect | null {
    if (this.start) {
      doc.selection = this.before;
      this.start = null;
    }
    return null;
  }
}

/** Drag a rectangle; the page applies it with ops.crop on Enter. */
export class CropTool extends DragRectTool {
  readonly id = "crop";
  readonly label = "Crop";
  readonly shortcut = "C";
  pending: Rect | null = null;

  override press(_doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    this.start = [x, y];
    this.pending = null;
    return null;
  }

  override move(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Rect | null {
    if (this.start) this.pending = this.rect(doc, x, y);
    return null;
  }

  override release(doc: EditorDoc, x: number, y: number, _ctx?: ToolContext): Command | null {
    this.move(doc, x, y);
    this.start = null;
    return null;
  }

  override cancel(_doc?: EditorDoc): Rect | null {
    this.start = null;
    this.pending = null;
    return null;
  }
}

export function selectAll(doc: EditorDoc): Command | null {
  const before = doc.selection;
  doc.selection = docBounds(doc);
  return doc.selection.equals(before) ? null : new SelectionCommand(before, doc.selection, "Select all");
}

export function deselect(doc: EditorDoc): Command | null {
  if (!doc.selection) return null;
  const before = doc.selection;
  doc.selection = null;
  return new SelectionCommand(before, null, "Deselect");
}

/** Make the selected part of the active layer transparent. */
export function clearSelection(doc: EditorDoc): Command | null {
  if (!doc.selection) return null;
  const layer = doc.layers[doc.active];
  const region = clipRect(doc, layer);
  if (region.empty) return null;
  const before = cropData(layer.data, layer.width, region);
  for (let y = region.y0; y < region.y1; y++) layer.data.fill(0, (y * layer.width + region.x0) * 4, (y * layer.width + region.x1) * 4);
  return PatchCommand.fromRegion("Clear selection", doc, doc.active, region, before);
}
