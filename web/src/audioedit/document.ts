// The sound being edited, its selection, and undo / redo (port of src/umc/audioedit/document.py).
// Every edit is one or more splices: "replace frames [start, start + old) with new"; undo puts
// `old` back. Same-length edits are written in place. The selection is [start, end) in frames;
// start === end is just the cursor. Amplify / normalize / reverse without a selection work on
// the whole sound; cut, delete, silence and fades need one.
import * as ops from "./ops";
import type { Samples } from "./ops";

export let HISTORY_BYTES = 300 * 1024 * 1024; // oldest steps are dropped beyond this
export const setHistoryBytes = (n: number) => (HISTORY_BYTES = n); // tests

interface Splice { start: number; old: Samples; new: Samples }
interface Step { label: string; splices: Splice[]; before: [number, number]; after: [number, number] }

const bytesOf = (x: Samples) => x.reduce((n, ch) => n + ch.byteLength, 0);
const stepBytes = (s: Step) => s.splices.reduce((n, sp) => n + bytesOf(sp.old) + bytesOf(sp.new), 0);

export class AudioDoc {
  selection: [number, number] = [0, 0];
  revision = 0; // bumped by every change (the waveform cache follows it)
  private undoStack: Step[] = [];
  private redoStack: Step[] = [];
  private saved: Step | null = null;
  private pending: Splice[] | null = null;

  constructor(readonly rate: number, public samples: Samples) {
    if (!samples.length) throw new Error("samples must have at least one channel");
  }

  get channels(): number { return this.samples.length; }
  get frames(): number { return this.samples[0].length; }
  get duration(): number { return this.frames / this.rate; }
  get dirty(): boolean { return (this.undoStack.at(-1) ?? null) !== this.saved; }
  markSaved(): void { this.saved = this.undoStack.at(-1) ?? null; }
  /** An opaque marker of the current state (for "did anything change while saving?"). */
  get state(): object | null { return this.undoStack.at(-1) ?? null; }
  markSavedAt(state: object | null): void { if (state === this.state) this.markSaved(); }
  get hasSelection(): boolean { return this.selection[1] > this.selection[0]; }

  /** The selection, or the whole sound when nothing is selected. */
  span(): [number, number] { return this.hasSelection ? this.selection : [0, this.frames]; }

  setSelection(start: number, end = start): void {
    const [a, b] = start <= end ? [start, end] : [end, start];
    const clamp = (x: number) => Math.max(0, Math.min(Math.round(x), this.frames));
    this.selection = [clamp(a), clamp(b)];
  }

  selectAll(): void { this.setSelection(0, this.frames); }

  copy(): Samples | null {
    const [a, b] = this.selection;
    return b > a ? ops.slice(this.samples, a, b) : null;
  }

  // ---------------------------------------------------------------- history
  canUndo(): boolean { return this.undoStack.length > 0; }
  canRedo(): boolean { return this.redoStack.length > 0; }
  undoLabel(): string { return this.undoStack.at(-1)?.label ?? ""; }
  redoLabel(): string { return this.redoStack.at(-1)?.label ?? ""; }
  get historySize(): number { return this.undoStack.length; }

  private put(start: number, length: number, data: Samples): void {
    const n = ops.frames(data);
    if (n === length) this.samples.forEach((ch, c) => ch.set(data[c], start));
    else {
      this.samples = this.samples.map((ch, c) => {
        const out = new Float32Array(ch.length - length + n);
        out.set(ch.subarray(0, start));
        out.set(data[c], start);
        out.set(ch.subarray(start + length), start + n);
        return out;
      });
    }
    this.revision++;
  }

  private splice(start: number, end: number, data: Samples): void {
    const fresh = ops.fitChannels(data, this.channels).map((ch) => Float32Array.from(ch));
    const old = ops.slice(this.samples, start, end);
    this.put(start, end - start, fresh);
    this.pending!.push({ start, old, new: fresh });
  }

  private edit(label: string, fn: () => void, after?: [number, number]): boolean {
    const before: [number, number] = [...this.selection];
    this.pending = [];
    let splices: Splice[];
    try {
      fn();
    } finally {
      splices = this.pending;
      this.pending = null;
    }
    if (!splices.length) return false;
    if (after) this.setSelection(...after);
    this.undoStack.push({ label, splices, before, after: [...this.selection] });
    this.redoStack = [];
    let total = this.undoStack.reduce((n, s) => n + stepBytes(s), 0);
    while (this.undoStack.length > 1 && total > HISTORY_BYTES) total -= stepBytes(this.undoStack.shift()!);
    return true;
  }

  undo(): boolean {
    const step = this.undoStack.pop();
    if (!step) return false;
    for (const s of [...step.splices].reverse()) this.put(s.start, ops.frames(s.new), s.old);
    this.selection = [...step.before];
    this.redoStack.push(step);
    return true;
  }

  redo(): boolean {
    const step = this.redoStack.pop();
    if (!step) return false;
    for (const s of step.splices) this.put(s.start, ops.frames(s.old), s.new);
    this.selection = [...step.after];
    this.undoStack.push(step);
    return true;
  }

  // ---------------------------------------------------------------- edits (true = changed)
  private empty(): Samples { return ops.silence(this.channels, 0); }

  delete(): boolean {
    const [a, b] = this.selection;
    if (b <= a) return false;
    return this.edit("Delete", () => this.splice(a, b, this.empty()), [a, a]);
  }

  cut(): Samples | null {
    const clip = this.copy();
    if (!clip) return null;
    const [a, b] = this.selection;
    this.edit("Cut", () => this.splice(a, b, this.empty()), [a, a]);
    return clip;
  }

  /** Insert at the cursor, or replace the selection; the pasted part is selected. */
  paste(clip: Samples | null): boolean {
    if (!clip || !ops.frames(clip)) return false;
    const [a, b] = this.selection;
    const n = ops.frames(clip);
    return this.edit("Paste", () => this.splice(a, b, clip), [a, a + n]);
  }

  /** Keep only the selection. */
  trim(): boolean {
    const [a, b] = this.selection;
    if (b <= a || (a === 0 && b === this.frames)) return false;
    return this.edit("Trim", () => {
      if (b < this.frames) this.splice(b, this.frames, this.empty());
      if (a > 0) this.splice(0, a, this.empty());
    }, [0, b - a]);
  }

  private effect(label: string, fn: (x: Samples) => Samples, needSelection = false): boolean {
    const [a, b] = needSelection ? this.selection : this.span();
    if (b <= a) return false;
    return this.edit(label, () => this.splice(a, b, fn(ops.slice(this.samples, a, b))));
  }

  silence(): boolean { return this.effect("Silence", (x) => ops.silence(x.length, ops.frames(x)), true); }
  fadeIn(): boolean { return this.effect("Fade in", (x) => ops.fade(x, true), true); }
  fadeOut(): boolean { return this.effect("Fade out", (x) => ops.fade(x, false), true); }

  amplify(db: number): boolean {
    if (db === 0) return false;
    return this.effect("Amplify", (x) => ops.gain(x, db));
  }

  normalize(target = ops.NORMALIZE_DB): boolean {
    const [a, b] = this.span();
    const db = ops.normalizeDb(ops.slice(this.samples, a, b), target);
    if (db === null || Math.abs(db) < 1e-3) return false;
    return this.effect("Normalize", (x) => ops.gain(x, db));
  }

  reverse(): boolean { return this.effect("Reverse", ops.reverse); }
}
