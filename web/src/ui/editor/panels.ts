// Tool bar (left), colour swatches, tool settings and the layers list (right).
import RAIL from "../../editor/rail.json";
import { ICONS as TABLER } from "../icon_data";
import { defaults, type Values } from "../../core/options";
import { selectedIndices, type EditorDoc } from "../../editor/document";
import type { Command } from "../../editor/history";
import type { Layer, RGBA } from "../../editor/layer";
import * as ops from "../../editor/ops";
import { sampleThumbnail } from "../../editor/ora";
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


type RailItem = { kind: "tool" | "action" | "menu"; id: string; label: string | null; icon: string };

const railIcon = (name: string): HTMLElement => {
  const span = h("span", { class: "rail-icon", ariaHidden: "true" });
  span.innerHTML = `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">${(TABLER as Record<string, string>)[name] ?? ""}</svg>`;
  return span;
};

/** The tool rail: tools and actions in groups (rail.json = src/umc/editor/rail.py), names shown
 *  by default; the chevron at the top narrows it to icons only (remembered in localStorage). */
export class ToolRail {
  readonly el = h("nav", { class: "tool-rail", ariaLabel: "Tools" });
  private buttons = new Map<string, HTMLButtonElement>();
  private toggle: HTMLButtonElement;
  private popup: HTMLElement | null = null;
  narrow = false;

  constructor(tools: Tool[], onSelect: (id: string) => void, onAction: (id: string) => void) {
    const byId = new Map(tools.map((t) => [t.id, t]));
    this.toggle = h("button", { type: "button", class: "rail-toggle" });
    this.toggle.addEventListener("click", () => this.setNarrow(!this.narrow));
    this.el.append(this.toggle);
    for (const g of RAIL.groups) {
      this.el.append(h("div", { class: "rail-group" }, g.group));
      for (const it of g.items as RailItem[]) {
        const t = byId.get(it.id);
        const label = it.kind === "tool" && t ? t.label : (it.label as string);
        const b = h("button", { type: "button", class: `rail-item ${it.kind}`, title: t ? `${t.label} (${t.shortcut})` : label, ariaLabel: label }, railIcon(it.icon), h("span", { class: "rail-label" }, label));
        b.dataset.id = it.id;
        if (it.kind === "tool") b.addEventListener("click", () => onSelect(it.id));
        else if (it.kind === "menu") {
          b.setAttribute("aria-haspopup", "menu");
          b.addEventListener("click", (e) => {
            e.stopPropagation();
            this.openMenu(b, it.id, onAction);
          });
        } else b.addEventListener("click", () => onAction(it.id));
        this.buttons.set(it.id, b);
        this.el.append(b);
      }
    }
    let narrow = false;
    try {
      narrow = localStorage.getItem("umc-rail-narrow") === "1";
    } catch {
      /* storage blocked: the default (names shown) */
    }
    this.setNarrow(narrow, false);
    document.addEventListener("pointerdown", (e) => {
      if (this.popup && !this.popup.contains(e.target as Node)) this.closeMenu();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.closeMenu();
    });
  }

  setNarrow(narrow: boolean, remember = true): void {
    this.narrow = narrow;
    this.el.classList.toggle("narrow", narrow);
    this.toggle.replaceChildren(railIcon(narrow ? "chevrons-right" : "chevrons-left"));
    this.toggle.title = narrow ? "Show tool names" : "Icons only";
    this.toggle.setAttribute("aria-label", this.toggle.title);
    if (!remember) return;
    try {
      localStorage.setItem("umc-rail-narrow", narrow ? "1" : "0");
    } catch {
      /* fine: just not remembered */
    }
  }

  private openMenu(anchor: HTMLElement, id: string, onAction: (id: string) => void): void {
    if (this.popup) return this.closeMenu();
    const menu = h("div", { class: "menu-items context-menu rail-menu", role: "menu", ariaLabel: anchor.getAttribute("aria-label") ?? "" });
    for (const entry of (RAIL.menus as Record<string, ([string, string] | null)[]>)[id]) {
      if (!entry) {
        menu.append(h("hr"));
        continue;
      }
      const [op, text] = entry;
      const item = h("button", { type: "button", class: "menu-item", role: "menuitem" }, text);
      item.addEventListener("click", () => {
        this.closeMenu();
        onAction(op);
      });
      menu.append(item);
    }
    document.body.append(menu);
    const r = anchor.getBoundingClientRect();
    menu.style.left = `${r.right + 4}px`;
    menu.style.top = `${Math.min(r.top, window.innerHeight - menu.offsetHeight - 8)}px`;
    this.popup = menu;
    (menu.querySelector("button") as HTMLButtonElement | null)?.focus();
  }

  private closeMenu(): void {
    this.popup?.remove();
    this.popup = null;
  }

  select(id: string): void {
    for (const [tid, b] of this.buttons) if (b.classList.contains("tool")) b.setAttribute("aria-pressed", String(tid === id));
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

  /** Set some of `tool`'s settings (e.g. from a text layer being edited). */
  setValues(tool: Tool, values: Values): void {
    this.values.set(tool.id, { ...this.optionsFor(tool), ...values });
    if (tool === this.tool) this.show(tool);
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
  // Point-sampled (no copy of the layer), so it's cheap enough to redo after every change.
  const t = sampleThumbnail(layer, THUMB);
  const c = h("canvas", { width: THUMB, height: THUMB, class: "thumb" });
  const ctx = c.getContext("2d") as CanvasRenderingContext2D;
  ctx.putImageData(new ImageData(t.data as Uint8ClampedArray<ArrayBuffer>, t.width, t.height), Math.floor((THUMB - t.width) / 2), Math.floor((THUMB - t.height) / 2));
  return c;
}

const ARROW_UP = '<svg viewBox="0 0 10 6" width="10" height="6" aria-hidden="true"><path d="M0 6 5 0l5 6z" fill="currentColor"/></svg>';
const ARROW_DOWN = '<svg viewBox="0 0 10 6" width="10" height="6" aria-hidden="true"><path d="M0 0h10L5 6z" fill="currentColor"/></svg>';

/** Layers list (top layer first). Click selects, Ctrl/⌘+click and Shift+click select several,
 *  dragging a row reorders, ▲▼ on each row move it, right-click (or Shift+F10) opens a menu with
 *  every layer action. The opacity slider and the buttons act on all selected layers. */
const MASK_THUMB = 26;

/** A small grey picture of the layer's mask (black hides, white shows). */
function maskThumbnail(layer: Layer): HTMLCanvasElement {
  const c = h("canvas", { width: MASK_THUMB, height: MASK_THUMB, class: "mask-thumb", title: "This layer has a mask: black hides, white shows" });
  const m = layer.mask as Uint8ClampedArray;
  const k = Math.max(layer.width, layer.height) / MASK_THUMB;
  const w = Math.max(1, Math.round(layer.width / k));
  const ht = Math.max(1, Math.round(layer.height / k));
  const img = new ImageData(w, ht);
  for (let y = 0; y < ht; y++)
    for (let x = 0; x < w; x++) {
      const v = m[(Math.min(layer.height - 1, Math.floor(y * k)) * layer.width + Math.min(layer.width - 1, Math.floor(x * k))) * 4];
      img.data.set([v, v, v, 255], (y * w + x) * 4);
    }
  (c.getContext("2d") as CanvasRenderingContext2D).putImageData(img, Math.floor((MASK_THUMB - w) / 2), Math.floor((MASK_THUMB - ht) / 2));
  return c;
}

export class LayersPanel {
  readonly el: HTMLElement;
  readonly list = h("ul", { class: "layers", ariaLabel: "Layers" });
  private opacity = h("input", { type: "range", min: "0", max: "100", ariaLabel: "Layer opacity" });
  private opacityOut = h("output");
  private buttons: Record<"add" | "duplicate" | "delete" | "merge", HTMLButtonElement>;
  private doc: EditorDoc | null = null;
  private anchor: number | null = null; // where a Shift+click range starts (layer index)
  private renamers = new Map<number, () => void>();
  private press: { index: number; y: number; pointerId: number } | null = null;
  private dragging = false;
  private suppressClick = false;
  private menu: HTMLElement | null = null;
  private closeMenuHandlers: (() => void) | null = null;

  constructor(
    private onCommand: (cmd: Command) => void,
    private onActive: () => void,
    private onEditText: (index: number) => void = () => {},
  ) {
    const b = (name: string, title: string, fn: () => void) => {
      const btn = h("button", { class: "btn small", title }, name);
      btn.addEventListener("click", fn);
      return btn;
    };
    this.buttons = {
      add: b("Add", "New empty layer", () => this.add()),
      duplicate: b("Duplicate", "Copy the selected layers", () => this.duplicate()),
      delete: b("Delete", "Delete the selected layers", () => this.remove()),
      merge: b("Merge down", "Merge into the layer below", () => this.merge()),
    };
    this.opacity.addEventListener("input", () => (this.opacityOut.textContent = `${this.opacity.value} %`));
    this.opacity.addEventListener("change", () => {
      const value = +this.opacity.value / 100;
      this.run((d) => ops.setOpacityLayers(d, selectedIndices(d), value));
    });
    this.list.title = "Double-click a name to rename · drag to reorder · Ctrl+click or Shift+click to select several · right-click for more";
    this.list.addEventListener("pointermove", (e) => this.pointerMove(e));
    this.list.addEventListener("pointerup", (e) => this.pointerUp(e));
    this.list.addEventListener("pointercancel", () => this.endDrag());
    this.list.addEventListener("contextmenu", (e) => {
      if (e.target === this.list) {
        e.preventDefault();
        this.openMenu(null, e.clientX, e.clientY);
      }
    });
    this.el = h("div", { class: "layers-panel" },
      h("div", { class: "section" }, "Layers"),
      this.list,
      h("label", { class: "opacity-row" }, "Opacity ", this.opacity, this.opacityOut),
      h("div", { class: "layer-buttons" }, this.buttons.add, this.buttons.duplicate, this.buttons.delete, this.buttons.merge));
  }

  // ---------------------------------------------------------------- actions
  private run(fn: (d: EditorDoc) => Command | null): void {
    if (!this.doc) return;
    const cmd = fn(this.doc);
    if (cmd) this.onCommand(cmd);
  }

  add(): void { this.run(ops.addLayer); }
  duplicate(): void { this.run((d) => ops.duplicateLayers(d, selectedIndices(d))); }
  remove(): void { this.run((d) => ops.deleteLayers(d, selectedIndices(d))); }
  merge(): void {
    this.run((d) => {
      const chosen = selectedIndices(d);
      return chosen.length > 1 ? ops.mergeLayers(d, chosen) : ops.mergeDown(d);
    });
  }
  move(delta: number): void { this.run((d) => ops.moveLayersBy(d, selectedIndices(d), delta)); }
  moveToEnd(top: boolean): void { this.run((d) => ops.moveLayersTo(d, selectedIndices(d), top ? d.layers.length : 0)); }
  setVisible(visible: boolean): void { this.run((d) => ops.setVisibleLayers(d, selectedIndices(d), visible)); }
  selectAll(): void {
    if (!this.doc) return;
    ops.selectLayers(this.doc, this.doc.layers.map((_, i) => i), this.doc.active);
    this.showSelection();
    this.onActive();
  }

  // ---------------------------------------------------------------- list
  render(doc: EditorDoc): void {
    this.doc = doc;
    this.closeMenu();
    this.renamers.clear();
    this.list.replaceChildren();
    const n = doc.layers.length;
    for (let i = n - 1; i >= 0; i--) {
      const layer = doc.layers[i];
      const eye = h("input", { type: "checkbox", checked: layer.visible, title: "Show / hide", ariaLabel: `Show ${layer.name}` });
      eye.addEventListener("click", (e) => {
        e.stopPropagation();
        if (e.altKey) {
          // Alt+click the eye: show only this layer (again: show all)
          e.preventDefault();
          this.run((d) => ops.soloLayer(d, i));
        }
      });
      eye.addEventListener("change", () => this.run((d) => ops.setVisible(d, i, eye.checked)));
      const name = h("span", { class: "lname", title: "Double-click to rename" }, layer.name);
      const arrow = (up: boolean) => {
        const a = h("button", { class: "arrow", title: up ? "Move up" : "Move down", ariaLabel: `Move ${layer.name} ${up ? "up" : "down"}`, disabled: up ? i === n - 1 : i === 0, tabIndex: -1 });
        a.innerHTML = up ? ARROW_UP : ARROW_DOWN;
        a.addEventListener("pointerdown", (e) => e.stopPropagation());
        a.addEventListener("dblclick", (e) => e.stopPropagation());
        a.addEventListener("click", (e) => {
          e.stopPropagation();
          if (!selectedIndices(doc).includes(i)) ops.selectLayer(doc, i); // another row: move just that one
          this.move(up ? 1 : -1);
        });
        return a;
      };
      const parts: (Node | string)[] = [eye, thumbnail(layer), name];
      if (layer.mask) parts.push(maskThumbnail(layer));
      const li = h("li", { tabIndex: 0, ariaLabel: `Layer ${layer.name}` }, ...parts, h("span", { class: "arrows" }, arrow(true), arrow(false)));
      li.dataset.index = String(i);
      const startRename = () => {
        const input = h("input", { type: "text", value: layer.name, class: "rename", ariaLabel: "Layer name" });
        let finished = false;
        const done = (save: boolean) => {
          if (finished) return;
          finished = true;
          if (save) this.run((d) => ops.renameLayer(d, i, input.value));
          this.render(doc); // also restores the name if nothing changed
        };
        for (const ev of ["click", "dblclick", "pointerdown", "contextmenu"]) input.addEventListener(ev, (e) => e.stopPropagation());
        input.addEventListener("keydown", (e) => {
          if (e.key === "Enter") done(true);
          if (e.key === "Escape") done(false);
          e.stopPropagation();
        });
        input.addEventListener("blur", () => done(true));
        name.replaceWith(input);
        input.focus();
        input.select();
      };
      this.renamers.set(i, startRename);
      li.addEventListener("dblclick", startRename);
      li.addEventListener("keydown", (e) => {
        if (e.key === "F2") startRename();
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          li.click();
        }
        if (e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
          e.preventDefault();
          const r = li.getBoundingClientRect();
          this.contextMenu(i, r.left + 24, r.bottom);
        }
      });
      li.addEventListener("click", (e) => this.click(i, e));
      li.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        this.contextMenu(i, e.clientX, e.clientY);
      });
      li.addEventListener("pointerdown", (e) => {
        if (e.button === 0) this.press = { index: i, y: e.clientY, pointerId: e.pointerId };
      });
      this.list.append(li);
    }
    this.showSelection();
  }

  private rows(): HTMLLIElement[] {
    return [...this.list.children] as HTMLLIElement[];
  }

  /** Update the row highlights and the controls without rebuilding (keeps double-click working). */
  private showSelection(): void {
    const doc = this.doc as EditorDoc;
    const chosen = new Set(selectedIndices(doc));
    this.list.classList.toggle("multi", chosen.size > 1);
    for (const row of this.rows()) {
      const i = Number(row.dataset.index);
      row.classList.toggle("selected", chosen.has(i));
      row.classList.toggle("active", i === doc.active);
      row.setAttribute("aria-selected", String(chosen.has(i)));
    }
    this.syncControls();
  }

  private click(i: number, e: MouseEvent): void {
    const doc = this.doc;
    if (!doc || this.suppressClick) return;
    const chosen = selectedIndices(doc);
    const before = JSON.stringify([doc.active, chosen]);
    if (e.shiftKey && this.anchor !== null && this.anchor < doc.layers.length) {
      const lo = Math.min(this.anchor, i);
      const hi = Math.max(this.anchor, i);
      const range = Array.from({ length: hi - lo + 1 }, (_, k) => lo + k);
      ops.selectLayers(doc, e.ctrlKey || e.metaKey ? [...chosen, ...range] : range, i);
    } else if (e.ctrlKey || e.metaKey) {
      if (chosen.includes(i)) {
        const rest = chosen.filter((k) => k !== i);
        if (rest.length) ops.selectLayers(doc, rest, i === doc.active ? rest[rest.length - 1] : doc.active);
      } else ops.selectLayers(doc, [...chosen, i], i);
      this.anchor = i;
    } else {
      ops.selectLayer(doc, i);
      this.anchor = i;
    }
    if (JSON.stringify([doc.active, selectedIndices(doc)]) === before) return;
    this.showSelection();
    this.onActive();
  }

  private syncControls(): void {
    const doc = this.doc as EditorDoc;
    const active = doc.layers[doc.active];
    this.opacity.value = String(Math.round(active.opacity * 100));
    this.opacityOut.textContent = `${this.opacity.value} %`;
    const chosen = selectedIndices(doc);
    const many = chosen.length > 1;
    this.buttons.delete.disabled = chosen.length >= doc.layers.length;
    this.buttons.merge.textContent = many ? "Merge layers" : "Merge down";
    this.buttons.merge.title = many ? "Merge the selected layers into one" : "Merge into the layer below";
    this.buttons.merge.disabled = !many && doc.active === 0;
  }

  // ---------------------------------------------------------------- drag to reorder
  /** The gap (in rows: 0 = above the first row, rows().length = below the last) under `y`. */
  private gapAt(y: number): number {
    const rows = this.rows();
    for (let r = 0; r < rows.length; r++) {
      const b = rows[r].getBoundingClientRect();
      if (y < b.top + b.height / 2) return r;
    }
    return rows.length;
  }

  private showGap(gap: number | null): void {
    const rows = this.rows();
    rows.forEach((row, r) => {
      row.classList.toggle("drop-above", gap === r);
      row.classList.toggle("drop-below", gap === rows.length && r === rows.length - 1);
    });
  }

  private pointerMove(e: PointerEvent): void {
    const doc = this.doc;
    if (!doc || !this.press || e.pointerId !== this.press.pointerId) return;
    if (!(e.buttons & 1)) {
      // The button was let go outside the list (its pointerup never reached us): no drag.
      this.endDrag();
      return;
    }
    if (!this.dragging) {
      if (Math.abs(e.clientY - this.press.y) < 5) return;
      this.dragging = true;
      this.list.setPointerCapture(e.pointerId);
      this.list.classList.add("dragging");
      document.body.classList.add("no-select");
      window.getSelection()?.removeAllRanges();
      if (!selectedIndices(doc).includes(this.press.index)) {
        ops.selectLayer(doc, this.press.index);
        this.anchor = this.press.index;
        this.showSelection();
        this.onActive();
      }
    }
    const box = this.list.getBoundingClientRect();
    if (e.clientY < box.top + 16) this.list.scrollTop -= 8;
    else if (e.clientY > box.bottom - 16) this.list.scrollTop += 8;
    this.showGap(this.gapAt(e.clientY));
  }

  private pointerUp(e: PointerEvent): void {
    const doc = this.doc;
    if (!this.dragging || !doc) {
      this.press = null;
      return;
    }
    const gapRow = this.gapAt(e.clientY);
    this.endDrag();
    // The click that follows this pointerup must not reselect a single row.
    this.suppressClick = true;
    setTimeout(() => (this.suppressClick = false), 0);
    const gap = doc.layers.length - gapRow;
    this.run((d) => ops.moveLayersTo(d, selectedIndices(d), gap));
  }

  private endDrag(): void {
    if (this.press && this.list.hasPointerCapture(this.press.pointerId)) this.list.releasePointerCapture(this.press.pointerId);
    this.press = null;
    this.dragging = false;
    this.list.classList.remove("dragging");
    document.body.classList.remove("no-select");
    this.showGap(null);
  }

  // ---------------------------------------------------------------- right-click menu
  private contextMenu(i: number, x: number, y: number): void {
    const doc = this.doc;
    if (!doc) return;
    if (!selectedIndices(doc).includes(i)) {
      ops.selectLayer(doc, i); // right-click outside the selection selects that row
      this.anchor = i;
      this.showSelection();
      this.onActive();
    }
    this.openMenu(i, x, y);
  }

  /** Menu items for a row (null = empty space below the layers): [label, action, enabled]. */
  menuItems(row: number | null): ([string, () => void, boolean] | null)[] {
    const items: ([string, () => void, boolean] | null)[] = [["New layer", () => this.add(), true]];
    const doc = this.doc;
    if (!doc || row === null) return items;
    const chosen = selectedIndices(doc);
    const n = chosen.length;
    const many = n > 1;
    const word = many ? `${n} layers` : "layer";
    const top = chosen[n - 1] === doc.layers.length - 1;
    const bottom = chosen[0] === 0;
    items.push(
      [`Duplicate ${word}`, () => this.duplicate(), true],
      [`Delete ${word}`, () => this.remove(), n < doc.layers.length],
      [many ? `Merge ${n} layers` : "Merge down", () => this.merge(), many || doc.active > 0],
      null,
      ["Move up", () => this.move(1), !top],
      ["Move down", () => this.move(-1), !bottom],
      ["Move to top", () => this.moveToEnd(true), !top || many],
      ["Move to bottom", () => this.moveToEnd(false), !bottom || many],
      null,
    );
    if (!many) items.push(["Rename…", () => this.renamers.get(doc.active)?.(), true]);
    if (!many && doc.layers[doc.active].text) items.push(["Edit text…", () => this.onEditText(doc.active), true]);
    if (chosen.some((i) => doc.layers[i].visible)) items.push([`Hide ${word}`, () => this.setVisible(false), true]);
    if (chosen.some((i) => !doc.layers[i].visible)) items.push([`Show ${word}`, () => this.setVisible(true), true]);
    if (chosen.length === 1 && doc.layers.length > 1) {
      const only = chosen[0];
      items.push([ops.isSolo(doc, only) ? "Show all layers" : "Show only this layer (Alt+click eye)", () => this.run((d) => ops.soloLayer(d, only)), true]);
    }
    items.push(["Select all layers", () => this.selectAll(), true]);
    if (!many) {
      // layer mask actions for the one selected layer
      items.push(null);
      if (!doc.layers[doc.active].mask) {
        items.push(["Add layer mask", () => this.run((d) => ops.addMask(d)), true]);
        if (doc.selection) items.push(["Mask from selection", () => this.run((d) => ops.addMask(d, true)), true]);
      } else {
        items.push(["Invert mask", () => this.run((d) => ops.invertMask(d)), true], ["Apply mask (bake it in)", () => this.run((d) => ops.applyMask(d)), true], ["Delete mask", () => this.run((d) => ops.deleteMask(d)), true]);
      }
    }
    return items;
  }

  private openMenu(row: number | null, x: number, y: number): void {
    this.closeMenu();
    const menu = h("div", { class: "menu-items context-menu", role: "menu", ariaLabel: "Layer actions" });
    for (const it of this.menuItems(row)) {
      if (!it) {
        menu.append(h("hr"));
        continue;
      }
      const [label, fn, enabled] = it;
      const b = h("button", { class: "menu-item", role: "menuitem", disabled: !enabled }, label);
      b.addEventListener("click", () => {
        this.closeMenu();
        fn();
      });
      menu.append(b);
    }
    document.body.append(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - r.width - 4))}px`;
    menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - r.height - 4))}px`;
    this.menu = menu;
    const outside = (e: Event) => {
      if (!menu.contains(e.target as Node)) this.closeMenu();
    };
    const keys = (e: KeyboardEvent) => {
      const items = [...menu.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
      const at = items.indexOf(document.activeElement as HTMLButtonElement);
      if (e.key === "Escape") this.closeMenu(true);
      else if (e.key === "ArrowDown") items[(at + 1) % items.length]?.focus();
      else if (e.key === "ArrowUp") items[(at - 1 + items.length) % items.length]?.focus();
      else return;
      e.preventDefault();
      e.stopPropagation(); // Esc must not also discard a crop, arrows must not reach the page
    };
    const close = () => this.closeMenu();
    window.addEventListener("pointerdown", outside, true);
    window.addEventListener("keydown", keys, true);
    window.addEventListener("blur", close);
    window.addEventListener("resize", close);
    this.closeMenuHandlers = () => {
      window.removeEventListener("pointerdown", outside, true);
      window.removeEventListener("keydown", keys, true);
      window.removeEventListener("blur", close);
      window.removeEventListener("resize", close);
    };
    menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }

  private closeMenu(refocus = false): void {
    if (!this.menu) return;
    this.closeMenuHandlers?.();
    this.closeMenuHandlers = null;
    this.menu.remove();
    this.menu = null;
    if (refocus && this.doc) this.rows().find((r) => Number(r.dataset.index) === this.doc?.active)?.focus();
  }

  /** Something changed (pixels, layers, undo): redraw the list — thumbnails are cheap samples. */
  refreshActiveThumb(): void {
    if (this.doc && !this.list.querySelector("input.rename") && !this.dragging) this.render(this.doc);
  }
}
