/**
 * The website's vector editor page (the desktop's gui/vectoreditor/): a tool rail, the drawing,
 * and on the right the Layers list and the Appearance panel. The model is vectoredit/ (shared
 * cases with the desktop), so a save rewrites only what changed. Loaded on demand.
 *
 * The drawing is shown as an <img> of the SVG itself (the browser draws it exactly); a <canvas>
 * on top draws the page, the selection, handles, the rubber band, path points and shapes being
 * drawn. A gesture only previews (`preview`) and becomes one undo step when it ends.
 *
 * Save downloads a file; "Save to a file…" (Chrome / Edge) writes to a picked file, and Save
 * (Ctrl+S) then writes there again. Export (PNG, JPG, WebP, TIFF) runs the vector converter in
 * the worker; PDF / EPS export is desktop only.
 */
import { converterById } from "../../core/registry";
import { defaults } from "../../core/options";
import type { InputFile } from "../../core/types";
import * as vdoc from "../../vectoredit/doc";
import { type Path, type VectorDoc, VectorError } from "../../vectoredit/doc";
import { Box, type Matrix, num } from "../../vectoredit/geom";
import { History } from "../../vectoredit/history";
import { type Opened, newDocument, openBytes, toBytes } from "../../vectoredit/io";
import * as pathdata from "../../vectoredit/pathdata";
import * as shapes from "../../vectoredit/shapes";
import * as xmlsrc from "../../vectoredit/xmlsrc";
import type { ConversionClient } from "../../worker/client";
import { backLabel } from "../back";
import { icon } from "../icons";
import type { IconName } from "../icon_data";
import { SHORTCUTS, keyName, showKey } from "./shortcuts";

export type Tool = "select" | "node" | "rect" | "ellipse" | "line" | "pen" | "text";
export const TOOLS: [Tool, string, string, string][] = [
  ["select", "pointer", "Select", "V"],
  ["node", "point", "Node", "A"],
  ["rect", "square", "Rectangle", "R"],
  ["ellipse", "circle", "Ellipse", "E"],
  ["line", "line", "Line", "L"],
  ["pen", "vector-bezier-2", "Pen", "P"],
  ["text", "typography", "Text", "T"],
];
const HANDLE = 4;
const GRAB = 8; // a little more than the desktop's 6: fingers
const DRAG_START = 3;
const MIN_ZOOM = 0.02;
const MAX_ZOOM = 64;
const NUDGE = 1;
const NUDGE_BIG = 10;
const PASTE_STEP = 10;
const HANDLES: [string, number, number][] = [
  ["nw", 0, 0], ["n", 0.5, 0], ["ne", 1, 0], ["e", 1, 0.5],
  ["se", 1, 1], ["s", 0.5, 1], ["sw", 0, 1], ["w", 0, 0.5],
];
export const NEW_SIZES: [string, number, number, string][] = [
  ["A4 portrait (210 × 297 mm)", 210, 297, "mm"],
  ["A4 landscape (297 × 210 mm)", 297, 210, "mm"],
  ["Letter (8.5 × 11 in)", 8.5, 11, "in"],
  ["Full HD (1920 × 1080 px)", 1920, 1080, "px"],
  ["Square (1000 × 1000 px)", 1000, 1000, "px"],
  ["Icon (512 × 512 px)", 512, 512, "px"],
];
const EXPORTS: Record<string, string> = { png: "PNG", jpg: "JPEG", webp: "WebP", tiff: "TIFF" };

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

export interface VectorEditorOptions {
  client: ConversionClient;
  onBack: () => void;
}

interface Gesture {
  kind: "move" | "resize" | "band" | "draw" | "point" | "pen" | "pan" | "";
  sx: number; // the press on screen
  sy: number;
  dx0: number; // the press in document units
  dy0: number;
  base: VectorDoc;
  handle?: string;
  box?: Box | null;
  point?: [number, string];
  moved: boolean;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

const extOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
};
const baseOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
};
const samePath = vdoc.samePath;
const hasPath = (list: Path[], p: Path) => list.some((q) => samePath(q, p));

/** Drags land on multiples of this (the desktop's VectorCanvas.step). */
export function stepFor(scale: number): number {
  return 10 ** Math.floor(Math.log10(Math.max(1 / scale, 1e-3)) + 1e-9);
}
export const snapTo = (v: number, step: number) => Math.round(Math.round(v / step) * step * 1e6) / 1e6;

export class VectorEditorPage {
  readonly el: HTMLElement = el("div", "editor vector-editor");
  opened: Opened = newDocument(800, 600);
  history = new History(this.opened.doc);
  selection: Path[] = [];
  tool: Tool = "select";
  scale = 1;
  ox = 0; // where document (0, 0) is in the stage (CSS px)
  oy = 0;
  node: [number, string] | null = null;
  styleForNew: [string, string][] = [["fill", "#4a90d9"], ["stroke", "#1d3c5e"]];
  private preview: VectorDoc | null = null;
  private penNodes: [number, number, number, number][] = [];
  private penHover: [number, number] | null = null;
  private gesture: Gesture | null = null;
  private band: [number, number, number, number] | null = null;
  private drawBox: Box | null = null;
  private drawEnd: [number, number] = [0, 0];
  private sourceName: string | null = null;
  private saveName: string | null = null;
  private handle: FileHandle | undefined;
  private active = false;
  private saving = false;
  private fitted = false;
  private pasteCount = 0;
  private lastCopy = "";
  private imageUrl = "";
  private imageFor: VectorDoc | null = null;

  private readonly title = el("span", "vec-title");
  private readonly message = el("div", "msg");
  private readonly zoomLabel = el("span", "vec-zoom");
  private readonly stage = el("div", "vec-stage");
  private readonly image = el("img", "vec-image");
  private readonly overlay = el("canvas", "vec-overlay");
  private readonly layersEl = el("ul", "vec-layers");
  private readonly appearance = el("div", "vec-appearance");
  private readonly fields: Record<string, HTMLInputElement> = {};
  private readonly textRow = el("label", "field vec-text-row");
  private readonly picker = el("input");
  private readonly helpDialog: HTMLDialogElement;
  private readonly saveDialog: HTMLDialogElement;
  private readonly sizeDialog: HTMLDialogElement;
  private readonly toolButtons = new Map<Tool, HTMLButtonElement>();
  private readonly undoBtn: HTMLButtonElement;
  private readonly redoBtn: HTMLButtonElement;
  private readonly saveBtn: HTMLButtonElement;
  readonly actions: Record<string, () => unknown>;

  constructor(private readonly opts: VectorEditorOptions) {
    this.el.hidden = true;
    const btn = (label: string, title: string, run: () => void, cls = "btn small") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", run);
      return b;
    };
    this.undoBtn = btn("Undo", "Undo (Ctrl+Z)", () => this.undo());
    this.redoBtn = btn("Redo", "Redo (Ctrl+Y)", () => this.redo());
    this.saveBtn = btn("Save", "Save (Ctrl+S)", () => void this.save(), "primary small");
    const tools = el("div", "top-tools");
    tools.append(
      btn("Open…", "Open a drawing (Ctrl+O)", () => this.picker.click()),
      btn("New…", "New drawing (Alt+N)", () => void this.askNew()),
      this.title,
      el("div", "spacer"),
      this.undoBtn,
      this.redoBtn,
      btn("?", "Keyboard shortcuts (F1)", () => this.helpDialog.showModal()),
      btn("Export…", "Export as PNG, JPG, WebP or TIFF (Alt+E)", () => void this.exportImage()),
      btn("Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs()),
    );
    const top = el("div", "editor-top");
    top.append(backLabel(btn("← Back to Convert", "Leave the editor", () => this.back(), "link")), el("span", "gap"), tools, this.saveBtn);

    const rail = el("div", "vec-rail");
    rail.setAttribute("role", "toolbar");
    rail.setAttribute("aria-label", "Tools");
    for (const [tool, iconName, label, key] of TOOLS) {
      const b = btn("", `${label} (${key})`, () => this.setTool(tool), "vec-tool");
      b.append(icon(iconName as IconName, 20));
      b.setAttribute("aria-label", label);
      b.dataset.tool = tool;
      b.addEventListener("mousedown", (e) => e.preventDefault());
      this.toolButtons.set(tool, b);
      rail.append(b);
    }

    this.stage.tabIndex = 0;
    this.stage.setAttribute("aria-label", "Drawing");
    this.image.alt = "";
    this.image.draggable = false;
    this.stage.append(this.image, this.overlay);
    this.stage.addEventListener("pointerdown", (e) => this.pointerDown(e));
    this.stage.addEventListener("pointermove", (e) => this.pointerMove(e));
    this.stage.addEventListener("pointerup", (e) => this.pointerUp(e));
    this.stage.addEventListener("pointercancel", () => this.cancelGesture());
    this.stage.addEventListener("dblclick", (e) => this.doubleClick(e));
    this.stage.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    this.stage.addEventListener("keydown", (e) => this.stageKey(e));
    this.stage.addEventListener("copy", (e) => this.clipboardEvent(e, false));
    this.stage.addEventListener("cut", (e) => this.clipboardEvent(e, true));
    this.stage.addEventListener("paste", (e) => {
      e.preventDefault();
      const text = e.clipboardData?.getData("image/svg+xml") || e.clipboardData?.getData("text/plain") || "";
      this.paste(text);
    });
    this.image.addEventListener("load", () => this.drawOverlay());
    new ResizeObserver(() => {
      if (!this.fitted) this.fit();
      else this.draw();
    }).observe(this.stage);

    const side = el("div", "vec-side");
    side.append(el("h3", "", "Layers"), this.layersEl, this.appearance);
    this.buildAppearance();
    this.layersEl.addEventListener("click", (e) => this.layerClick(e));
    this.layersEl.addEventListener("dblclick", (e) => {
      const row = (e.target as HTMLElement).closest<HTMLElement>("li[data-path]");
      if (row && !(e.target instanceof HTMLInputElement)) void this.askRename(JSON.parse(row.dataset.path!));
    });
    this.layersEl.addEventListener("change", (e) => {
      const box = e.target as HTMLInputElement;
      const row = box.closest<HTMLElement>("li[data-path]");
      if (row) this.edit((d) => vdoc.setHidden(d, [JSON.parse(row.dataset.path!)], !box.checked), box.checked ? "Show" : "Hide");
    });

    const body = el("div", "vec-body");
    body.append(rail, this.stage, side);
    const status = el("div", "editor-status");
    status.append(
      this.message,
      el("div", "spacer"),
      btn("−", "Zoom out (Ctrl+-)", () => this.zoomBy(1 / 1.25)),
      btn("Fit", "Fit the page (Ctrl+0)", () => this.fit()),
      btn("+", "Zoom in (Ctrl+=)", () => this.zoomBy(1.25)),
      this.zoomLabel,
    );

    this.picker.type = "file";
    this.picker.accept = ".svg,.svgz,image/svg+xml";
    this.picker.hidden = true;
    this.picker.addEventListener("change", () => {
      const f = this.picker.files?.[0];
      this.picker.value = "";
      if (f) void this.openFiles([f]);
    });

    this.helpDialog = this.buildHelp();
    this.saveDialog = this.buildSaveDialog();
    this.sizeDialog = this.buildSizeDialog();
    this.el.append(top, body, status, this.picker, this.helpDialog, this.saveDialog, this.sizeDialog);

    this.actions = {
      new: () => void this.askNew(),
      open: () => this.picker.click(),
      save: () => void this.save(),
      save_as: () => void this.saveAs(),
      export: () => void this.exportImage(),
      undo: () => this.undo(),
      redo: () => this.redo(),
      copy: () => void this.copyToClipboard(),
      cut: () => void this.copyToClipboard().then((ok) => ok && this.delete()),
      paste: () => void this.pasteFromClipboard(),
      duplicate: () => this.duplicate(),
      delete: () => this.delete(),
      select_all: () => this.selectAll(),
      deselect: () => this.escape(),
      group: () => this.group(),
      ungroup: () => this.ungroup(),
      forward: () => this.reorder("forward"),
      backward: () => this.reorder("backward"),
      front: () => this.reorder("front"),
      back: () => this.reorder("back"),
      to_path: () => this.toPath(),
      finish: () => this.finishPen(),
      fit: () => this.fit(),
      actual: () => this.setZoom(1),
      zoom_in: () => this.zoomBy(1.25),
      zoom_out: () => this.zoomBy(1 / 1.25),
      help: () => this.helpDialog.showModal(),
    };
    for (const [tool] of TOOLS) this.actions[`tool_${tool}`] = () => this.setTool(tool);
    document.addEventListener("keydown", this.onKey);
    window.addEventListener("beforeunload", this.onUnload);
    if (typeof location !== "undefined" && location.search.includes("e2e")) this.installTestHook();
    this.setTool("select");
    this.showDoc(true);
  }

  // ---------------------------------------------------------------- test hook
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __vectoreditor: object }).__vectoreditor = {
      text: () => vdoc.write(page.doc),
      items: () => vdoc.items(page.doc).map((i) => ({ ...i })),
      get selection() {
        return page.selection.map((p) => [...p]);
      },
      get dirty() {
        return page.history.dirty;
      },
      get tool() {
        return page.tool;
      },
      select: (paths: Path[]) => page.setSelection(paths),
      /** A document point → client (screen) coordinates, for real mouse moves in tests. */
      client: (x: number, y: number) => {
        const r = page.stage.getBoundingClientRect();
        return [r.left + page.ox + x * page.scale, r.top + page.oy + y * page.scale];
      },
      setZoom: (s: number) => page.setZoom(s),
    };
  }

  // ---------------------------------------------------------------- state
  get doc(): VectorDoc {
    return this.history.doc;
  }

  get dirty(): boolean {
    return this.history.dirty;
  }

  private shown(): VectorDoc {
    return this.preview ?? this.doc;
  }

  show(): void {
    this.active = true;
    this.el.hidden = false;
    this.fitted = false;
    requestAnimationFrame(() => {
      this.fit();
      this.stage.focus();
    });
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
  }

  lastError(): string {
    return this.message.classList.contains("error") ? (this.message.textContent ?? "") : "";
  }

  private showMessage(text: string, error = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", error);
  }

  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.active && this.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  private readonly onKey = (e: KeyboardEvent) => {
    if (!this.active || this.el.hidden || this.el.querySelector("dialog[open]")) return;
    const s = SHORTCUTS.find((x) => x.keys.includes(keyName(e)) && !x.canvas);
    if (!s) return;
    const t = e.target;
    const inBox = t instanceof HTMLTextAreaElement || (t instanceof HTMLInputElement && t.type !== "checkbox");
    if (inBox && !["save", "save_as"].includes(s.id)) return;
    e.preventDefault();
    e.stopPropagation();
    this.actions[s.id]();
  };

  private stageKey(e: KeyboardEvent): void {
    const name = keyName(e);
    const s = SHORTCUTS.find((x) => x.canvas && x.keys.includes(name));
    if (!s) return;
    if (["copy", "cut", "paste"].includes(s.id)) return; // the browser's clipboard events (below)
    e.preventDefault();
    if (s.id.startsWith("nudge_")) {
      const dir: Record<string, [number, number]> = { nudge_left: [-1, 0], nudge_right: [1, 0], nudge_up: [0, -1], nudge_down: [0, 1] };
      this.nudge(...dir[s.id], e.shiftKey);
      return;
    }
    this.actions[s.id]();
  }

  // ---------------------------------------------------------------- opening
  async openFiles(list: File[]): Promise<boolean> {
    const f = list.find((x) => ["svg", "svgz"].includes(extOf(x.name)));
    if (!f) {
      this.showMessage("Choose an SVG drawing (.svg or .svgz).", true);
      return false;
    }
    if (this.saving || !this.discardOk()) return false;
    let opened: Opened;
    try {
      opened = openBytes(new Uint8Array(await f.arrayBuffer()));
    } catch (e) {
      this.showMessage(e instanceof VectorError ? `“${f.name}”: ${e.message}` : `“${f.name}” couldn't be opened.`, true);
      if (!(e instanceof VectorError)) console.error(e);
      return false;
    }
    this.load(f.name, opened);
    return true;
  }

  open(files: InputFile[]): Promise<boolean> {
    return this.openFiles(files.map((f) => f.file));
  }

  /** The start-screen tile: asks for the size of a new drawing. */
  openEmpty(): Promise<boolean> {
    return this.askNew();
  }

  async askNew(): Promise<boolean> {
    if (this.saving || !this.discardOk()) return false;
    const size = await this.askSize();
    if (!size) return false;
    this.load(null, newDocument(...size));
    return true;
  }

  private discardOk(): boolean {
    return !this.dirty || window.confirm("Discard the unsaved changes to the drawing?");
  }

  private load(name: string | null, opened: Opened): void {
    this.opened = opened;
    this.history = new History(opened.doc);
    this.sourceName = this.saveName = name;
    this.handle = undefined;
    this.selection = [];
    this.node = null;
    this.pasteCount = 0;
    this.fitted = false;
    this.showDoc(true);
    this.showMessage(name ? `Opened “${name}”.` : "A new drawing.");
  }

  maybeLeave(): boolean {
    if (!this.discardOk()) return false;
    this.history.markSaved();
    return true;
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- showing
  private showDoc(fit = false): void {
    this.selection = this.selection.filter((p) => this.exists(this.doc, p));
    if (fit) this.fit();
    else this.draw();
    this.renderLayers();
    this.renderAppearance();
    this.title.textContent = (this.dirty ? "• " : "") + (this.sourceName ?? "Untitled drawing");
    this.undoBtn.disabled = !this.history.canUndo;
    this.redoBtn.disabled = !this.history.canRedo;
    this.saveBtn.disabled = this.saving;
  }

  private exists(doc: VectorDoc, p: Path): boolean {
    try {
      vdoc.get(doc, p);
      return p.length > 0;
    } catch {
      return false;
    }
  }

  viewBox(): Box {
    return vdoc.canvas(this.doc)[2];
  }

  fit(): void {
    const view = this.viewBox();
    const w = Math.max(this.stage.clientWidth, 50);
    const h = Math.max(this.stage.clientHeight, 50);
    const s = Math.min((w - 48) / Math.max(view.width, 1e-9), (h - 48) / Math.max(view.height, 1e-9));
    this.scale = Math.min(Math.max(s, MIN_ZOOM), MAX_ZOOM);
    this.ox = (w - view.width * this.scale) / 2 - view.x0 * this.scale;
    this.oy = (h - view.height * this.scale) / 2 - view.y0 * this.scale;
    this.fitted = this.stage.clientWidth > 50;
    this.draw();
  }

  setZoom(scale: number, ax?: number, ay?: number): void {
    const cx = ax ?? this.stage.clientWidth / 2;
    const cy = ay ?? this.stage.clientHeight / 2;
    const [x, y] = this.toDoc(cx, cy);
    this.scale = Math.min(Math.max(scale, MIN_ZOOM), MAX_ZOOM);
    this.ox = cx - x * this.scale;
    this.oy = cy - y * this.scale;
    this.draw();
  }

  zoomBy(f: number, ax?: number, ay?: number): void {
    this.setZoom(this.scale * f, ax, ay);
  }

  private toScreen(x: number, y: number): [number, number] {
    return [this.ox + x * this.scale, this.oy + y * this.scale];
  }

  private toDoc(sx: number, sy: number): [number, number] {
    return [(sx - this.ox) / this.scale, (sy - this.oy) / this.scale];
  }

  private snapped(v: number): number {
    return snapTo(v, stepFor(this.scale));
  }

  private local(e: PointerEvent | MouseEvent | WheelEvent): [number, number] {
    const r = this.stage.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  /** The drawing as an image (only when the document changed), then the overlay. */
  private draw(): void {
    const doc = this.shown();
    const view = vdoc.canvas(doc)[2];
    if (this.imageFor !== doc) {
      this.imageFor = doc;
      const old = this.imageUrl;
      this.imageUrl = URL.createObjectURL(new Blob([vdoc.write(doc)], { type: "image/svg+xml" }));
      this.image.src = this.imageUrl;
      if (old) setTimeout(() => URL.revokeObjectURL(old), 1000);
    }
    const [x0, y0] = this.toScreen(view.x0, view.y0);
    Object.assign(this.image.style, {
      left: `${x0}px`,
      top: `${y0}px`,
      width: `${view.width * this.scale}px`,
      height: `${view.height * this.scale}px`,
    });
    this.zoomLabel.textContent = `${Math.round(this.scale * 100)} %`;
    this.drawOverlay();
  }

  private sRect(b: Box): [number, number, number, number] {
    const [ax, ay] = this.toScreen(b.x0, b.y0);
    const [bx, by] = this.toScreen(b.x1, b.y1);
    return [Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay)];
  }

  selectionBox(): Box | null {
    return vdoc.selectionBounds(this.shown(), this.selection);
  }

  private handlePoints(b: Box): [string, number, number][] {
    const [x, y, w, h] = this.sRect(b);
    return HANDLES.map(([n, fx, fy]) => [n, x + fx * w, y + fy * h]);
  }

  private elementMatrix(p: Path): Matrix {
    const doc = this.shown();
    return vdoc.parentMatrix(doc, p).times(shapes.ownTransform(vdoc.get(doc, p)));
  }

  nodePoints(): [number, string, number, number][] {
    if (this.tool !== "node" || this.selection.length !== 1) return [];
    const doc = this.shown();
    const elx = vdoc.get(doc, this.selection[0]);
    if (shapes.kind(elx) !== "path") return [];
    const m = this.elementMatrix(this.selection[0]);
    return pathdata.points(pathdata.parse(elx.get("d") ?? "")).map(([i, slot, x, y]) => {
      const [dx, dy] = m.apply(x, y);
      return [i, slot, ...this.toScreen(dx, dy)];
    });
  }

  private drawOverlay(): void {
    const c = this.overlay;
    const ratio = window.devicePixelRatio || 1;
    const w = this.stage.clientWidth;
    const h = this.stage.clientHeight;
    if (c.width !== Math.round(w * ratio) || c.height !== Math.round(h * ratio)) {
      c.width = Math.round(w * ratio);
      c.height = Math.round(h * ratio);
    }
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, w, h);
    const css = getComputedStyle(this.el);
    const accent = css.getPropertyValue("--accent").trim() || "#4f6bed";
    const border = css.getPropertyValue("--border").trim() || "#ccc";
    const doc = this.shown();
    g.strokeStyle = border;
    g.lineWidth = 1;
    g.strokeRect(...this.sRect(vdoc.canvas(doc)[2]));
    g.strokeStyle = accent;
    g.setLineDash([4, 3]);
    for (const p of this.selection) {
      const b = vdoc.bounds(doc, p);
      if (b) g.strokeRect(...this.sRect(b));
    }
    g.setLineDash([]);
    const box = this.selectionBox();
    if (this.tool === "select" && box && !this.gesture) {
      g.fillStyle = "#fff";
      for (const [, x, y] of this.handlePoints(box)) {
        g.fillRect(x - HANDLE, y - HANDLE, 2 * HANDLE, 2 * HANDLE);
        g.strokeRect(x - HANDLE, y - HANDLE, 2 * HANDLE, 2 * HANDLE);
      }
    }
    if (this.band) {
      g.fillStyle = accent + "33";
      g.setLineDash([4, 3]);
      g.fillRect(...this.band);
      g.strokeRect(...this.band);
      g.setLineDash([]);
    }
    if (this.drawBox) {
      g.setLineDash([4, 3]);
      const [x, y, bw, bh] = this.sRect(this.drawBox);
      g.beginPath();
      if (this.tool === "ellipse") g.ellipse(x + bw / 2, y + bh / 2, bw / 2, bh / 2, 0, 0, 2 * Math.PI);
      else if (this.tool === "line" && this.gesture) {
        g.moveTo(...this.toScreen(this.gesture.dx0, this.gesture.dy0));
        g.lineTo(...this.toScreen(...this.drawEnd));
      } else g.rect(x, y, bw, bh);
      g.stroke();
      g.setLineDash([]);
    }
    if (this.tool === "node") {
      const pts = this.nodePoints();
      const ends = new Map(pts.filter((p) => p[1] === "end").map((p) => [p[0], p]));
      g.fillStyle = "#fff";
      for (const [i, slot, x, y] of pts) {
        if (slot === "end") continue;
        const owner = slot === "c1" ? ends.get(i - 1) : ends.get(i);
        if (owner) {
          g.beginPath();
          g.moveTo(owner[2], owner[3]);
          g.lineTo(x, y);
          g.stroke();
        }
        g.beginPath();
        g.arc(x, y, 3, 0, 2 * Math.PI);
        g.fill();
        g.stroke();
      }
      for (const [i, slot, x, y] of pts) {
        if (slot !== "end") continue;
        g.fillStyle = this.node && this.node[0] === i && this.node[1] === "end" ? accent : "#fff";
        g.fillRect(x - 3.5, y - 3.5, 7, 7);
        g.strokeRect(x - 3.5, y - 3.5, 7, 7);
      }
    }
    if (this.tool === "pen" && this.penNodes.length) {
      g.lineWidth = 1.5;
      g.beginPath();
      for (const s of this.penSegs(false, this.gesture ? null : this.penHover)) {
        const v = s.slice(1) as number[];
        if (s[0] === "M") g.moveTo(...this.toScreen(v[0], v[1]));
        else if (s[0] === "L") g.lineTo(...this.toScreen(v[0], v[1]));
        else if (s[0] === "C") g.bezierCurveTo(...this.toScreen(v[0], v[1]), ...this.toScreen(v[2], v[3]), ...this.toScreen(v[4], v[5]));
      }
      g.stroke();
      for (const [x, y] of this.penNodes) {
        const [sx, sy] = this.toScreen(x, y);
        g.strokeRect(sx - 3, sy - 3, 6, 6);
      }
    }
  }

  // ---------------------------------------------------------------- layers / appearance
  private renderLayers(): void {
    const items = vdoc.items(this.doc);
    const made = new Map<string, HTMLUListElement>();
    const root = el("ul", "vec-layers") as HTMLUListElement;
    for (const item of items) {
      const li = el("li");
      li.dataset.path = JSON.stringify(item.path);
      li.classList.toggle("selected", hasPath(this.selection, item.path));
      const row = el("div", "vec-layer");
      const eye = el("input");
      eye.type = "checkbox";
      eye.checked = !item.hidden;
      eye.setAttribute("aria-label", `Show ${item.name}`);
      row.append(eye, el("span", "vec-layer-name", item.name));
      row.title = `${item.kind} — tick to show, double-click to rename`;
      li.append(row);
      if (item.container) {
        const kids = el("ul") as HTMLUListElement;
        li.append(kids);
        made.set(JSON.stringify(item.path), kids);
      }
      const parent = made.get(JSON.stringify(item.path.slice(0, -1))) ?? root;
      parent.prepend(li); // topmost first
    }
    this.layersEl.replaceChildren(...root.childNodes);
  }

  private layerClick(e: MouseEvent): void {
    if (e.target instanceof HTMLInputElement) return;
    const row = (e.target as HTMLElement).closest<HTMLElement>("li[data-path]");
    if (!row) return;
    const p: Path = JSON.parse(row.dataset.path!);
    if (e.shiftKey || e.ctrlKey || e.metaKey) {
      this.setSelection(hasPath(this.selection, p) ? this.selection.filter((q) => !samePath(q, p)) : [...this.selection, p]);
    } else this.setSelection([p]);
  }

  private buildAppearance(): void {
    const a = this.appearance;
    a.append(el("h3", "", "Appearance"));
    const field = (key: string, label: string, type: string, attrs: Record<string, string> = {}) => {
      const row = el("label", "field");
      const input = el("input");
      input.type = type;
      input.setAttribute("aria-label", label);
      for (const [k, v] of Object.entries(attrs)) input.setAttribute(k, v);
      row.append(el("span", "", label), input);
      this.fields[key] = input;
      return row;
    };
    const colour = (key: string, label: string) => {
      const row = field(key, label, "color");
      const none = el("input");
      none.type = "checkbox";
      none.setAttribute("aria-label", `No ${label.toLowerCase()}`);
      const noneLabel = el("label", "check");
      noneLabel.append(none, " None");
      row.append(noneLabel);
      this.fields[`${key}_none`] = none;
      this.fields[key].addEventListener("change", () => this.setColour(key, this.fields[key].value));
      none.addEventListener("change", () => this.setColour(key, none.checked ? "none" : key === "stroke" ? "#000000" : "#808080"));
      return row;
    };
    a.append(colour("fill", "Fill"), colour("stroke", "Outline"));
    a.append(field("width", "Width", "number", { min: "0", step: "0.5" }));
    this.fields.width.addEventListener("change", () => this.setStrokeWidth(Number(this.fields.width.value)));
    a.append(field("opacity", "Opacity", "range", { min: "0", max: "100" }));
    this.fields.opacity.addEventListener("change", () => this.setOpacity(Number(this.fields.opacity.value) / 100));
    a.append(el("h3", "", "Position and size"));
    for (const [key, label] of [["x", "X"], ["y", "Y"], ["w", "Width"], ["h", "Height"]]) {
      a.append(field(key, label, "number", { step: "any" }));
      this.fields[key].addEventListener("change", () => {
        const f = this.fields;
        this.setGeometry(Number(f.x.value), Number(f.y.value), Number(f.w.value), Number(f.h.value));
      });
    }
    const text = el("input");
    text.type = "text";
    text.setAttribute("aria-label", "Text");
    text.addEventListener("change", () => this.setTextContent(text.value));
    this.fields.text = text;
    this.textRow.append(el("span", "", "Text"), text);
    a.append(this.textRow);
  }

  private renderAppearance(): void {
    const f = this.fields;
    const has = this.selection.length > 0;
    for (const input of Object.values(f)) input.disabled = !has;
    this.textRow.hidden = true;
    if (!has) return;
    const elx = vdoc.get(this.doc, this.selection[0]);
    for (const key of ["fill", "stroke"]) {
      const v = shapes.styleGet(elx, key);
      f[`${key}_none`].checked = key === "fill" ? v === "none" : v === null || v === "none";
      f[key].value = /^#[0-9a-f]{6}$/i.test(v ?? "") ? v! : "#808080";
    }
    f.width.value = String(shapes.length(shapes.styleGet(elx, "stroke-width")) || 1);
    const op = shapes.length(shapes.styleGet(elx, "opacity"));
    f.opacity.value = String(Math.round(100 * (op === null ? 1 : op)));
    const box = vdoc.selectionBounds(this.doc, this.selection);
    if (box) {
      f.x.value = num(box.x0);
      f.y.value = num(box.y0);
      f.w.value = num(box.width);
      f.h.value = num(box.height);
    }
    if (this.selection.length === 1 && shapes.kind(elx) === "text") {
      this.textRow.hidden = false;
      f.text.value = elx.text();
    }
  }

  // ---------------------------------------------------------------- selection / tools
  setSelection(paths: Path[]): void {
    const keep = paths.filter((p) => this.exists(this.shown(), p));
    const same = keep.length === this.selection.length && keep.every((p, i) => samePath(p, this.selection[i]));
    if (!same) {
      this.selection = keep;
      this.node = null;
      this.history.endMerge();
      this.renderLayers();
      this.renderAppearance();
    }
    this.drawOverlay();
  }

  setTool(tool: Tool): void {
    this.finishPen();
    this.tool = tool;
    this.node = null;
    for (const [t, b] of this.toolButtons) b.setAttribute("aria-pressed", String(t === tool));
    this.stage.dataset.tool = tool;
    const hints: Record<Tool, string> = {
      select: "Click to choose, drag to move, Shift+click adds; double-click text to type.",
      node: "Drag a path's points and handles. Double-click its line to add a point.",
      rect: "Drag to draw a rectangle (Shift: square).",
      ellipse: "Drag to draw an ellipse (Shift: circle).",
      line: "Drag to draw a line.",
      pen: "Click for corners, drag for curves; Enter ends, the first point closes.",
      text: "Click where the text should start.",
    };
    this.showMessage(hints[tool]);
    this.drawOverlay();
  }

  private hitAt(sx: number, sy: number): Path | null {
    const [x, y] = this.toDoc(sx, sy);
    return vdoc.hit(this.shown(), x, y, [], 2 / this.scale);
  }

  private hitHandle(sx: number, sy: number): string | null {
    const box = this.selectionBox();
    if (this.tool !== "select" || !box) return null;
    for (const [n, x, y] of this.handlePoints(box)) if (Math.abs(sx - x) <= GRAB && Math.abs(sy - y) <= GRAB) return n;
    return null;
  }

  private hitPoint(sx: number, sy: number): [number, string] | null {
    let best: [number, string] | null = null;
    let bestD = GRAB;
    for (const [i, slot, x, y] of this.nodePoints()) {
      const d = Math.hypot(x - sx, y - sy);
      if (d <= bestD) {
        best = [i, slot];
        bestD = d;
      }
    }
    return best;
  }

  // ---------------------------------------------------------------- pointer
  private wheel(e: WheelEvent): void {
    e.preventDefault();
    const [sx, sy] = this.local(e);
    if (e.ctrlKey || e.metaKey) {
      this.zoomBy(1.15 ** (-e.deltaY / 100), sx, sy);
      return;
    }
    const [dx, dy] = e.shiftKey ? [e.deltaY, 0] : [e.deltaX, e.deltaY];
    this.ox -= dx / 2;
    this.oy -= dy / 2;
    this.draw();
  }

  private pointerDown(e: PointerEvent): void {
    this.stage.focus();
    const [sx, sy] = this.local(e);
    const [x, y] = this.toDoc(sx, sy);
    const g: Gesture = { kind: "", sx, sy, dx0: x, dy0: y, base: this.doc, moved: false };
    if (e.button === 1) {
      g.kind = "pan";
    } else if (e.button !== 0) return;
    else if (this.tool === "select") {
      const handle = this.hitHandle(sx, sy);
      if (handle) {
        g.kind = "resize";
        g.handle = handle;
        g.box = this.selectionBox();
      } else {
        const hit = this.hitAt(sx, sy);
        if (hit === null) {
          if (!e.shiftKey) this.setSelection([]);
          g.kind = "band";
        } else {
          if (e.shiftKey) {
            this.setSelection(hasPath(this.selection, hit) ? this.selection.filter((q) => !samePath(q, hit)) : [...this.selection, hit]);
          } else if (!hasPath(this.selection, hit)) this.setSelection([hit]);
          g.kind = this.selection.length ? "move" : "";
        }
      }
    } else if (this.tool === "node") {
      const point = this.hitPoint(sx, sy);
      if (point) {
        this.node = point;
        g.kind = "point";
        g.point = point;
      } else {
        const hit = this.hitAt(sx, sy);
        this.setSelection(hit ? [hit] : []);
        this.node = null;
      }
    } else if (this.tool === "rect" || this.tool === "ellipse" || this.tool === "line") {
      g.kind = "draw";
      g.dx0 = this.snapped(x);
      g.dy0 = this.snapped(y);
      this.drawEnd = [g.dx0, g.dy0];
    } else if (this.tool === "pen") {
      if (this.closesPen(sx, sy)) {
        this.finishPen(true);
        return;
      }
      g.kind = "pen";
      this.penNodes.push([this.snapped(x), this.snapped(y), 0, 0]);
    } else if (this.tool === "text") {
      void this.addText(this.snapped(x), this.snapped(y));
      return;
    }
    this.gesture = g;
    this.stage.setPointerCapture(e.pointerId);
    this.drawOverlay();
  }

  private pointerMove(e: PointerEvent): void {
    const [sx, sy] = this.local(e);
    if (this.tool === "pen") this.penHover = this.toDoc(sx, sy);
    const g = this.gesture;
    if (!g) {
      this.hoverCursor(sx, sy);
      if (this.tool === "pen") this.drawOverlay();
      return;
    }
    if (!g.moved && Math.hypot(sx - g.sx, sy - g.sy) < DRAG_START) return;
    g.moved = true;
    const [rx, ry] = this.toDoc(sx, sy);
    const x = this.snapped(rx);
    const y = this.snapped(ry);
    let dx = this.snapped(x - this.snapped(g.dx0));
    let dy = this.snapped(y - this.snapped(g.dy0));
    try {
      if (g.kind === "pan") {
        this.ox += sx - g.sx;
        this.oy += sy - g.sy;
        g.sx = sx;
        g.sy = sy;
      } else if (g.kind === "move") {
        if (e.shiftKey) [dx, dy] = Math.abs(dx) >= Math.abs(dy) ? [dx, 0] : [0, dy];
        this.preview = vdoc.move(g.base, this.selection, dx, dy);
      } else if (g.kind === "resize" && g.box) {
        const box = resized(g.box, g.handle!, x, y, e.shiftKey);
        if (box) this.preview = vdoc.resize(g.base, this.selection, box);
      } else if (g.kind === "band") {
        this.band = [Math.min(g.sx, sx), Math.min(g.sy, sy), Math.abs(sx - g.sx), Math.abs(sy - g.sy)];
      } else if (g.kind === "draw") {
        this.drawEnd = [x, y];
        this.drawBox = this.drawnBox([g.dx0, g.dy0], [x, y], e.shiftKey);
      } else if (g.kind === "point" && g.point) {
        this.preview = this.movedPoint(g, x, y);
      } else if (g.kind === "pen" && this.penNodes.length) {
        const last = this.penNodes[this.penNodes.length - 1];
        this.penNodes[this.penNodes.length - 1] = [last[0], last[1], x - last[0], y - last[1]];
      }
    } catch (err) {
      if (!(err instanceof VectorError)) throw err;
    }
    this.draw();
  }

  private pointerUp(e: PointerEvent): void {
    const g = this.gesture;
    this.gesture = null;
    if (!g) return;
    const preview = this.preview;
    this.preview = null;
    if (g.kind === "band" && this.band) {
      const [bx, by, bw, bh] = this.band;
      const [ax, ay] = this.toDoc(bx, by);
      const [cx, cy] = this.toDoc(bx + bw, by + bh);
      const found = vdoc.inBox(this.doc, new Box(ax, ay, cx, cy));
      const keep = e.shiftKey ? this.selection.filter((p) => !hasPath(found, p)) : [];
      this.setSelection([...keep, ...found]);
    } else if (g.kind === "draw" && this.drawBox) this.finishDraw(g);
    else if (g.moved && preview) {
      const label = g.kind === "move" ? "Move" : g.kind === "resize" ? "Resize" : g.kind === "point" ? "Move point" : "Edit";
      this.push(preview, label);
    }
    this.band = null;
    this.drawBox = null;
    this.draw();
  }

  private doubleClick(e: MouseEvent): void {
    const [sx, sy] = this.local(e);
    if (this.tool === "pen") {
      this.finishPen();
      return;
    }
    const hit = this.hitAt(sx, sy);
    if (this.tool === "select" && hit) {
      const kind = shapes.kind(vdoc.get(this.doc, hit));
      if (kind === "text") void this.editText(hit);
      else if (kind === "path" || kind in shapes.GEOMETRY) {
        this.setSelection([hit]);
        this.setTool("node");
      }
      return;
    }
    if (this.tool !== "node") return;
    if (hit && !hasPath(this.selection, hit)) this.setSelection([hit]);
    if (this.selection.length !== 1) return;
    const p = this.selection[0];
    const elx = vdoc.get(this.doc, p);
    if (shapes.kind(elx) in shapes.GEOMETRY) {
      this.push(vdoc.toPath(this.doc, [p]), "Turn into path");
      return;
    }
    if (shapes.kind(elx) !== "path") return;
    const found = this.nearestSegment(p, sx, sy);
    if (found) this.push(vdoc.setPath(this.doc, p, pathdata.split(pathdata.parse(elx.get("d") ?? ""), ...found)), "Add point");
  }

  private nearestSegment(p: Path, sx: number, sy: number): [number, number] | null {
    const segs = pathdata.parse(vdoc.get(this.doc, p).get("d") ?? "");
    const m = this.elementMatrix(p);
    let best: [number, number] | null = null;
    let bestD = GRAB * 2;
    let x = 0;
    let y = 0;
    let startX = 0;
    let startY = 0;
    segs.forEach((s, i) => {
      if (s[0] !== "M" && s[0] !== "Z") {
        for (let k = 1; k < 32; k++) {
          const t = k / 32;
          const [px, py] = pointOn(s, x, y, t);
          const [qx, qy] = this.toScreen(...m.apply(px, py));
          const d = Math.hypot(qx - sx, qy - sy);
          if (d < bestD) {
            best = [i, t];
            bestD = d;
          }
        }
      }
      if (s[0] === "Z") [x, y] = [startX, startY];
      else if (s[0] === "M") [x, y, startX, startY] = [s[1] as number, s[2] as number, s[1] as number, s[2] as number];
      else [x, y] = [s[s.length - 2] as number, s[s.length - 1] as number];
    });
    return best;
  }

  private hoverCursor(sx: number, sy: number): void {
    if (this.tool !== "select") return;
    const h = this.hitHandle(sx, sy);
    const cursors: Record<string, string> = { nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize" };
    this.stage.style.cursor = h ? cursors[h] : this.hitAt(sx, sy) ? "move" : "";
  }

  private drawnBox(a: [number, number], b: [number, number], square: boolean): Box {
    let [x1, y1] = b;
    const [x0, y0] = a;
    if (square) {
      const side = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0));
      x1 = x0 + side * Math.sign(x1 - x0 || 1);
      y1 = y0 + side * Math.sign(y1 - y0 || 1);
    }
    if (this.tool === "line") return new Box(x0, y0, x1, y1);
    return new Box(Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1));
  }

  private finishDraw(g: Gesture): void {
    const box = this.drawBox!;
    const big = Math.max(Math.abs(box.x1 - box.x0), Math.abs(box.y1 - box.y0)) * this.scale;
    if (!g.moved || big < 2) return;
    let style = [...this.styleForNew];
    if (this.tool === "line") {
      style = style.filter(([k]) => k !== "fill");
      if (!style.some(([k]) => k === "stroke")) style.push(["stroke", "#000000"]);
    }
    const kind = this.tool as "rect" | "ellipse" | "line";
    const [doc, p] = vdoc.add(this.doc, vdoc.newShape(kind, box, style, vdoc.ids(this.doc)));
    this.push(doc, { rect: "Rectangle", ellipse: "Ellipse", line: "Line" }[kind], null, [p]);
  }

  private movedPoint(g: Gesture, x: number, y: number): VectorDoc {
    const p = this.selection[0];
    const elx = vdoc.get(g.base, p);
    const local = vdoc.parentMatrix(g.base, p).times(shapes.ownTransform(elx)).inverse();
    const [lx, ly] = local.apply(x, y);
    const segs = pathdata.parse(elx.get("d") ?? "");
    return vdoc.setPath(g.base, p, pathdata.movePoint(segs, g.point![0], g.point![1], lx, ly));
  }

  private penSegs(close: boolean, hover: [number, number] | null): pathdata.Seg[] {
    const nodes = [...this.penNodes];
    if (hover) nodes.push([hover[0], hover[1], 0, 0]);
    if (!nodes.length) return [];
    const segs: pathdata.Seg[] = [["M", nodes[0][0], nodes[0][1]]];
    const pairs: [number[], number[]][] = [];
    for (let i = 0; i + 1 < nodes.length; i++) pairs.push([nodes[i], nodes[i + 1]]);
    if (close && nodes.length > 2) pairs.push([nodes[nodes.length - 1], nodes[0]]);
    for (const [[ax, ay, ahx, ahy], [bx, by, bhx, bhy]] of pairs) {
      if (ahx === 0 && ahy === 0 && bhx === 0 && bhy === 0) segs.push(["L", bx, by]);
      else segs.push(["C", ax + ahx, ay + ahy, bx - bhx, by - bhy, bx, by]);
    }
    if (close) segs.push(["Z"]);
    return segs;
  }

  private closesPen(sx: number, sy: number): boolean {
    if (this.penNodes.length < 3) return false;
    const [fx, fy] = this.toScreen(this.penNodes[0][0], this.penNodes[0][1]);
    return Math.hypot(fx - sx, fy - sy) <= GRAB;
  }

  finishPen(close = false): void {
    const nodes = this.penNodes;
    this.penHover = null;
    if (nodes.length < 2) {
      this.penNodes = [];
      this.drawOverlay();
      return;
    }
    const segs = this.penSegs(close, null);
    this.penNodes = [];
    let style: [string, string][] = close ? [...this.styleForNew] : [["fill", "none"], ...this.styleForNew.filter(([k]) => k !== "fill")];
    if (!close && !style.some(([k]) => k === "stroke")) style = [...style, ["stroke", "#000000"]];
    const attrs: xmlsrc.Attr[] = [["id", vdoc.uniqueId(vdoc.ids(this.doc), "path")], ["d", pathdata.write(segs)], ["style", shapes.writeStyle(style)]];
    const [doc, p] = vdoc.add(this.doc, xmlsrc.element("path", attrs));
    this.push(doc, "Pen", null, [p]);
  }

  cancelGesture(): boolean {
    const busy = this.gesture !== null || this.penNodes.length > 0;
    this.gesture = null;
    this.preview = null;
    this.penNodes = [];
    this.band = null;
    this.drawBox = null;
    this.draw();
    return busy;
  }

  // ---------------------------------------------------------------- editing
  private push(doc: VectorDoc, label: string, merge: string | null = null, select?: Path[]): boolean {
    if (doc === this.doc) return false;
    this.history.push(doc, label, merge);
    if (select) this.selection = select;
    this.showDoc();
    this.showMessage(label);
    return true;
  }

  private edit(fn: (d: VectorDoc) => VectorDoc, label: string, merge: string | null = null): boolean {
    try {
      return this.push(fn(this.doc), label, merge);
    } catch (e) {
      if (!(e instanceof VectorError)) throw e;
      this.showMessage(e.message, true);
      return false;
    }
  }

  private pushResult(fn: () => [VectorDoc, Path[] | Path], label: string): boolean {
    try {
      const [doc, paths] = fn();
      const sel = (paths.length && Array.isArray(paths[0]) ? paths : [paths]) as Path[];
      return this.push(doc, label, null, sel);
    } catch (e) {
      if (!(e instanceof VectorError)) throw e;
      this.showMessage(e.message, true);
      return false;
    }
  }

  selectAll(): void {
    this.setSelection(vdoc.inBox(this.doc, new Box(-1e12, -1e12, 1e12, 1e12)));
  }

  escape(): void {
    if (!this.cancelGesture()) this.setSelection([]);
  }

  delete(): boolean {
    if (this.tool === "node" && this.node && this.selection.length === 1) {
      const p = this.selection[0];
      const segs = pathdata.parse(vdoc.get(this.doc, p).get("d") ?? "");
      const left = pathdata.deletePoint(segs, this.node[0]);
      this.node = null;
      if (!left.length) {
        this.selection = [];
        return this.edit((d) => vdoc.remove(d, [p]), "Delete");
      }
      return this.edit((d) => vdoc.setPath(d, p, left), "Delete point");
    }
    if (!this.selection.length) return false;
    const paths = this.selection;
    this.selection = [];
    return this.edit((d) => vdoc.remove(d, paths), "Delete");
  }

  duplicate(): boolean {
    return this.selection.length > 0 && this.pushResult(() => vdoc.duplicate(this.doc, this.selection), "Duplicate");
  }

  group(): boolean {
    if (this.selection.length < 2) {
      this.showMessage("Choose two or more things to group.", true);
      return false;
    }
    return this.pushResult(() => vdoc.group(this.doc, this.selection), "Group");
  }

  ungroup(): boolean {
    return this.selection.length === 1 && this.pushResult(() => vdoc.ungroup(this.doc, this.selection[0]), "Ungroup");
  }

  reorder(where: "forward" | "backward" | "front" | "back"): boolean {
    if (this.selection.length !== 1) return false;
    const labels = { forward: "Bring forward", backward: "Send backward", front: "Bring to front", back: "Send to back" };
    return this.pushResult(() => vdoc.reorder(this.doc, this.selection[0], where), labels[where]);
  }

  toPath(): boolean {
    return this.selection.length > 0 && this.edit((d) => vdoc.toPath(d, this.selection), "Turn into path");
  }

  nudge(dx: number, dy: number, big = false): boolean {
    if (!this.selection.length) return false;
    const step = big ? NUDGE_BIG : NUDGE;
    return this.edit((d) => vdoc.move(d, this.selection, dx * step, dy * step), "Move", "nudge");
  }

  setColour(which: string, value: string): boolean {
    this.styleForNew = [...this.styleForNew.filter(([k]) => k !== which), [which, value]];
    if (!this.selection.length) return false;
    return this.edit((d) => vdoc.setStyle(d, this.selection, which, value), which === "fill" ? "Fill" : "Outline");
  }

  setStrokeWidth(width: number): boolean {
    return this.selection.length > 0 && this.edit((d) => vdoc.setStyle(d, this.selection, "stroke-width", num(Math.max(0, width))), "Outline width");
  }

  setOpacity(value: number): boolean {
    const v = value >= 1 ? null : num(Math.max(0, value));
    return this.selection.length > 0 && this.edit((d) => vdoc.setStyle(d, this.selection, "opacity", v), "Opacity");
  }

  setGeometry(x: number, y: number, w: number, h: number): boolean {
    const box = this.selection.length ? vdoc.selectionBounds(this.doc, this.selection) : null;
    if (!box || [x, y, w, h].some((v) => !Number.isFinite(v))) return false;
    return this.edit((d) => {
      if ((w !== box.width || h !== box.height) && w > 0 && h > 0 && box.width > 0 && box.height > 0) {
        return vdoc.resize(d, this.selection, new Box(x, y, x + w, y + h));
      }
      return vdoc.move(d, this.selection, x - box.x0, y - box.y0);
    }, "Position and size");
  }

  async addText(x: number, y: number, text?: string): Promise<boolean> {
    const value = text ?? (await this.askText("Type the text:", ""));
    if (!value) return false;
    const fill = this.styleForNew.find(([k]) => k === "fill")?.[1] ?? "#000000";
    const attrs: xmlsrc.Attr[] = [
      ["id", vdoc.uniqueId(vdoc.ids(this.doc), "text")],
      ["x", num(x)],
      ["y", num(y)],
      ["style", shapes.writeStyle([["font-size", "24px"], ["font-family", "sans-serif"], ["fill", fill]])],
    ];
    const [doc, p] = vdoc.add(this.doc, xmlsrc.element("text", attrs, [new xmlsrc.Text(xmlsrc.escapeText(value))]));
    this.setTool("select");
    return this.push(doc, "Text", null, [p]);
  }

  async editText(p: Path): Promise<boolean> {
    const value = await this.askText("Change the text:", vdoc.get(this.doc, p).text());
    return value !== null && this.setTextContent(value, p);
  }

  setTextContent(text: string, p?: Path): boolean {
    const path = p ?? (this.selection.length === 1 ? this.selection[0] : null);
    return path !== null && this.edit((d) => vdoc.setText(d, path, text), "Text", `text:${path.join(",")}`);
  }

  async askRename(p: Path): Promise<boolean> {
    const name = await this.askText("Name (the element's id):", vdoc.get(this.doc, p).get("id") ?? "");
    return name !== null && this.edit((d) => vdoc.rename(d, p, name), "Rename");
  }

  /** Replaceable in tests. */
  askText = async (label: string, value: string): Promise<string | null> => window.prompt(label, value);

  undo(): void {
    const label = this.history.undo();
    if (label) {
      this.showDoc();
      this.showMessage(`Undid: ${label}`);
    }
  }

  redo(): void {
    const label = this.history.redo();
    if (label) {
      this.showDoc();
      this.showMessage(`Redid: ${label}`);
    }
  }

  // ---------------------------------------------------------------- clipboard
  copyText(): string {
    return this.selection.length ? vdoc.copyText(this.doc, this.selection) : "";
  }

  private remember(text: string): void {
    this.lastCopy = text;
    this.pasteCount = 0;
  }

  private clipboardEvent(e: ClipboardEvent, cut: boolean): void {
    const text = this.copyText();
    if (!text) return;
    e.preventDefault();
    e.clipboardData?.setData("text/plain", text);
    this.remember(text);
    this.showMessage(cut ? "Cut." : "Copied.");
    if (cut) this.delete();
  }

  private async copyToClipboard(): Promise<boolean> {
    const text = this.copyText();
    if (!text) return false;
    this.remember(text);
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      /* kept for Paste in this page anyway */
    }
    this.showMessage("Copied.");
    return true;
  }

  private async pasteFromClipboard(): Promise<void> {
    let text = this.lastCopy;
    try {
      text = (await navigator.clipboard.readText()) || text;
    } catch {
      /* the browser said no: paste what was copied here */
    }
    this.paste(text);
  }

  paste(text: string): boolean {
    const head = text.trimStart().slice(0, 200);
    if (!(head.startsWith("<svg") || head.startsWith("<?xml") || head.startsWith("<!--"))) {
      this.showMessage("The clipboard holds no drawing.", true);
      return false;
    }
    if (text === this.lastCopy) this.pasteCount++;
    const offset = PASTE_STEP * this.pasteCount;
    return this.pushResult(() => vdoc.pasteText(this.doc, text, offset, offset), "Paste");
  }

  // ---------------------------------------------------------------- saving
  async save(): Promise<boolean> {
    if (this.saving) return false;
    if (!this.saveName && !this.handle) return this.saveAs();
    return this.write(this.handle?.name ?? this.saveName!);
  }

  async saveAs(): Promise<boolean> {
    if (this.saving) return false;
    const ext = this.opened.gz ? "svgz" : "svg";
    const choice = await this.askSave(`${this.sourceName ? baseOf(this.sourceName) : "Drawing"}.${ext}`, ["svg", "svgz"]);
    if (!choice) return false;
    let name = choice.name;
    if (!["svg", "svgz"].includes(extOf(name))) name = `${name}.${choice.format}`;
    if (choice.toFile) {
      try {
        this.handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({ suggestedName: name.replace(/[\\/:*?"<>|]/g, "_") });
      } catch (err) {
        if ((err as { name?: string } | null)?.name !== "AbortError") {
          console.error("The save window couldn't be opened", err);
          this.showMessage("The browser couldn't open its save window. Try again, or use Save as without “Save to a file…”.", true);
        }
        return false;
      }
      name = this.handle.name ?? name;
    } else this.handle = undefined;
    return this.write(name);
  }

  private async write(name: string): Promise<boolean> {
    const doc = this.doc;
    const gz = extOf(name) === "svgz";
    const opened: Opened = gz !== this.opened.gz ? { ...this.opened, gz, new: true } : this.opened;
    let data: Uint8Array;
    try {
      data = toBytes(opened, doc);
    } catch (e) {
      if (!(e instanceof Error) || e.message !== "encoding") throw e;
      if (!window.confirm(`Some characters can't be stored in this file's encoding (${opened.file.encoding}). Save it as UTF-8 instead?`)) return false;
      data = toBytes(opened, doc, true);
    }
    this.saving = true;
    this.showDoc();
    try {
      const blob = new Blob([data as BlobPart], { type: gz ? "application/gzip" : "image/svg+xml" });
      if (this.handle) {
        const w = await this.handle.createWritable();
        await w.write(blob);
        await w.close();
      } else this.download(blob, name);
      const reread = openBytes(data);
      this.opened = { ...reread, doc };
      this.sourceName = this.saveName = name;
      this.history.markSaved(doc);
      this.showMessage(`Saved “${name}”.`);
      return true;
    } catch (err) {
      console.error(`Couldn't save ${name}`, err);
      this.showMessage(`Couldn't save “${name}”.`, true);
      return false;
    } finally {
      this.saving = false;
      this.showDoc();
    }
  }

  private download(blob: Blob, name: string): void {
    const url = URL.createObjectURL(blob);
    const a = el("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }

  /** PNG / JPG / WebP / TIFF through the vector converter in the worker. */
  async exportImage(): Promise<boolean> {
    const choice = await this.askSave(`${this.sourceName ? baseOf(this.sourceName) : "Drawing"}.png`, Object.keys(EXPORTS), "Export");
    if (!choice) return false;
    let fmt = extOf(choice.name);
    fmt = fmt === "jpeg" ? "jpg" : fmt === "tif" ? "tiff" : fmt;
    let name = choice.name;
    if (!(fmt in EXPORTS)) {
      fmt = choice.format;
      name = `${baseOf(name)}.${fmt}`;
    }
    const conv = converterById("vector");
    const file = new File([vdoc.write(this.doc)], `${baseOf(name)}.svg`, { type: "image/svg+xml" });
    const input: InputFile = { file, name: file.name, ext: "svg", family: "vector" };
    const target = conv.targets([input]).find((t) => t.id === `vec:${fmt}`);
    if (!target) {
      this.showMessage(`Drawings can't be exported as ${EXPORTS[fmt] ?? fmt} here.`, true);
      return false;
    }
    this.showMessage(`Exporting ${name}…`);
    try {
      const [out] = await this.opts.client.run(target, [input], defaults(conv.options), (_p, m) => m && this.showMessage(m));
      this.download(new Blob([out.data as BlobPart], { type: out.type }), name);
      this.showMessage(`Exported “${name}”.`);
      return true;
    } catch (e) {
      console.error("Export failed", e);
      this.showMessage(`Couldn't export “${name}”${e instanceof Error ? `: ${e.message}` : "."}`, true);
      return false;
    }
  }

  // ---------------------------------------------------------------- dialogs
  private dialog(cls: string, title: string): [HTMLDialogElement, HTMLElement] {
    const d = el("dialog", `editor-dialog ${cls}`);
    d.setAttribute("aria-label", title);
    const buttons = el("div", "row");
    d.append(el("h2", "", title), buttons);
    return [d, buttons];
  }

  private buildSaveDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("vec-save-dialog", "Save as");
    const name = el("input");
    name.type = "text";
    name.setAttribute("aria-label", "File name");
    const format = el("select");
    format.setAttribute("aria-label", "Format");
    format.addEventListener("change", () => (name.value = `${baseOf(name.value)}.${format.value}`));
    const row = el("label", "field");
    row.append(el("span", "", "File name"), name);
    const row2 = el("label", "field");
    row2.append(el("span", "", "Format"), format);
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    const toFile = button("Save to a file…", "btn", "file");
    toFile.title = "Choose where to save; Save (Ctrl+S) then writes to the same file";
    buttons.append(el("div", "spacer"), button("Cancel", "btn", ""), toFile, button("Save", "primary small", "save"));
    dialog.insertBefore(row, buttons);
    dialog.insertBefore(row2, buttons);
    return dialog;
  }

  /** Asks the file name and format (and whether to pick a file); replaceable in tests. */
  askSave = (suggested: string, fmts: string[], title = "Save as"): Promise<{ name: string; format: string; toFile: boolean } | null> => {
    const dialog = this.saveDialog;
    (dialog.querySelector("h2") as HTMLElement).textContent = title;
    const name = dialog.querySelector("input") as HTMLInputElement;
    const format = dialog.querySelector("select") as HTMLSelectElement;
    const toFile = dialog.querySelector('[data-value="file"]') as HTMLButtonElement;
    toFile.hidden = title !== "Save as" || !("showSaveFilePicker" in window);
    const label = (f: string) => EXPORTS[f] ?? (f === "svgz" ? "Compressed SVG" : "SVG");
    format.replaceChildren(...fmts.map((f) => new Option(`${label(f)} (.${f})`, f)));
    name.value = suggested;
    return new Promise((resolve) => {
      const finish = (value: string) => {
        dialog.removeEventListener("click", clicked);
        dialog.removeEventListener("cancel", cancelled);
        dialog.removeEventListener("keydown", keyed);
        dialog.close();
        resolve(value ? { name: name.value.trim() || suggested, format: format.value, toFile: value === "file" } : null);
      };
      const clicked = (e: Event) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-value]");
        if (b) finish(b.dataset.value ?? "");
      };
      const cancelled = (e: Event) => {
        e.preventDefault();
        finish("");
      };
      const keyed = (e: KeyboardEvent) => {
        if (e.key === "Enter" && e.target === name) {
          e.preventDefault();
          finish("save");
        }
      };
      dialog.addEventListener("click", clicked);
      dialog.addEventListener("cancel", cancelled);
      dialog.addEventListener("keydown", keyed);
      dialog.showModal();
      name.focus();
    });
  };

  private buildSizeDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("vec-new-dialog", "New drawing");
    const preset = el("select");
    preset.id = "vec-size";
    preset.setAttribute("aria-label", "Size");
    for (const [label] of NEW_SIZES) preset.append(new Option(label, label));
    preset.append(new Option("Custom size", "custom"));
    const num_ = (id: string, label: string) => {
      const i = el("input");
      i.type = "number";
      i.id = id;
      i.min = "0.01";
      i.step = "any";
      i.setAttribute("aria-label", label);
      return i;
    };
    const w = num_("vec-width", "Width");
    const h = num_("vec-height", "Height");
    const unit = el("select");
    unit.id = "vec-unit";
    unit.setAttribute("aria-label", "Unit");
    for (const u of ["px", "mm", "in"]) unit.append(new Option(u, u));
    const apply = () => {
      const p = NEW_SIZES.find(([l]) => l === preset.value);
      if (p) {
        w.value = String(p[1]);
        h.value = String(p[2]);
        unit.value = p[3];
      }
    };
    preset.addEventListener("change", apply);
    apply();
    for (const [label, input] of [["Size", preset], ["Width", w], ["Height", h], ["Unit", unit]] as [string, HTMLElement][]) {
      const row = el("label", "field");
      row.append(el("span", "", label), input);
      dialog.insertBefore(row, buttons);
    }
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    buttons.append(el("div", "spacer"), button("Cancel", "btn", ""), button("Create", "primary small", "ok"));
    return dialog;
  }

  /** The new drawing's size; replaceable in tests. */
  askSize = (): Promise<[number, number, string] | null> => {
    const d = this.sizeDialog;
    const get = (id: string) => d.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`)!;
    return new Promise((resolve) => {
      const finish = (ok: boolean) => {
        d.removeEventListener("click", clicked);
        d.removeEventListener("cancel", cancelled);
        d.close();
        const w = Number(get("vec-width").value);
        const h = Number(get("vec-height").value);
        resolve(ok && w > 0 && h > 0 ? [w, h, get("vec-unit").value] : null);
      };
      const clicked = (e: Event) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-value]");
        if (b) finish(b.dataset.value === "ok");
      };
      const cancelled = (e: Event) => {
        e.preventDefault();
        finish(false);
      };
      d.addEventListener("click", clicked);
      d.addEventListener("cancel", cancelled);
      d.showModal();
    });
  };

  private buildHelp(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("code-help", "Keyboard shortcuts");
    const rows = el("div", "code-help-rows");
    let group = "";
    for (const s of SHORTCUTS) {
      if (s.group !== group) {
        group = s.group;
        rows.append(el("h3", "", group));
      }
      const row = el("div", "code-help-row");
      row.append(el("kbd", "", s.keys.map(showKey).join(" / ")), el("span", "", s.label));
      rows.append(row);
    }
    const close = el("button", "primary small", "Close");
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    buttons.append(el("div", "spacer"), close);
    dialog.insertBefore(rows, buttons);
    return dialog;
  }
}

/** A resize drag from handle `handle` to (x, y): the new box (the desktop's _resized). */
export function resized(box: Box, handle: string, x: number, y: number, keepRatio: boolean): Box | null {
  let { x0, y0, x1, y1 } = box;
  if (handle.includes("w")) x0 = x;
  if (handle.includes("e")) x1 = x;
  if (handle.includes("n")) y0 = y;
  if (handle.includes("s")) y1 = y;
  if (keepRatio && box.width > 0 && box.height > 0 && handle.length === 2) {
    const s = Math.max(Math.abs(x1 - x0) / box.width, Math.abs(y1 - y0) / box.height);
    const w = box.width * s;
    const h = box.height * s;
    [x0, x1] = handle.includes("w") ? [x1 - w, x1] : [x0, x0 + w];
    [y0, y1] = handle.includes("n") ? [y1 - h, y1] : [y0, y0 + h];
  }
  let out = new Box(Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1));
  if (box.width === 0) out = new Box(box.x0, out.y0, box.x1, out.y1);
  if (box.height === 0) out = new Box(out.x0, box.y0, out.x1, box.y1);
  if ((out.width <= 0 && box.width > 0) || (out.height <= 0 && box.height > 0)) return null;
  return out;
}

function pointOn(s: pathdata.Seg, x0: number, y0: number, t: number): [number, number] {
  const v = s.slice(1) as number[];
  const u = 1 - t;
  if (s[0] === "C") {
    return [u ** 3 * x0 + 3 * u * u * t * v[0] + 3 * u * t * t * v[2] + t ** 3 * v[4], u ** 3 * y0 + 3 * u * u * t * v[1] + 3 * u * t * t * v[3] + t ** 3 * v[5]];
  }
  if (s[0] === "Q") return [u * u * x0 + 2 * u * t * v[0] + t * t * v[2], u * u * y0 + 2 * u * t * v[1] + t * t * v[3]];
  return [x0 + (v[v.length - 2] - x0) * t, y0 + (v[v.length - 1] - y0) * t];
}

export function mountVectorEditor(host: HTMLElement, opts: VectorEditorOptions): VectorEditorPage {
  const page = new VectorEditorPage(opts);
  host.append(page.el);
  return page;
}
