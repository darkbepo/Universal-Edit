// The website's image editor page: top bar, tool bar, canvas, settings / colours / layers, status.
// Loaded on demand from ui/app.ts. The picture lives in the page; the conversion worker is only
// used to decode formats the browser can't and to encode saves other than .ora.
import { defaults } from "../../core/options";
import { converterById } from "../../core/registry";
import type { InputFile } from "../../core/types";
import { checkSize, composite, EditorError, type EditorDoc } from "../../editor/document";
import { History, type Command } from "../../editor/history";
import type { RGBA } from "../../editor/layer";
import { loadLayers, openFiles, type OpenSource } from "../../editor/open";
import * as ops from "../../editor/ops";
import { writeOra } from "../../editor/ora";
import { canvasPng, canvasRasterizer, decodeBlob, type Decoded } from "../../editor/raster";
import type { Tool, ToolContext } from "../../editor/tools/base";
import { makeTools } from "../../editor/tools/index";
import { clearSelection, deselect, selectAll } from "../../editor/tools/select";
import { addTextLayer } from "../../editor/tools/text";
import type { ConversionClient } from "../../worker/client";
import { EditorCanvas } from "./canvas";
import { brightnessDialog, canvasSizeDialog, resizeDialog, saveDialog } from "./dialogs";
import { CropBar, type BarMode } from "./cropbar";
import { h, LayersPanel, Swatches, ToolBar, ToolSettings } from "./panels";

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
  private committing = false;
  private sources: string[] = [];
  private lastSave: { name: string; format: string; values: Record<string, unknown>; handle?: FileHandle } | null = null;
  private saveValues = new Map<string, Record<string, never>>();
  saving = false;
  private active = false;

  constructor(private opts: EditorOptions) {
    this.toolBar = new ToolBar(this.tools, (id) => this.selectTool(id));
    this.settings = new ToolSettings(() => this.canvas.draw());
    this.swatches = new Swatches();
    this.layers = new LayersPanel(
      (cmd) => this.push(cmd),
      () => this.updateInfo(),
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
        ["Brightness / contrast…", "brightness"], null,
        ["Layer: rotate 90° right", "layer_rotate_right"], ["Layer: rotate 90° left", "layer_rotate_left"],
        ["Layer: rotate 180°", "layer_rotate_180"], ["Layer: flip horizontal", "layer_flip_h"], ["Layer: flip vertical", "layer_flip_v"],
      ])));
    const top = h("div", { class: "editor-top" },
      btn("← Back to Convert", "Leave the editor", () => this.back(), "link"),
      h("span", { class: "gap" }), this.undoBtn, this.redoBtn,
      h("span", { class: "gap" }),
      btn("−", "Zoom out", () => this.canvas.zoomOut()), btn("+", "Zoom in", () => this.canvas.zoomIn()),
      btn("Fit", "Fit to window (Ctrl+0)", () => this.canvas.fit()), btn("100 %", "Actual pixels (Ctrl+1)", () => this.canvas.setZoom(1)),
      h("span", { class: "gap" }), imageMenu,
      h("div", { class: "spacer" }), this.saveAsBtn, this.saveBtn);
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
    if (mod) {
      if (k === "z" && !e.shiftKey) run(() => this.canvas.undo());
      else if (k === "y" || (k === "z" && e.shiftKey)) run(() => this.canvas.redo());
      else if (k === "a") run(() => this.push(selectAll(this.doc as EditorDoc)));
      else if (k === "d") run(() => this.push(deselect(this.doc as EditorDoc)));
      else if (k === "0") run(() => this.canvas.fit());
      else if (k === "1") run(() => this.canvas.setZoom(1));
      return;
    }
    if (e.altKey) return;
    if (k === " ") {
      e.preventDefault();
      this.canvas.spaceDown = true;
      return;
    }
    if (k === "delete" || k === "backspace") return run(() => this.push(clearSelection(this.doc as EditorDoc)));
    if (k === "enter") return run(() => this.applyCrop());
    if (k === "escape") return run(() => this.discardCrop());
    if (k === "x") return run(() => this.swatches.swap());
    if (k === "[" || k === "]") return run(() => this.settings.changeSize(this.tool, k === "]" ? 1.2 : 1 / 1.2));
    if (k === "u") return run(() => this.selectTool(this.tool.id === "rect" ? "ellipse" : "rect"));
    const tool = this.tools.find((tt) => tt.shortcut.toLowerCase() === k);
    if (tool) run(() => this.selectTool(tool.id));
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
      } else if (name === "brightness") {
        const r = await brightnessDialog((b, c) => this.canvas.setPreviewLayer(doc.active, b || c ? ops.previewBrightnessContrast(doc, b, c) : null));
        this.canvas.setPreviewLayer(null, null);
        if (r) cmd = ops.applyBrightnessContrast(doc, r[0], r[1]);
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
  private openTextBox(x: number, y: number): void {
    this.commitText();
    this.textAnchor = [x, y];
    const o = this.settings.optionsFor(this.tools.find((t) => t.id === "text") as Tool);
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
      const font = { family: this.settings.font.value, size: Number(o.size), bold: !!o.bold, italic: !!o.italic };
      this.push(addTextLayer(this.doc, canvasRasterizer, text, x, y, font, this.swatches.colors[0] as RGBA));
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
