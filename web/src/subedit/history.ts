// Undo / redo for the subtitle editor: a stack of document snapshots (a port of history.py).
// Documents are never changed in place, so a snapshot is just a reference.

import type { SubDoc } from "./formats";

export const MAX_STEPS = 500;

export class History {
  doc: SubDoc;
  version = 0;
  private undoStack: [string, SubDoc][] = [];
  private redoStack: [string, SubDoc][] = [];
  private saved: SubDoc;
  private merge: unknown = null; // the last step's merge key

  constructor(doc: SubDoc) {
    this.doc = doc;
    this.saved = doc;
  }

  /** A new step; with the same `merge` key as the step before (typing in one cue's text) it
   * becomes part of that step instead. */
  push(doc: SubDoc, label: string, merge: unknown = null): void {
    if (doc === this.doc) return;
    if (merge !== null && merge === this.merge && this.undoStack.length) {
      this.doc = doc;
      this.redoStack = [];
      this.version++;
      return;
    }
    this.merge = merge;
    this.undoStack.push([label, this.doc]);
    if (this.undoStack.length > MAX_STEPS) this.undoStack.shift();
    this.redoStack = [];
    this.doc = doc;
    this.version++;
  }

  undo(): string {
    this.merge = null;
    const step = this.undoStack.pop();
    if (!step) return "";
    this.redoStack.push([step[0], this.doc]);
    this.doc = step[1];
    this.version++;
    return step[0];
  }

  redo(): string {
    this.merge = null;
    const step = this.redoStack.pop();
    if (!step) return "";
    this.undoStack.push([step[0], this.doc]);
    this.doc = step[1];
    this.version++;
    return step[0];
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }

  get dirty(): boolean {
    return this.doc !== this.saved;
  }

  /** `doc` (default: the current one) is what the file now holds. */
  markSaved(doc: SubDoc = this.doc): void {
    this.saved = doc;
    this.merge = null;
  }

  /** The next push starts a step of its own (e.g. another cue was chosen). */
  endMerge(): void {
    this.merge = null;
  }
}
