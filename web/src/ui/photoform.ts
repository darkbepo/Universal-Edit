// The "Change details" card for JPG / PNG / WebP pictures in the Properties pane — the web side
// of the desktop's gui/properties/photo_form.py (same fields and rules). The browser can't write
// into the original file, so the button downloads the changed picture (a ZIP for several); the
// queue then holds the changed picture, so later conversions use it.
import type { InputFile } from "../core/types";
import type { Field, PhotoDetails, PhotoEdit } from "../properties/photo";

// The same lists as properties/photo.ts (a test checks), kept here so the page doesn't load the
// photo code until a picture's details are shown.
export const FIELDS: readonly Field[] = ["title", "comment", "author", "copyright", "tags", "taken"];
export const ROTATIONS: [number, string][] = [[1, "Upright"], [6, "Turned 90° right"], [3, "Upside down"], [8, "Turned 90° left"]];

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const LABELS: Record<Field, string> = { title: "Title", comment: "Comments", author: "Author", copyright: "Copyright", tags: "Tags", taken: "Date taken" };
const HINTS: Partial<Record<Field, string>> = { tags: "Separate with commas", taken: "Like 2026-11-12 14:03" };
const SEVERAL = "Several values";
const KEEP_ROTATION = 0;

type Photo = typeof import("../properties/photo");
const loadPhoto = () => import("../properties/photo");

/** Each field's value when every picture has the same one, else null ("Several values"). */
export function common(details: PhotoDetails[]): Record<Field, string | null> {
  const out = {} as Record<Field, string | null>;
  for (const name of FIELDS) {
    const values = new Set(details.map((d) => String(d[name])));
    out[name] = values.size === 1 ? [...values][0] : null;
  }
  return out;
}

/** ``name``, or "name (1).ext", "name (2).ext"… when the ZIP already holds one by that name. */
export function uniqueName(taken: Record<string, unknown>, name: string): string {
  if (!(name in taken)) return name;
  const dot = name.lastIndexOf(".");
  const [stem, ext] = dot > 0 ? [name.slice(0, dot), name.slice(dot)] : [name, ""];
  for (let n = 1; ; n++) if (!(`${stem} (${n})${ext}` in taken)) return `${stem} (${n})${ext}`;
}

export class PhotoForm {
  readonly el: HTMLElement;
  private readonly inputs = {} as Record<Field, HTMLInputElement>;
  private readonly rotation = h("select", { id: "ph-rotation", ariaLabel: "Rotation" });
  private readonly removeLocation = h("input", { type: "checkbox", id: "ph-remove-location" });
  private readonly removeAll = h("input", { type: "checkbox", id: "ph-remove-all" });
  private readonly applyAll = h("input", { type: "checkbox", id: "ph-apply-all" });
  private readonly applyAllRow: HTMLElement;
  private readonly applyAllText = h("span");
  private readonly note = h("div", { class: "hint ph-note" });
  private readonly save = h("button", { class: "primary", type: "button", disabled: true, title: "Download the picture with the new details (the original on your device isn't changed)" }, "Download with changes");
  private start = {} as Record<Field, string | null>;
  private startRotation = 1;
  private one: { file: InputFile; details: PhotoDetails } | null = null;
  private all: { file: InputFile; details: PhotoDetails }[] = [];

  constructor(private readonly onSaved: (changed: InputFile[]) => void) {
    const rows = h("div", { class: "ph-grid" });
    for (const name of FIELDS) {
      const input = h("input", { type: "text", id: `ph-${name}`, ariaLabel: LABELS[name] });
      input.addEventListener("input", () => this.changed());
      this.inputs[name] = input;
      rows.append(h("label", { htmlFor: input.id }, LABELS[name]), input);
    }
    rows.append(h("label", { htmlFor: this.rotation.id }, "Rotation"), this.rotation);
    this.rotation.addEventListener("change", () => this.changed());
    const check = (box: HTMLInputElement, text: string | HTMLElement) => {
      box.addEventListener("change", () => this.changed());
      return h("label", { class: "ph-check" }, box, text);
    };
    this.applyAllRow = check(this.applyAll, this.applyAllText);
    this.applyAll.addEventListener("change", () => this.reload());
    this.save.addEventListener("click", () => void this.download());
    this.el = h("section", { class: "prop-card ph-form", ariaLabel: "Change details", hidden: true },
      h("h3", {}, "Change details"), rows,
      check(this.removeLocation, "Remove the location"),
      check(this.removeAll, "Remove all hidden details (camera, dates, location, …)"),
      this.applyAllRow, this.note, h("div", { class: "ph-actions" }, this.save));
    this.el.dataset.title = "Change details";
  }

  /** The shown picture and every changeable picture (for "Apply to all"); null hides the form. */
  set(current: { file: InputFile; details: PhotoDetails } | null, everyone: { file: InputFile; details: PhotoDetails }[]): void {
    this.one = current;
    this.all = everyone;
    this.el.hidden = !current;
    if (!current) return;
    this.applyAllText.textContent = `Apply to all ${everyone.length} pictures`;
    this.applyAllRow.hidden = everyone.length < 2;
    if (everyone.length < 2) this.applyAll.checked = false;
    this.reload();
  }

  private get many(): boolean { return this.applyAll.checked && this.all.length > 1; }
  private targets() { return this.many ? this.all : this.one ? [this.one] : []; }

  private reload(): void {
    const details = this.targets().map((t) => t.details);
    if (!details.length) return;
    this.start = common(details);
    for (const name of FIELDS) {
      const value = this.start[name];
      this.inputs[name].value = value ?? "";
      this.inputs[name].placeholder = value === null ? SEVERAL : HINTS[name] ?? "";
    }
    this.rotation.replaceChildren();
    if (this.many) this.rotation.append(h("option", { value: String(KEEP_ROTATION) }, "Keep as they are"));
    for (const [value, label] of ROTATIONS) this.rotation.append(h("option", { value: String(value) }, label));
    this.startRotation = this.many ? KEEP_ROTATION : details[0].orientation;
    if (![...this.rotation.options].some((o) => o.value === String(this.startRotation))) {
      this.rotation.append(h("option", { value: String(this.startRotation) }, `As it is (${this.startRotation})`));
    }
    this.rotation.value = String(this.startRotation);
    this.removeLocation.checked = false;
    this.removeLocation.disabled = !details.some((d) => d.has_location);
    this.removeAll.checked = false;
    this.note.textContent = "";
    this.changed();
  }

  edit(): PhotoEdit {
    const values: Partial<Record<Field, string>> = {};
    for (const name of FIELDS) {
      const start = this.start[name];
      const text = this.inputs[name].value;
      if (start === null && text === "") continue; // "Several values" left alone
      if (text.trim() !== (start ?? "").trim()) values[name] = text;
    }
    const rot = Number(this.rotation.value);
    const orientation = rot === KEEP_ROTATION || rot === this.startRotation ? null : rot;
    return { values, orientation, remove_location: this.removeLocation.checked, remove_all: this.removeAll.checked };
  }

  private changed(): void {
    const e = this.edit();
    const dirty = Object.keys(e.values ?? {}).length > 0 || e.orientation !== null || e.remove_location || e.remove_all;
    this.save.disabled = !dirty;
    this.save.textContent = this.many ? "Download changed pictures (.zip)" : "Download with changes";
    const taken = this.inputs.taken.value.trim();
    void loadPhoto().then((p) => {
      let ok = true;
      try { if (taken) p.exifDate(taken); } catch { ok = false; }
      this.inputs.taken.classList.toggle("invalid", !ok);
    });
  }

  private message(text: string, error = false): void {
    this.note.textContent = text;
    this.note.classList.toggle("error", error);
  }

  private async download(): Promise<void> {
    const p: Photo = await loadPhoto();
    const edit = this.edit();
    const targets = this.targets();
    const outputs: { target: { file: InputFile; details: PhotoDetails }; data: Uint8Array }[] = [];
    try {
      if (edit.values?.taken) p.exifDate(edit.values.taken);
      for (const t of targets) outputs.push({ target: t, data: p.apply(new Uint8Array(await t.file.file.arrayBuffer()), edit) });
    } catch (e) {
      this.message(e instanceof p.PhotoError ? e.message : "The details couldn't be changed.", true);
      return;
    }
    let blob: Blob, name: string;
    if (outputs.length === 1) {
      const f = outputs[0].target.file;
      blob = new Blob([outputs[0].data as BlobPart], { type: f.file.type });
      name = f.name;
    } else {
      const { zipSync } = await import("fflate");
      const tree: Record<string, Uint8Array> = {};
      for (const o of outputs) tree[uniqueName(tree, o.target.file.name)] = o.data;
      blob = new Blob([zipSync(tree, { level: 0 }) as BlobPart], { type: "application/zip" });
      name = "pictures (changed details).zip";
    }
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    // the queue now holds the changed pictures, so a conversion afterwards uses them
    for (const o of outputs) {
      const f = o.target.file;
      f.file = new File([o.data as BlobPart], f.file.name, { type: f.file.type, lastModified: f.file.lastModified });
    }
    const n = outputs.length;
    this.message(n === 1 ? `Downloaded “${name}” with the new details.` : `Downloaded ${n} pictures in “${name}”.`);
    this.onSaved(outputs.map((o) => o.target.file));
  }

  /** After a save the form is filled again; this puts the "Downloaded …" message back. */
  keepNote(text: string): void { this.message(text); }

  /** (tests) the note under the form */
  get noteText(): string { return this.note.textContent ?? ""; }
}
