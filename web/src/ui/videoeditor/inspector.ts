// Settings of the selected item, or of the project when nothing is selected (port of
// gui/videoedit/inspector.py). It never changes the project: `change` gets the fields that differ
// from the shown item while a setting moves, `commit` once the edit is finished (the form's native
// `change` event: slider released, number / text left, colour picked, box ticked, choice made).
import type { Values } from "../../core/options";
import { changedFields, schemaFor, targetOf, TITLES, valuesOf, type TargetKind } from "../../videoedit/schemas";
import type { Project } from "../../videoedit/project";
import { SettingsForm } from "../settings";
import { FALLBACK_FONT } from "../../videoedit/textlayout";
import { hasFont } from "./fonts";

export const MISSING_FONT = `Not on this device — using ${FALLBACK_FONT}.`;

export class Inspector {
  readonly el: HTMLElement;
  private title = document.createElement("div");
  private form: SettingsForm;
  private fontNote = document.createElement("div");
  private kind: TargetKind | null = null;
  private id: string | null = null;
  private shown: Values = {};

  constructor(private on: { change(changes: Record<string, unknown>): void; commit(): void }) {
    this.title.className = "ve-inspector-title";
    this.fontNote.className = "ve-font-note";
    this.form = new SettingsForm(() => {
      this.updateFontNote();
      this.on.change(changedFields(this.shown, this.form.values()));
    }, "ve");
    this.form.el.addEventListener("change", () => this.on.commit());
    this.el = document.createElement("div");
    this.el.className = "ve-inspector";
    this.el.append(this.title, this.form.el);
  }

  get selectionId(): string | null {
    return this.id;
  }

  /** Show the item's settings (the project's when `selectionId` is null); the same item keeps its
   *  fields (and the focus) and only gets the new values. */
  show(p: Project | null, selectionId: string | null): void {
    if (!p) {
      this.kind = null;
      this.id = null;
      this.shown = {};
      this.title.textContent = "";
      this.form.render([], "", {});
      return;
    }
    let found: ReturnType<typeof targetOf>;
    try {
      found = targetOf(p, selectionId);
    } catch {
      selectionId = null;
      found = targetOf(p, null);
    }
    const values = valuesOf(found.kind, found.target);
    if (found.kind === this.kind && selectionId === this.id) {
      this.form.setValues(values);
    } else {
      this.form.render(schemaFor(found.kind), "", values);
      const font = this.form.el.querySelector("#ve-font");
      font?.parentElement?.append(this.fontNote);
    }
    this.kind = found.kind;
    this.id = selectionId;
    this.shown = values;
    this.title.textContent = TITLES[found.kind].toUpperCase();
    this.updateFontNote();
  }

  /** Re-check the font (after the bundled fonts loaded). */
  updateFontNote(): void {
    if (this.kind !== "text") return;
    const font = String(this.form.values().font ?? "").trim();
    const missing = font !== "" && font !== FALLBACK_FONT && !hasFont(font);
    this.fontNote.textContent = missing ? MISSING_FONT : "";
    this.fontNote.hidden = !missing;
  }
}
