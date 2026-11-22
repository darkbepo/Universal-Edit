// Undo / redo for the data editor: a stack of document snapshots (a port of
// src/umc/dataedit/history.py). Documents are never changed in place, so a snapshot is just a
// reference.

import { type DataDoc, sameDoc } from "./table";

export const MAX_STEPS = 500;

export class History {
  doc: DataDoc;
  version = 0;
  private undoStack: [string, DataDoc][] = [];
  private redoStack: [string, DataDoc][] = [];
  private saved: DataDoc;
  private merge: unknown = null; // the last step's merge key

  constructor(doc: DataDoc) {
    this.doc = doc;
    this.saved = doc;
  }

  /** A new step; with the same `merge` key as the step before (typing in one cell: "cell:<r>:<c>") it
   * becomes part of that step instead. */
  push(doc: DataDoc, label: string, merge: unknown = null): void {
    if (sameDoc(doc, this.doc)) return;
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
    return !sameDoc(this.doc, this.saved);
  }

  /** `doc` (default: the current one) is what the file now holds. */
  markSaved(doc: DataDoc = this.doc): void {
    this.saved = doc;
    this.merge = null;
  }

  /** The next push starts a step of its own (e.g. another cell was chosen). */
  endMerge(): void {
    this.merge = null;
  }
}
