// Undo / redo — port of src/umc/editor/history.py (including the review fixes).
// Every change is applied first, then pushed as a Command. Layer objects are never mutated
// except pixel edits, which are recorded as patches of the changed rectangle only.
import type { EditorDoc } from "./document";
import { Rect } from "./geometry";
import { cropData, pasteData, type Layer } from "./layer";

export interface Command {
  label: string;
  undo(doc: EditorDoc): void;
  redo(doc: EditorDoc): void;
  readonly bytes: number;
  /** What to repaint after undo / redo, in canvas coordinates (null = everything). */
  repaint(): Rect | null;
}

export class PatchCommand implements Command {
  private origin: [number, number] = [0, 0];

  constructor(
    public label: string,
    public layerIndex: number,
    public rect: Rect, // layer coordinates
    public before: Uint8ClampedArray,
    public after: Uint8ClampedArray,
  ) {}

  /** `before` holds just `rect`; the current layer pixels become `after`. */
  static fromRegion(label: string, doc: EditorDoc, index: number, rect: Rect, before: Uint8ClampedArray): PatchCommand | null {
    const layer = doc.layers[index];
    if (rect.empty) return null;
    const cmd = new PatchCommand(label, index, rect, before, cropData(layer.data, layer.width, rect));
    cmd.origin = [layer.x, layer.y];
    return cmd;
  }

  private apply(doc: EditorDoc, data: Uint8ClampedArray): void {
    const layer = doc.layers[this.layerIndex];
    pasteData(layer.data, layer.width, data, this.rect);
    this.origin = [layer.x, layer.y];
  }

  undo(doc: EditorDoc): void {
    this.apply(doc, this.before);
  }

  redo(doc: EditorDoc): void {
    this.apply(doc, this.after);
  }

  get bytes(): number {
    return this.before.byteLength + this.after.byteLength;
  }

  repaint(): Rect {
    return this.rect.offset(this.origin[0], this.origin[1]);
  }
}

/** A changed rectangle of a layer's mask (the mask brush). Same as PatchCommand, on `mask`. */
export class MaskPatchCommand implements Command {
  private origin: [number, number] = [0, 0];

  constructor(
    public label: string,
    private index: number,
    private rect: Rect,
    private before: Uint8ClampedArray,
    private after: Uint8ClampedArray,
  ) {}

  static fromRegion(label: string, doc: EditorDoc, index: number, rect: Rect, before: Uint8ClampedArray): MaskPatchCommand | null {
    const l = doc.layers[index];
    if (rect.empty || !l.mask) return null;
    const cmd = new MaskPatchCommand(label, index, rect, before, cropData(l.mask, l.width, rect));
    cmd.origin = [l.x, l.y];
    return cmd;
  }

  private apply(doc: EditorDoc, src: Uint8ClampedArray): void {
    const l = doc.layers[this.index];
    if (!l.mask) return;
    const r = this.rect;
    for (let y = 0; y < r.height; y++) l.mask.set(src.subarray(y * r.width * 4, (y + 1) * r.width * 4), ((r.y0 + y) * l.width + r.x0) * 4);
    this.origin = [l.x, l.y];
  }

  undo(doc: EditorDoc): void {
    this.apply(doc, this.before);
  }

  redo(doc: EditorDoc): void {
    this.apply(doc, this.after);
  }

  get bytes(): number {
    return this.before.byteLength + this.after.byteLength;
  }

  repaint(): Rect {
    return this.rect.offset(this.origin[0], this.origin[1]);
  }
}

/** Bytes of the pixel arrays only one side holds (shared arrays cost nothing extra). */
function uniqueBytes(a: Layer[], b: Layer[]): number {
  const planes = (ls: Layer[]) => ls.flatMap((l) => (l.mask ? [l.data, l.mask] : [l.data]));
  const sa = new Set(planes(a));
  const sb = new Set(planes(b));
  let total = 0;
  for (const d of new Set([...sa, ...sb])) if (!(sa.has(d) && sb.has(d))) total += d.byteLength;
  return total;
}

export class LayerListCommand implements Command {
  private before: Layer[];
  private after: Layer[];

  constructor(
    public label: string,
    before: Layer[],
    after: Layer[],
    private activeBefore: number,
    private activeAfter: number,
    private selectedBefore: number[] = [],
    private selectedAfter: number[] = [],
  ) {
    this.before = [...before];
    this.after = [...after];
    this.selectedBefore = [...selectedBefore];
    this.selectedAfter = [...selectedAfter];
  }

  undo(doc: EditorDoc): void {
    doc.layers = [...this.before];
    doc.active = this.activeBefore;
    doc.selected = [...this.selectedBefore];
  }

  redo(doc: EditorDoc): void {
    doc.layers = [...this.after];
    doc.active = this.activeAfter;
    doc.selected = [...this.selectedAfter];
  }

  get bytes(): number {
    return uniqueBytes(this.before, this.after);
  }

  repaint(): null {
    return null;
  }
}

export type DocState = [number, number, Layer[]];

/** Canvas size + layers (crop, resize, canvas size, rotate, flip). */
export class DocStateCommand implements Command {
  private before: DocState;
  private after: DocState;

  constructor(
    public label: string,
    before: DocState,
    after: DocState,
  ) {
    this.before = [before[0], before[1], [...before[2]]];
    this.after = [after[0], after[1], [...after[2]]];
  }

  private set(doc: EditorDoc, [w, h, layers]: DocState): void {
    doc.width = w;
    doc.height = h;
    doc.layers = [...layers];
    doc.active = Math.min(doc.active, doc.layers.length - 1);
    doc.selection = null;
    doc.selectionMask = null;
  }

  undo(doc: EditorDoc): void {
    this.set(doc, this.before);
  }

  redo(doc: EditorDoc): void {
    this.set(doc, this.after);
  }

  get bytes(): number {
    return uniqueBytes(this.before[2], this.after[2]);
  }

  repaint(): null {
    return null;
  }
}

/** The selection before and after: its rectangle and (for other shapes) its mask. */
export class SelectionCommand implements Command {
  /** Set by arrow-key nudges: where the run of nudges started and how far it has gone. */
  nudge: { base: [Rect | null, Uint8Array | null]; dx: number; dy: number } | null = null;

  constructor(
    private before: Rect | null,
    readonly after: Rect | null,
    public label = "Select",
    private maskBefore: Uint8Array | null = null,
    readonly maskAfter: Uint8Array | null = null,
  ) {}

  undo(doc: EditorDoc): void {
    doc.selection = this.before;
    doc.selectionMask = this.maskBefore;
  }

  redo(doc: EditorDoc): void {
    doc.selection = this.after;
    doc.selectionMask = this.maskAfter;
  }

  get bytes(): number {
    return (this.maskBefore?.length ?? 0) + (this.maskAfter?.length ?? 0);
  }

  repaint(): null {
    return null;
  }
}

export class History {
  private undoStack: Command[] = [];
  private redoStack: Command[] = [];
  // Each pushed command gets a unique, ever-increasing token; a state is identified by the token
  // of the command on top of the undo stack (`base` when it is empty).
  private tokens = new WeakMap<Command, number>();
  private nextToken = 1;
  private base = 0;
  private saved: number | null = 0;

  constructor(
    readonly doc: EditorDoc,
    readonly limitBytes = 500 * 2 ** 20,
  ) {
    this.sync();
  }

  /** The command undo would take back next (null when there is none). */
  get last(): Command | null {
    return this.undoStack[this.undoStack.length - 1] ?? null;
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  get undoLabel(): string {
    return this.undoStack.at(-1)?.label ?? "";
  }

  get redoLabel(): string {
    return this.redoStack.at(-1)?.label ?? "";
  }

  /** Token for the current state; pass it to markSaved when a save started earlier. */
  get position(): number {
    const top = this.undoStack.at(-1);
    return top ? (this.tokens.get(top) as number) : this.base;
  }

  markSaved(position?: number): void {
    this.saved = position ?? this.position;
    this.sync();
  }

  private sync(): void {
    this.doc.dirty = this.saved !== this.position;
  }

  push(cmd: Command | null | undefined): void {
    if (!cmd) return;
    this.redoStack = [];
    this.undoStack.push(cmd);
    this.tokens.set(cmd, this.nextToken++);
    let total = this.undoStack.reduce((n, c) => n + c.bytes, 0);
    while (this.undoStack.length > 1 && total > this.limitBytes) {
      const dropped = this.undoStack.shift() as Command;
      total -= dropped.bytes;
      this.base = this.tokens.get(dropped) as number; // the bottom state is now "after dropped"
    }
    this.sync();
  }

  undo(): Rect | null {
    const cmd = this.undoStack.pop();
    if (!cmd) return null;
    cmd.undo(this.doc);
    this.redoStack.push(cmd);
    this.sync();
    return cmd.repaint();
  }

  redo(): Rect | null {
    const cmd = this.redoStack.pop();
    if (!cmd) return null;
    cmd.redo(this.doc);
    this.undoStack.push(cmd);
    this.sync();
    return cmd.repaint();
  }
}
