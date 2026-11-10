// The editor canvas: zoom / pan, pointer → tool, a cached composite repainted by dirty rect.
import { composite, docBounds, pasteboard, type EditorDoc } from "../../editor/document";
import { Rect } from "../../editor/geometry";
import type { Command, History } from "../../editor/history";
import type { RGBA } from "../../editor/layer";
import type { Tool, ToolContext } from "../../editor/tools/base";
import { outline as selectionOutline } from "../../editor/selection";
import type { Outline } from "../../editor/tools/select";
import { nearestCorner, placeBar, type BarMode, type CropBar } from "./cropbar";

const SELECT_TOOLS = ["select", "ellipse_select", "lasso", "polygon_lasso", "wand"];

const cursorFor = (tool: Tool) => ({ text: "text", move: "move" })[tool.cursor as string] ?? "crosshair";

const MIN_ZOOM = 0.05;
const MAX_ZOOM = 32;

function checkerPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
  const c = new OffscreenCanvas(16, 16);
  const g = c.getContext("2d") as OffscreenCanvasRenderingContext2D;
  g.fillStyle = "#fff";
  g.fillRect(0, 0, 16, 16);
  g.fillStyle = "#d9d9d9";
  g.fillRect(0, 0, 8, 8);
  g.fillRect(8, 8, 8, 8);
  return ctx.createPattern(c, "repeat");
}

export class EditorCanvas {
  readonly el: HTMLDivElement;
  readonly view: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  doc: EditorDoc | null = null;
  history: History | null = null;
  tool: Tool | null = null;
  zoom = 1;
  private origin = { x: 0, y: 0 }; // css px position of image pixel (0, 0)
  private cache: OffscreenCanvas | null = null;
  private cacheCtx: OffscreenCanvasRenderingContext2D | null = null;
  /** The area the cache covers: the canvas plus the pasteboard around it. */
  private area = new Rect(0, 0, 0, 0);
  private previewLayer: [number, Uint8ClampedArray] | null = null;
  private pointers = new Map<number, { x: number; y: number }>();
  private gesture: "left" | "right" | "pan" | "pinch" | null = null;
  private panFrom = { x: 0, y: 0 };
  private pinch: { dist: number; mid: { x: number; y: number } } | null = null;
  private mouse: { x: number; y: number } | null = null;
  private ants = 0;
  private frame = 0;
  private pattern: CanvasPattern | null = null;
  spaceDown = false;
  /** Keep fitting the picture to the window until the user zooms or pans. */
  private autoFit = true;
  private docSize: [number, number] = [0, 0];
  contextProvider: () => ToolContext = () => {
    throw new Error("no context provider");
  };
  onChanged: () => void = () => {};
  /** Floating Apply / Cancel bar (set by the page), next to the crop rectangle or the selection. */
  bar: CropBar | null = null;
  /** Where the last drag ended, in image coordinates (the bar goes next to that corner). */
  private releaseAt: [number, number] | null = null;
  private outlineFor: Uint8Array | null = null;
  private outlinePath: Path2D | null = null;
  onColorPicked: (c: RGBA, button: "left" | "right") => void = () => {};
  onCursor: (x: number, y: number) => void = () => {};
  onTextRequested: (x: number, y: number) => void = () => {};
  /** A tool needs something first (e.g. a clone source). */
  onNotice: (text: string) => void = () => {};
  onView: () => void = () => {};

  constructor() {
    this.el = document.createElement("div");
    this.el.className = "canvas-host";
    this.view = document.createElement("canvas");
    this.view.className = "view";
    this.view.tabIndex = 0;
    this.el.append(this.view);
    this.ctx = this.view.getContext("2d") as CanvasRenderingContext2D;
    new ResizeObserver(() => this.resize()).observe(this.el);
    this.view.addEventListener("pointerdown", (e) => this.down(e));
    this.view.addEventListener("pointermove", (e) => this.moveEvt(e));
    this.view.addEventListener("pointerup", (e) => this.up(e));
    this.view.addEventListener("pointercancel", (e) => this.up(e, true));
    this.view.addEventListener("pointerleave", () => {
      this.mouse = null;
      this.draw();
    });
    this.view.addEventListener("contextmenu", (e) => e.preventDefault());
    this.view.addEventListener("dblclick", () => this.finishTool()); // finishes a polygon lasso
    this.view.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this.view.getBoundingClientRect();
      this.setZoom(this.zoom * 1.25 ** (-e.deltaY / 100), { x: e.clientX - r.left, y: e.clientY - r.top });
    }, { passive: false });
    setInterval(() => {
      if (this.doc && (this.doc.selection || this.cropRect() || (this.tool as unknown as { outline?: unknown } | null)?.outline)) {
        this.ants = (this.ants + 1) % 8;
        this.draw();
      }
    }, 150);
  }

  // ---------------------------------------------------------------- document
  setDocument(doc: EditorDoc, history: History): void {
    this.doc = doc;
    this.history = history;
    this.previewLayer = null;
    this.refresh();
    this.fit();
  }

  setTool(tool: Tool): void {
    this.cancelGesture();
    this.tool = tool;
    this.view.style.cursor = cursorFor(tool);
    this.draw();
  }

  /** Re-composite `rect` (canvas coordinates; omitted = everything) into the cache. */
  refresh(rect?: Rect | null): void {
    const doc = this.doc;
    if (!doc) return;
    const override = this.previewLayer ? new Map([this.previewLayer]) : undefined;
    const area = pasteboard(doc, Math.max(16, Math.floor(Math.max(doc.width, doc.height) / 8)));
    const grown = !area.union(this.area).equals(this.area);
    if (!rect || !this.cache || grown) {
      const resized = !!this.cache && (this.docSize[0] !== doc.width || this.docSize[1] !== doc.height);
      if (resized) this.autoFit = true; // crop / resize / rotate: show the whole new picture
      this.docSize = [doc.width, doc.height];
      this.area = area;
      this.cache = new OffscreenCanvas(area.width, area.height);
      this.cacheCtx = this.cache.getContext("2d") as OffscreenCanvasRenderingContext2D;
      this.cacheCtx.putImageData(new ImageData(composite(doc, area, override, false) as Uint8ClampedArray<ArrayBuffer>, area.width, area.height), 0, 0);
      if (!rect && !this.autoFit) this.clamp(); // not while dragging: the view would jump
    } else {
      const r = rect.intersect(this.area);
      if (!r.empty) (this.cacheCtx as OffscreenCanvasRenderingContext2D).putImageData(new ImageData(composite(doc, r, override, false) as Uint8ClampedArray<ArrayBuffer>, r.width, r.height), r.x0 - this.area.x0, r.y0 - this.area.y0);
    }
    if (this.autoFit) this.fit();
    this.draw();
  }

  /** A live preview (adjustment windows) is showing instead of the real layer. */
  get previewing(): boolean {
    return this.previewLayer !== null;
  }

  setPreviewLayer(index: number | null, data: Uint8ClampedArray | null): void {
    this.previewLayer = index === null || !data ? null : [index, data];
    this.refresh();
  }

  // ---------------------------------------------------------------- history
  cancelGesture(): void {
    if ((this.gesture === "left" || this.gesture === "right") && this.tool && this.doc) {
      this.gesture = null;
      const r = this.tool.cancel(this.doc);
      if (r) this.refresh(r);
      this.draw();
    }
  }

  push(cmd: Command | null): void {
    if (!cmd || !this.history) return;
    this.history.push(cmd);
    this.onChanged();
  }

  undo(): void {
    this.cancelGesture();
    if (!this.history?.canUndo) return;
    this.history.undo();
    this.refresh();
    this.onChanged();
  }

  redo(): void {
    this.cancelGesture();
    if (!this.history?.canRedo) return;
    this.history.redo();
    this.refresh();
    this.onChanged();
  }

  // ---------------------------------------------------------------- geometry
  setZoom(zoom: number, anchor?: { x: number; y: number }): void {
    const z = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
    const a = anchor ?? { x: this.el.clientWidth / 2, y: this.el.clientHeight / 2 };
    const [ix, iy] = this.widgetToImage(a.x, a.y);
    this.zoom = z;
    this.autoFit = false;
    this.origin = { x: a.x - ix * z, y: a.y - iy * z };
    this.clamp();
    this.viewChanged();
  }

  fit(): void {
    if (!this.doc) return;
    const w = this.el.clientWidth || 800;
    const h = this.el.clientHeight || 600;
    this.zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, Math.min((w - 24) / this.doc.width, (h - 24) / this.doc.height)));
    this.origin = { x: (w - this.doc.width * this.zoom) / 2, y: (h - this.doc.height * this.zoom) / 2 };
    this.autoFit = true;
    this.viewChanged();
  }

  zoomIn(): void {
    this.setZoom(this.zoom * 1.25);
  }

  zoomOut(): void {
    this.setZoom(this.zoom / 1.25);
  }

  panBy(dx: number, dy: number): void {
    this.autoFit = false;
    this.origin = { x: this.origin.x + dx, y: this.origin.y + dy };
    this.clamp();
    this.viewChanged();
  }

  private clamp(): void {
    if (!this.doc) return;
    const vw = this.el.clientWidth;
    const vh = this.el.clientHeight;
    // keep part of the picture (and the layers on the pasteboard around it) on screen; centre the
    // picture when everything fits
    const z = this.zoom;
    const a = this.area.empty ? docBounds(this.doc) : this.area;
    const keep = 40;
    const axis = (view: number, size: number, lo: number, hi: number, o: number) =>
      (hi - lo) * z <= view ? Math.max(-lo * z, Math.min(view - hi * z, (view - size * z) / 2)) : Math.min(keep - lo * z, Math.max(view - hi * z - keep, o));
    this.origin = { x: axis(vw, this.doc.width, a.x0, a.x1, this.origin.x), y: axis(vh, this.doc.height, a.y0, a.y1, this.origin.y) };
  }

  private viewChanged(): void {
    this.draw();
    this.onView();
  }

  widgetToImage(x: number, y: number): [number, number] {
    return [(x - this.origin.x) / this.zoom, (y - this.origin.y) / this.zoom];
  }

  imageToWidget(x: number, y: number): { x: number; y: number } {
    return { x: this.origin.x + x * this.zoom, y: this.origin.y + y * this.zoom };
  }

  private resize(): void {
    const dpr = window.devicePixelRatio || 1;
    const w = this.el.clientWidth;
    const h = this.el.clientHeight;
    this.view.width = Math.max(1, Math.round(w * dpr));
    this.view.height = Math.max(1, Math.round(h * dpr));
    this.view.style.width = `${w}px`;
    this.view.style.height = `${h}px`;
    this.pattern = null;
    if (this.doc && this.autoFit) this.fit();
    else this.clamp();
    this.draw();
  }

  // ---------------------------------------------------------------- painting
  private cropRect(): Rect | null {
    return (this.tool as unknown as { pending?: Rect | null })?.pending ?? null;
  }

  draw(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.paint();
      this.placeBar();
    });
  }

  private screenRect(r: Rect): [number, number, number, number] {
    return [this.origin.x + r.x0 * this.zoom, this.origin.y + r.y0 * this.zoom, r.width * this.zoom, r.height * this.zoom];
  }

  private paint(): void {
    const ctx = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, this.view.width, this.view.height);
    const doc = this.doc;
    if (!doc || !this.cache) return;
    const [x, y, w, h] = this.screenRect(docBounds(doc));
    this.pattern ??= checkerPattern(ctx);
    if (this.pattern) {
      ctx.fillStyle = this.pattern;
      ctx.fillRect(x, y, w, h);
    }
    ctx.imageSmoothingEnabled = this.zoom < 1;
    ctx.imageSmoothingQuality = "high";
    const [ax, ay, aw, ah] = this.screenRect(this.area);
    ctx.drawImage(this.cache, ax, ay, aw, ah);
    if (!this.area.equals(docBounds(doc))) {
      // the pasteboard: layers around the picture are shown faded — they aren't saved
      ctx.fillStyle = getComputedStyle(this.el).getPropertyValue("--pasteboard").trim() || "rgba(128,128,128,0.6)";
      const top = Math.max(ay, Math.min(y, ay + ah));
      const bottom = Math.min(ay + ah, Math.max(y + h, ay));
      ctx.fillRect(ax, ay, aw, top - ay);
      ctx.fillRect(ax, bottom, aw, ay + ah - bottom);
      ctx.fillRect(ax, top, x - ax, bottom - top);
      ctx.fillRect(x + w, top, ax + aw - (x + w), bottom - top);
      ctx.strokeStyle = "rgba(128,128,128,0.8)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x - 0.5, y - 0.5, w + 1, h + 1);
    }
    const prev = this.tool?.preview(doc);
    if (prev) {
      const c = new OffscreenCanvas(prev.width, prev.height);
      (c.getContext("2d") as OffscreenCanvasRenderingContext2D).putImageData(new ImageData(new Uint8ClampedArray(prev.data), prev.width, prev.height), 0, 0);
      ctx.drawImage(c, this.origin.x + prev.x * this.zoom, this.origin.y + prev.y * this.zoom, prev.width * this.zoom, prev.height * this.zoom);
    }
    const crop = this.cropRect();
    if (crop) {
      const [cx, cy, cw, ch] = this.screenRect(crop);
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.fillRect(x, y, w, cy - y);
      ctx.fillRect(x, cy + ch, w, y + h - (cy + ch));
      ctx.fillRect(x, cy, cx - x, ch);
      ctx.fillRect(cx + cw, cy, x + w - (cx + cw), ch);
      this.antsRect(cx, cy, cw, ch);
    }
    if (doc.selection) {
      if (doc.selectionMask) this.antsPath(this.selectionPath());
      else this.antsRect(...this.screenRect(doc.selection));
    }
    this.paintToolOutline();
    if (this.mouse && this.tool?.cursor === "brush") {
      const size = Number(this.contextProvider().options.size) || 0;
      const r = (size * this.zoom) / 2;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.strokeStyle = "rgba(0,0,0,.65)";
      ctx.beginPath();
      ctx.arc(this.mouse.x, this.mouse.y, Math.max(1, r), 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = "rgba(255,255,255,.8)";
      ctx.beginPath();
      ctx.arc(this.mouse.x, this.mouse.y, Math.max(1, r) + 1, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  /** A stroke / drag is in progress (arrow-key nudges wait for it to end). */
  get dragging(): boolean {
    return this.gesture === "left" || this.gesture === "right";
  }

  /** [mode, rectangle] the floating bar belongs to, or null to hide it. */
  barTarget(): [BarMode, Rect] | null {
    if (!this.doc || !this.tool || this.gesture === "left" || this.gesture === "right") return null;
    const crop = this.cropRect();
    if (this.tool.id === "crop" && crop) return ["crop", crop];
    if (SELECT_TOOLS.includes(this.tool.id) && this.doc.selection) {
      if ((this.tool as unknown as { outline?: unknown }).outline) return null; // a polygon still being drawn
      return ["select", this.doc.selection];
    }
    return null;
  }

  private placeBar(): void {
    const bar = this.bar;
    if (!bar) return;
    const target = this.barTarget();
    if (!target) {
      if (!bar.el.hidden) bar.hide();
      return;
    }
    const [mode, rect] = target;
    bar.showFor(mode, rect.width, rect.height);
    const [x, y, w, h] = this.screenRect(rect);
    const box: [number, number, number, number] = [x, y, x + w, y + h];
    let corner: [boolean, boolean] = [true, true];
    if (this.releaseAt) {
      const ax = this.origin.x + this.releaseAt[0] * this.zoom;
      const ay = this.origin.y + this.releaseAt[1] * this.zoom;
      corner = nearestCorner(box, ax, ay);
    }
    const area: [number, number] = [this.el.clientWidth, this.el.clientHeight];
    const [left, top] = placeBar(box, corner, [bar.el.offsetWidth, bar.el.offsetHeight], area);
    bar.el.style.left = `${left}px`;
    bar.el.style.top = `${top}px`;
  }

  /** The selection's outline in canvas pixels (cached until the selection changes). */
  private selectionPath(): Path2D {
    const doc = this.doc as EditorDoc;
    if (this.outlineFor !== doc.selectionMask || !this.outlinePath) {
      const path = new Path2D();
      for (const [x0, y0, x1, y1] of selectionOutline(doc)) {
        path.moveTo(x0, y0);
        path.lineTo(x1, y1);
      }
      this.outlineFor = doc.selectionMask ?? null;
      this.outlinePath = path;
    }
    return this.outlinePath;
  }

  /** Marching ants along `path` (canvas pixels), 1 screen pixel wide at any zoom. */
  private antsPath(path: Path2D): void {
    const ctx = this.ctx;
    ctx.save();
    const m = new DOMMatrix([this.zoom, 0, 0, this.zoom, this.origin.x + 0.5, this.origin.y + 0.5]);
    const screen = new Path2D();
    screen.addPath(path, m);
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    ctx.strokeStyle = "#fff";
    ctx.stroke(screen);
    ctx.setLineDash([4, 4]);
    ctx.lineDashOffset = -this.ants;
    ctx.strokeStyle = "#000";
    ctx.stroke(screen);
    ctx.restore();
  }

  /** The shape a selection tool is drawing (before the pointer is let go). */
  private paintToolOutline(): void {
    const marker = (this.tool as unknown as { marker?: (x: number, y: number) => [number, number] | null } | null)?.marker;
    if (marker && this.mouse) {
      const pt = marker.call(this.tool, ...this.widgetToImage(this.mouse.x, this.mouse.y));
      if (pt) {
        const c = this.imageToWidget(pt[0], pt[1]);
        const ctx = this.ctx;
        for (const [color, width] of [["rgba(0,0,0,0.65)", 3], ["rgba(255,255,255,0.9)", 1]] as const) {
          ctx.strokeStyle = color;
          ctx.lineWidth = width;
          ctx.beginPath();
          ctx.moveTo(c.x - 8, c.y);
          ctx.lineTo(c.x + 8, c.y);
          ctx.moveTo(c.x, c.y - 8);
          ctx.lineTo(c.x, c.y + 8);
          ctx.stroke();
        }
      }
    }
    const outline = (this.tool as unknown as { outline?: Outline | null } | null)?.outline;
    if (!outline) return;
    const path = new Path2D();
    if (outline[0] === "poly") {
      const pts = outline[1];
      if (!pts.length) return;
      path.moveTo(...pts[0]);
      for (const pt of pts.slice(1)) path.lineTo(...pt);
      if (this.tool?.id === "polygon_lasso" && this.mouse) path.lineTo(...this.widgetToImage(this.mouse.x, this.mouse.y)); // the next corner
    } else {
      const r = outline[1];
      if (outline[0] === "rect") path.rect(r.x0, r.y0, r.width, r.height);
      else path.ellipse(r.x0 + r.width / 2, r.y0 + r.height / 2, r.width / 2, r.height / 2, 0, 0, Math.PI * 2);
    }
    this.antsPath(path);
  }

  /** Finish a tool that collects clicks (polygon lasso); true if it had something. */
  finishTool(): boolean {
    const t = this.tool as unknown as { finish?: (d: EditorDoc) => Command | null; points?: unknown[] } | null;
    if (!t?.finish || !this.doc || !t.points?.length) return false;
    this.push(t.finish(this.doc));
    this.draw();
    return true;
  }

  private antsRect(x: number, y: number, w: number, h: number): void {
    const ctx = this.ctx;
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    ctx.strokeStyle = "#fff";
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);
    ctx.setLineDash([4, 4]);
    ctx.lineDashOffset = -this.ants;
    ctx.strokeStyle = "#000";
    ctx.strokeRect(x + 0.5, y + 0.5, w, h);
    ctx.setLineDash([]);
  }

  // ---------------------------------------------------------------- input
  private local(e: PointerEvent): { x: number; y: number } {
    const r = this.view.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private ctxFor(button: "left" | "right", e: PointerEvent): ToolContext {
    return { ...this.contextProvider(), button, shift: e.shiftKey, alt: e.altKey, zoom: this.zoom };
  }

  private down(e: PointerEvent): void {
    if (!this.doc) return;
    const p = this.local(e);
    this.pointers.set(e.pointerId, p);
    try {
      this.view.setPointerCapture(e.pointerId);
    } catch {
      /* not an active pointer (e.g. synthetic events) — capture is only a nicety */
    }
    if (e.pointerType === "touch" && this.pointers.size >= 2) {
      this.cancelGesture(); // a second finger: pinch / pan instead of painting
      this.gesture = "pinch";
      this.pinch = this.pinchState();
      return;
    }
    if (this.gesture) return;
    if (e.button === 1 || (e.button === 0 && this.spaceDown)) {
      this.gesture = "pan";
      this.panFrom = p;
      this.view.style.cursor = "grabbing";
      return;
    }
    const button = e.button === 2 ? "right" : e.button === 0 ? "left" : null;
    if (!button || !this.tool) return;
    const [x, y] = this.widgetToImage(p.x, p.y);
    if (this.tool.id === "text") {
      this.tool.press(this.doc, x, y, this.ctxFor(button, e));
      e.preventDefault(); // keep focus where the text box puts it
      this.onTextRequested(x, y);
      return;
    }
    this.view.focus();
    this.gesture = button;
    const r = this.tool.press(this.doc, x, y, this.ctxFor(button, e));
    if (r) this.refresh(r);
    const t = this.tool as unknown as { notice?: string | null };
    if (t.notice) {
      this.onNotice(t.notice);
      t.notice = null;
      this.gesture = null;
    }
    this.emitPick();
    this.draw();
  }

  private pinchState() {
    const [a, b] = [...this.pointers.values()];
    return { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
  }

  private moveEvt(e: PointerEvent): void {
    const p = this.local(e);
    if (this.pointers.has(e.pointerId)) this.pointers.set(e.pointerId, p);
    this.mouse = e.pointerType === "mouse" ? p : null;
    const [x, y] = this.widgetToImage(p.x, p.y);
    this.onCursor(Math.floor(x), Math.floor(y));
    if (this.gesture === "pinch") {
      if (this.pointers.size >= 2 && this.pinch) {
        const now = this.pinchState();
        this.panBy(now.mid.x - this.pinch.mid.x, now.mid.y - this.pinch.mid.y);
        this.setZoom(this.zoom * (now.dist / this.pinch.dist), now.mid);
        this.pinch = now;
      }
      return;
    }
    if (this.gesture === "pan") {
      this.panBy(p.x - this.panFrom.x, p.y - this.panFrom.y);
      this.panFrom = p;
      return;
    }
    if ((this.gesture === "left" || this.gesture === "right") && this.tool && this.doc) {
      const r = this.tool.move(this.doc, x, y, this.ctxFor(this.gesture, e));
      if (r) this.refresh(r);
      this.emitPick();
    }
    this.draw();
  }

  private up(e: PointerEvent, cancelled = false): void {
    this.pointers.delete(e.pointerId);
    if (this.gesture === "pinch") {
      if (this.pointers.size === 0) {
        this.gesture = null;
        this.pinch = null;
      }
      return;
    }
    if (this.gesture === "pan") {
      this.gesture = null;
      this.view.style.cursor = this.tool ? cursorFor(this.tool) : "crosshair";
      return;
    }
    if ((this.gesture !== "left" && this.gesture !== "right") || !this.tool || !this.doc) return;
    const button = this.gesture;
    this.gesture = null;
    if (cancelled) {
      const r = this.tool.cancel(this.doc);
      if (r) this.refresh(r);
      this.draw();
      return;
    }
    const p = this.local(e);
    const [x, y] = this.widgetToImage(p.x, p.y);
    this.releaseAt = [x, y];
    const cmd = this.tool.release(this.doc, x, y, this.ctxFor(button, e));
    if (cmd) {
      this.push(cmd);
      this.refresh(cmd.repaint());
    }
    this.draw();
  }

  private emitPick(): void {
    const t = this.tool as unknown as { picked?: RGBA | null; pickedButton?: "left" | "right" };
    if (t?.picked) this.onColorPicked(t.picked, t.pickedButton ?? "left");
  }
}
