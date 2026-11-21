// The timeline (port of gui/videoedit/timeline.py's painting and events): a canvas that paints the
// project and forwards pointer events to the maths in timelinemath.ts. Mouse and touch alike
// (pointer events with pointer capture); the page gets edits, seeks, selections and drops. Two
// fingers pinch to zoom and drag to scroll (pinchView / panView), dropping a clip drag they interrupt.
import { layout, type Project, type Source, type Span, type TextItem } from "../../videoedit/project";
import { addRow } from "../../videoedit/edits";
import { SOURCE_MIME } from "./media";
import { WAVE_H, thumbSlots, waveBars } from "./playmath";
import {
  DESKTOP, ZOOM_STEP, addRowRect, boundaryX, dragMoved, dragResult, ghost, hitTest, panView, pinchView, rowAt, rows,
  rulerTicks, selectionFor, spanRect, tFor, totalHeight, wheel, xFor, zoom, DEFAULT_PPS, follow as followScroll,
  type Drag, type Metrics, type Rect, type Row, type View,
} from "./timelinemath";

export interface TimelineEvents {
  edit(p: Project, label: string): void;
  seek(t: number): void;
  select(id: string | null): void;
  drop(sourceId: string, t: number, kind: Row["kind"], row: number): void;
}


const TOKENS = ["bg", "surface", "surface-alt", "border", "text", "muted", "accent", "accent-soft", "clip-video", "clip-picture", "clip-audio", "clip-text", "playhead"] as const;
type Palette = Record<(typeof TOKENS)[number], string>;

/** Qt's QColor.lightness() of a "#rrggbb" colour (0–255). */
function lightness(hex: string): number {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})/i.exec(hex.trim());
  if (!m) return 0;
  const [r, g, b] = [m[1], m[2], m[3]].map((x) => parseInt(x, 16));
  return (Math.max(r, g, b) + Math.min(r, g, b)) / 2;
}

function roundRect(ctx: CanvasRenderingContext2D, r: Rect, radius: number): void {
  const rad = Math.max(0, Math.min(radius, r.w / 2, r.h / 2));
  ctx.beginPath();
  ctx.moveTo(r.x + rad, r.y);
  ctx.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + r.h, rad);
  ctx.arcTo(r.x + r.w, r.y + r.h, r.x, r.y + r.h, rad);
  ctx.arcTo(r.x, r.y + r.h, r.x, r.y, rad);
  ctx.arcTo(r.x, r.y, r.x + r.w, r.y, rad);
  ctx.closePath();
}

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export class TimelineView {
  readonly el: HTMLCanvasElement;
  private project: Project | null = null;
  private spans = new Map<string, Span>();
  private sourceById = new Map<string, Source>();
  /** Scanned thumbnails and waveforms by source id (the scanner owns the bitmaps). */
  private thumbs = new Map<string, ImageBitmap[]>();
  private waves = new Map<string, Float32Array>();
  private rowList: Row[] = [];
  private items = new Map<string, Project["main"][number] | TextItem | Project["music_rows"][number][number]>();
  private pps = DEFAULT_PPS;
  private scroll = 0;
  private width = 0;
  private playhead = 0;
  private selected: string | null = null;
  private drag: Drag | null = null;
  private seeking = false;
  /** The pointer of the gesture in progress (captured), if any. */
  private pointer: number | null = null;
  /** Sizes: DESKTOP, or PHONE (bigger rows and handles; the page switches). */
  private m: Metrics = DESKTOP;
  /** The project's end (s), for two-finger panning. */
  private end = 0;
  /** Touch pointers down, in client coordinates. */
  private touches = new Map<number, { x: number; y: number }>();
  /** Two fingers down: where they started (canvas x of their centre, client y) and the view then. */
  private pinch: { dist: number; cx: number; cy: number; view: View } | null = null;
  /** Touch: what the finger picked, chosen when it lifts (undefined: nothing) — so a finger that
   *  becomes half of a pinch selects nothing (on phones a selection opens the inspector sheet). */
  private pendingChoice: string | null | undefined = undefined;

  constructor(private on: TimelineEvents) {
    const c = document.createElement("canvas");
    c.className = "ve-timeline";
    c.tabIndex = 0;
    c.setAttribute("aria-label", "Timeline");
    this.el = c;
    c.addEventListener("pointerdown", (e) => this.pointerDown(e));
    c.addEventListener("pointermove", (e) => this.pointerMove(e));
    c.addEventListener("pointerup", (e) => this.pointerUp(e));
    c.addEventListener("pointercancel", (e) => {
      this.liftTouch(e);
      this.cancel();
    });
    c.addEventListener("lostpointercapture", () => this.cancel());
    c.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    c.addEventListener("dragover", (e) => {
      if (!e.dataTransfer?.types.includes(SOURCE_MIME)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    });
    c.addEventListener("drop", (e) => this.onDrop(e));
    new ResizeObserver(() => this.resize()).observe(c);
    // the palette follows the theme
    new MutationObserver(() => this.draw()).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.draw());
  }

  // ---- state ------------------------------------------------------------------------------
  /** Show `p`; null clears the timeline (nothing painted, clicks do nothing). */
  setProject(p: Project | null): void {
    if (p === this.project) return;
    this.endGesture();
    this.project = p;
    this.spans = p ? layout(p) : new Map();
    this.rowList = p ? rows(p, this.m) : [];
    this.items = new Map();
    this.end = 0;
    for (const s of this.spans.values()) this.end = Math.max(this.end, s.end);
    this.sourceById = new Map(p ? p.sources.map((src) => [src.id, src]) : []);
    if (p) {
      for (const m of p.main) this.items.set(m.id, m);
      for (const r of p.text_rows) for (const t of r) this.items.set(t.id, t);
      for (const r of p.music_rows) for (const a of r) this.items.set(a.id, a);
    } else {
      this.selected = null;
      this.playhead = 0;
      this.el.style.cursor = "";
    }
    this.resize();
  }

  setThumbnails(id: string, images: ImageBitmap[]): void {
    if (this.thumbs.get(id) === images) return;
    this.thumbs.set(id, images);
    this.draw();
  }

  setWaveform(id: string, peaks: Float32Array): void {
    if (this.waves.get(id) === peaks) return;
    this.waves.set(id, peaks);
    this.draw();
  }

  /** Let go of a source's scanned pictures (before the scanner closes them). */
  forgetSource(id: string): void {
    const hadThumbs = this.thumbs.delete(id);
    const hadWave = this.waves.delete(id); // both go: `||` would skip this one
    if (hadThumbs || hadWave) this.draw();
  }

  /** Tests: what the timeline holds for a source. */
  holds(id: string): { thumbs: boolean; wave: boolean } {
    return { thumbs: this.thumbs.has(id), wave: this.waves.has(id) };
  }

  forgetAllSources(): void {
    if (!this.thumbs.size && !this.waves.size) return;
    this.thumbs.clear();
    this.waves.clear();
    this.draw();
  }

  /** Phone or desktop sizes (drops a gesture in progress). */
  setMetrics(m: Metrics): void {
    if (m === this.m) return;
    this.endGesture();
    this.m = m;
    this.rowList = this.project ? rows(this.project, m) : [];
    this.resize();
  }

  /** Drop a drag, ruler seek or pinch in progress (no edit, no more seeks) and release the pointer. */
  endGesture(): void {
    this.drag = null;
    this.seeking = false;
    this.pinch = null;
    this.pendingChoice = undefined;
    for (const id of this.touches.keys()) this.release(id);
    this.touches.clear();
    if (this.pointer !== null) {
      const id = this.pointer;
      this.pointer = null;
      this.release(id);
    }
    this.draw();
  }

  private release(id: number): void {
    try {
      if (this.el.hasPointerCapture?.(id)) this.el.releasePointerCapture(id);
    } catch {
      // the pointer is already gone
    }
  }

  private capture(id: number): void {
    try {
      this.el.setPointerCapture?.(id);
    } catch {
      // not an active pointer (a synthetic event): the canvas still gets its own events
    }
  }

  /** `follow` (playing): the view pages along when the playhead leaves it (timeline.set_playhead). */
  setPlayhead(t: number, follow = false): void {
    this.playhead = Math.max(0, t);
    if (follow) this.scroll = followScroll(this.view(), this.playhead);
    this.draw();
  }

  setSelected(id: string | null): void {
    if (id === this.selected) return;
    this.selected = id;
    this.draw();
  }

  zoomIn(): void {
    this.applyZoom(zoom(this.view(), ZOOM_STEP));
  }

  zoomOut(): void {
    this.applyZoom(zoom(this.view(), 1 / ZOOM_STEP));
  }

  view(): View {
    const box = this.el.parentElement;
    return {
      pps: this.pps, scroll: this.scroll, width: this.width, playhead: this.playhead, m: this.m,
      top: box?.scrollTop ?? 0, height: box?.clientHeight ?? 0, content: this.el.offsetHeight, length: this.end,
    };
  }

  /** Row geometry (tests). */
  rows(): Row[] {
    return this.rowList;
  }

  /** The items painted now (tests). */
  itemIds(): string[] {
    return [...this.items.keys()];
  }

  /** The label painted on an item (tests). */
  labelOf(id: string): string | null {
    const item = this.items.get(id);
    return item ? this.label(item) : null;
  }

  private applyZoom(z: { pps: number; scroll: number }): void {
    this.pps = z.pps;
    this.scroll = z.scroll;
    this.draw();
  }

  private resize(): void {
    const c = this.el;
    const h = this.project ? totalHeight(this.rowList) : this.m.ruler;
    c.style.height = `${h}px`;
    this.width = c.clientWidth;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(this.width * dpr);
    const hh = Math.round(h * dpr);
    if (c.width !== w || c.height !== hh) {
      c.width = w;
      c.height = hh;
    }
    this.draw();
  }

  // ---- events -----------------------------------------------------------------------------
  private pos(e: { clientX: number; clientY: number }): { x: number; y: number } {
    const r = this.el.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  private inside(r: Rect, x: number, y: number): boolean {
    return r.x <= x && x < r.x + r.w && r.y <= y && y < r.y + r.h;
  }

  private choose(id: string | null): void {
    if (id === this.selected) return;
    this.selected = id;
    this.draw();
    this.on.select(id);
  }

  private pointerDown(e: PointerEvent): void {
    if (e.button !== 0 || !this.project) return;
    if (e.pointerType === "touch") {
      this.touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (this.touches.size >= 2) {
        this.capture(e.pointerId);
        if (this.touches.size === 2) this.startPinch(); // a third finger changes nothing
        return;
      }
    }
    const p = this.project;
    const { x, y } = this.pos(e);
    for (const kind of ["text", "music"] as const) {
      if (this.inside(addRowRect(this.rowList, kind, this.m), x, y)) {
        this.on.edit(addRow(p, kind), "Add row");
        return;
      }
    }
    this.capture(e.pointerId);
    this.pointer = e.pointerId;
    if (y < this.m.ruler) {
      this.seeking = true;
      this.on.seek(Math.max(0, tFor(this.view(), x)));
      return;
    }
    const hit = hitTest(this.view(), p, this.spans, this.rowList, x, y);
    if (e.pointerType === "touch") this.pendingChoice = selectionFor(hit);
    else this.choose(selectionFor(hit));
    if (hit === null) {
      if (x >= this.m.header) this.on.seek(Math.max(0, tFor(this.view(), x)));
      return;
    }
    if (hit.part === "transition" || this.project !== p) return;
    this.drag = { id: hit.itemId, part: hit.part, span: this.spans.get(hit.itemId)!, ox: x, oy: y, x, y, moved: false, alt: e.altKey };
  }

  private pointerMove(e: PointerEvent): void {
    if (!this.project) return;
    const finger = this.touches.get(e.pointerId);
    if (finger) {
      finger.x = e.clientX;
      finger.y = e.clientY;
    }
    if (this.pinch) {
      if (finger && this.touches.size >= 2) this.movePinch();
      return;
    }
    const { x, y } = this.pos(e);
    if (this.seeking) {
      this.on.seek(Math.max(0, tFor(this.view(), x)));
      return;
    }
    const d = this.drag;
    if (!d) {
      const hit = this.project ? hitTest(this.view(), this.project, this.spans, this.rowList, x, y) : null;
      this.el.style.cursor = hit && (hit.part === "left" || hit.part === "right") ? "ew-resize" : hit?.part === "transition" ? "pointer" : "";
      return;
    }
    d.x = x;
    d.y = y;
    d.alt = e.altKey;
    if (!d.moved && dragMoved(d)) d.moved = true;
    this.draw();
  }

  private pointerUp(e: PointerEvent): void {
    if (e.button !== 0) return;
    if (this.liftTouch(e)) return;
    if (e.pointerId === this.pointer) this.pointer = null;
    this.seeking = false;
    const d = this.drag;
    this.drag = null;
    const picked = this.pendingChoice;
    this.pendingChoice = undefined;
    if (picked !== undefined) this.choose(picked);
    this.draw();
    if (!d || !d.moved || !this.project) return;
    const { x, y } = this.pos(e);
    d.x = x;
    d.y = y;
    d.alt = e.altKey;
    const result = dragResult(this.view(), this.project, this.spans, this.rowList, d);
    if (result) this.on.edit(result.project, result.label);
  }

  /** A second finger: whatever the first one started (drag, seek) is dropped, no edit. */
  private startPinch(): void {
    this.drag = null;
    this.seeking = false;
    this.pendingChoice = undefined;
    this.pointer = null; // the first finger's capture stays: its moves feed the pinch
    const [a, b] = [...this.touches.values()];
    const r = this.el.getBoundingClientRect();
    this.pinch = { dist: Math.hypot(b.x - a.x, b.y - a.y), cx: (a.x + b.x) / 2 - r.left, cy: (a.y + b.y) / 2, view: this.view() };
    this.draw();
  }

  private movePinch(): void {
    const start = this.pinch!;
    const [a, b] = [...this.touches.values()];
    const r = this.el.getBoundingClientRect();
    const cx = (a.x + b.x) / 2 - r.left;
    const cy = (a.y + b.y) / 2;
    const zoomed = pinchView(this.view(), start.dist, Math.hypot(b.x - a.x, b.y - a.y), start.cx, start.view);
    const v = panView({ ...zoomed, top: start.view.top }, cx - start.cx, cy - start.cy);
    this.pps = v.pps;
    this.scroll = v.scroll;
    const box = this.el.parentElement;
    if (box && v.top !== undefined) box.scrollTop = v.top;
    this.draw();
  }

  /** A finger lifted (or cancelled). True while a pinch had it: that gesture makes no edit, and
   *  the finger left behind does nothing until every finger is up. */
  private liftTouch(e: PointerEvent): boolean {
    if (!this.touches.delete(e.pointerId)) return false;
    if (!this.pinch) return false;
    // `pinch` stays until the last finger is up, so the one left can't start a drag
    if (this.touches.size === 0) this.pinch = null;
    return true;
  }

  private cancel(): void {
    this.pointer = null;
    this.pendingChoice = undefined;
    this.seeking = false;
    if (this.drag) {
      this.drag = null;
      this.draw();
    }
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    const z = wheel(this.view(), e.deltaY || e.deltaX, e.ctrlKey || e.metaKey, this.pos(e).x);
    this.applyZoom(z);
  }

  private onDrop(e: DragEvent): void {
    const id = e.dataTransfer?.getData(SOURCE_MIME);
    if (!id) return;
    e.preventDefault();
    const { x, y } = this.pos(e);
    const row = rowAt(this.rowList, y);
    this.on.drop(id, Math.max(0, tFor(this.view(), x)), row ? row.kind : "main", row ? row.row : 0);
  }

  // ---- painting ---------------------------------------------------------------------------
  private palette(): Palette {
    const cs = getComputedStyle(this.el);
    return Object.fromEntries(TOKENS.map((t) => [t, cs.getPropertyValue(`--${t}`).trim() || "#888888"])) as Palette;
  }

  private colour(item: { type: string }, pal: Palette): string {
    if (item.type === "picture") return pal["clip-picture"];
    if (item.type === "audio") return pal["clip-audio"];
    if (item.type === "text") return pal["clip-text"];
    return pal["clip-video"];
  }

  private label(item: { type: string; text?: string; source?: string }): string {
    if (item.type === "text") return (item as TextItem).text.replace(/\n/g, " ");
    const src = this.project?.sources.find((s) => s.id === item.source);
    return src ? baseName(src.path) : "";
  }

  draw(): void {
    const ctx = this.el.getContext("2d");
    if (!ctx || !this.el.width) return;
    const dpr = this.el.width / Math.max(1, this.width);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const pal = this.palette();
    const W = this.width;
    const H = this.el.height / dpr;
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, W, H);
    const p = this.project;
    if (!p) return;
    const font = getComputedStyle(document.body).fontFamily || "sans-serif";
    this.rowList.forEach((r, i) => {
      ctx.fillStyle = i % 2 ? pal.surface : pal["surface-alt"];
      ctx.fillRect(0, r.y, W, r.h - 1);
    });
    ctx.save();
    ctx.beginPath();
    ctx.rect(this.m.header, 0, W - this.m.header, H);
    ctx.clip();
    this.paintRuler(ctx, pal, font);
    this.paintItems(ctx, pal, font, p);
    this.paintGhost(ctx, pal, p);
    const x = xFor(this.view(), this.playhead);
    ctx.strokeStyle = pal.playhead;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, H);
    ctx.stroke();
    ctx.restore();
    this.paintHeader(ctx, pal, font, p, H);
  }

  private paintRuler(ctx: CanvasRenderingContext2D, pal: Palette, font: string): void {
    ctx.fillStyle = pal.bg;
    ctx.fillRect(this.m.header, 0, this.width, this.m.ruler);
    ctx.font = `10px ${font}`;
    ctx.textBaseline = "alphabetic";
    ctx.lineWidth = 1;
    for (const tick of rulerTicks(this.view())) {
      ctx.strokeStyle = pal.border;
      ctx.beginPath();
      ctx.moveTo(tick.x + 0.5, this.m.ruler - 8);
      ctx.lineTo(tick.x + 0.5, this.m.ruler);
      ctx.stroke();
      ctx.fillStyle = pal.muted;
      ctx.fillText(tick.label, tick.x + 3, 12);
    }
  }

  private paintItems(ctx: CanvasRenderingContext2D, pal: Palette, font: string, p: Project): void {
    const v = this.view();
    for (const [id, span] of this.spans) {
      const rect = spanRect(v, this.rowList, span);
      if (rect.x + rect.w < this.m.header || rect.x > this.width) continue; // off screen
      this.paintClip(ctx, pal, font, this.items.get(id)!, rect, id === this.selected);
    }
    const mainY = this.rowList.find((r) => r.kind === "main")!.y;
    p.main.forEach((m, i) => {
      const bx = boundaryX(v, p, this.spans, i);
      if (bx === null) return;
      const sel = this.selected === `tr:${m.id}`;
      const r = { x: bx - this.m.handle / 2, y: mainY + 2, w: this.m.handle, h: this.m.handleH - 4 };
      ctx.fillStyle = m.transition_in.kind !== "none" || sel ? pal.accent : pal.surface;
      ctx.strokeStyle = sel ? pal.accent : pal.border;
      ctx.lineWidth = 1;
      roundRect(ctx, r, 3);
      ctx.fill();
      ctx.stroke();
    });
  }

  private paintClip(ctx: CanvasRenderingContext2D, pal: Palette, font: string, item: { type: string }, rect: Rect, selected: boolean): void {
    const fill = this.colour(item, pal);
    ctx.fillStyle = fill;
    roundRect(ctx, rect, 5);
    ctx.fill();
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x, rect.y, rect.w, rect.h);
    ctx.clip();
    this.paintScans(ctx, item, rect);
    ctx.font = `11px ${font}`;
    ctx.textBaseline = "top";
    ctx.fillStyle = lightness(fill) > 150 ? "#101114" : "#ffffff";
    ctx.fillText(this.label(item), rect.x + 6, rect.y + 3);
    ctx.restore();
    if (selected) {
      ctx.strokeStyle = pal.text;
      ctx.lineWidth = 2;
      roundRect(ctx, rect, 5);
      ctx.stroke();
    }
  }

  /** Thumbnails (video / picture clips) and the waveform (video / music clips) of a clip. */
  private paintScans(ctx: CanvasRenderingContext2D, item: { type: string }, rect: Rect): void {
    if (item.type === "text") return;
    const clip = item as { type: string; source: string; in_?: number; out?: number };
    const src = this.sourceById.get(clip.source);
    if (!src) return;
    const images = this.thumbs.get(src.id);
    if (images?.length && item.type !== "audio") {
      const first = images[0];
      const aspect = first.width / Math.max(1, first.height);
      for (const slot of thumbSlots(rect, aspect, images.length, this.m.header, this.width)) {
        ctx.drawImage(images[slot.index], slot.rect.x, slot.rect.y, slot.rect.w, slot.rect.h);
      }
    }
    const peaks = this.waves.get(src.id);
    if (peaks && item.type !== "picture" && clip.in_ !== undefined && clip.out !== undefined) {
      const waveH = item.type === "video" ? WAVE_H : rect.h - 12;
      const wave = { x: rect.x, y: rect.y + rect.h - waveH, w: rect.w, h: waveH };
      const mid = Math.round(wave.y + wave.h / 2);
      ctx.fillStyle = "rgba(255,255,255,0.67)";
      for (const bar of waveBars(wave, peaks, clip.in_, clip.out, src.duration, this.m.header, this.width)) {
        ctx.fillRect(bar.x, mid - bar.half, 1, bar.half * 2 + 1);
      }
    }
  }

  private paintGhost(ctx: CanvasRenderingContext2D, pal: Palette, p: Project): void {
    const d = this.drag;
    if (!d || !d.moved) return;
    const g = ghost(this.view(), p, this.spans, this.rowList, d);
    if (g.insertX !== null) {
      ctx.strokeStyle = pal.accent;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(g.insertX, g.rect.y);
      ctx.lineTo(g.insertX, g.rect.y + g.rect.h);
      ctx.stroke();
    }
    ctx.save();
    ctx.globalAlpha = 0.55;
    ctx.fillStyle = pal["accent-soft"];
    ctx.strokeStyle = pal.text;
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    roundRect(ctx, g.rect, 5);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  private paintHeader(ctx: CanvasRenderingContext2D, pal: Palette, font: string, p: Project, H: number): void {
    ctx.fillStyle = pal.bg;
    ctx.fillRect(0, 0, this.m.header, H);
    ctx.font = `11px ${font}`;
    ctx.textBaseline = "middle";
    const counts: Record<string, number> = { text: p.text_rows.length, main: 1, music: p.music_rows.length };
    for (const r of this.rowList) {
      const name = { text: "Text", main: "Video", music: "Music" }[r.kind];
      ctx.fillStyle = pal.muted;
      ctx.fillText(counts[r.kind] > 1 ? `${name} ${r.row + 1}` : name, 8, r.y + r.h / 2, this.m.header - 32);
    }
    for (const kind of ["text", "music"] as const) {
      const r = addRowRect(this.rowList, kind, this.m);
      ctx.fillStyle = pal.surface;
      ctx.strokeStyle = pal.border;
      ctx.lineWidth = 1;
      roundRect(ctx, { x: r.x + 0.5, y: r.y + 0.5, w: r.w, h: r.h }, 4);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = pal.text;
      ctx.textAlign = "center";
      ctx.fillText("+", r.x + r.w / 2 + 0.5, r.y + r.h / 2 + 0.5);
      ctx.textAlign = "start";
    }
  }
}
