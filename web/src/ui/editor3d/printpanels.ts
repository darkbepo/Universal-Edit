// The 3D viewer's Arrange and Check side panels (ports of the desktop's
// gui/editor3d/print_panels.py). They only show and collect choices; the page changes the scene.
import { BEDS, DEFAULT_BED, type Bed } from "../../editor3d/arrange";
import type { Vec3 } from "../../editor3d/camera";

export const CUSTOM = "Custom size";

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) {
  const e = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(e, rest);
  if (cls) e.className = cls;
  e.append(...children);
  return e;
}

const btn = (label: string, title: string, fn: () => void, cls = "btn small") => {
  const b = el("button", { class: cls, type: "button", title }, label);
  b.addEventListener("click", fn);
  return b;
};

function num(id: string, min: number, max: number, step: number, value: number, onChange: (v: number) => void): HTMLInputElement {
  const i = el("input", { id, type: "number", min: String(min), max: String(max), step: String(step), value: String(value) });
  i.addEventListener("change", () => {
    const v = Number(i.value);
    if (Number.isFinite(v)) onChange(Math.min(max, Math.max(min, v)));
  });
  return i;
}

const row = (label: string, input: HTMLElement) => el("div", { class: "logo-row" }, input.id ? el("label", { htmlFor: input.id }, label) : el("span", {}, label), input);

export interface ArrangeActions {
  rotate(axis: "x" | "y" | "z", degrees: number): void;
  mirror(axis: "x" | "y" | "z"): void;
  scaleBy(sx: number, sy: number, sz: number): void;
  sizeTo(axis: "x" | "y" | "z", mm: number, keep: boolean): void;
  moveTo(x: number, y: number): void;
  centre(): void;
  units(key: string): void;
  layFlat(on: boolean): void;
  bedChanged(bed: Bed): void;
  done(): void;
}

export class ArrangePanel {
  readonly el: HTMLElement;
  readonly fit = el("p", { class: "muted small arr-fit" });
  readonly note = el("p", { class: "muted small arr-note" });
  readonly flatButton: HTMLButtonElement;
  private bedSelect = el("select", { id: "arr-bed" });
  private custom = el("div", { class: "arr-custom" });
  private bedDims: HTMLInputElement[];
  private sizes: Record<string, HTMLInputElement> = {};
  private keep = el("input", { id: "arr-keep", type: "checkbox", checked: true });
  private percent: HTMLInputElement;
  private posX: HTMLInputElement;
  private posY: HTMLInputElement;
  private quiet = false;

  constructor(private on: ArrangeActions) {
    BEDS.forEach(([name, w, d, h], i) => this.bedSelect.append(el("option", { value: String(i) }, `${name} (${w} × ${d} × ${h})`)));
    this.bedSelect.append(el("option", { value: "custom" }, CUSTOM));
    this.bedSelect.addEventListener("change", () => this.bedEdited());
    this.bedDims = (["w", "d", "h"] as const).map((k, i) => {
      const input = num(`arr-bed-${k}`, 10, 2000, 10, DEFAULT_BED[i + 1] as number, () => this.bedEdited());
      input.title = ["Width (X)", "Depth (Y)", "Height (Z)"][i];
      return input;
    });
    this.custom.append(...this.bedDims);
    const sizeRows = (["x", "y", "z"] as const).map((axis, i) => {
      const input = num(`arr-size-${axis}`, 0.1, 100000, 1, 10, (v) => !this.quiet && this.on.sizeTo(axis, v, this.keep.checked));
      this.sizes[axis] = input;
      return row(["Width (X)", "Depth (Y)", "Height (Z)"][i], input);
    });
    this.percent = num("arr-percent", 1, 10000, 10, 100, () => undefined);
    // "+90°" alone says little: these buttons are named by their full description
    const named = (b: HTMLButtonElement) => (b.setAttribute("aria-label", b.title), b);
    const turnRows = (["x", "y", "z"] as const).map((axis) =>
      el("div", { class: "arr-turn" }, el("span", {}, axis.toUpperCase()),
        named(btn("−90°", `Turn −90° about ${axis.toUpperCase()}`, () => this.on.rotate(axis, -90))),
        named(btn("+90°", `Turn +90° about ${axis.toUpperCase()}`, () => this.on.rotate(axis, 90))),
        named(btn("Mirror", `Mirror along ${axis.toUpperCase()}`, () => this.on.mirror(axis)))));
    const angle = num("arr-angle", -360, 360, 5, 15, () => undefined);
    const angleAxis = el("select", { id: "arr-angle-axis", title: "Axis" }, el("option", { value: "z" }, "Z"), el("option", { value: "x" }, "X"), el("option", { value: "y" }, "Y"));
    this.flatButton = btn("Lay flat on a face…", "Then click the face to put on the bed", () => {
      const on = this.flatButton.getAttribute("aria-pressed") !== "true";
      this.flatButton.setAttribute("aria-pressed", String(on));
      this.on.layFlat(on);
    });
    this.flatButton.setAttribute("aria-pressed", "false");
    this.posX = num("arr-pos-x", -5000, 5000, 1, 0, () => !this.quiet && this.on.moveTo(Number(this.posX.value), Number(this.posY.value)));
    this.posY = num("arr-pos-y", -5000, 5000, 1, 0, () => !this.quiet && this.on.moveTo(Number(this.posX.value), Number(this.posY.value)));
    this.posX.title = "Centre X (mm)";
    this.posY.title = "Centre Y (mm)";
    this.el = el("div", { class: "editor-side viewer3d-side arrange-panel", hidden: true },
      el("h2", { class: "logo-title" }, "Arrange"),
      el("p", { class: "muted small" }, "Printer"), this.bedSelect, this.custom, this.fit,
      el("p", { class: "muted small" }, "Size (mm)"),
      el("div", { class: "logo-form" }, ...sizeRows,
        el("div", { class: "logo-row check" }, el("span", {}), el("label", {}, this.keep, " Keep proportions")),
        el("div", { class: "logo-row" }, el("label", { htmlFor: "arr-percent" }, "Scale (%)"),
          el("div", { class: "arr-inline" }, this.percent, btn("Scale", "Scale by this percentage", () => {
            const k = Number(this.percent.value) / 100;
            if (Number.isFinite(k) && k > 0 && k !== 1) this.on.scaleBy(k, k, k);
          })))),
      el("div", { class: "logo-buttons arr-units" },
        btn("Made in inches", "Make it 25.4× bigger (the file was drawn in inches)", () => this.on.units("inch")),
        btn("Made in cm", "Make it 10× bigger (the file was drawn in centimetres)", () => this.on.units("cm"))),
      el("p", { class: "muted small" }, "Turn"), ...turnRows,
      el("div", { class: "arr-inline" }, angle, angleAxis, btn("Turn", "Turn by this angle", () => this.on.rotate(angleAxis.value as "x" | "y" | "z", Number(angle.value) || 0))),
      this.flatButton,
      el("p", { class: "muted small" }, "Position of the centre (mm)"),
      el("div", { class: "arr-inline" }, this.posX, this.posY, btn("Centre", "Put it in the middle of the bed", () => this.on.centre())),
      this.note,
      el("div", { class: "logo-buttons" }, btn("Done", "Back to the model's details", () => this.on.done(), "btn primary")));
    this.syncCustom();
  }

  bed(): Bed {
    const v = this.bedSelect.value;
    if (v !== "custom") return BEDS[Number(v)];
    const [w, d, h] = this.bedDims.map((i) => Number(i.value));
    return [CUSTOM, w, d, h];
  }

  setBed(bed: Bed): void {
    this.quiet = true;
    const i = BEDS.findIndex((b) => b[0] === bed[0]);
    if (i < 0) {
      this.bedSelect.value = "custom";
      this.bedDims.forEach((input, k) => (input.value = String(bed[k + 1])));
    } else this.bedSelect.value = String(i);
    this.quiet = false;
    this.syncCustom();
  }

  private syncCustom(): void {
    this.custom.hidden = this.bedSelect.value !== "custom";
  }

  private bedEdited(): void {
    this.syncCustom();
    if (!this.quiet) this.on.bedChanged(this.bed());
  }

  /** The model's size and X / Y centre now (no callbacks). */
  showModel(size: Vec3, centre: Vec3, fitNote: string): void {
    this.quiet = true;
    (["x", "y", "z"] as const).forEach((a, i) => (this.sizes[a].value = size[i].toFixed(1)));
    this.posX.value = centre[0].toFixed(1);
    this.posY.value = centre[1].toFixed(1);
    this.quiet = false;
    this.fit.textContent = fitNote;
  }

  stopLayFlat(): void {
    this.flatButton.setAttribute("aria-pressed", "false");
  }
}

export interface CheckActions {
  repair(): void;
  overhangs(): void;
  done(): void;
}

export class CheckPanel {
  readonly el: HTMLElement;
  readonly result = el("ul", { class: "check-result" });
  readonly repaired = el("p", { class: "muted small check-repaired", role: "status" });
  readonly overhang = el("p", { class: "muted small check-overhang" });
  readonly repairButton: HTMLButtonElement;
  readonly show = el("input", { id: "check-show", type: "checkbox", checked: true });
  readonly angle: HTMLInputElement;

  constructor(on: CheckActions) {
    this.repairButton = btn("Repair", "Close holes, turn triangles to face outwards, remove empty, repeated and tiny bits", () => on.repair(), "btn primary");
    this.angle = num("check-angle", 10, 89, 5, 45, () => on.overhangs());
    this.show.addEventListener("change", () => on.overhangs());
    this.el = el("div", { class: "editor-side viewer3d-side check-panel", hidden: true },
      el("h2", { class: "logo-title" }, "Check for printing"),
      this.result, this.repairButton, this.repaired,
      el("p", { class: "muted small" }, "Overhangs (need supports)"),
      el("div", { class: "arr-inline" }, el("label", {}, this.show, " Show in red, steeper than"), this.angle, el("span", {}, "°")),
      this.overhang,
      el("div", { class: "logo-buttons" }, btn("Done", "Back to the model's details", () => on.done(), "btn primary")));
    this.result.append(el("li", {}, "Checking…"));
  }

  checking(): void {
    this.result.replaceChildren(el("li", {}, "Checking…"));
  }

  showReport(lines: string[], ok: boolean, overhangArea: number): void {
    this.result.replaceChildren(...lines.map((l) => el("li", {}, l)));
    this.repairButton.disabled = ok;
    this.overhang.textContent = overhangArea ? `${(overhangArea / 100).toFixed(1)} cm² of overhang — mostly underneath: turn the view to see it in red.` : "No overhangs.";
  }
}
