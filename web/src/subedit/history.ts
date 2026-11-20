// Undo / redo for the subtitle editor: a stack of document snapshots (a port of history.py).
// Documents are never changed in place, so a snapshot is just a reference.

import type { Cue, SubDoc } from "./formats";

export const MAX_STEPS = 500;

const sameCue = (a: Cue, b: Cue) =>
  a === b || (a.start === b.start && a.end === b.end && a.text === b.text && a.extra.length === b.extra.length && a.extra.every(([k, v], i) => b.extra[i][0] === k && b.extra[i][1] === v));

/** Same contents (the desktop's dataclass ==): unchanged cues are the same objects, so it's quick. */
export function sameDoc(a: SubDoc, b: SubDoc): boolean {
  if (a === b) return true;
  if (a.format !== b.format || a.header !== b.header || a.footer !== b.footer || a.fields.join("\0") !== b.fields.join("\0")) return false;
  return a.cues.length === b.cues.length && a.cues.every((c, i) => sameCue(c, b.cues[i]));
}

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
  markSaved(doc: SubDoc = this.doc): void {
    this.saved = doc;
    this.merge = null;
  }

  /** The next push starts a step of its own (e.g. another cue was chosen). */
  endMerge(): void {
    this.merge = null;
  }
}
