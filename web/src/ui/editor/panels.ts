// Tool bar (left), colour swatches, tool settings and the layers list (right).
import { defaults, type Values } from "../../core/options";
import type { EditorDoc } from "../../editor/document";
import type { Command } from "../../editor/history";
import type { Layer, RGBA } from "../../editor/layer";
import * as ops from "../../editor/ops";
import type { Tool } from "../../editor/tools/base";
import { SettingsForm } from "../settings";

export const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

// ------------------------------------------------------------------ icons (inline SVG, no files)

const ICONS: Record<string, string> = {
  brush: '<path d="M19 4 9.5 13.5"/><path d="M9.5 13.5c-2.5 0-3 2.5-3 4-.5 1-1.5 1.5-2.5 1.5 3 1.5 7 0 7-3"/>',
  eraser: '<path d="m7 20-3-3 10-10 6 6-7 7z"/><path d="M11 10l6 6"/><path d="M7 20h13"/>',
  fill: '<path d="m4 11 7-7 8 8-7 7z"/><path d="M4 11h15"/><path d="M20 15s2 2.5 2 3.5a2 2 0 0 1-4 0c0-1 2-3.5 2-3.5z"/>',
  picker: '<path d="m5 19 9-9"/><circle cx="17" cy="7" r="3"/><path d="M5 19l-1 1"/>',
  line: '<path d="M5 19 19 5"/>',
  rect: '<rect x="4" y="6" width="16" height="12" rx="1"/>',
  ellipse: '<ellipse cx="12" cy="12" rx="8" ry="6"/>',
  text: '<path d="M6 6h12M12 6v13"/>',
  select: '<rect x="4" y="6" width="16" height="12" stroke-dasharray="3 2"/>',
  crop: '<path d="M7 3v14h14"/><path d="M3 7h14v14"/>',
};

const icon = (id: string) => {
  const span = h("span", { class: "ico" });
  span.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[id] ?? ""}</svg>`;
  return span;
};

export class ToolBar {
  readonly el = h("div", { class: "tool-bar" });
  private buttons = new Map<string, HTMLButtonElement>();

  constructor(tools: Tool[], onSelect: (id: string) => void) {
    for (const t of tools) {
      const b = h("button", { class: "tool", title: `${t.label} (${t.shortcut})`, ariaLabel: t.label }, icon(t.id));
      b.addEventListener("click", () => onSelect(t.id));
      this.buttons.set(t.id, b);
      this.el.append(b);
    }
  }

  select(id: string): void {
    for (const [tid, b] of this.buttons) b.setAttribute("aria-pressed", String(tid === id));
  }
}

// ------------------------------------------------------------------ colours

const hex = (c: RGBA) => `#${c.slice(0, 3).map((v) => v.toString(16).padStart(2, "0")).join("")}`;

class Swatch {
  readonly el: HTMLElement;
  private input: HTMLInputElement;
  private alpha: HTMLInputElement;
  color: RGBA;

  constructor(label: string, color: RGBA, onChange: () => void) {
    this.color = color;
    this.input = h("input", { type: "color", value: hex(color), title: label, ariaLabel: label });
    this.alpha = h("input", { type: "range", min: "0", max: "100", value: String(Math.round((color[3] / 255) * 100)), title: `${label} opacity`, ariaLabel: `${label} opacity` });
    const read = () => {
      const v = this.input.value;
      this.color = [parseInt(v.slice(1, 3), 16), parseInt(v.slice(3, 5), 16), parseInt(v.slice(5, 7), 16), Math.round((+this.alpha.value / 100) * 255)];
      onChange();
    };
    this.input.addEventListener("input", read);
    this.alpha.addEventListener("input", read);
    this.el = h("div", { class: "swatch" }, this.input, this.alpha);
  }

  set(c: RGBA): void {
    this.color = [...c] as RGBA;
    this.input.value = hex(c);
    this.alpha.value = String(Math.round((c[3] / 255) * 100));
  }
}

export class Swatches {
  readonly el: HTMLElement;
  private primary: Swatch;
  private secondary: Swatch;

  constructor(onChange: () => void = () => {}) {
    this.primary = new Swatch("Primary colour (left button)", [0, 0, 0, 255], onChange);
    this.secondary = new Swatch("Secondary colour (right button, shape fill)", [255, 255, 255, 255], onChange);
    const swap = h("button", { class: "link", title: "Swap colours (X)" }, "Swap");
    swap.addEventListener("click", () => this.swap());
    this.el = h("div", { class: "swatches" }, this.primary.el, this.secondary.el, swap);
  }

  get colors(): [RGBA, RGBA] {
    return [this.primary.color, this.secondary.color];
  }

  set(c: RGBA, which: "left" | "right"): void {
    (which === "right" ? this.secondary : this.primary).set(c);
  }

  swap(): void {
    const a = this.primary.color;
    this.primary.set(this.secondary.color);
    this.secondary.set(a);
  }
}

// ------------------------------------------------------------------ tool settings

export const FONTS = ["DejaVu Sans", "Arial", "Helvetica", "Verdana", "Georgia", "Times New Roman", "Courier New", "Trebuchet MS", "Impact", "Comic Sans MS"];

export class ToolSettings {
  readonly el: HTMLElement;
  private title = h("div", { class: "section" });
  private form: SettingsForm;
  private fontRow: HTMLElement;
  readonly font: HTMLSelectElement;
  private values = new Map<string, Values>();
  private tool: Tool | null = null;

  constructor(onChange: () => void) {
    this.form = new SettingsForm(() => {
      if (this.tool) this.values.set(this.tool.id, this.form.values());
      onChange();
    });
    this.font = h("select", { ariaLabel: "Font" });
    for (const f of FONTS) this.font.append(h("option", { value: f }, f));
    this.fontRow = h("label", { class: "font-row" }, "Font ", this.font);
    this.el = h("div", { class: "tool-settings" }, this.title, this.fontRow, this.form.el);
  }

  show(tool: Tool): void {
    this.tool = tool;
    this.title.textContent = tool.label;
    this.fontRow.hidden = tool.id !== "text";
    this.form.render(tool.options, tool.id, this.optionsFor(tool));
  }

  optionsFor(tool: Tool): Values {
    return { ...defaults(tool.options), ...(this.values.get(tool.id) ?? {}) };
  }

  /** [ / ] — grow or shrink a brush-like tool's size. */
  changeSize(tool: Tool, factor: number): void {
    const spec = tool.options.find((s) => s.name === "size");
    if (!spec) return;
    const cur = Number(this.optionsFor(tool).size);
    let next = factor > 1 ? Math.ceil(cur * factor) : Math.floor(cur * factor);
    if (next === cur) next = cur + (factor > 1 ? 1 : -1);
    next = Math.max(spec.min ?? 1, Math.min(spec.max ?? 500, next));
    this.values.set(tool.id, { ...this.optionsFor(tool), size: next });
    if (tool === this.tool) this.show(tool);
  }
}

// ------------------------------------------------------------------ layers

const THUMB = 40;

function thumbnail(layer: Layer): HTMLCanvasElement {
  const c = h("canvas", { width: THUMB, height: THUMB, class: "thumb" });
  const ctx = c.getContext("2d") as CanvasRenderingContext2D;
  const src = new OffscreenCanvas(layer.width, layer.height);
  (src.getContext("2d") as OffscreenCanvasRenderingContext2D).putImageData(new ImageData(new Uint8ClampedArray(layer.data), layer.width, layer.height), 0, 0);
  const s = Math.min(THUMB / layer.width, THUMB / layer.height);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, (THUMB - layer.width * s) / 2, (THUMB - layer.height * s) / 2, layer.width * s, layer.height * s);
  return c;
}

export class LayersPanel {
  readonly el: HTMLElement;
  readonly list = h("ul", { class: "layers" });
  private opacity = h("input", { type: "range", min: "0", max: "100", ariaLabel: "Layer opacity" });
  private opacityOut = h("output");
  private buttons: Record<string, HTMLButtonElement> = {};
  private doc: EditorDoc | null = null;
  private thumbs = new Map<Uint8ClampedArray, HTMLCanvasElement>();

  constructor(
    private onCommand: (cmd: Command) => void,
    private onActive: () => void,
  ) {
    const b = (name: string, title: string, fn: (d: EditorDoc) => Command | null) => {
      const btn = h("button", { class: "btn small", title }, name);
      btn.addEventListener("click", () => this.run(fn));
      this.buttons[name] = btn;
      return btn;
    };
    this.opacity.addEventListener("input", () => (this.opacityOut.textContent = `${this.opacity.value} %`));
    this.opacity.addEventListener("change", () => {
      const value = +this.opacity.value / 100;
      this.run((d) => ops.setOpacity(d, d.active, value));
    });
    this.el = h("div", { class: "layers-panel" },
      h("div", { class: "section" }, "Layers"),
      this.list,
      h("label", { class: "opacity-row" }, "Opacity ", this.opacity, this.opacityOut),
      h("div", { class: "layer-buttons" },
        b("Add", "New empty layer", ops.addLayer),
        b("Duplicate", "Copy the selected layer", ops.duplicateLayer),
        b("Delete", "Delete the selected layer", ops.deleteLayer),
        b("Merge down", "Merge into the layer below", ops.mergeDown),
        b("Up", "Move up", (d) => ops.moveLayer(d, 1)),
        b("Down", "Move down", (d) => ops.moveLayer(d, -1))));
  }

  private run(fn: (d: EditorDoc) => Command | null): void {
    if (!this.doc) return;
    const cmd = fn(this.doc);
    if (cmd) this.onCommand(cmd);
  }

  render(doc: EditorDoc): void {
    this.doc = doc;
    this.list.replaceChildren();
    const live = new Set<Uint8ClampedArray>();
    for (let i = doc.layers.length - 1; i >= 0; i--) {
      const layer = doc.layers[i];
      live.add(layer.data);
      const eye = h("input", { type: "checkbox", checked: layer.visible, title: "Show / hide", ariaLabel: `Show ${layer.name}` });
      eye.addEventListener("click", (e) => e.stopPropagation());
      eye.addEventListener("change", () => this.run((d) => ops.setVisible(d, i, eye.checked)));
      const name = h("span", { class: "lname", title: "Double-click to rename" }, layer.name);
      name.addEventListener("dblclick", () => {
        const input = h("input", { type: "text", value: layer.name, class: "rename", ariaLabel: "Layer name" });
        const done = (save: boolean) => {
          if (save) this.run((d) => ops.renameLayer(d, i, input.value));
          else this.render(doc);
        };
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") done(true);
          if (e.key === "Escape") done(false);
          e.stopPropagation();
        });
        input.addEventListener("blur", () => done(true));
        name.replaceWith(input);
        input.focus();
        input.select();
      });
      let thumb = this.thumbs.get(layer.data);
      if (!thumb) {
        thumb = thumbnail(layer);
        this.thumbs.set(layer.data, thumb);
      }
      const li = h("li", { class: i === doc.active ? "active" : "" }, eye, thumb, name);
      li.addEventListener("click", () => {
        ops.selectLayer(doc, i);
        this.render(doc);
        this.onActive();
      });
      this.list.append(li);
    }
    for (const key of [...this.thumbs.keys()]) if (!live.has(key)) this.thumbs.delete(key);
    const active = doc.layers[doc.active];
    this.opacity.value = String(Math.round(active.opacity * 100));
    this.opacityOut.textContent = `${this.opacity.value} %`;
    const n = doc.layers.length;
    this.buttons.Delete.disabled = n <= 1;
    this.buttons["Merge down"].disabled = doc.active === 0;
    this.buttons.Up.disabled = doc.active >= n - 1;
    this.buttons.Down.disabled = doc.active === 0;
  }

  /** Pixels of the active layer changed: redraw just its thumbnail. */
  refreshActiveThumb(): void {
    if (!this.doc) return;
    const layer = this.doc.layers[this.doc.active];
    this.thumbs.delete(layer.data);
    this.render(this.doc);
  }
}
