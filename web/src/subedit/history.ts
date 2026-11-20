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

  constructor(doc: SubDoc) {
    this.doc = doc;
    this.saved = doc;
  }

  push(doc: SubDoc, label: string): void {
    if (doc === this.doc) return;
    this.undoStack.push([label, this.doc]);
    if (this.undoStack.length > MAX_STEPS) this.undoStack.shift();
    this.redoStack = [];
    this.doc = doc;
    this.version++;
  }

  undo(): string {
    const step = this.undoStack.pop();
    if (!step) return "";
    this.redoStack.push([step[0], this.doc]);
    this.doc = step[1];
    this.version++;
    return step[0];
  }

  redo(): string {
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

  markSaved(): void {
    this.saved = this.doc;
  }
}
