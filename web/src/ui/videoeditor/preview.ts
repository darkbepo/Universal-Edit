// The video editor's still preview (port of the desktop's gui/videoedit/preview_view.py screen):
// the frame under the playhead, made from the sources the page decodes (sources.ts) and drawn
// with draw.ts. Requests go through a StillQueue, so scrubbing makes only the newest still and a
// late decode never paints. Each still is drawn on an offscreen canvas first and then copied, so
// a half-drawn frame never shows. The transport row is laid out now; playback comes later.
import { frameAt, type Layer } from "../../videoedit/compose";
import { outputSize, type Project } from "../../videoedit/project";
import { textAt } from "../../videoedit/textlayout";
import { drawFrame, drawText, type Picture, type Surfaces } from "./draw";
import { hasFont, loadBundledFonts } from "./fonts";
import type { SourceBank } from "./sources";
import { StillQueue } from "./stillqueue";
import { formatTime } from "./timelinemath";

/** A dark "window" behind the frame in both themes (the desktop's SCREEN_BG). */
export const SCREEN_BG = "#0b0c0f";
/** The still is made at most this many device pixels high (enough for a sharp preview). */
export const MAX_PREVIEW_H = 720;

const even = (v: number) => Math.max(2, Math.floor(v / 2) * 2);

/** The canvas size for a frame of `out` shown in `box` (CSS px): the output's aspect fitted in
 *  the box, in device pixels, at most MAX_PREVIEW_H high, both sides even and ≥ 2. */
export function previewSize(box: { width: number; height: number }, dpr: number, out: [number, number]): [number, number] {
  const [ow, oh] = out;
  const scale = Math.min((box.width * dpr) / ow, (box.height * dpr) / oh);
  let h = Math.min(oh * scale, MAX_PREVIEW_H);
  if (!(h >= 2) || !(ow * scale >= 2)) return [2, 2]; // a box too small to show anything
  h = even(h);
  return [even(Math.round((h * ow) / oh / 2) * 2), h];
}

interface Job { p: Project; t: number; size: [number, number] }

const surfaces: Surfaces = {
  make(w, h) {
    const canvas = new OffscreenCanvas(w, h);
    return { canvas, ctx: canvas.getContext("2d", { willReadFrequently: true })! };
  },
};

let fontsReady = false;

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export class Preview {
  readonly el: HTMLElement;
  private screen = h("div", { class: "ve-screen" });
  private canvas = h("canvas", { class: "ve-still" });
  private time = h("span", { class: "ve-time" });
  private queue: StillQueue<Job>;
  private last: { p: Project; t: number } | null = null;
  private resize: ResizeObserver | null = null;

  /** `bank` gives the page's current SourceBank (the page makes a new one per project). */
  constructor(private bank: () => SourceBank, on: { step(n: number): void }) {
    this.queue = new StillQueue((job, isCurrent) => this.still(job, isCurrent));
    this.canvas.width = 2;
    this.canvas.height = 2;
    this.canvas.hidden = true;
    this.screen.style.background = SCREEN_BG;
    this.screen.append(this.canvas);
    const button = (label: string, title: string, fn: (() => void) | null) => {
      const b = h("button", { class: "btn small", type: "button", title }, label);
      if (fn) b.addEventListener("click", fn);
      else b.disabled = true;
      return b;
    };
    const transport = h("div", { class: "ve-transport" },
      button("⏮", "Previous frame (←)", () => on.step(-1)),
      button("▶", "Playback comes soon", null),
      button("⏭", "Next frame (→)", () => on.step(1)),
      this.time,
      button("Loop", "Playback comes soon", null));
    this.el = h("div", { class: "ve-preview" }, this.screen, transport);
    this.setTime(0, 0);
    if (typeof ResizeObserver !== "undefined") {
      this.resize = new ResizeObserver(() => {
        if (this.last) this.show(this.last.p, this.last.t);
      });
      this.resize.observe(this.screen);
    }
  }

  /** Show the frame of `p` at `t` (made in the background; the newest call wins). */
  show(p: Project, t: number): void {
    this.last = { p, t };
    const box = this.screen.getBoundingClientRect();
    if (box.width < 1 || box.height < 1) return; // not laid out (hidden): the resize brings it
    const out = outputSize(p);
    const size = previewSize(box, window.devicePixelRatio || 1, out);
    // shown at the fitted size in CSS px (the backing store may be smaller: the 720 px cap)
    const scale = Math.min(box.width / out[0], box.height / out[1]);
    this.canvas.style.width = `${Math.max(1, Math.floor(out[0] * scale))}px`;
    this.canvas.style.height = `${Math.max(1, Math.floor(out[1] * scale))}px`;
    this.queue.request({ p, t, size });
  }

  /** Nothing to show (no project, left, new files opening): no stale frame may paint. */
  clear(): void {
    this.last = null;
    this.queue.clear();
    this.canvas.hidden = true;
    this.setTime(0, 0);
  }

  setTime(t: number, total: number): void {
    this.time.textContent = `${formatTime(t)} / ${formatTime(total)}`;
  }

  idle(): Promise<void> {
    return this.queue.idle();
  }

  /** The shown still's pixel at fractions (0…1) of its width / height; null when none shows. */
  pixel(fx: number, fy: number): [number, number, number, number] | null {
    if (this.canvas.hidden) return null;
    const x = Math.min(this.canvas.width - 1, Math.max(0, Math.floor(fx * this.canvas.width)));
    const y = Math.min(this.canvas.height - 1, Math.max(0, Math.floor(fy * this.canvas.height)));
    const d = this.canvas.getContext("2d")!.getImageData(x, y, 1, 1).data;
    return [d[0], d[1], d[2], d[3]];
  }

  private async still({ p, t, size }: Job, isCurrent: () => boolean): Promise<void> {
    const frame = frameAt(p, t, size);
    let boxes = textAt(p, t, size, hasFont);
    if (boxes.length && !fontsReady) {
      await loadBundledFonts().catch(() => undefined);
      fontsReady = true;
      if (!isCurrent()) return;
      boxes = textAt(p, t, size, hasFont);
    }
    const bank = this.bank();
    const pictures = await Promise.all(frame.layers.map((pl) => bank.picture(pl.layer.sourceId, pl.layer.time, size[1])));
    if (!isCurrent()) return;
    const byLayer = new Map<Layer, Picture | null>(frame.layers.map((pl, i) => [pl.layer, pictures[i]]));
    const off = surfaces.make(size[0], size[1]);
    drawFrame(off.ctx, frame, (l) => byLayer.get(l) ?? null, surfaces);
    drawText(off.ctx, boxes, size[1] / outputSize(p)[1]);
    if (!isCurrent()) return;
    if (this.canvas.width !== size[0]) this.canvas.width = size[0];
    if (this.canvas.height !== size[1]) this.canvas.height = size[1];
    const ctx = this.canvas.getContext("2d")!;
    ctx.drawImage(off.canvas, 0, 0);
    this.canvas.hidden = false;
  }
}
