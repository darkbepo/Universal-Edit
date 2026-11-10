// The website's image editor page: top bar, tool bar, canvas, settings / colours / layers, status.
// Loaded on demand from ui/app.ts. The picture lives in the page; the conversion worker is only
// used to decode formats the browser can't and to encode saves other than .ora.
import { defaults } from "../../core/options";
import { converterById } from "../../core/registry";
import type { InputFile } from "../../core/types";
import { checkSize, composite, EditorError, selectedIndices, type EditorDoc } from "../../editor/document";
import { History, type Command } from "../../editor/history";
import type { RGBA } from "../../editor/layer";
import { loadLayers, openFiles, type OpenSource } from "../../editor/open";
import * as ops from "../../editor/ops";
import { writeOra } from "../../editor/ora";
import { canvasPng, canvasRasterizer, decodeBlob, type Decoded } from "../../editor/raster";
import type { Tool, ToolContext } from "../../editor/tools/base";
import { makeTools } from "../../editor/tools/index";
import { clearSelection, deselect, selectAll } from "../../editor/tools/select";
import { addTextLayer, anchorOf, editTextLayer, makeSpec, textLayerAt, type TextSpec } from "../../editor/tools/text";
import type { ConversionClient } from "../../worker/client";
import * as effects from "../../editor/effects";
import type { Effect } from "../../editor/effects";
import * as clipboard from "../../editor/clipboard";
import { EditorCanvas } from "./canvas";
import { backgroundBlurDialog, blurDialog, canvasSizeDialog, featherDialog, curvesDialog, levelsDialog, looksDialog, quickFixDialog, resizeDialog, saveDialog, sharpenDialog, shortcutsDialog, type AdjustHost } from "./dialogs";
import { CropBar, type BarMode } from "./cropbar";
import { h, LayersPanel, Swatches, ToolBar, ToolSettings } from "./panels";
import { SHORTCUTS, shortcutFor, toolGroups } from "./shortcuts";
import * as selection from "../../editor/selection";

/** Same size and (nearly) the same pixels — the clipboard may round semi-transparent colours. */
function samePicture(a: { data: Uint8ClampedArray; width: number; height: number }, b: { data: Uint8ClampedArray; width: number; height: number }): boolean {
  if (a.width !== b.width || a.height !== b.height) return false;
  for (let i = 0; i < a.data.length; i += 4) {
    if (Math.abs(a.data[i + 3] - b.data[i + 3]) > 3) return false;
    if (a.data[i + 3] === 0) continue;
    for (let c = 0; c < 3; c++) if (Math.abs(a.data[i + c] - b.data[i + c]) > 3 + 255 / Math.max(1, a.data[i + 3])) return false;
  }
  return true;
}

// Adjust menu: runOp name → (dialog, undo label). Same names as the desktop app.
const ADJUST_DIALOGS: Record<string, [(host: AdjustHost) => Promise<Effect | null>, string]> = {
  quick_fix: [quickFixDialog, "Quick fix"],
  levels: [levelsDialog, "Levels"],
  curves: [curvesDialog, "Curves"],
  blur: [blurDialog, "Blur"],
  sharpen: [sharpenDialog, "Sharpen"],
  looks: [looksDialog, "Look"],
  background_blur: [backgroundBlurDialog, "Background blur"],
};

const CROP_HINT = "Drag a rectangle on the picture, then apply it with the button next to it (or Enter).";
const NATIVE = new Set(["png", "jpg", "jpeg", "webp", "gif", "avif", "bmp", "ico"]);
const MIME: Record<string, string> = { ora: "image/openraster" };

export interface EditorOptions {
  client: ConversionClient;
  onBack(): void;
}

interface FileHandle {
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

/** Canvas-based high-quality resizer for "lanczos" (the browser's own smoothing). */
const canvasResize: ops.Resizer = (data, w, h, nw, nh, method) => {
  if (method !== "lanczos") return ops.jsResize(data, w, h, nw, nh, method);
  const src = new OffscreenCanvas(w, h);
  (src.getContext("2d") as OffscreenCanvasRenderingContext2D).putImageData(new ImageData(new Uint8ClampedArray(data), w, h), 0, 0);
  const dst = new OffscreenCanvas(nw, nh);
  const ctx = dst.getContext("2d", { willReadFrequently: true }) as OffscreenCanvasRenderingContext2D;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(src, 0, 0, nw, nh);
  return ctx.getImageData(0, 0, nw, nh).data;
};

export class EditorPage {
  readonly el: HTMLElement;
  readonly canvas = new EditorCanvas();
  doc: EditorDoc | null = null;
  history: History | null = null;
  private tools = makeTools(canvasRasterizer);
  tool: Tool = this.tools[0];
  private toolBar: ToolBar;
  private settings: ToolSettings;
  private swatches: Swatches;
  private layers: LayersPanel;
  private message = h("div", { class: "msg" });
  private info = h("div", { class: "info" });
  private undoBtn: HTMLButtonElement;
  private redoBtn: HTMLButtonElement;
  private saveBtn: HTMLButtonElement;
  private saveAsBtn: HTMLButtonElement;
  private textBox: HTMLTextAreaElement | null = null;
  private cropBar!: CropBar;
  private textAnchor: [number, number] = [0, 0];
  private editingText: number | null = null; // the text layer being edited again, if any
  private committing = false;
  private sources: string[] = [];
  private lastSave: { name: string; format: string; values: Record<string, unknown>; handle?: FileHandle } | null = null;
  private saveValues = new Map<string, Record<string, never>>();
  saving = false;
  private active = false;
  /** The last copy: pasted back in place when the system clipboard still holds the same picture,
   *  or used directly when the browser wouldn't let us write images to the clipboard. */
  private clip: clipboard.Clip | null = null;
  private clipOnSystem = false;

  constructor(private opts: EditorOptions) {
    this.toolBar = new ToolBar(this.tools, (id) => this.selectTool(id));
    this.settings = new ToolSettings(() => this.canvas.draw());
    this.swatches = new Swatches();
    this.layers = new LayersPanel(
      (cmd) => this.push(cmd),
      () => this.updateInfo(),
      (index) => this.editText(index),
    );
    this.canvas.contextProvider = () => this.context();
    this.canvas.onChanged = () => this.changed();
    this.canvas.onColorPicked = (c, b) => this.swatches.set(c, b);
    this.canvas.onCursor = (x, y) => this.updateInfo([x, y]);
    this.canvas.onTextRequested = (x, y) => this.openTextBox(x, y);
    this.canvas.onView = () => {
      this.updateInfo();
      this.placeTextBox();
    };

    const btn = (label: string, title: string, fn: () => void, cls = "btn small") => {
      const b = h("button", { class: cls, title }, label);
      b.addEventListener("click", fn);
      return b;
    };
    this.undoBtn = btn("Undo", "Undo (Ctrl+Z)", () => this.canvas.undo());
    this.redoBtn = btn("Redo", "Redo (Ctrl+Y)", () => this.canvas.redo());
    this.saveAsBtn = btn("Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs());
    this.saveBtn = btn("Save", "Save (Ctrl+S)", () => void this.save(), "primary small");
    const imageMenu = h("details", { class: "menu" }, h("summary", { class: "btn small" }, "Image ▾"),
      h("div", { class: "menu-items" }, ...this.menuItems([
        ["Resize image…", "resize"], ["Canvas size…", "canvas_size"], ["Crop to selection", "crop_selection"], null,
        ["Rotate 90° right", "rotate_right"], ["Rotate 90° left", "rotate_left"], ["Rotate 180°", "rotate_180"],
        ["Flip horizontal", "flip_h"], ["Flip vertical", "flip_v"], null,
        ["Layer: rotate 90° right", "layer_rotate_right"], ["Layer: rotate 90° left", "layer_rotate_left"],
        ["Layer: rotate 180°", "layer_rotate_180"], ["Layer: flip horizontal", "layer_flip_h"], ["Layer: flip vertical", "layer_flip_v"],
      ])));
    const selectMenu = h("details", { class: "menu" }, h("summary", { class: "btn small", title: "Select all, deselect, invert, feather" }, "Select ▾"),
      h("div", { class: "menu-items" }, ...this.menuItems([["Select all", "select_all"], ["Deselect", "deselect"], ["Invert", "invert_selection"], ["Feather…", "feather"]])));
    const adjustMenu = h("details", { class: "menu" }, h("summary", { class: "btn small", title: "Fix the tones and colours of the selected layer" }, "Adjust ▾"),
      h("div", { class: "menu-items" }, ...this.menuItems([
        ["Quick fix… (exposure, contrast, colours)", "quick_fix"], ["Levels…", "levels"], ["Curves…", "curves"],
      ])));
    const filterMenu = h("details", { class: "menu" }, h("summary", { class: "btn small", title: "Blur, sharpen and one-click looks for the selected layer" }, "Filters ▾"),
      h("div", { class: "menu-items" }, ...this.menuItems([["Looks…", "looks"], ["Blur…", "blur"], ["Background blur… (portraits)", "background_blur"], ["Sharpen…", "sharpen"]])));
    const top = h("div", { class: "editor-top" },
      btn("← Back to Convert", "Leave the editor", () => this.back(), "link"),
      h("span", { class: "gap" }), this.undoBtn, this.redoBtn,
      h("span", { class: "gap" }),
      btn("−", "Zoom out", () => this.canvas.zoomOut()), btn("+", "Zoom in", () => this.canvas.zoomIn()),
      btn("Fit", "Fit to window (Ctrl+0)", () => this.canvas.fit()), btn("100 %", "Actual pixels (Ctrl+1)", () => this.canvas.setZoom(1)),
      h("span", { class: "gap" }), imageMenu, selectMenu, adjustMenu, filterMenu,
      h("div", { class: "spacer" }), btn("?", "Keyboard shortcuts (?)", () => void this.showShortcuts()), this.saveAsBtn, this.saveBtn);
    // Crop needs buttons too (Enter isn't available on touch screens): they float next to the
    // rectangle once one is drawn, and also offer "Crop to selection" for a selection.
    this.cropBar = new CropBar((mode) => this.barApply(mode), (mode) => this.barCancel(mode));
    this.canvas.el.append(this.cropBar.el);
    this.canvas.bar = this.cropBar;
    const side = h("div", { class: "editor-side" }, this.settings.el, h("div", { class: "section" }, "Colours"), this.swatches.el, this.layers.el);
    this.el = h("div", { class: "editor", hidden: true },
      top,
      h("div", { class: "editor-body" }, this.toolBar.el, this.canvas.el, side),
      h("div", { class: "editor-status" }, this.message, this.info));
    this.selectTool("brush");
    this.updateButtons();
    window.addEventListener("keydown", (e) => this.key(e));
    window.addEventListener("paste", (e) => void this.onPaste(e));
    window.addEventListener("keyup", (e) => {
      if (e.key === " ") this.canvas.spaceDown = false;
    });
    window.addEventListener("beforeunload", (e) => {
      if (this.active && this.doc?.dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    });
    if (location.search.includes("e2e")) this.installTestHook();
  }

  private menuItems(items: ([string, string] | null)[]): HTMLElement[] {
    return items.map((it) => {
      if (!it) return h("hr");
      const b = h("button", { class: "menu-item" }, it[0]);
      b.addEventListener("click", () => {
        (b.closest("details") as HTMLDetailsElement).open = false;
        void this.runOp(it[1]);
      });
      return b;
    });
  }

  // ---------------------------------------------------------------- visibility
  show(): void {
    this.active = true;
    this.el.hidden = false;
    requestAnimationFrame(() => this.canvas.fit());
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
  }

  lastError(): string {
    return this.message.textContent ?? "";
  }

  get dirty(): boolean {
    return !!this.doc?.dirty;
  }

  /** True when it's fine to leave (nothing unsaved, or the user agreed to discard). */
  maybeLeave(): boolean {
    this.commitText();
    if (!this.dirty) return true;
    return window.confirm("Discard unsaved changes to this image?");
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- opening
  private source(f: InputFile): OpenSource {
    return {
      name: f.name,
      read: async () => new Uint8Array(await f.file.arrayBuffer()),
      decode: async (): Promise<Decoded> => {
        if (NATIVE.has(f.ext)) {
          try {
            return await decodeBlob(f.file);
          } catch {
            /* fall through to the worker */
          }
        }
        return this.decodeViaWorker(f);
      },
    };
  }

  private async decodeViaWorker(f: InputFile): Promise<Decoded> {
    const conv = converterById("image");
    const target = conv.targets([f]).find((t) => t.id === "png");
    if (!target) throw new EditorError(`Couldn't open “${f.name}” as an image.`);
    this.showMessage(`Opening ${f.name}…`);
    const [out] = await this.opts.client.run(target, [f], defaults(conv.options), (_p, m) => m && this.showMessage(m));
    return canvasPng.decode(out.data);
  }

  async open(files: InputFile[]): Promise<boolean> {
    try {
      const { doc, errors } = await openFiles(files.map((f) => this.source(f)), canvasPng);
      this.setDocument(doc);
      this.sources = files.map((f) => f.name);
      this.lastSave = null;
      this.showMessage(errors.join(" "), !!errors.length);
      return true;
    } catch (e) {
      this.showMessage(e instanceof EditorError ? e.message : "Couldn't open the images.", true);
      console.error(e);
      return false;
    }
  }

  async addFiles(files: InputFile[]): Promise<void> {
    if (!this.doc) {
      await this.open(files);
      return;
    }
    const { layers, width, height, errors } = await loadLayers(files.map((f) => this.source(f)), canvasPng);
    try {
      if (layers.length) checkSize(width, height);
    } catch (e) {
      this.showMessage((e as Error).message, true);
      return;
    }
    this.push(ops.addLayers(this.doc, layers));
    this.showMessage(errors.join(" "), !!errors.length);
  }

  private setDocument(doc: EditorDoc): void {
    this.doc = doc;
    this.history = new History(doc);
    this.canvas.setDocument(doc, this.history);
    this.layers.render(doc);
    this.updateButtons();
    this.updateInfo();
  }

  // ---------------------------------------------------------------- tools
  context(): ToolContext {
    const [primary, secondary] = this.swatches.colors;
    return { primary, secondary, options: this.settings.optionsFor(this.tool), shift: false, button: "left", font: this.settings.font.value };
  }

  selectTool(id: string): void {
    this.commitText();
    const tool = this.tools.find((t) => t.id === id);
    if (!tool) return;
    this.tool = tool;
    this.canvas.setTool(tool);
    this.toolBar.select(id);
    this.settings.show(tool);
    if (id === "crop") this.showMessage(CROP_HINT);
    else if (this.message.textContent === CROP_HINT) this.showMessage("");
  }

  private push(cmd: Command | null): void {
    if (!cmd || !this.doc) return;
    const size = [this.doc.width, this.doc.height];
    this.canvas.push(cmd);
    this.canvas.refresh();
    if (this.doc.width !== size[0] || this.doc.height !== size[1]) this.canvas.fit();
  }

  private changed(): void {
    if (!this.doc) return;
    this.layers.refreshActiveThumb();
    this.updateButtons();
    this.updateInfo();
  }

  private updateButtons(): void {
    const hst = this.history;
    this.undoBtn.disabled = !hst?.canUndo;
    this.redoBtn.disabled = !hst?.canRedo;
    this.undoBtn.title = hst?.canUndo ? `Undo ${hst.undoLabel} (Ctrl+Z)` : "Undo";
    this.redoBtn.title = hst?.canRedo ? `Redo ${hst.redoLabel} (Ctrl+Y)` : "Redo";
    this.saveBtn.disabled = this.saveAsBtn.disabled = !this.doc || this.saving;
  }

  private updateInfo(cursor?: [number, number]): void {
    if (!this.doc) return;
    const parts: string[] = [];
    if (cursor && cursor[0] >= 0 && cursor[1] >= 0 && cursor[0] < this.doc.width && cursor[1] < this.doc.height) parts.push(`${cursor[0]}, ${cursor[1]}`);
    parts.push(`${this.doc.width} × ${this.doc.height} px`, `${Math.round(this.canvas.zoom * 100)} %`);
    this.info.textContent = parts.join("   ·   ");
  }

  private showMessage(text: string, error = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", error);
  }

  // ---------------------------------------------------------------- keyboard
  private key(e: KeyboardEvent): void {
    if (!this.active || !this.doc) return;
    const t = e.target as HTMLElement;
    const k = e.key.toLowerCase();
    const mod = e.ctrlKey || e.metaKey;
    // Ctrl+S is always ours — never the browser's "Save page" — even while typing.
    if (mod && k === "s") {
      e.preventDefault();
      if (t.closest("dialog")) return;
      this.canvas.cancelGesture();
      void (e.shiftKey ? this.saveAs() : this.save());
      return;
    }
    if (t.closest("dialog") || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable) return;
    // Enter / Space on a focused button, menu or layer row must press it, not run a shortcut.
    if ((k === "enter" || k === " ") && t !== document.body && t !== this.canvas.view && t !== this.el) return;
    const run = (fn: () => void) => {
      e.preventDefault();
      this.canvas.cancelGesture();
      fn();
    };
    if (k === " " && !mod) {
      e.preventDefault();
      this.canvas.spaceDown = true;
      return;
    }
    if (k === "escape") return run(() => this.discardCrop());
    const arrows: Record<string, [number, number]> = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] };
    const onCanvas = t === this.canvas.view || t === document.body || t === this.el;
    if (this.tool.id === "move" && k in arrows && !mod && !e.altKey && onCanvas && !this.canvas.dragging) {
      const step = e.shiftKey ? 10 : 1;
      const [dx, dy] = arrows[k];
      return run(() => this.push(ops.nudgeLayers(this.doc as EditorDoc, selectedIndices(this.doc as EditorDoc), dx * step, dy * step)));
    }
    const shortcut = shortcutFor(e);
    if (shortcut) {
      // Ctrl+V: let the browser fire its paste event (that's how we get to read the clipboard).
      if (shortcut.id === "paste") return;
      const action = this.actions()[shortcut.id];
      if (action) return run(action);
      return;
    }
    if (mod || e.altKey) return;
    const group = toolGroups(this.tools).get(e.key.toUpperCase());
    if (group) run(() => this.cycleTool(group.map((t) => t.id)));
  }

  /** What each shortcut in shortcuts.ts does (the e2e test checks every id has one). */
  actions(): Record<string, () => void> {
    const doc = () => this.doc as EditorDoc;
    const lp = this.layers;
    return {
      undo: () => this.canvas.undo(),
      redo: () => this.canvas.redo(),
      copy: () => void this.copy(),
      copy_merged: () => void this.copy(true),
      cut: () => this.cut(),
      paste: () => {},
      delete: () => (this.layers.list.contains(document.activeElement) ? lp.remove() : this.push(clearSelection(doc()))),
      save: () => void this.save(),
      save_as: () => void this.saveAs(),
      select_all: () => this.push(selectAll(doc())),
      deselect: () => this.push(deselect(doc())),
      apply: () => {
        if (!this.canvas.finishTool()) this.applyCrop(); // Enter finishes a polygon lasso first
      },
      invert_selection: () => this.push(selection.invert(doc())),
      feather: () => void this.feather(),
      new_layer: () => lp.add(),
      duplicate: () => lp.duplicate(),
      merge: () => lp.merge(),
      layer_up: () => lp.move(1),
      layer_down: () => lp.move(-1),
      layer_top: () => lp.moveToEnd(true),
      layer_bottom: () => lp.moveToEnd(false),
      select_all_layers: () => lp.selectAll(),
      zoom_in: () => this.canvas.zoomIn(),
      zoom_out: () => this.canvas.zoomOut(),
      fit: () => this.canvas.fit(),
      actual: () => this.canvas.setZoom(1),
      help: () => void this.showShortcuts(),
      swap_colours: () => this.swatches.swap(),
      smaller: () => this.settings.changeSize(this.tool, 1 / 1.2),
      bigger: () => this.settings.changeSize(this.tool, 1.2),
    };
  }

  /** The first tool of a letter's group, or the next one if a tool of it is already chosen. */
  private cycleTool(ids: string[]): void {
    const i = ids.indexOf(this.tool.id);
    this.selectTool(i >= 0 ? ids[(i + 1) % ids.length] : ids[0]);
  }

  async feather(): Promise<void> {
    if (!this.doc?.selection) return this.showMessage("Select an area first.");
    const doc = this.doc;
    const r = await featherDialog();
    if (r !== null && this.doc === doc) this.push(selection.feather(doc, r));
  }

  showShortcuts(): Promise<void> {
    return shortcutsDialog(this.tools);
  }

  // ---------------------------------------------------------------- clipboard
  /** Ctrl+C / Ctrl+Shift+C. The picture also goes to the system clipboard where the browser
   *  allows it (so it can be pasted into other programs). */
  copy(merged = false): clipboard.Clip | null {
    if (!this.doc) return null;
    const clip = clipboard.copy(this.doc, merged);
    if (!clip) {
      this.showMessage("Nothing to copy: the selection doesn't touch this layer.");
      return null;
    }
    this.remember(clip);
    this.showMessage(`Copied ${clip.width} × ${clip.height} px.`);
    return clip;
  }

  /** Keep `clip` for pasting here, and put it on the system clipboard in the background. */
  private remember(clip: clipboard.Clip): void {
    this.clip = clip;
    this.clipOnSystem = false;
    void (async () => {
      try {
        const png = await canvasPng.encode(clip.data, clip.width, clip.height);
        await navigator.clipboard.write([new ClipboardItem({ "image/png": new Blob([png as BlobPart], { type: "image/png" }) })]);
        if (this.clip === clip) this.clipOnSystem = true;
      } catch {
        // No image clipboard here (older browser, no permission): copy / paste still work inside the editor.
      }
    })();
  }

  /** Copy and clear in one go (nothing can change the selection or layer in between). */
  cut(): void {
    if (!this.doc) return;
    const [clip, cmd] = clipboard.cut(this.doc);
    if (!clip) {
      this.showMessage("Nothing to cut: the selection doesn't touch this layer.");
      return;
    }
    this.remember(clip);
    this.push(cmd);
  }

  /** The canvas point in the middle of what the user is looking at. */
  viewCenter(): [number, number] {
    return this.canvas.widgetToImage(this.canvas.view.clientWidth / 2, this.canvas.view.clientHeight / 2);
  }

  private async onPaste(e: ClipboardEvent): Promise<void> {
    if (!this.active || !this.doc) return;
    const t = e.target as HTMLElement;
    if (t.closest?.("dialog") || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.isContentEditable) return;
    e.preventDefault();
    const file = [...(e.clipboardData?.items ?? [])].find((it) => it.kind === "file" && it.type.startsWith("image/"))?.getAsFile();
    if (!file) {
      if (this.clip && !this.clipOnSystem) return this.pasteClip(this.clip); // the browser kept the picture from us
      this.showMessage("There's no picture on the clipboard to paste.");
      return;
    }
    let decoded: Decoded;
    try {
      decoded = await decodeBlob(file);
    } catch {
      this.showMessage("The picture on the clipboard couldn't be read.", true);
      return;
    }
    // Our own copy comes back without its position (browsers re-encode clipboard pictures).
    const own = this.clip && samePicture(this.clip, decoded) ? this.clip : null;
    this.pasteClip(own ?? { ...decoded, x: null, y: null });
  }

  pasteClip(clip: clipboard.Clip): void {
    if (!this.doc) return;
    try {
      this.push(clipboard.paste(this.doc, clip, this.viewCenter()));
    } catch (err) {
      if (!(err instanceof EditorError)) throw err;
      this.showMessage(err.message, true);
      return;
    }
    this.showMessage("Pasted as a new layer. Move it with the Move tool (V).");
  }

  private barApply(mode: BarMode): void {
    if (mode === "crop") this.applyCrop();
    else void this.runOp("crop_selection");
    this.canvas.view.focus();
  }

  private barCancel(mode: BarMode): void {
    if (mode === "crop") this.discardCrop();
    else if (this.doc) this.push(deselect(this.doc));
    this.canvas.view.focus();
  }

  private discardCrop(): void {
    if (this.tool.id === "crop" && this.doc) {
      this.tool.cancel(this.doc);
      this.canvas.draw();
    }
  }

  private applyCrop(): void {
    const crop = this.tool as unknown as { pending?: import("../../editor/geometry").Rect | null };
    if (!this.doc) return;
    if (!crop.pending) {
      if (this.tool.id === "crop") this.showMessage("Drag a rectangle on the picture first.");
      return;
    }
    const r = crop.pending;
    crop.pending = null;
    this.push(ops.crop(this.doc, r));
  }

  // ---------------------------------------------------------------- operations
  async runOp(name: string): Promise<void> {
    const doc = this.doc;
    if (!doc) return;
    if (["select_all", "deselect", "invert_selection", "feather"].includes(name)) return this.actions()[name](); // Select menu
    this.commitText();
    try {
      let cmd: Command | null = null;
      if (name === "resize") {
        const r = await resizeDialog(doc);
        if (r) cmd = ops.resizeImage(doc, r[0], r[1], r[2], canvasResize);
      } else if (name === "canvas_size") {
        const r = await canvasSizeDialog(doc);
        if (r) cmd = ops.canvasSize(doc, ...r);
      } else if (name === "crop_selection") {
        if (!doc.selection) return this.showMessage("Select an area first (rectangle select, M).");
        cmd = ops.crop(doc, doc.selection);
      } else if (name === "background_blur" && !doc.selection) {
        return this.showMessage("Select the subject first (lasso, magic wand…): what is around it gets blurred.");
      } else if (name in ADJUST_DIALOGS) {
        const [dialog, label] = ADJUST_DIALOGS[name];
        const host: AdjustHost = {
          region: effects.regionData(doc),
          preview: (e) => this.canvas.setPreviewLayer(doc.active, e ? effects.preview(doc, e) : null),
        };
        const e = await dialog(host);
        this.canvas.setPreviewLayer(null, null);
        if (e && this.doc === doc) cmd = effects.applyToDocument(doc, e, label);
      } else {
        const layerOnly = name.startsWith("layer_");
        const key = name.replace(/^layer_/, "");
        const rot: Record<string, number> = { rotate_right: 90, rotate_left: 270, rotate_180: 180 };
        if (key in rot) cmd = ops.rotate(doc, rot[key], layerOnly);
        else if (key === "flip_h" || key === "flip_v") cmd = ops.flip(doc, key === "flip_h", layerOnly);
      }
      this.push(cmd);
    } catch (e) {
      this.showMessage(e instanceof EditorError ? e.message : "That didn't work.", true);
      console.error(e);
    }
  }

  // ---------------------------------------------------------------- text
  /** Open text layer `index` again (layer menu → Edit text…). */
  editText(index: number): void {
    if (!this.doc?.layers[index]?.text) return;
    this.selectTool("text");
    this.openTextBox(...anchorOf(this.doc.layers[index]), index);
  }

  private openTextBox(x: number, y: number, editing: number | null = null): void {
    this.commitText();
    if (editing === null && this.doc) {
      editing = textLayerAt(this.doc, x, y); // a click on a text layer edits it
      if (editing !== null) [x, y] = anchorOf(this.doc.layers[editing]);
    }
    this.editingText = editing;
    const textTool = this.tools.find((t) => t.id === "text") as Tool;
    if (editing !== null && this.doc) {
      const s = this.doc.layers[editing].text as TextSpec;
      this.settings.setValues(textTool, { size: s.size, bold: s.bold, italic: s.italic, align: s.align, outline: s.outline, shadow: s.shadow, box: s.box, watermark: s.watermark });
      this.settings.font.value = s.font;
      this.swatches.set(s.color, "left");
      this.swatches.set(s.box ? s.box_color : s.outline_color, "right");
    }
    this.textAnchor = [x, y];
    const o = this.settings.optionsFor(textTool);
    const box = h("textarea", { class: "text-box", rows: 1, ariaLabel: "Text" });
    const [r, g, b, a] = this.swatches.colors[0];
    box.style.color = `rgba(${r},${g},${b},${a / 255})`;
    box.style.fontFamily = `"${this.settings.font.value}", sans-serif`;
    box.style.fontWeight = o.bold ? "bold" : "normal";
    box.style.fontStyle = o.italic ? "italic" : "normal";
    box.addEventListener("keydown", (e) => {
      if (e.key === "Escape" || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        this.commitText();
      }
    });
    if (editing !== null && this.doc) box.value = (this.doc.layers[editing].text as TextSpec).text;
    box.addEventListener("input", () => this.placeTextBox());
    box.addEventListener("blur", () => this.commitText());
    this.textBox = box;
    this.canvas.el.append(box);
    this.placeTextBox();
    box.focus();
  }

  private placeTextBox(): void {
    const box = this.textBox;
    if (!box) return;
    const o = this.settings.optionsFor(this.tools.find((t) => t.id === "text") as Tool);
    const size = Math.max(6, Number(o.size) * this.canvas.zoom);
    const p = this.canvas.imageToWidget(...this.textAnchor);
    box.style.left = `${p.x}px`;
    box.style.top = `${p.y}px`;
    box.style.fontSize = `${size}px`;
    const lines = box.value.split("\n");
    box.style.width = `${Math.max(4, ...lines.map((l) => l.length + 1)) * size * 0.62}px`;
    box.style.height = `${lines.length * size * 1.25 + 8}px`;
  }

  commitText(): void {
    const box = this.textBox;
    if (!box || this.committing) return;
    this.committing = true;
    try {
      this.textBox = null;
      const text = box.value;
      box.remove();
      if (!this.doc) return;
      const o = this.settings.optionsFor(this.tools.find((t) => t.id === "text") as Tool);
      const [x, y] = this.textAnchor;
      const [front, back] = this.swatches.colors;
      const spec = makeSpec(text, {
        font: this.settings.font.value, size: Number(o.size), bold: !!o.bold, italic: !!o.italic,
        color: front as RGBA, align: (o.align as TextSpec["align"]) ?? "left", outline: Number(o.outline) || 0,
        outline_color: back as RGBA, shadow: !!o.shadow, box: !!o.box, box_color: back as RGBA, watermark: !!o.watermark,
      });
      const editing = this.editingText;
      this.editingText = null;
      if (editing !== null && editing < this.doc.layers.length) this.push(editTextLayer(this.doc, canvasRasterizer, editing, spec));
      else this.push(addTextLayer(this.doc, canvasRasterizer, spec, x, y));
      this.canvas.view.focus();
    } finally {
      this.committing = false;
    }
  }

  // ---------------------------------------------------------------- saving
  private formats(): [string, string][] {
    const conv = converterById("image");
    return [["ora", "OpenRaster — keeps layers (.ora)"], ...conv.targets([]).map((t): [string, string] => [t.id, `${t.label} (.${t.ext})`])];
  }

  async saveAs(): Promise<void> {
    if (!this.doc || this.saving) return;
    this.commitText();
    const stem = (this.lastSave?.name ?? (this.sources[0]?.replace(/\.[^.]+$/, "") ?? "image") + " edited").replace(/\.[^.]+$/, "");
    const format = this.lastSave?.format ?? (this.doc.layers.length > 1 ? "ora" : "png");
    const conv = converterById("image");
    const choice = await saveDialog(stem, this.formats(), format, (f) => (f === "ora" ? [] : conv.options), this.saveValues as never);
    if (!choice) return;
    let handle: FileHandle | undefined;
    const ext = this.extOf(choice.format);
    if (choice.toFile) {
      try {
        handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({ suggestedName: `${choice.name}.${ext}` });
      } catch {
        return; // picker cancelled
      }
    }
    this.lastSave = { name: choice.name, format: choice.format, values: choice.values, handle };
    await this.write();
  }

  async save(): Promise<void> {
    if (!this.lastSave) return this.saveAs();
    if (!this.doc || this.saving) return;
    this.commitText();
    await this.write();
  }

  private extOf(format: string): string {
    return format === "ora" ? "ora" : (converterById("image").targets([]).find((t) => t.id === format)?.ext ?? format);
  }

  private async write(): Promise<void> {
    const doc = this.doc as EditorDoc;
    const hst = this.history as History;
    const save = this.lastSave as NonNullable<typeof this.lastSave>;
    const snapshot: EditorDoc = { ...doc, layers: doc.layers.map((l) => ({ ...l, data: new Uint8ClampedArray(l.data) })) };
    const position = hst.position;
    const ext = this.extOf(save.format);
    const fileName = `${save.name}.${ext}`;
    this.saving = true;
    this.updateButtons();
    this.showMessage(`Saving “${fileName}”…`);
    try {
      let bytes: Uint8Array;
      let type: string;
      if (save.format === "ora") {
        bytes = await writeOra(snapshot, canvasPng);
        type = MIME.ora;
      } else {
        const png = await canvasPng.encode(composite(snapshot), snapshot.width, snapshot.height);
        const conv = converterById("image");
        const input: InputFile = { file: new File([png as BlobPart], `${save.name}.png`, { type: "image/png" }), name: `${save.name}.png`, ext: "png", family: "image" };
        const target = conv.targets([input]).find((t) => t.id === save.format);
        if (!target) throw new EditorError(`Can't save as ${save.format.toUpperCase()}.`);
        const [out] = await this.opts.client.run(target, [input], { ...defaults(conv.options), ...(save.values as object) }, (_p, m) => m && this.showMessage(m));
        bytes = out.data;
        type = out.type;
      }
      const blob = new Blob([bytes as BlobPart], { type });
      if (save.handle) {
        const w = await save.handle.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        const url = URL.createObjectURL(blob);
        h("a", { href: url, download: fileName }).click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      if (this.doc === doc) hst.markSaved(position);
      this.showMessage(`Saved “${fileName}”.`);
    } catch (e) {
      console.error(e);
      this.showMessage(e instanceof Error && e.message ? `Couldn't save: ${e.message}` : "Couldn't save the image.", true);
    } finally {
      this.saving = false;
      this.updateButtons();
    }
  }

  // ---------------------------------------------------------------- e2e hook
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __editor: object }).__editor = {
      get doc() {
        return page.doc;
      },
      get history() {
        return page.history;
      },
      get toolId() {
        return page.tool.id;
      },
      /** Shortcut ids in shortcuts.ts that have no action (should be none). */
      missingActions() {
        const actions = page.actions();
        return SHORTCUTS.map((sc) => sc.id).filter((id) => !(id in actions));
      },
      pasteClip(data: number[], width: number, height: number) {
        page.pasteClip({ data: new Uint8ClampedArray(data), width, height, x: null, y: null });
      },
      viewCenter() {
        return page.viewCenter();
      },
      previewing() {
        return page.canvas.previewing;
      },
      imageToPage(x: number, y: number) {
        const r = page.canvas.view.getBoundingClientRect();
        const p = page.canvas.imageToWidget(x, y);
        return { x: r.left + p.x, y: r.top + p.y };
      },
      pixel(x: number, y: number, layer?: number) {
        const d = page.doc as EditorDoc;
        const l = d.layers[layer ?? d.active];
        const i = ((y - l.y) * l.width + (x - l.x)) * 4;
        return Array.from(l.data.subarray(i, i + 4));
      },
    };
  }
}

export function mountEditor(host: HTMLElement, opts: EditorOptions): EditorPage {
  const page = new EditorPage(opts);
  host.append(page.el);
  return page;
}
