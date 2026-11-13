// The audio editor page (port of the desktop's gui/audioeditor/), laid out like a simple Audacity:
// top bar (open, undo, save), a tool row (play / stop, cut / copy / paste, effects, zoom), the
// waveform with a time ruler, and a status line with the selection's start, end and length.
// Files are decoded in the conversion worker (FFmpeg → float WAV, hidden "audio-edit-open"
// converter); the samples stay in the page; saving goes through the normal audio converter.
// Playback uses Web Audio. Loaded on demand from ui/app.ts.
import { AudioDoc } from "../../audioedit/document";
import * as ops from "../../audioedit/ops";
import type { Samples } from "../../audioedit/ops";
import * as peaks from "../../audioedit/peaks";
import { DEFAULT_VOLUME, gainFor, VOLUME_KEY } from "../../audioedit/volume";
import { readWav, writeWav } from "../../audioedit/wav";
import { MAX_SAMPLES, OPEN_TARGET, TOO_LONG } from "../../converters/audioedit";
import { defaults } from "../../core/options";
import { converterById } from "../../core/registry";
import type { InputFile } from "../../core/types";
import { JobCancelled, type ConversionClient } from "../../worker/client";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export const HINT = "Click to place the cursor · drag to select · Ctrl+wheel to zoom · Space plays";
const EMPTY_TEXT = "Open a sound with “Open…”, or drop audio files here (MP3, WAV, FLAC, M4A, OGG, …).";
const AUDIO_ACCEPT = "audio/*,.mp3,.wav,.flac,.m4a,.aac,.ogg,.oga,.opus,.wma,.aiff,.aif,.ac3,.amr,.ape,.mka";
const RULER_H = 24;
const LANE_GAP = 6;
const MIN_FPP = 1 / 32; // most zoomed in: 32 px per sample
const SCROLL_STEPS = 1000; // the scroll bar works in 1/1000 of a screen
const TICK_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const BITRATES = [64, 96, 128, 160, 192, 256, 320];
const LOSSY = new Set(["mp3", "m4a", "ogg", "opus", "wma", "ac3"]);
export const NO_AUDIO_OUTPUT = "This browser can't play sound here.";

type Action = "cut" | "copy" | "paste" | "delete" | "trim" | "silence" | "fade_in" | "fade_out" | "amplify" | "normalize" | "reverse" | "zoom_in" | "zoom_out" | "zoom_sel" | "fit" | "select_all" | "deselect";

const TOOL_GROUPS: [Action, string, string][][] = [
  [
    ["cut", "Cut", "Cut the selection (Ctrl+X)"],
    ["copy", "Copy", "Copy the selection (Ctrl+C)"],
    ["paste", "Paste", "Paste at the cursor, or over the selection (Ctrl+V)"],
    ["delete", "Delete", "Delete the selection (Delete)"],
    ["trim", "Trim", "Keep only the selection (Ctrl+T)"],
    ["silence", "Silence", "Make the selection silent (Ctrl+L)"],
  ],
  [
    ["fade_in", "Fade in", "Fade the selection in"],
    ["fade_out", "Fade out", "Fade the selection out"],
    ["amplify", "Amplify…", "Make the selection (or everything) louder or quieter"],
    ["normalize", "Normalize", "Make the loudest peak −1 dB (selection or everything)"],
    ["reverse", "Reverse", "Play the selection (or everything) backwards"],
  ],
  [
    ["zoom_in", "Zoom in", "Zoom in (+, or Ctrl+wheel)"],
    ["zoom_out", "Zoom out", "Zoom out (−)"],
    ["zoom_sel", "Selection", "Zoom to the selection (Ctrl+E)"],
    ["fit", "Fit", "Show the whole sound (Ctrl+F)"],
  ],
];

export interface AudioEditorOptions {
  client: ConversionClient;
  onBack: () => void;
}

interface FileHandle { createWritable(): Promise<{ write(b: Blob): Promise<void>; close(): Promise<void> }> }

const cssColor = (name: string, fallback: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

export class AudioEditorPage {
  readonly el: HTMLElement;
  doc: AudioDoc | null = null;
  sources: string[] = [];
  clipboard: Samples | null = null;
  start = 0; // frame at the left edge
  fpp = 1; // frames per pixel
  playHead: number | null = null;
  busy = false;
  private summary: peaks.Peaks | null = null;
  private summaryRev = -1;
  private canvas = h("canvas", { class: "audio-canvas", tabIndex: 0, ariaLabel: "Waveform" });
  private host = h("div", { class: "audio-wave" });
  private placeholder = h("div", { class: "audio-placeholder" }, EMPTY_TEXT);
  private scroll = h("input", { type: "range", class: "audio-scroll", min: "0", max: "0", value: "0", ariaLabel: "Scroll" });
  private message = h("div", { class: "msg" });
  private selectionLabel = h("div", { class: "audio-selection" });
  private infoLabel = h("div", { class: "audio-info" });
  private title = h("span", { class: "muted" });
  private buttons = new Map<string, HTMLButtonElement>();
  private anchor: number | null = null;
  private fitted = true;
  private frame = 0;
  private active = false;
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  volume = DEFAULT_VOLUME; // the slider, 0…1
  private volumeSlider = h("input", { type: "range", class: "audio-volume", min: "0", max: "100", step: "1", ariaLabel: "Volume" });
  private volumeLabel = h("span", { class: "audio-volume-value muted" });
  private player: { src: AudioBufferSourceNode; origin: number; end: number; t0: number; paused: boolean } | null = null;
  private saveTarget: { name: string; format: string; bitrate: number; handle?: FileHandle } | null = null;
  private bitrate = 192;

  constructor(private opts: AudioEditorOptions) {
    const btn = (key: string, label: string, title: string, fn: () => void, cls = "btn small") => {
      const b = h("button", { class: cls, title, type: "button" }, label);
      b.addEventListener("click", fn);
      this.buttons.set(key, b);
      return b;
    };
    const picker = h("input", { type: "file", multiple: true, accept: AUDIO_ACCEPT, hidden: true });
    picker.addEventListener("change", () => {
      if (picker.files?.length) void this.openFiles(Array.from(picker.files));
      picker.value = "";
    });
    const top = h("div", { class: "editor-top" },
      btn("back", "← Back to Convert", "Leave the audio editor", () => this.back(), "link"),
      h("span", { class: "gap" }),
      btn("open", "Open…", "Open audio files (several are joined)", () => picker.click()), picker,
      btn("undo", "Undo", "Undo (Ctrl+Z)", () => this.undo()),
      btn("redo", "Redo", "Redo (Ctrl+Y)", () => this.redo()),
      h("span", { class: "gap" }), this.title,
      h("div", { class: "spacer" }),
      btn("save_as", "Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs()),
      btn("save", "Save", "Save (Ctrl+S)", () => void this.save(), "primary small"));
    const transport = h("div", { class: "audio-group" },
      btn("home", "|◀", "Go to the start (Home)", () => this.goTo(0)),
      btn("play", "▶ Play", "Play the selection, or from the cursor (Space)", () => this.togglePlay()),
      btn("stop", "■ Stop", "Stop playing", () => this.stop()),
      btn("end", "▶|", "Go to the end (End)", () => this.goTo(this.doc?.frames ?? 0)),
      h("label", { class: "audio-volume-box" }, h("span", { class: "muted" }, "Volume"), this.volumeSlider, this.volumeLabel));
    const groups = TOOL_GROUPS.map((items) => h("div", { class: "audio-group" },
      ...items.map(([key, label, title]) => btn(key, label, title, () => void this.run(key)))));
    const tools = h("div", { class: "audio-tools", role: "toolbar", ariaLabel: "Audio tools" }, transport, ...groups);
    this.host.append(this.canvas);
    this.el = h("div", { class: "editor audio-editor", hidden: true },
      top,
      h("div", { class: "audio-body" }, tools, h("div", { class: "audio-view" }, this.host, this.placeholder), this.scroll),
      h("div", { class: "editor-status" }, this.selectionLabel, this.message, this.infoLabel));

    let saved = NaN;
    try {
      saved = Number(localStorage.getItem(VOLUME_KEY) ?? NaN);
    } catch {
      /* storage blocked: the default */
    }
    this.volumeSlider.value = String(Number.isFinite(saved) ? Math.min(100, Math.max(0, Math.round(saved))) : Math.round(DEFAULT_VOLUME * 100));
    this.showVolume();
    this.volumeSlider.addEventListener("input", () => {
      this.showVolume();
      try {
        localStorage.setItem(VOLUME_KEY, this.volumeSlider.value);
      } catch {
        /* fine: just not remembered */
      }
    });
    this.scroll.addEventListener("input", () => this.setStart((+this.scroll.value * this.visibleFrames()) / SCROLL_STEPS));
    this.installMouse();
    // On the document, not the page: a button that disables itself (Cut) drops the focus to <body>.
    document.addEventListener("keydown", (e) => {
      if (this.active && !this.el.hidden) this.onKey(e);
    });
    this.el.addEventListener("dragover", (e) => {
      if (e.dataTransfer?.types.includes("Files")) e.preventDefault();
    });
    this.el.addEventListener("drop", (e) => {
      if (!e.dataTransfer?.files.length) return;
      e.preventDefault();
      e.stopPropagation();
      void this.openFiles(Array.from(e.dataTransfer.files));
    });
    new ResizeObserver(() => this.onResize()).observe(this.host);
    if (location.search.includes("e2e")) (window as unknown as { __audio: AudioEditorPage }).__audio = this;
    this.update();
  }

  // ---------------------------------------------------------------- visibility
  show(): void {
    this.active = true;
    this.el.hidden = false;
    requestAnimationFrame(() => {
      this.onResize();
      this.canvas.focus();
    });
  }

  hide(): void {
    this.stop();
    this.active = false;
    this.el.hidden = true;
  }

  lastError(): string {
    return this.message.classList.contains("error") ? this.message.textContent ?? "" : "";
  }

  get dirty(): boolean {
    return !!this.doc?.dirty;
  }

  maybeLeave(): boolean {
    this.stop();
    if (!this.dirty) return true;
    return window.confirm("Discard unsaved changes to this sound?");
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  showMessage(text: string, error = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", error);
  }

  // ---------------------------------------------------------------- opening
  openEmpty(): void {
    this.stop();
    this.setDocument(null, []);
    this.showMessage("");
  }

  /** Files picked or dropped on the page: the audio ones replace the sound (asks first). */
  async openFiles(list: File[]): Promise<void> {
    const { detect } = await import("../../core/detect");
    const found = (await Promise.all(list.map((f) => detect(f)))).filter((f): f is InputFile => f?.family === "audio");
    if (!found.length) {
      this.showMessage("Only audio files can be opened here.", true);
      return;
    }
    if (this.maybeLeave()) await this.open(found);
  }

  /** Decode (and join) `files`. False if it failed (lastError() says why). */
  async open(files: InputFile[]): Promise<boolean> {
    if (this.busy || !files.length) return false;
    this.stop();
    this.busy = true;
    const name = files.length === 1 ? `“${files[0].name}”` : `${files.length} files`;
    this.placeholder.textContent = `Opening ${name}…`;
    this.placeholder.hidden = false;
    this.host.hidden = true;
    this.showMessage("");
    this.update();
    try {
      const outs = await this.opts.client.run(OPEN_TARGET, files, {}, (p, m) => this.showMessage(`${m || "Opening…"} ${Math.round(p * 100)} %`));
      const parts = outs.map((o) => readWav(o.data));
      const total = parts.reduce((n, p) => n + p.samples.length * ops.frames(p.samples), 0);
      if (total > MAX_SAMPLES) throw new Error(TOO_LONG);
      const joined = parts.length > 1 ? ops.join(parts.map((p) => ops.fitChannels(p.samples, parts[0].samples.length))) : parts[0].samples;
      this.setDocument(new AudioDoc(parts[0].rate, joined), files.map((f) => f.name));
      this.showMessage("");
      return true;
    } catch (e) {
      if (!(e instanceof JobCancelled)) {
        console.error(e);
        this.showMessage(e instanceof Error && e.message ? e.message : `Couldn't open ${name}.`, true);
      }
      this.setDocument(this.doc, this.sources);
      return false;
    } finally {
      this.busy = false;
      this.update();
    }
  }

  private setDocument(doc: AudioDoc | null, sources: string[]): void {
    if (doc !== this.doc) this.saveTarget = null;
    this.doc = doc;
    this.sources = sources;
    this.summary = null;
    this.playHead = null;
    this.placeholder.textContent = EMPTY_TEXT;
    this.placeholder.hidden = !!doc;
    this.host.hidden = !doc;
    this.fit();
  }

  // ---------------------------------------------------------------- view
  private width(): number {
    return Math.max(1, this.host.clientWidth);
  }

  get frames(): number {
    return this.doc?.frames ?? 0;
  }

  private maxFpp(): number {
    return Math.max(MIN_FPP, this.frames / this.width());
  }

  visibleFrames(): number {
    return this.fpp * this.width();
  }

  private clamp(): void {
    this.fpp = Math.max(MIN_FPP, Math.min(this.fpp, this.maxFpp()));
    this.start = Math.max(0, Math.min(this.start, this.frames - this.visibleFrames()));
  }

  fit(): void {
    this.fitted = true;
    this.fpp = this.maxFpp();
    this.start = 0;
    this.changed();
  }

  zoom(factor: number, atX?: number): void {
    if (!this.doc) return;
    if (atX === undefined) {
      const x = this.xOf(this.doc.selection[0]);
      atX = x >= 0 && x <= this.width() ? x : this.width() / 2;
    }
    const frame = this.start + atX * this.fpp;
    this.fitted = false;
    this.fpp /= factor;
    this.clamp();
    this.start = frame - atX * this.fpp;
    this.changed();
  }

  zoomTo(a: number, b: number): void {
    if (b <= a) return;
    this.fitted = false;
    this.fpp = ((b - a) / this.width()) * 1.05;
    this.clamp();
    this.start = (a + b) / 2 - this.visibleFrames() / 2;
    this.changed();
  }

  setStart(frame: number): void {
    this.start = frame;
    this.changed();
  }

  private ensureVisible(frame: number): void {
    if (frame < this.start || frame > this.start + this.visibleFrames()) this.setStart(frame - this.visibleFrames() * 0.05);
  }

  private changed(): void {
    this.clamp();
    const total = this.frames;
    const page = this.visibleFrames();
    if (total <= page) {
      this.scroll.max = "0";
      this.scroll.disabled = true;
    } else {
      const unit = page / SCROLL_STEPS;
      this.scroll.disabled = false;
      this.scroll.max = String(Math.floor((total - page) / unit));
      this.scroll.value = String(Math.floor(this.start / unit));
    }
    this.requestDraw();
  }

  private onResize(): void {
    if (this.fitted) this.fpp = this.maxFpp(); // the whole sound stays in view
    this.changed();
  }

  xOf(frame: number): number {
    return (frame - this.start) / this.fpp;
  }

  frameAt(x: number): number {
    return Math.max(0, Math.min(this.frames, Math.round(this.start + x * this.fpp)));
  }

  // ---------------------------------------------------------------- mouse
  private installMouse(): void {
    const c = this.canvas;
    const x = (e: PointerEvent | MouseEvent | WheelEvent) => e.clientX - c.getBoundingClientRect().left;
    c.addEventListener("pointerdown", (e) => {
      if (!this.doc || e.button !== 0 || this.busy) return;
      c.setPointerCapture(e.pointerId);
      c.focus();
      const f = this.frameAt(x(e));
      if (e.shiftKey) {
        const [a, b] = this.doc.selection;
        this.anchor = Math.abs(f - a) < Math.abs(f - b) ? b : a;
        this.doc.setSelection(this.anchor, f);
      } else {
        this.anchor = f;
        this.doc.setSelection(f);
        if (this.player) this.play(f, this.doc.frames);
      }
      this.update();
    });
    c.addEventListener("pointermove", (e) => {
      if (!this.doc || this.anchor === null) return;
      const px = x(e);
      if (px < 0 || px > this.width()) this.setStart(this.start + (px < 0 ? px : px - this.width()) * this.fpp);
      this.doc.setSelection(this.anchor, this.frameAt(px));
      this.update();
    });
    const end = () => (this.anchor = null);
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", end);
    c.addEventListener("dblclick", () => {
      this.doc?.selectAll();
      this.update();
    });
    c.addEventListener("wheel", (e) => {
      if (!this.doc) return;
      e.preventDefault();
      const delta = e.deltaY || e.deltaX;
      if (e.ctrlKey || e.metaKey) this.zoom(2 ** (-delta / 240), x(e));
      else this.setStart(this.start + (delta / 100) * (this.visibleFrames() / 10));
    }, { passive: false });
  }

  private onKey(e: KeyboardEvent): void {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "INPUT" && (e.target as HTMLInputElement).type !== "range") return;
    if (tag === "SELECT" || (e.target as HTMLElement).closest("dialog")) return;
    // Home / End move the volume slider itself while it has the focus
    if (e.target === this.volumeSlider && (e.key === "Home" || e.key === "End")) return;
    const ctrl = e.ctrlKey || e.metaKey;
    const k = e.key.toLowerCase();
    let handled = true;
    if (ctrl && k === "s") void (e.shiftKey ? this.saveAs() : this.save());
    else if (ctrl && (k === "y" || (k === "z" && e.shiftKey))) this.redo();
    else if (ctrl && k === "z") this.undo();
    else if (ctrl && k === "x") void this.run("cut");
    else if (ctrl && k === "c") void this.run("copy");
    else if (ctrl && k === "v") void this.run("paste");
    else if (ctrl && k === "t") void this.run("trim");
    else if (ctrl && k === "l") void this.run("silence");
    else if (ctrl && k === "a") void this.run("select_all");
    else if (ctrl && k === "e") void this.run("zoom_sel");
    else if (ctrl && k === "f") void this.run("fit");
    else if (ctrl) handled = false;
    else if (e.key === " " && tag !== "BUTTON") this.togglePlay();
    else if (e.key === "Delete" || e.key === "Backspace") void this.run("delete");
    else if (e.key === "Home") this.goTo(0);
    else if (e.key === "End") this.goTo(this.frames);
    else if (e.key === "Escape") void this.run("deselect");
    else if (e.key === "+" || e.key === "=") void this.run("zoom_in");
    else if (e.key === "-") void this.run("zoom_out");
    else handled = false;
    if (handled) e.preventDefault();
  }

  // ---------------------------------------------------------------- editing
  /** One tool-row action by name. True if it did something. */
  async run(key: Action): Promise<boolean> {
    const doc = this.doc;
    if (!doc || this.busy) return false;
    let ok = true;
    if (key === "zoom_in") this.zoom(2);
    else if (key === "zoom_out") this.zoom(0.5);
    else if (key === "fit") this.fit();
    else if (key === "zoom_sel") this.zoomTo(...doc.selection);
    else if (key === "select_all") doc.selectAll();
    else if (key === "deselect") doc.setSelection(doc.selection[0]);
    else if (key === "copy") {
      const clip = doc.copy();
      if (clip) this.clipboard = clip;
      ok = !!clip;
    } else {
      this.stop();
      if (key === "cut") {
        const clip = doc.cut();
        if (clip) this.clipboard = clip;
        ok = !!clip;
      } else if (key === "paste") ok = doc.paste(this.clipboard);
      else if (key === "amplify") {
        const [a, b] = doc.span();
        const db = await amplifyDialog(ops.peakDb(ops.slice(doc.samples, a, b)));
        ok = db !== null && doc.amplify(db);
      } else if (key === "delete") ok = doc.delete();
      else if (key === "trim") ok = doc.trim();
      else if (key === "silence") ok = doc.silence();
      else if (key === "fade_in") ok = doc.fadeIn();
      else if (key === "fade_out") ok = doc.fadeOut();
      else if (key === "reverse") ok = doc.reverse();
      else if (key === "normalize") {
        ok = doc.normalize();
        if (!ok) this.showMessage("Already at −1 dB (or silent): nothing to change.");
      }
      this.clamp();
      this.changed();
    }
    this.update();
    return ok;
  }

  undo(): void {
    if (!this.doc || this.busy) return;
    this.stop();
    if (this.doc.undo()) this.changed();
    this.update();
  }

  redo(): void {
    if (!this.doc || this.busy) return;
    this.stop();
    if (this.doc.redo()) this.changed();
    this.update();
  }

  goTo(frame: number): void {
    if (!this.doc) return;
    this.stop();
    this.doc.setSelection(frame);
    this.ensureVisible(this.doc.selection[0]);
    this.update();
  }

  // ---------------------------------------------------------------- playback
  get playing(): boolean {
    return !!this.player;
  }

  togglePlay(): void {
    const doc = this.doc;
    if (!doc || this.busy) return;
    if (this.player) {
      if (this.player.paused) void this.ctx?.resume();
      else void this.ctx?.suspend();
      this.player.paused = !this.player.paused;
      this.update();
      return;
    }
    const [a, b] = doc.selection;
    if (b > a) this.play(a, b);
    else this.play(a < doc.frames ? a : 0, doc.frames);
  }

  play(start: number, end: number): void {
    this.stop();
    const doc = this.doc;
    if (!doc || end <= start) return;
    try {
      this.ctx ??= new AudioContext();
      const buf = this.ctx.createBuffer(doc.channels, end - start, doc.rate);
      doc.samples.forEach((ch, c) => buf.copyToChannel(ch.subarray(start, end) as Float32Array<ArrayBuffer>, c));
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      if (!this.gain) {
        this.gain = this.ctx.createGain();
        this.gain.connect(this.ctx.destination);
      }
      this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
      this.gain.gain.value = gainFor(this.volume);
      src.connect(this.gain);
      void this.ctx.resume();
      src.start();
      const player = { src, origin: start, end, t0: this.ctx.currentTime, paused: false };
      src.onended = () => {
        if (this.player === player) this.stop();
      };
      this.player = player;
      this.tick();
    } catch (e) {
      console.error(e);
      this.showMessage(NO_AUDIO_OUTPUT, true);
    }
    this.update();
  }

  /** 0…1 as heard; applies at once while playing. */
  setVolume(volume: number): void {
    this.volume = Math.min(1, Math.max(0, volume));
    if (!this.gain || !this.ctx) return;
    // a short glide while it plays: a jump in gain clicks
    if (this.player) this.gain.gain.setTargetAtTime(gainFor(this.volume), this.ctx.currentTime, 0.015);
    else this.gain.gain.value = gainFor(this.volume);
  }

  /** The gain being played (for tests), or null before the first playback. */
  get playGain(): number | null {
    return this.gain?.gain.value ?? null;
  }

  private showVolume(): void {
    const value = Number(this.volumeSlider.value);
    this.volumeLabel.textContent = `${value} %`;
    this.volumeSlider.title = `Playback volume (${value} %); saving is not affected`;
    this.setVolume(value / 100);
  }

  /** The frame being heard. */
  position(): number | null {
    const p = this.player;
    if (!p || !this.ctx) return null;
    return Math.min(p.end, Math.round(p.origin + (this.ctx.currentTime - p.t0) * (this.doc?.rate ?? 1)));
  }

  private tick(): void {
    if (!this.player) return;
    this.playHead = this.position();
    if (this.playHead !== null) this.ensureVisible(this.playHead);
    this.requestDraw();
    requestAnimationFrame(() => this.tick());
  }

  stop(): void {
    const p = this.player;
    if (!p) return;
    this.player = null;
    p.src.onended = null;
    try {
      p.src.stop();
    } catch {
      /* never started */
    }
    if (p.paused) void this.ctx?.resume();
    this.playHead = null;
    this.requestDraw();
    this.update();
  }

  // ---------------------------------------------------------------- saving
  private formats(): [string, string][] {
    return converterById("audio-file").targets([]).map((t): [string, string] => [t.id, `${t.label} (.${t.ext})`]);
  }

  async saveAs(): Promise<void> {
    const doc = this.doc;
    if (!doc || this.busy || !doc.frames) return;
    this.stop();
    const first = this.sources[0] ?? "sound.wav";
    const ext = first.split(".").pop()?.toLowerCase() ?? "wav";
    const ids = this.formats().map(([id]) => id);
    const stem = this.saveTarget?.name ?? `${first.replace(/\.[^.]+$/, "")} edited`;
    const format = this.saveTarget?.format ?? (ids.includes(ext) ? ext : "wav");
    const choice = await saveDialog(stem, this.formats(), format, this.bitrate);
    if (!choice) return;
    let handle: FileHandle | undefined;
    if (choice.toFile) {
      try {
        handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({ suggestedName: `${choice.name}.${choice.format}` });
      } catch {
        return; // picker cancelled
      }
    }
    this.bitrate = choice.bitrate;
    this.saveTarget = { ...choice, handle };
    await this.write();
  }

  async save(): Promise<void> {
    if (!this.saveTarget) return this.saveAs();
    if (!this.doc || this.busy) return;
    this.stop();
    await this.write();
  }

  private async write(): Promise<void> {
    const doc = this.doc as AudioDoc;
    const save = this.saveTarget!;
    const state = doc.state;
    const fileName = `${save.name}.${save.format}`;
    this.busy = true;
    this.update();
    this.showMessage(`Saving “${fileName}”…`);
    try {
      const wav = writeWav(doc.rate, doc.samples.map((ch) => ch.map((v) => Math.max(-1, Math.min(1, v)))), 32);
      const conv = converterById("audio-file");
      const input: InputFile = { file: new File([wav as BlobPart], `${save.name}.wav`, { type: "audio/wav" }), name: `${save.name}.wav`, ext: "wav", family: "audio" };
      const target = conv.targets([input]).find((t) => t.id === save.format);
      if (!target) throw new Error(`Can't save as ${save.format.toUpperCase()}.`);
      const values = { ...defaults(conv.options), ...(LOSSY.has(save.format) ? { bitrate: save.bitrate } : {}) };
      const [out] = await this.opts.client.run(target, [input], values, (p) => this.showMessage(`Saving “${fileName}”… ${Math.round(p * 100)} %`));
      const blob = new Blob([out.data as BlobPart], { type: out.type });
      if (save.handle) {
        const w = await save.handle.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        const url = URL.createObjectURL(blob);
        h("a", { href: url, download: fileName }).click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      doc.markSavedAt(state);
      this.showMessage(`Saved “${fileName}”.`);
    } catch (e) {
      console.error(e);
      this.showMessage(e instanceof Error && e.message ? `Couldn't save: ${e.message}` : "Couldn't save the sound.", true);
    } finally {
      this.busy = false;
      this.update();
    }
  }

  // ---------------------------------------------------------------- state
  update(): void {
    const doc = this.doc;
    const ready = !!doc && !this.busy;
    const sel = ready && doc!.hasSelection;
    const has = ready && doc!.frames > 0;
    const enabled: Record<string, boolean> = {
      cut: sel, copy: sel, paste: ready && !!this.clipboard, delete: sel, trim: sel, silence: sel,
      fade_in: sel, fade_out: sel, amplify: has, normalize: has, reverse: has,
      zoom_in: ready, zoom_out: ready, zoom_sel: sel, fit: ready,
      home: has, end: has, play: has, stop: this.playing,
      undo: ready && doc!.canUndo(), redo: ready && doc!.canRedo(),
      save: has, save_as: has, open: !this.busy, back: true,
    };
    for (const [key, b] of this.buttons) b.disabled = !enabled[key];
    this.buttons.get("play")!.textContent = this.player && !this.player.paused ? "❚❚ Pause" : "▶ Play";
    this.buttons.get("undo")!.title = ready && doc!.canUndo() ? `Undo ${doc!.undoLabel()} (Ctrl+Z)` : "Undo (Ctrl+Z)";
    this.buttons.get("redo")!.title = ready && doc!.canRedo() ? `Redo ${doc!.redoLabel()} (Ctrl+Y)` : "Redo (Ctrl+Y)";
    if (!doc) {
      this.selectionLabel.textContent = "";
      this.infoLabel.textContent = "";
      this.title.textContent = "";
      this.requestDraw();
      return;
    }
    const t = (f: number) => ops.formatTime(f / doc.rate);
    const [a, b] = doc.selection;
    this.selectionLabel.textContent = b > a ? `Selection ${t(a)} – ${t(b)} · Length ${t(b - a)}` : `Cursor ${t(a)}`;
    const layout = doc.channels === 1 ? "Mono" : doc.channels === 2 ? "Stereo" : `${doc.channels} channels`;
    this.infoLabel.textContent = `${doc.rate / 1000} kHz · ${layout} · ${ops.formatTime(doc.duration)}${doc.dirty ? " · Unsaved changes" : ""}`;
    this.title.textContent = this.sources.length ? this.sources[0] + (this.sources.length > 1 ? ` + ${this.sources.length - 1} more` : "") : "";
    this.requestDraw();
  }

  // ---------------------------------------------------------------- drawing
  private requestDraw(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.draw();
    });
  }

  /** Draws now (tests call it; normally once per animation frame). */
  draw(): void {
    if (!this.active || this.host.hidden) return;
    const dpr = devicePixelRatio || 1;
    const w = this.width();
    const hgt = Math.max(1, this.host.clientHeight);
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(hgt * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(hgt * dpr);
    }
    const g = this.canvas.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const col = {
      surface: cssColor("--surface", "#fff"), alt: cssColor("--surface-alt", "#eff0f4"), border: cssColor("--border", "#e3e5ea"),
      strong: cssColor("--border-strong", "#cfd3db"), text: cssColor("--text", "#121419"), muted: cssColor("--muted", "#6a7080"),
      accent: cssColor("--accent", "#5b5ef0"), success: cssColor("--success", "#16955b"),
    };
    g.fillStyle = col.surface;
    g.fillRect(0, 0, w, hgt);
    const doc = this.doc;
    if (!doc || !doc.frames) return;
    const laneH = (hgt - RULER_H - LANE_GAP * (doc.channels - 1)) / doc.channels;
    const [a, b] = doc.selection;
    const x0 = this.xOf(a), x1 = this.xOf(b);
    const endX = Math.min(w, this.xOf(doc.frames));
    if (!this.summary || this.summaryRev !== doc.revision) {
      this.summary = peaks.build(doc.samples);
      this.summaryRev = doc.revision;
    }
    const { mins, maxs } = peaks.columns(doc.samples, this.summary, this.start, this.fpp, Math.ceil(w));
    for (let c = 0; c < doc.channels; c++) {
      const top = RULER_H + c * (laneH + LANE_GAP);
      g.fillStyle = col.alt;
      g.fillRect(0, top, endX, laneH);
      if (b > a) {
        g.fillStyle = col.accent;
        g.globalAlpha = 0.18;
        g.fillRect(x0, top, x1 - x0, laneH);
        g.globalAlpha = 1;
      }
      const mid = top + laneH / 2, half = (laneH / 2) * 0.95;
      g.fillStyle = col.strong;
      g.fillRect(0, mid - 0.5, endX, 1);
      g.fillStyle = col.accent;
      g.strokeStyle = col.accent;
      const lo = mins[c], hi = maxs[c];
      const clip = (v: number) => Math.max(-1, Math.min(1, v));
      if (this.fpp < 1) {
        g.beginPath();
        for (let i = 0; i < hi.length; i++) {
          const y = mid - clip(hi[i]) * half;
          if (i) g.lineTo(i, y);
          else g.moveTo(i, y);
        }
        g.stroke();
        continue;
      }
      for (let i = 0; i < hi.length; i++) {
        const y0 = mid - clip(hi[i]) * half;
        const y1 = Math.max(mid - clip(lo[i]) * half, y0 + 1);
        g.fillRect(i, y0, 1, y1 - y0);
      }
    }
    this.drawRuler(g, w, col);
    g.fillStyle = col.text;
    for (const x of new Set([x0, x1])) if (x >= 0 && x <= w) g.fillRect(Math.round(x), RULER_H, 1, hgt - RULER_H);
    if (this.playHead !== null) {
      const x = this.xOf(this.playHead);
      g.fillStyle = col.success;
      if (x >= 0 && x <= w) g.fillRect(Math.round(x) - 1, 0, 2, hgt);
    }
  }

  private drawRuler(g: CanvasRenderingContext2D, w: number, col: Record<string, string>): void {
    const rate = this.doc!.rate;
    g.fillStyle = col.surface;
    g.fillRect(0, 0, w, RULER_H);
    g.fillStyle = col.border;
    g.fillRect(0, RULER_H - 1, w, 1);
    const secPerPx = this.fpp / rate;
    const step = TICK_STEPS.find((s) => s / secPerPx >= 90) ?? TICK_STEPS[TICK_STEPS.length - 1];
    const t0 = this.start / rate;
    g.font = "11px system-ui, sans-serif";
    g.textBaseline = "top";
    g.fillStyle = col.muted;
    for (let i = Math.floor(t0 / step); ; i++) {
      const t = i * step;
      const x = (t - t0) / secPerPx;
      if (x > w) break;
      if (x < -60) continue;
      g.fillRect(Math.round(x), RULER_H - 8, 1, 7);
      for (let m = 1; m < 5; m++) g.fillRect(Math.round(x + (m * step) / 5 / secPerPx), RULER_H - 4, 1, 3);
      let label = ops.formatTime(t);
      if (step >= 1) label = label.replace(/\.\d+$/, "");
      g.fillText(label, x + 3, 4);
    }
  }
}

// ---------------------------------------------------------------- dialogs
function dialog<T>(cls: string, title: string, body: HTMLElement[], okLabel: string, read: () => T, extra: HTMLButtonElement[] = []): Promise<{ value: T; button: string } | null> {
  return new Promise((resolve) => {
    const ok = h("button", { class: "primary small", type: "button" }, okLabel);
    const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
    const dlg = h("dialog", { class: `editor-dialog ${cls}` }, h("h2", {}, title), ...body, h("div", { class: "row" }, h("div", { class: "spacer" }), cancel, ...extra, ok));
    const finish = (button: string | null) => {
      const value = button ? read() : null;
      dlg.close();
      dlg.remove();
      resolve(button ? { value: value as T, button } : null);
    };
    ok.addEventListener("click", () => finish(okLabel));
    for (const b of extra) b.addEventListener("click", () => finish(b.textContent ?? ""));
    cancel.addEventListener("click", () => finish(null));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(null);
    });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !["SELECT", "BUTTON"].includes((e.target as HTMLElement).tagName)) {
        e.preventDefault();
        finish(okLabel);
      }
      e.stopPropagation();
    });
    document.body.append(dlg);
    dlg.showModal();
  });
}

/** Amplify by a number of dB (shows the most it can go up without clipping); null = cancelled. */
async function amplifyDialog(peakDb: number | null): Promise<number | null> {
  const headroom = peakDb !== null ? -peakDb : 0;
  const start = Math.round(Math.max(ops.GAIN_MIN_DB, Math.min(ops.GAIN_MAX_DB, headroom)) * 10) / 10;
  const input = h("input", { type: "number", value: String(start), min: String(ops.GAIN_MIN_DB), max: String(ops.GAIN_MAX_DB), step: "0.5", ariaLabel: "Change the volume by (dB)" });
  const r = await dialog("amplify", "Amplify", [
    h("label", { class: "field" }, h("span", {}, "Change the volume by"), h("span", { class: "with-unit" }, input, "dB")),
    h("div", { class: "hint" }, peakDb === null ? "Silent" : `${headroom.toFixed(1)} dB louder at most without clipping`),
  ], "OK", () => Math.max(ops.GAIN_MIN_DB, Math.min(ops.GAIN_MAX_DB, Number(input.value) || 0)));
  return r ? r.value : null;
}

interface SaveChoice { name: string; format: string; bitrate: number; toFile: boolean }

async function saveDialog(defaultName: string, formats: [string, string][], defaultFormat: string, bitrate: number): Promise<SaveChoice | null> {
  const name = h("input", { type: "text", value: defaultName, ariaLabel: "File name" });
  const format = h("select", { ariaLabel: "Format" });
  for (const [id, label] of formats) format.append(h("option", { value: id, selected: id === defaultFormat }, label));
  const rate = h("select", { ariaLabel: "Quality (bitrate)" });
  for (const b of BITRATES) rate.append(h("option", { value: String(b), selected: b === bitrate }, `${b} kbps`));
  const rateRow = h("label", { class: "field" }, h("span", {}, "Quality (bitrate)"), rate);
  const sync = () => (rateRow.hidden = !LOSSY.has(format.value));
  format.addEventListener("change", sync);
  sync();
  const extra: HTMLButtonElement[] = [];
  if ("showSaveFilePicker" in window) extra.push(h("button", { class: "btn", type: "button", title: "Choose where to save; Save (Ctrl+S) then writes to the same file" }, "Save to a file…"));
  const r = await dialog("save", "Save sound", [
    h("label", { class: "field" }, h("span", {}, "File name"), name),
    h("label", { class: "field" }, h("span", {}, "Format"), format),
    rateRow,
  ], "Save", () => ({ name: name.value.trim() || defaultName, format: format.value, bitrate: Number(rate.value) }), extra);
  if (!r) return null;
  return { ...r.value, toFile: r.button !== "Save" };
}

export function mountAudioEditor(host: HTMLElement, opts: AudioEditorOptions): AudioEditorPage {
  const page = new AudioEditorPage(opts);
  host.append(page.el);
  return page;
}
