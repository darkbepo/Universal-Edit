// Undo / redo for the video editor: a stack of immutable project snapshots (port of history.py).
import type { Project } from "./project";

export const MAX_STEPS = 300;

export class History {
  project: Project;
  version = 0;
  private undoStack: [string, Project][] = []; // (label, project before the step)
  private redoStack: [string, Project][] = []; // (label, project before the undone step)
  private saved: Project;

  constructor(project: Project) {
    this.project = project;
    this.saved = project;
  }

  push(project: Project, label: string): void {
    if (project === this.project) return;
    this.undoStack.push([label, this.project]);
    if (this.undoStack.length > MAX_STEPS) this.undoStack.shift();
    this.redoStack.length = 0;
    this.project = project;
    this.version += 1;
  }

  undo(): void {
    const step = this.undoStack.pop();
    if (!step) return;
    this.redoStack.push([step[0], this.project]);
    this.project = step[1];
    this.version += 1;
  }

  redo(): void {
    const step = this.redoStack.pop();
    if (!step) return;
    this.undoStack.push([step[0], this.project]);
    this.project = step[1];
    this.version += 1;
  }

  get canUndo(): boolean {
    return this.undoStack.length > 0;
  }
  get canRedo(): boolean {
    return this.redoStack.length > 0;
  }
  get undoLabel(): string {
    return this.undoStack.length ? this.undoStack[this.undoStack.length - 1][0] : "";
  }
  get redoLabel(): string {
    return this.redoStack.length ? this.redoStack[this.redoStack.length - 1][0] : "";
  }
  get dirty(): boolean {
    return this.project !== this.saved;
  }

  markSaved(): void {
    this.saved = this.project;
  }
}
