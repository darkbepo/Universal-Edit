// Brush and eraser: round dabs along the pointer path (port of tools/paint.py). The original
// pixels are remembered per 128 px tile, only where the stroke goes — never a whole-layer copy.
import { opt } from "../../core/options";
import { composite, selectedIndices, type EditorDoc } from "../document";
import { Rect } from "../geometry";
import { LayerListCommand, MaskPatchCommand, PatchCommand, type Command } from "../history";
import { cropData, layerBounds, solidMask, type Layer, type RGBA } from "../layer";
import { blendPixel, clipRect, colorOf, selectionWeight, Tool, type ToolContext } from "./base";

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

  /** The layer the stroke paints on (the mask brush paints the mask, laid out like pixels). */
  protected target(doc: EditorDoc, index: number): Layer {
    return doc.layers[index];
  }

  /** The undo step for a finished stroke. */
  protected command(doc: EditorDoc, index: number, dirty: Rect, before: Uint8ClampedArray): Command | null {
    return PatchCommand.fromRegion(this.label, doc, index, dirty, before);
  }

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    const layer = this.target(doc, doc.active);
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
    const before = this.original(this.target(doc, idx), dirty);
    this.tiles = new Map();
    return this.command(doc, idx, dirty, before);
  }

  override cancel(doc: EditorDoc): Rect | null {
    if (this.layerIndex === null || !this.dirty) {
      this.layerIndex = null;
      return null;
    }
    const layer = this.target(doc, this.layerIndex);
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
    const layer = this.target(doc, this.layerIndex as number);
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
        const sel = selectionWeight(doc, layer, part); // soft / shaped selections paint partly
        for (let py = part.y0; py < part.y1; py++) {
          for (let px = part.x0; px < part.x1; px++) {
            const m = Math.min(1, Math.max(0, (radius - Math.hypot(px + 0.5 - cx, py + 0.5 - cy)) / soft)) * strength * (sel ? sel[(py - part.y0) * part.width + (px - part.x0)] : 1);
            const ti = (py - t.rect.y0) * t.rect.width + (px - t.rect.x0);
            if (m <= t.cov[ti]) continue;
            t.cov[ti] = m;
            this.paintPixel(layer, (py * layer.width + px) * 4, t.base, ti * 4, px, py, m);
          }
        }
      }
    }
    this.dirty = this.dirty ? this.dirty.union(region) : region;
    return region.offset(layer.x, layer.y);
  }

  /** Layer pixel `di` (at px, py in layer coordinates) from its original `base[bi]`, coverage `m`. */
  protected paintPixel(layer: Layer, di: number, base: Uint8ClampedArray, bi: number, _px: number, _py: number, m: number): void {
    if (this.erase) {
      layer.data.set(base.subarray(bi, bi + 3), di);
      layer.data[di + 3] = base[bi + 3] * (1 - m) + 0.5;
    } else blendPixel(layer.data, di, base, bi, this.color, m);
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

/** A broad see-through marker: crossing your own stroke doesn't darken it. */
export class HighlighterTool extends StrokeTool {
  readonly id = "highlighter";
  readonly label = "Highlighter";
  readonly shortcut = "H";
  protected readonly erase = false;
  override readonly options = [
    opt.slider("size", "Size", 24, 2, 300, { unit: "px" }),
    opt.slider("hardness", "Hardness", 90, 0, 100, { unit: "%" }),
    opt.slider("opacity", "Strength", 40, 5, 100, { unit: "%" }),
  ];
}

/** Alt+click where to copy from, then paint: the pixels there are painted under the brush (port
 *  of CloneStampTool in paint.py). The source is a snapshot taken when a stroke starts;
 *  "aligned" keeps the brush–source distance for later strokes. */
export class CloneStampTool extends StrokeTool {
  readonly id = "clone";
  readonly label = "Clone stamp";
  readonly shortcut = "S";
  protected readonly erase = false;
  override readonly options = [
    opt.slider("size", "Size", 40, 1, 500, { unit: "px" }),
    opt.slider("hardness", "Hardness", 60, 0, 100, { unit: "%" }),
    opt.slider("opacity", "Opacity", 100, 1, 100, { unit: "%" }),
    opt.choice("sample", "Copy from", "layer", [["layer", "This layer"], ["all", "All layers"]]),
    opt.bool("aligned", "Aligned (the source moves along with every stroke)", true),
  ];
  source: [number, number] | null = null; // canvas coordinates
  offset: [number, number] | null = null; // brush − source
  notice: string | null = null;
  private src: { data: Uint8ClampedArray; width: number; height: number; x: number; y: number } | null = null;
  private layerXY: [number, number] = [0, 0];

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    if (ctx.alt) {
      this.source = [x, y];
      this.offset = null;
      return null;
    }
    if (!this.source) {
      this.notice = "Alt+click the place to copy from first.";
      return null;
    }
    if (!this.offset || !ctx.options.aligned) this.offset = [Math.round(x - this.source[0]), Math.round(y - this.source[1])];
    const l = doc.layers[doc.active];
    this.layerXY = [l.x, l.y];
    this.src = ctx.options.sample === "all" ? { data: composite(doc), width: doc.width, height: doc.height, x: 0, y: 0 } : { data: new Uint8ClampedArray(l.data), width: l.width, height: l.height, x: l.x, y: l.y };
    return super.press(doc, x, y, ctx);
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    const cmd = this.src ? super.release(doc, x, y, ctx) : null;
    this.src = null;
    return cmd;
  }

  override move(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    return this.src ? super.move(doc, x, y, ctx) : null;
  }

  /** Where the source is for the brush at (x, y) — drawn as a cross by the canvas. */
  marker(x: number, y: number): [number, number] | null {
    return this.offset ? [x - this.offset[0], y - this.offset[1]] : this.source;
  }

  protected override paintPixel(layer: Layer, di: number, base: Uint8ClampedArray, bi: number, px: number, py: number, m: number): void {
    const s = this.src;
    if (!s || !this.offset) return;
    const sx = px + this.layerXY[0] - this.offset[0] - s.x;
    const sy = py + this.layerXY[1] - this.offset[1] - s.y;
    const color: RGBA = sx >= 0 && sy >= 0 && sx < s.width && sy < s.height ? (Array.from(s.data.subarray((sy * s.width + sx) * 4, (sy * s.width + sx) * 4 + 4)) as RGBA) : [0, 0, 0, 0];
    blendPixel(layer.data, di, base, bi, color, m);
  }
}

/** Paints the active layer's mask: left button hides (black), right button shows (white). */
export class MaskBrushTool extends StrokeTool {
  readonly id = "mask_brush";
  readonly label = "Mask brush";
  readonly shortcut = "K";
  protected readonly erase = false;
  override readonly options = [
    opt.slider("size", "Size", 24, 1, 500, { unit: "px" }),
    opt.slider("hardness", "Hardness", 50, 0, 100, { unit: "%" }),
    opt.slider("opacity", "Strength", 100, 1, 100, { unit: "%" }),
  ];

  protected override target(doc: EditorDoc, index: number): Layer {
    const l = doc.layers[index];
    return { ...l, data: l.mask as Uint8ClampedArray }; // the mask, edited in place
  }

  protected override command(doc: EditorDoc, index: number, dirty: Rect, before: Uint8ClampedArray): Command | null {
    if (this.withoutMask) {
      const b = this.withoutMask;
      this.withoutMask = null;
      const sel = selectedIndices(doc);
      return new LayerListCommand(this.label, b, [...doc.layers], doc.active, doc.active, sel, sel);
    }
    return MaskPatchCommand.fromRegion(this.label, doc, index, dirty, before);
  }

  override release(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Command | null {
    const cmd = super.release(doc, x, y, ctx);
    if (cmd || !this.withoutMask) return cmd;
    // nothing painted (outside the layer), but the mask was added: keep it as one undo step
    return this.command(doc, doc.active, new Rect(0, 0, 0, 0), new Uint8ClampedArray(0));
  }

  override cancel(doc: EditorDoc): Rect | null {
    const r = super.cancel(doc);
    if (this.withoutMask) {
      doc.layers = this.withoutMask;
      this.withoutMask = null;
      return layerBounds(doc.layers[doc.active]);
    }
    return r;
  }

  /** The layers before the first stroke gave the active layer a mask (undo takes it away). */
  private withoutMask: Layer[] | null = null;

  override press(doc: EditorDoc, x: number, y: number, ctx: ToolContext): Rect | null {
    this.withoutMask = null;
    const l = doc.layers[doc.active];
    if (!l.mask) {
      this.withoutMask = [...doc.layers];
      doc.layers = [...doc.layers];
      doc.layers[doc.active] = { ...l, mask: solidMask(l.width, l.height, 255) };
    }
    const black: RGBA = [0, 0, 0, 255];
    const white: RGBA = [255, 255, 255, 255];
    return super.press(doc, x, y, { ...ctx, primary: black, secondary: white });
  }
}
