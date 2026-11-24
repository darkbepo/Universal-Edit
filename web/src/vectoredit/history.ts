// Undo / redo for the vector editor: a stack of document snapshots (a port of
// src/umc/vectoredit/history.py). Documents are never changed in place.

import type { VectorDoc } from "./doc";

export const MAX_STEPS = 500;

export class History {
  doc: VectorDoc;
  version = 0;
  private undoStack: [string, VectorDoc][] = [];
  private redoStack: [string, VectorDoc][] = [];
  private saved: VectorDoc;
  private merge: string | null = null;

  constructor(doc: VectorDoc) {
    this.doc = doc;
    this.saved = doc;
  }

  /** A new step; with the same `merge` key as the step before (typing into one text, nudging
   * with the arrow keys) it becomes part of that step instead. */
  push(doc: VectorDoc, label: string, merge: string | null = null): void {
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
    const step = this.undoStack.pop();
    if (!step) return "";
    this.merge = null;
    this.redoStack.push([step[0], this.doc]);
    this.doc = step[1];
    this.version++;
    return step[0];
  }

  redo(): string {
    const step = this.redoStack.pop();
    if (!step) return "";
    this.merge = null;
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

  markSaved(doc?: VectorDoc): void {
    this.saved = doc ?? this.doc;
    this.merge = null;
  }

  endMerge(): void {
    this.merge = null;
  }
}
