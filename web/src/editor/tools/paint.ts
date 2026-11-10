// Brush and eraser: round dabs along the pointer path (port of tools/paint.py). The original
// pixels are remembered per 128 px tile, only where the stroke goes — never a whole-layer copy.
import { opt } from "../../core/options";
import type { EditorDoc } from "../document";
import { Rect } from "../geometry";
import { PatchCommand, type Command } from "../history";
import { cropData, type Layer, type RGBA } from "../layer";
import { blendPixel, clipRect, colorOf, Tool, type ToolContext } from "./base";

const TILE = 128;

interface Tile {
  rect: Rect;
  base: Uint8ClampedArray;
  cov: Float32Array;
}

abstract class StrokeTool extends Tool {
  protected abstract readonly erase: boolean;
  override readonly cursor = "brush" as const;
  private layerIndex: number | null = null;
  private tiles = new Map<number, Tile>();
  private clip = new Rect(0, 0, 0, 0);
  private dirty: Rect | null = null;
  private last: [number, number] = [0, 0];
  private color: RGBA = [0, 0, 0, 255];

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    const layer = doc.layers[doc.active];
    this.layerIndex = doc.active;
    this.tiles = new Map();
    this.clip = clipRect(doc, layer);
    this.dirty = null;
    this.last = [x, y];
    this.color = colorOf(ctx);
    return this.dab(doc, x, y, ctx);
  }

  override move(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    if (this.layerIndex === null) return null;
    const [lx, ly] = this.last;
    const dist = Math.hypot(x - lx, y - ly);
    const spacing = Math.max(1, Number(ctx.options.size) / 4);
    if (dist < spacing) return null;
    const steps = Math.floor(dist / spacing);
    let changed: Rect | null = null;
    for (let i = 1; i <= steps; i++) {
      const t = (i * spacing) / dist;
      const r = this.dab(doc, lx + (x - lx) * t, ly + (y - ly) * t, ctx);
      changed = changed ? changed.union(r) : r;
    }
    const t = (steps * spacing) / dist;
    this.last = [lx + (x - lx) * t, ly + (y - ly) * t];
    return changed;
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    if (this.layerIndex === null) return null;
    this.move(doc, x, y, ctx);
    const idx = this.layerIndex;
    this.layerIndex = null;
    const dirty = this.dirty;
    if (!dirty) {
      this.tiles = new Map();
      return null;
    }
    const before = this.original(doc.layers[idx], dirty);
    this.tiles = new Map();
    return PatchCommand.fromRegion(this.label, doc, idx, dirty, before);
  }

  override cancel(doc: EditorDoc): Rect | null {
    if (this.layerIndex === null || !this.dirty) {
      this.layerIndex = null;
      return null;
    }
    const layer = doc.layers[this.layerIndex];
    const orig = this.original(layer, this.dirty);
    const r = this.dirty;
    for (let y = 0; y < r.height; y++) layer.data.set(orig.subarray(y * r.width * 4, (y + 1) * r.width * 4), ((r.y0 + y) * layer.width + r.x0) * 4);
    this.layerIndex = null;
    this.tiles = new Map();
    return r.offset(layer.x, layer.y);
  }

  /** `rect` as it was before the stroke. */
  private original(layer: Layer, rect: Rect): Uint8ClampedArray {
    const out = cropData(layer.data, layer.width, rect);
    for (const t of this.tiles.values()) {
      const part = t.rect.intersect(rect);
      for (let y = part.y0; y < part.y1; y++) {
        const from = ((y - t.rect.y0) * t.rect.width + (part.x0 - t.rect.x0)) * 4;
        out.set(t.base.subarray(from, from + part.width * 4), ((y - rect.y0) * rect.width + (part.x0 - rect.x0)) * 4);
      }
    }
    return out;
  }

  private tile(layer: Layer, tx: number, ty: number): Tile {
    const key = ty * 100_000 + tx;
    let t = this.tiles.get(key);
    if (!t) {
      const rect = new Rect(tx * TILE, ty * TILE, Math.min(layer.width, (tx + 1) * TILE), Math.min(layer.height, (ty + 1) * TILE));
      t = { rect, base: cropData(layer.data, layer.width, rect), cov: new Float32Array(rect.width * rect.height) };
      this.tiles.set(key, t);
    }
    return t;
  }

  private dab(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    const layer = doc.layers[this.layerIndex as number];
    const cx = x - layer.x;
    const cy = y - layer.y;
    const radius = Math.max(0.5, Number(ctx.options.size) / 2);
    const region = Rect.around(cx, cy, radius).intersect(this.clip);
    if (region.empty) return null;
    const soft = Math.max(1, radius * (1 - Number(ctx.options.hardness) / 100));
    const strength = Number(ctx.options.opacity) / 100;
    for (let ty = Math.floor(region.y0 / TILE); ty <= Math.floor((region.y1 - 1) / TILE); ty++) {
      for (let tx = Math.floor(region.x0 / TILE); tx <= Math.floor((region.x1 - 1) / TILE); tx++) {
        const t = this.tile(layer, tx, ty);
        const part = t.rect.intersect(region);
        for (let py = part.y0; py < part.y1; py++) {
          for (let px = part.x0; px < part.x1; px++) {
            const m = Math.min(1, Math.max(0, (radius - Math.hypot(px + 0.5 - cx, py + 0.5 - cy)) / soft)) * strength;
            const ti = (py - t.rect.y0) * t.rect.width + (px - t.rect.x0);
            if (m <= t.cov[ti]) continue;
            t.cov[ti] = m;
            const di = (py * layer.width + px) * 4;
            if (this.erase) {
              layer.data.set(t.base.subarray(ti * 4, ti * 4 + 3), di);
              layer.data[di + 3] = t.base[ti * 4 + 3] * (1 - m) + 0.5;
            } else blendPixel(layer.data, di, t.base, ti * 4, this.color, m);
          }
        }
      }
    }
    this.dirty = this.dirty ? this.dirty.union(region) : region;
    return region.offset(layer.x, layer.y);
  }
}

export class BrushTool extends StrokeTool {
  readonly id = "brush";
  readonly label = "Brush";
  readonly shortcut = "B";
  protected readonly erase = false;
  override readonly options = [
    opt.slider("size", "Size", 12, 1, 500, { unit: "px" }),
    opt.slider("hardness", "Hardness", 80, 0, 100, { unit: "%" }),
    opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
  ];
}

export class EraserTool extends StrokeTool {
  readonly id = "eraser";
  readonly label = "Eraser";
  readonly shortcut = "E";
  protected readonly erase = true;
  override readonly options = [
    opt.slider("size", "Size", 24, 1, 500, { unit: "px" }),
    opt.slider("hardness", "Hardness", 80, 0, 100, { unit: "%" }),
    opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
  ];
}
