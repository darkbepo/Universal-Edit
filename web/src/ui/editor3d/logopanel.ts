// The "Add logo" side panel of the 3D viewer (port of the desktop's
// gui/editor3d/logo_panel.py): picture, how its shape is found, size, where it goes. It only
// shows and collects settings; the page builds and places the logo.
import { DEFAULT_SETTINGS, type LogoSettings } from "../../editor3d/scene";

export const FACE_HINT = "Click a flat face of the model to place the logo, then drag the logo to move it.";
const MODES: [string, string][] = [["alpha", "Transparent background"], ["dark", "Dark parts"], ["light", "Light parts"]];
const DETAILS: [string, string][] = [["smooth", "Smooth"], ["normal", "Normal"], ["fine", "Fine"]];
const WHERE: [string, string][] = [["face", "On the model"], ["badge", "Separate badge"]];
const PLATES: [string, string][] = [["none", "No plate"], ["rectangle", "Rectangle"], ["rounded", "Rounded rectangle"]];
export const PICTURE_ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,.bmp,.avif,.svg";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) {
  const e = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(e, rest);
  if (cls) e.className = cls;
  e.append(...children);
  return e;
}

export class LogoPanel {
  readonly el: HTMLElement;
  readonly title = el("h2", { class: "logo-title" }, "Add a logo");
  readonly pictureName = el("span", { class: "muted small logo-picture-name" });
  readonly preview = el("canvas", { class: "logo-preview", width: 200, height: 100 });
  readonly note = el("p", { class: "muted small logo-note", role: "status" });
  readonly heightText = el("span", { class: "logo-height" });
  readonly picker = el("input", { type: "file", accept: PICTURE_ACCEPT, hidden: true });
  readonly applyButton = el("button", { class: "btn primary", type: "button" }, "Apply");
  readonly cancelButton = el("button", { class: "btn small", type: "button" }, "Cancel");
  private hasModel = true;
  private quiet = false;
  private inputs: Record<string, HTMLInputElement | HTMLSelectElement> = {};
  private rows: Record<string, HTMLElement> = {};

  constructor(private on: { changed: () => void; picture: (f: File) => void; apply: () => void; cancel: () => void }) {
    const choose = el("button", { class: "btn small", type: "button", title: "PNG with a transparent background works best" }, "Choose picture…");
    choose.addEventListener("click", () => this.picker.click());
    this.picker.addEventListener("change", () => {
      const f = this.picker.files?.[0];
      this.picker.value = "";
      if (f) this.on.picture(f);
    });
    const form = el("div", { class: "logo-form" });
    const row = (key: string, label: string, input: HTMLElement) => {
      const name = input.id ? el("label", { htmlFor: input.id }, label) : el("span", {}, label);
      const r = el("div", { class: "logo-row" }, name, input);
      this.rows[key] = r;
      form.append(r);
    };
    const select = (key: string, items: [string, string][]) => {
      const s = el("select", { id: `logo-${key}` });
      for (const [k, label] of items) s.append(el("option", { value: k }, label));
      this.inputs[key] = s;
      s.addEventListener("change", () => this.changed());
      return s;
    };
    const num = (key: string, min: number, max: number, step: number, value: number) => {
      const i = el("input", { id: `logo-${key}`, type: "number", min: String(min), max: String(max), step: String(step), value: String(value) });
      this.inputs[key] = i;
      i.addEventListener("change", () => this.changed());
      return i;
    };
    const check = (key: string, label: string) => {
      const i = el("input", { id: `logo-${key}`, type: "checkbox" });
      this.inputs[key] = i;
      i.addEventListener("change", () => this.changed());
      const l = el("div", { class: "logo-row check" }, el("span", {}), el("label", {}, i, ` ${label}`));
      this.rows[key] = l;
      form.append(l);
    };
    row("mode", "Shape from", select("mode", MODES));
    const threshold = el("input", { id: "logo-threshold", type: "range", min: "1", max: "254", value: "128", title: "Which pixels count as dark / light" });
    threshold.addEventListener("input", () => this.changed());
    this.inputs.threshold = threshold;
    row("threshold", "Threshold", threshold);
    check("invert", "Invert");
    row("detail", "Detail", select("detail", DETAILS));
    (this.inputs.detail as HTMLSelectElement).value = "normal";
    row("width", "Width (mm)", num("width", 2, 500, 1, 40));
    row("height", "Height", this.heightText);
    row("thickness", "Thickness (mm)", num("thickness", 0.2, 50, 0.2, 2));
    row("where", "Place", select("where", WHERE));
    row("rotation", "Rotation (°)", num("rotation", -180, 180, 15, 0));
    row("sink", "Sink into surface (mm)", num("sink", 0, 2, 0.1, 0.2));
    row("plate", "Base plate", select("plate", PLATES));
    row("margin", "Margin (mm)", num("margin", 0, 50, 0.5, 3));
    row("plate_thickness", "Plate thickness (mm)", num("plate_thickness", 0.4, 20, 0.2, 2));
    check("hole", "Keychain hole");
    this.applyButton.addEventListener("click", () => this.on.apply());
    this.cancelButton.addEventListener("click", () => this.on.cancel());
    this.el = el("div", { class: "editor-side viewer3d-side logo-panel", hidden: true },
      this.title,
      el("div", { class: "logo-picture" }, choose, this.picker, this.pictureName),
      this.preview, form, this.note,
      el("div", { class: "logo-buttons" }, this.cancelButton, this.applyButton));
    this.sync();
  }

  private value(key: string): string {
    return this.inputs[key].value;
  }

  private number(key: string, fallback: number): number {
    const i = this.inputs[key] as HTMLInputElement;
    const v = Number(i.value);
    if (!Number.isFinite(v)) return fallback;
    return Math.min(Number(i.max), Math.max(Number(i.min), v));
  }

  settings(): LogoSettings {
    const d = DEFAULT_SETTINGS;
    return {
      mode: this.value("mode") as LogoSettings["mode"],
      threshold: Number(this.value("threshold")),
      invert: (this.inputs.invert as HTMLInputElement).checked,
      detail: this.value("detail") as LogoSettings["detail"],
      width: this.number("width", d.width),
      thickness: this.number("thickness", d.thickness),
      where: this.hasModel ? (this.value("where") as LogoSettings["where"]) : "badge",
      rotation: this.number("rotation", 0),
      sink: this.number("sink", d.sink),
      plate: this.value("plate") as LogoSettings["plate"],
      margin: this.number("margin", d.margin),
      plate_thickness: this.number("plate_thickness", d.plate_thickness),
      hole: (this.inputs.hole as HTMLInputElement).checked,
    };
  }

  setSettings(s: LogoSettings): void {
    this.quiet = true;
    for (const [k, v] of Object.entries(s)) {
      const i = this.inputs[k];
      if (!i) continue;
      if (i instanceof HTMLInputElement && i.type === "checkbox") i.checked = Boolean(v);
      else i.value = String(v);
    }
    this.quiet = false;
    this.sync();
  }

  setHasModel(has: boolean): void {
    this.hasModel = has;
    this.sync();
  }

  private changed(): void {
    this.sync();
    if (!this.quiet) this.on.changed();
  }

  private sync(): void {
    const s = this.settings();
    this.rows.where.hidden = !this.hasModel;
    this.rows.threshold.hidden = s.mode === "alpha";
    this.rows.sink.hidden = s.where !== "face";
    const badge = s.where === "badge", plate = s.plate !== "none";
    this.rows.plate.hidden = !badge;
    for (const k of ["margin", "plate_thickness", "hole"]) this.rows[k].hidden = !(badge && plate);
  }

  setPicture(name: string): void {
    this.pictureName.textContent = name;
  }

  /** The silhouette, scaled into the preview box (``rgba`` null clears it). */
  setPreview(rgba: Uint8ClampedArray | null, w = 0, h = 0): void {
    const c = this.preview.getContext("2d");
    if (!c) return;
    c.clearRect(0, 0, this.preview.width, this.preview.height);
    if (!rgba || !w || !h) return;
    const src = document.createElement("canvas");
    src.width = w;
    src.height = h;
    src.getContext("2d")!.putImageData(new ImageData(new Uint8ClampedArray(rgba), w, h), 0, 0);
    const k = Math.min(this.preview.width / w, this.preview.height / h);
    const dw = w * k, dh = h * k;
    c.drawImage(src, (this.preview.width - dw) / 2, (this.preview.height - dh) / 2, dw, dh);
  }

  setSize(width: number, height: number): void {
    this.heightText.textContent = width ? `${height.toFixed(1)} mm` : "";
  }

  setNote(text: string): void {
    this.note.textContent = text;
  }
}
