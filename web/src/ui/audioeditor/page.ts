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
import { carriedTags, editFor, EMPTY_TAGS, type CarriedTags } from "../../audioedit/tags";
import type { MusicDetails } from "../../properties/music";
import { DENOISE_TARGET, MAX_SAMPLES, OPEN_TARGET, SPEED_TARGET, TOO_LONG } from "../../converters/audioedit";
import * as noise from "../../audioedit/noise";
import * as spectrum from "../../audioedit/spectrum";
import * as record from "../../audioedit/record";
import * as tracks from "../../audioedit/tracks";
import * as speed from "../../audioedit/speed";
import { MUSIC_TAGS_TARGET } from "../../converters/musictags";
import { defaults, type Values } from "../../core/options";
import { converterById } from "../../core/registry";
import type { InputFile, OutputFile } from "../../core/types";
import { JobCancelled, type ConversionClient } from "../../worker/client";
import { backLabel } from "../back";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export const MAX_TRACK_NAME = 60;
/** What a typed track name becomes ("" = keep the old one). */
export const cleanTrackName = (text: string) => text.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_TRACK_NAME);
export const HINT = "Click to place the cursor · drag to select · middle-click to play from there · Ctrl+wheel to zoom · Space plays";
const EMPTY_TEXT = "Open a sound with “Open…”, or drop audio files here (MP3, WAV, FLAC, M4A, OGG, …).";
const AUDIO_ACCEPT = "audio/*,.mp3,.wav,.flac,.m4a,.aac,.ogg,.oga,.opus,.wma,.aiff,.aif,.ac3,.amr,.ape,.mka";
const RULER_H = 24;
const LANE_GAP = 6;
const MIN_FPP = 1 / 32; // most zoomed in: 32 px per sample
const COLOURS = spectrum.colourTable();
const SETTLE_MS = 150; // zooming / scrolling faster than this stretches the last spectrogram instead
const SPECTROGRAM_KEY = "umc-audio-spectrogram"; // localStorage: "1" = show the spectrogram
const SCROLL_STEPS = 1000; // the scroll bar works in 1/1000 of a screen
const TICK_STEPS = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const BITRATES = [64, 96, 128, 160, 192, 256, 320];
const LOSSY = new Set(["mp3", "m4a", "ogg", "opus", "wma", "ac3"]);
export const NO_AUDIO_OUTPUT = "This browser can't play sound here.";

type Action = "cut" | "copy" | "paste" | "delete" | "trim" | "silence" | "fade_in" | "fade_out" | "amplify" | "normalize" | "reverse" | "speed" | "learn_noise" | "reduce_noise" | "zoom_in" | "zoom_out" | "zoom_sel" | "fit" | "spectrogram" | "select_all" | "deselect";

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
    ["speed", "Speed / pitch…", "Change the speed, tempo or pitch of the selection (or everything)"],
  ],
  [
    ["learn_noise", "Learn noise", "Select a part with only background noise, then press this"],
    ["reduce_noise", "Reduce noise…", "Turn down the learned background noise in the selection (or everything)"],
  ],
  [
    ["zoom_in", "Zoom in", "Zoom in (+, or Ctrl+wheel)"],
    ["zoom_out", "Zoom out", "Zoom out (−)"],
    ["zoom_sel", "Selection", "Zoom to the selection (Ctrl+E)"],
    ["fit", "Fit", "Show the whole sound (Ctrl+F)"],
    ["spectrogram", "Spectrogram", "Show how loud each pitch is, instead of the waveform"],
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
  private rec: Recording | null = null; // set while recording
  private converting = false; // a stopped take is being resampled
  tracks: tracks.Track[] = []; // doc = tracks[activeTrack].doc
  activeTrack = 0;
  private tracksRev = 0; // bumped by every track change (add, remove, volume, mute …)
  private tracksSavedRev = 0;
  private tracksEl = h("div", { class: "audio-tracks", hidden: true });
  private trackRows: { track: tracks.Track; label: string; row: HTMLElement; name: HTMLButtonElement; more: HTMLButtonElement }[] = [];
  private trackMenu: { menu: HTMLElement; done: () => void } | null = null;
  private trackFile = h("input", { type: "file", accept: "audio/*", hidden: true });
  recordRoom: number | null = null; // tests: a smaller limit for the take, in frames
  private recordLevel = h("meter", { class: "audio-record-level", title: "Recording level", hidden: true });
  private summary: peaks.Peaks | null = null;
  private summaryRev = -1;
  spectrogram = false; // lanes show the spectrogram instead of the waveform
  private spectroKey = "";
  private spectro: HTMLCanvasElement[] = [];
  private spectroPlace = { first: 0, perColumn: 1 }; // the images' first frame, frames per image column
  private spectroAt = 0; // when they were computed (performance.now)
  private spectroTimer = 0;
  private canvas = h("canvas", { class: "audio-canvas", tabIndex: 0, ariaLabel: "Waveform" });
  private host = h("div", { class: "audio-wave" });
  private placeholder = h("div", { class: "audio-placeholder" }, EMPTY_TEXT);
  private scroll = h("input", { type: "range", class: "audio-scroll", min: "0", max: "0", value: "0", ariaLabel: "Scroll" });
  private message = h("div", { class: "msg" });
  /** Shown while a speed / pitch change runs in the worker. */
  private cancelButton = h("button", { class: "btn small", type: "button", hidden: true, title: "Stop changing the sound" }, "Cancel");
  private lastSpeed: SpeedChoice = { mode: "tempo", value: 100 };
  /** Counts worker changes (speed, noise); leaving the page bumps it so a late answer is ignored. */
  private changeRun = 0;
  private changing = false;
  private noise: { rate: number; profile: Float64Array } | null = null;
  private lastNoise: NoiseChoice = { reduceDb: noise.REDUCE_DEFAULT, sensitivityDb: noise.SENSITIVITY_DEFAULT, smoothing: noise.SMOOTHING_DEFAULT };
  private selectionLabel = h("div", { class: "audio-selection" });
  /** Start / End / Length: typed times change the selection (Enter or leaving the box). */
  private timeBoxes = new Map<ops.TimeField, HTMLInputElement>();
  private shownTimes = new Map<ops.TimeField, string>();
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
  private tags: CarriedTags = EMPTY_TAGS; // song details of the opened files, written when saving

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
      backLabel(btn("back", "← Back to Convert", "Leave the audio editor", () => this.back(), "link")),
      // a second row on phones (styles.css .top-tools), invisible on wide screens
      h("div", { class: "top-tools" },
        h("span", { class: "gap" }),
        btn("open", "Open…", "Open audio files (several are joined)", () => picker.click()), picker,
        btn("undo", "Undo", "Undo (Ctrl+Z)", () => this.undo()),
        btn("redo", "Redo", "Redo (Ctrl+Y)", () => this.redo()),
        h("span", { class: "gap" }), this.title),
      h("div", { class: "spacer" }),
      btn("save_as", "Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs()),
      btn("save", "Save", "Save (Ctrl+S)", () => void this.save(), "primary small"));
    const transport = h("div", { class: "audio-group" },
      btn("home", "|◀", "Go to the start (Home)", () => this.goTo(0)),
      btn("play", "▶ Play", "Play the selection, or from the cursor (Space)", () => this.togglePlay()),
      btn("stop", "■ Stop", "Stop playing", () => this.stop()),
      btn("end", "▶|", "Go to the end (End)", () => this.goTo(this.doc?.frames ?? 0)),
      btn("record", "● Record", "Record from the microphone at the cursor, or over the selection (R)", () => void this.toggleRecord()),
      this.recordLevel,
      h("label", { class: "audio-volume-box" }, h("span", { class: "muted" }, "Volume"), this.volumeSlider, this.volumeLabel));
    const groups = TOOL_GROUPS.map((items) => h("div", { class: "audio-group" },
      ...items.map(([key, label, title]) => btn(key, label, title, () => void this.run(key)))));
    const tools = h("div", { class: "audio-tools", role: "toolbar", ariaLabel: "Audio tools" }, transport, ...groups);
    this.host.append(this.canvas);
    this.el = h("div", { class: "editor audio-editor", hidden: true },
      top,
      h("div", { class: "audio-body" }, tools, this.tracksEl, h("div", { class: "audio-view" }, this.host, this.placeholder), this.scroll),
      this.trackFile,
      h("div", { class: "editor-status" }, this.selectionLabel, this.message, this.cancelButton, this.infoLabel));
    this.cancelButton.addEventListener("click", () => this.cancelChange());
    this.trackFile.addEventListener("change", () => {
      const file = this.trackFile.files?.[0];
      this.trackFile.value = "";
      if (file) void this.addTrackFile(file);
    });
    this.buildTimes();

    let saved = NaN;
    try {
      saved = Number(localStorage.getItem(VOLUME_KEY) ?? NaN);
    } catch {
      /* storage blocked: the default */
    }
    this.volumeSlider.value = String(Number.isFinite(saved) ? Math.min(100, Math.max(0, Math.round(saved))) : Math.round(DEFAULT_VOLUME * 100));
    this.showVolume();
    let spectro = false;
    try {
      spectro = localStorage.getItem(SPECTROGRAM_KEY) === "1";
    } catch {
      /* storage blocked: the waveform */
    }
    this.setSpectrogram(spectro, false);
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
    return !!this.doc?.dirty || this.tracks.some((t) => t.doc.dirty) || this.tracksRev !== this.tracksSavedRev;
  }

  /** Play and save the mix: two or more tracks, or one with a volume / pan / mute / solo. */
  get mixing(): boolean {
    return this.tracks.length > 1 || this.tracks.some(tracks.changed);
  }

  /** Frames that play / save: the mix's, or the sound's. */
  length(): number {
    return this.mixing ? tracks.length(this.tracks) : this.doc?.frames ?? 0;
  }

  private usedSamples(): number {
    return this.tracks.length ? tracks.totalSamples(this.tracks) : (this.doc ? this.doc.channels * this.doc.frames : 0);
  }

  // ---------------------------------------------------------------- tracks
  private renderTracks(): void {
    const el = this.tracksEl;
    el.hidden = !this.doc;
    el.replaceChildren();
    this.closeTrackMenu();
    this.trackRows = [];
    if (!this.doc) return;
    const off = this.busy;
    const add = (label: string, title: string, fn: () => void) => {
      const b = h("button", { class: "btn small", type: "button", title, textContent: label, disabled: off });
      b.dataset.busyOff = "";
      b.addEventListener("click", fn);
      return b;
    };
    el.append(h("div", { class: "audio-tracks-head" }, h("span", { class: "muted" }, "Tracks"),
      add("+ Empty track", "A silent track to Record or Paste into", () => this.addEmptyTrack()),
      add("+ Track from a file…", "Add a sound to mix with the others", () => this.trackFile.click())));
    this.trackRows = this.tracks.map((t, i) => {
      const toggle = (text: string, title: string, on: boolean, fn: (on: boolean) => void) => {
        const b = h("button", { class: "btn small audio-track-toggle", type: "button", title, textContent: text, ariaLabel: title });
        b.setAttribute("aria-pressed", String(on));
        b.addEventListener("click", () => {
          const now = b.getAttribute("aria-pressed") !== "true";
          b.setAttribute("aria-pressed", String(now));
          fn(now);
        });
        return b;
      };
      const name = h("button", { class: "audio-track-name", type: "button", title: `${t.name}\nClick: edit this track · double-click: rename`, textContent: t.name });
      name.addEventListener("click", () => this.selectTrack(i));
      name.addEventListener("dblclick", () => this.startRename(i));
      const gainLabel = h("span", { class: "muted audio-track-value", textContent: gainText(t.gainDb) });
      const gain = h("input", { type: "range", min: String(tracks.MIN_GAIN_DB), max: String(tracks.MAX_GAIN_DB), step: "1", value: String(t.gainDb), ariaLabel: `${t.name} volume`, title: "Volume of this track in the mix" });
      gain.addEventListener("input", () => {
        gainLabel.textContent = gainText(Number(gain.value));
        this.setTrack(t, "gainDb", Number(gain.value));
      });
      const panLabel = h("span", { class: "muted audio-track-value", textContent: panText(t.pan) });
      const pan = h("input", { type: "range", min: "-100", max: "100", step: "1", value: String(Math.round(t.pan * 100)), ariaLabel: `${t.name} left / right`, title: "Left / right (double-click: centre)" });
      const onPan = () => {
        panLabel.textContent = panText(Number(pan.value) / 100);
        this.setTrack(t, "pan", Number(pan.value) / 100);
      };
      pan.addEventListener("input", onPan);
      pan.addEventListener("dblclick", () => {
        pan.value = "0";
        onPan();
      });
      const more = h("button", { class: "btn small audio-track-more", type: "button", title: `More for ${t.name}: rename, remove`, ariaLabel: `More for ${t.name}`, textContent: "⋯" });
      more.setAttribute("aria-haspopup", "menu");
      more.addEventListener("click", () => this.openTrackMenu(i, more));
      const row = h("div", { class: "audio-track", role: "group", ariaLabel: t.name }, name,
        toggle("M", `Mute ${t.name}`, t.mute, (on) => this.setTrack(t, "mute", on)),
        toggle("S", `Solo ${t.name} (hear only the soloed tracks)`, t.solo, (on) => this.setTrack(t, "solo", on)),
        h("label", { class: "audio-track-slider audio-track-gain" }, gain, gainLabel),
        h("label", { class: "audio-track-slider audio-track-pan" }, pan, panLabel), more);
      el.append(row);
      return { track: t, label: t.name, row, name, more };
    });
    this.syncTracks();
  }

  /** The highlight and what may be clicked; rows stay (so a double-click on a name still lands). */
  private syncTracks(): void {
    const off = this.busy;
    this.trackRows.forEach((r, i) => {
      const active = i === this.activeTrack;
      r.row.classList.toggle("active", active);
      r.name.setAttribute("aria-pressed", String(active));
      r.name.disabled = off && !active;
      r.more.disabled = off;
    });
  }

  /** Rebuild the strip only when the tracks or their names changed. */
  private showTracks(): void {
    const same = this.doc && this.trackRows.length === this.tracks.length && this.trackRows.every((r, i) => r.track === this.tracks[i] && r.label === r.track.name);
    if (same && !this.tracksEl.hidden) this.syncTracks();
    else this.renderTracks();
  }

  private openTrackMenu(i: number, anchor: HTMLElement): void {
    this.closeTrackMenu();
    const menu = h("div", { class: "menu-items context-menu", role: "menu", ariaLabel: `${this.tracks[i]?.name ?? ""} actions` });
    const item = (label: string, fn: () => void) => {
      const b = h("button", { class: "menu-item", role: "menuitem", type: "button" }, label);
      b.addEventListener("click", () => {
        this.closeTrackMenu();
        fn();
      });
      menu.append(b);
    };
    item("Rename…", () => this.startRename(i));
    item("Remove track", () => this.removeTrack(i));
    document.body.append(menu);
    const a = anchor.getBoundingClientRect(), r = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(4, Math.min(a.right - r.width, window.innerWidth - r.width - 4))}px`;
    menu.style.top = `${a.bottom + 4 + r.height > window.innerHeight ? Math.max(4, a.top - r.height - 4) : a.bottom + 4}px`;
    const outside = (e: Event) => {
      if (!menu.contains(e.target as Node) && e.target !== anchor) this.closeTrackMenu();
    };
    const keys = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        this.closeTrackMenu();
        anchor.focus();
      }
    };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", keys, true);
    this.trackMenu = { menu, done: () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", keys, true);
    } };
    menu.querySelector<HTMLButtonElement>("button")?.focus();
  }

  private closeTrackMenu(): void {
    this.trackMenu?.done();
    this.trackMenu?.menu.remove();
    this.trackMenu = null;
  }

  /** Swap the name for a text box: Enter or leaving it keeps the new name, Esc the old one. */
  startRename(i: number): void {
    const r = this.trackRows[i];
    if (!r || this.busy) return; // a save / recording rebuilds nothing meanwhile
    const box = h("input", { class: "audio-track-rename", type: "text", value: r.track.name, maxLength: MAX_TRACK_NAME, ariaLabel: "Track name" });
    let done = false;
    const finish = (keep: boolean) => {
      if (done) return;
      done = true;
      if (!keep || !this.renameTrack(i, box.value)) box.replaceWith(r.name);
    };
    box.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") {
        e.stopPropagation();
        finish(false);
      } else return;
      e.preventDefault();
    });
    box.addEventListener("blur", () => finish(true));
    r.name.replaceWith(box);
    box.focus();
    box.select();
  }

  /** Give track `i` a new name (only a label: the saved mix doesn't carry it). */
  renameTrack(i: number, typed: string): boolean {
    const t = this.tracks[i];
    const name = cleanTrackName(typed);
    if (!t || !name || name === t.name) return false;
    t.name = name;
    this.renderTracks();
    return true;
  }

  private setTrack<K extends "gainDb" | "pan" | "mute" | "solo">(t: tracks.Track, key: K, value: tracks.Track[K]): void {
    if (t[key] === value) return;
    t[key] = value;
    this.tracksRev++; // heard from the next Play
    this.update();
  }

  private setTracks(list: tracks.Track[], active: number): void {
    this.tracks = list;
    this.activeTrack = active;
    this.tracksRev++;
  }

  /** Edit track `i` (its own selection and undo). */
  selectTrack(i: number): boolean {
    if (this.busy || i < 0 || i >= this.tracks.length) return false;
    this.showTrack(i);
    return true;
  }

  private showTrack(i: number): void {
    const doc = this.tracks[i].doc;
    if (doc !== this.doc) {
      this.stop();
      this.doc = doc;
      this.summary = null;
      this.spectroKey = "";
      this.spectro = [];
      this.playHead = null;
      this.fit();
    }
    this.activeTrack = i;
    this.showTracks();
    this.update();
  }

  private addTrack(doc: AudioDoc, name: string): void {
    this.setTracks([...this.tracks, tracks.track(doc, name)], this.activeTrack);
    this.showTrack(this.tracks.length - 1);
  }

  /** A silent track (no frames) to Record or Paste into. */
  addEmptyTrack(): boolean {
    if (this.busy || !this.doc) return false;
    const rate = this.tracks[0].doc.rate;
    const channels = tracks.outChannels(this.tracks);
    this.addTrack(new AudioDoc(rate, Array.from({ length: channels }, () => new Float32Array(0))), tracks.nextName(this.tracks));
    this.showMessage("New empty track: press “● Record” or paste sound into it.");
    return true;
  }

  /** Read `file` (at the tracks' sample rate) into a new track. False if it failed. */
  async addTrackFile(file: File): Promise<boolean> {
    if (this.busy || !this.doc) return false;
    this.stop();
    this.busy = true; // first: nothing else may start while the file is checked
    const rate = this.tracks[0].doc.rate;
    this.showMessage(`Adding “${file.name}”…`);
    this.update();
    try {
      const { detect } = await import("../../core/detect");
      const input = await detect(file);
      if (input?.family !== "audio") {
        this.showMessage("Only audio files can be added as a track.", true);
        return false;
      }
      const [out] = await this.opts.client.run(OPEN_TARGET, [input], { rate }, (p) => this.showMessage(`Adding the track… ${Math.round(p * 100)} %`));
      const part = readWav(out.data);
      if (this.usedSamples() + part.samples.length * ops.frames(part.samples) > MAX_SAMPLES) throw new Error(TRACK_NO_ROOM);
      this.busy = false;
      this.addTrack(new AudioDoc(part.rate, part.samples), file.name.replace(/\.[^.]+$/, ""));
      this.showMessage("");
      return true;
    } catch (e) {
      if (!(e instanceof JobCancelled)) {
        console.error(e);
        this.showMessage(e instanceof Error && e.message ? e.message : `Couldn't add “${file.name}”.`, true);
      }
      return false;
    } finally {
      this.busy = false;
      this.showTracks();
      this.update();
    }
  }

  removeTrack(i: number): boolean {
    if (this.busy || i < 0 || i >= this.tracks.length) return false;
    const t = this.tracks[i];
    if (t.doc.frames && !window.confirm(`Remove “${t.name}”? Its sound is lost.`)) return false;
    this.stop();
    if (this.tracks.length === 1) {
      this.openEmpty();
      this.tracksSavedRev = this.tracksRev; // nothing left to save
      return true;
    }
    const rest = this.tracks.filter((_, j) => j !== i);
    const active = Math.min(this.activeTrack - (i < this.activeTrack ? 1 : 0), rest.length - 1);
    this.setTracks(rest, active);
    this.showTrack(active);
    return true;
  }

  maybeLeave(): boolean {
    this.stop();
    if (this.rec) void this.stopRecording("", true); // keep the take; then asked about like any edit
    if (this.converting) return false; // the take goes in within a moment: stay until it has
    if (this.changing) {
      // the change is dropped: the page is free at once for the next files
      this.cancelChange();
      this.changeRun++;
      this.changing = this.busy = false;
      this.cancelButton.hidden = true;
      this.showMessage("");
      this.update();
    }
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
    this.tags = EMPTY_TAGS;
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
      this.tags = await readSongTags(files);
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
    if (doc !== this.doc) {
      this.saveTarget = null;
      const name = sources.length === 1 ? sources[0].replace(/\.[^.]+$/, "") : "Track 1";
      this.setTracks(doc ? [tracks.track(doc, name)] : [], 0);
      this.tracksSavedRev = this.tracksRev;
    }
    this.doc = doc;
    this.sources = sources;
    this.summary = null;
    this.spectroKey = ""; // a new sound starts at revision 0 too
    this.spectro = [];
    this.playHead = null;
    this.placeholder.textContent = EMPTY_TEXT;
    this.placeholder.hidden = !!doc;
    this.host.hidden = !doc;
    this.fit();
    this.renderTracks();
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

  /** Turn the page with the play head, but only while it is on screen: scrolled away from it, the
   * view stays where the user put it (it follows again once the head is back in view). */
  follow(before: number | null, frame: number): void {
    if (before === null || (before >= this.start && before <= this.start + this.visibleFrames())) this.ensureVisible(frame);
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
      if (e.button === 1) {
        e.preventDefault(); // no autoscroll
        if (this.doc && !this.busy) this.seek(this.frameAt(x(e)));
        return;
      }
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
        this.doc.setSelection(f); // playback stays where it is (middle click moves it)
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
    c.addEventListener("auxclick", (e) => e.button === 1 && e.preventDefault()); // no paste (Linux)
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

  private buildTimes(): void {
    for (const [key, name] of [["start", "Start"], ["end", "End"], ["length", "Length"]] as const) {
      const box = h("input", {
        type: "text", class: "audio-time", ariaLabel: name, inputMode: "decimal", spellcheck: false,
        title: `${name} — type a time like 1:05.250 and press Enter`,
      });
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); this.commitTime(key); }
        else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
          // the page ignores keys typed into boxes; saving still works from here
          e.preventDefault();
          void (e.shiftKey ? this.saveAs() : this.save());
        }
        else if (e.key === "Escape") {
          e.preventDefault();
          box.value = this.shownTimes.get(key) ?? "";
          box.classList.remove("invalid");
          box.removeAttribute("aria-invalid");
          box.blur();
        }
      });
      box.addEventListener("change", () => this.commitTime(key));
      this.timeBoxes.set(key, box);
      this.selectionLabel.append(h("label", {}, h("span", { class: "muted" }, name), box));
    }
  }

  private commitTime(key: ops.TimeField): void {
    const box = this.timeBoxes.get(key)!;
    if (box.value.trim() === this.shownTimes.get(key)) return;
    this.typeTime(key, box.value);
  }

  /** A time typed into the Start / End / Length box. False (and a hint) if it isn't one. */
  typeTime(key: ops.TimeField, text: string): boolean {
    const box = this.timeBoxes.get(key)!;
    const seconds = ops.parseTime(text);
    if (seconds === null) {
      box.classList.add("invalid");
      box.setAttribute("aria-invalid", "true");
      this.showMessage(ops.TIME_HINT, true);
      return false;
    }
    box.classList.remove("invalid");
    box.removeAttribute("aria-invalid");
    this.showMessage("");
    const doc = this.doc;
    if (!doc || this.busy) return false;
    this.stop();
    doc.setSelection(...ops.editedSelection(key, seconds, doc.selection, doc.rate, doc.frames));
    this.ensureVisible(doc.selection[0]);
    this.update();
    box.value = this.shownTimes.get(key)!; // the real (maybe clamped) time, not what was typed
    return true;
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
    else if (k === "r" && !e.altKey) void this.toggleRecord();
    else handled = false;
    if (handled) e.preventDefault();
  }

  // ---------------------------------------------------------------- editing
  /** One tool-row action by name. True if it did something. */
  async run(key: Action): Promise<boolean> {
    const doc = this.doc;
    if (key === "spectrogram") { // only changes the view: fine while busy
      if (doc) this.setSpectrogram(!this.spectrogram);
      return !!doc;
    }
    if (!doc || this.busy) return false;
    let ok = true;
    if (key === "zoom_in") this.zoom(2);
    else if (key === "zoom_out") this.zoom(0.5);
    else if (key === "fit") this.fit();
    else if (key === "zoom_sel") this.zoomTo(...doc.selection);
    else if (key === "select_all") doc.selectAll();
    else if (key === "deselect") doc.setSelection(doc.selection[0]);
    else if (key === "learn_noise") ok = this.learnNoise();
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
      else if (key === "speed") ok = await this.changeSpeed();
      else if (key === "reduce_noise") ok = await this.reduceNoise();
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

  /** Stops the running worker change (speed / pitch, noise). Cancel is only shown once the worker runs *this* job
   *  (the client is shared, and `cancel()` stops whatever job it is running). */
  private cancelChange(): void {
    if (this.changing && !this.cancelButton.hidden) this.opts.client.cancel();
  }

  /** Speed / tempo / pitch of the selection (or everything) through FFmpeg in the worker. */
  private async changeSpeed(): Promise<boolean> {
    const doc = this.doc!;
    const [a, b] = doc.span();
    const seconds = (b - a) / doc.rate;
    if (seconds < speed.MIN_SECONDS) {
      this.showMessage(speed.TOO_SHORT, true);
      return false;
    }
    const choice = await speedDialog(seconds, doc.rate, this.lastSpeed);
    if (!choice) return false;
    this.lastSpeed = choice;
    const { mode, value } = choice;
    if (speed.isIdentity(mode, value)) return false;
    const grows = (speed.newSeconds(mode, value, seconds, doc.rate) - seconds) * doc.rate * doc.channels;
    if (this.usedSamples() + grows > MAX_SAMPLES) {
      this.showMessage(TOO_LONG_RESULT, true);
      return false;
    }
    const part = speed.padded(ops.slice(doc.samples, a, b), doc.rate);
    const want = speed.expectedFrames(mode, value, b - a, doc.rate);
    return this.runChange(SPEED_LABELS[mode], SPEED_TARGET, { mode, value }, part, a, b, want);
  }

  /** The selection is background noise only: remember its profile for Reduce noise. */
  learnNoise(): boolean {
    const doc = this.doc;
    if (!doc || this.busy || !doc.hasSelection) return false;
    const [a, b] = doc.selection;
    try {
      this.noise = { rate: doc.rate, profile: noise.learn(ops.slice(doc.samples, a, b), doc.rate) };
    } catch (e) {
      this.showMessage(e instanceof Error ? e.message : String(e), true);
      return false;
    }
    const seconds = Math.min((b - a) / doc.rate, noise.LEARN_MAX_SECONDS);
    this.showMessage(`Noise learned from ${ops.formatTime(seconds)}. Now select what to clean (or nothing, for everything) and press “Reduce noise…”.`);
    this.update();
    return true;
  }

  /** The learned profile, if it was learned at this sound's sample rate. */
  private noiseProfile(): Float64Array | null {
    return this.noise && this.doc && this.noise.rate === this.doc.rate ? this.noise.profile : null;
  }

  private async reduceNoise(): Promise<boolean> {
    const doc = this.doc!;
    const profile = this.noiseProfile();
    if (!profile) {
      this.showMessage(noise.NO_PROFILE, true);
      return false;
    }
    const [a, b] = doc.span();
    const choice = await noiseDialog(this.lastNoise);
    if (!choice) return false;
    this.lastNoise = choice;
    const values = { profile: JSON.stringify(Array.from(profile)), reduce_db: choice.reduceDb, sensitivity_db: choice.sensitivityDb, smoothing: choice.smoothing };
    return this.runChange("Reduce noise", DENOISE_TARGET, values, ops.slice(doc.samples, a, b), a, b, b - a);
  }

  /** Sends `part` to the worker's `target`; the answer (fitted to `want` frames) replaces
   *  [a, b) as one undo step called `label`. Cancel and leaving the page drop it. */
  private async runChange(label: string, target: typeof SPEED_TARGET, values: Values, part: Float32Array[], a: number, b: number, want: number): Promise<boolean> {
    const doc = this.doc!;
    const run = ++this.changeRun;
    this.busy = this.changing = true;
    this.update();
    try {
      const wav = writeWav(doc.rate, part, 32);
      const input: InputFile = { file: new File([wav as BlobPart], "part.wav", { type: "audio/wav" }), name: "part.wav", ext: "wav", family: "audio" };
      const [out] = await this.opts.client.run(target, [input], values, (p, m) => {
        if (run !== this.changeRun) return;
        this.cancelButton.hidden = false; // our job is the one running now
        this.showMessage(`${m || "Changing the sound…"} ${Math.round(p * 100)} %`);
      });
      if (run !== this.changeRun) return false; // left the page meanwhile
      const { rate, samples } = readWav(out.data);
      if (rate !== doc.rate || !ops.frames(samples)) throw new Error(`Couldn't do “${label}”.`);
      this.showMessage("");
      return doc.replacePart(label, a, b, speed.fitLength(ops.fitChannels(samples, doc.channels), want));
    } catch (e) {
      if (run !== this.changeRun) return false;
      if (e instanceof JobCancelled) this.showMessage("Cancelled: nothing was changed.");
      else {
        console.error(e);
        this.showMessage(e instanceof Error && e.message ? e.message : `Couldn't do “${label}”.`, true);
      }
      return false;
    } finally {
      if (run === this.changeRun) {
        this.busy = this.changing = false;
        this.cancelButton.hidden = true;
      }
    }
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
    const n = this.length();
    if (b > a) this.play(a, b);
    else this.play(a < n ? a : 0, n);
  }

  /** Middle click: playback moves there (stays paused if it was); the cursor stays put. */
  seek(frame: number): void {
    if (!this.doc || this.busy) return;
    const n = this.length();
    this.play(Math.min(frame, Math.max(0, n - 1)), n, !!this.player?.paused);
  }

  /** Play frames [start, end); `paused`: the context is suspended already (a paused player
   * moved by a middle click) and stays so until Play resumes it. */
  play(start: number, end: number, paused = false): void {
    this.stop(paused);
    const doc = this.doc;
    if (!doc || end <= start) return;
    try {
      this.ctx ??= new AudioContext();
      // two or more tracks: the audible ones mixed, just the part being played
      const part = this.mixing ? tracks.mix(this.tracks, start, end) : doc.samples.map((ch) => ch.subarray(start, end));
      const buf = this.ctx.createBuffer(part.length, end - start, doc.rate);
      part.forEach((ch, c) => buf.copyToChannel(ch as Float32Array<ArrayBuffer>, c));
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      if (!this.gain) {
        this.gain = this.ctx.createGain();
        this.gain.connect(this.ctx.destination);
      }
      this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
      this.gain.gain.value = gainFor(this.volume);
      src.connect(this.gain);
      if (!paused) void this.ctx.resume();
      src.start();
      const player = { src, origin: start, end, t0: this.ctx.currentTime, paused };
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
    const before = this.playHead;
    this.playHead = this.position();
    if (this.playHead !== null) this.follow(before, this.playHead);
    this.requestDraw();
    requestAnimationFrame(() => this.tick());
  }

  // ---------------------------------------------------------------- recording
  get recording(): boolean {
    return !!this.rec;
  }

  async toggleRecord(): Promise<boolean> {
    return this.rec ? this.stopRecording() : this.startRecording();
  }

  /** Record from the microphone; Stop recording puts the take at the cursor. */
  async startRecording(): Promise<boolean> {
    if (this.busy || this.rec) return false;
    this.stop();
    this.busy = true; // while the browser asks for the microphone
    this.update();
    const doc = this.doc;
    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new DOMException("", "NotFoundError");
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      if (this.doc !== doc || !this.active) throw new DOMException("", "AbortError"); // left meanwhile
      // record at the sound's own rate where the browser can (Firefox can't: then converted after)
      let source: MediaStreamAudioSourceNode;
      try {
        ctx = new AudioContext(doc ? { sampleRate: doc.rate } : {});
        source = ctx.createMediaStreamSource(stream);
      } catch {
        void ctx?.close();
        ctx = new AudioContext();
        source = ctx.createMediaStreamSource(stream);
      }
      const channels = doc ? doc.channels : record.NEW_CHANNELS;
      const rate = ctx.sampleRate;
      const [a, b] = doc ? doc.selection : [0, 0];
      const others = this.usedSamples() - (doc ? doc.frames * doc.channels : 0); // the other tracks share the limit
      let room = record.room(MAX_SAMPLES - others, doc?.frames ?? 0, doc?.channels ?? 0, b - a, channels);
      if (doc && doc.rate !== rate) room = Math.floor((room * rate) / doc.rate);
      if (this.recordRoom !== null) room = Math.min(room, this.recordRoom);
      if (room <= 0) throw new Error(record.NO_ROOM);
      const url = URL.createObjectURL(new Blob([RECORDER_WORKLET], { type: "text/javascript" }));
      try {
        await ctx.audioWorklet.addModule(url);
      } finally {
        URL.revokeObjectURL(url);
      }
      await ctx.resume(); // made after an await (the permission prompt): it may start suspended
      if (this.doc !== doc || !this.active) throw new DOMException("", "AbortError");
      const node = new AudioWorkletNode(ctx, "umc-recorder", { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: channels, channelCountMode: "explicit", channelInterpretation: "speakers" });
      const mute = ctx.createGain();
      mute.gain.value = 0; // the worklet only runs when something pulls it
      source.connect(node).connect(mute).connect(ctx.destination);
      const rec: Recording = { take: new record.Take(rate, channels, room), at: [a, b], stream, ctx, node, flushed: null };
      node.port.onmessage = (e: MessageEvent<Float32Array[] | "flushed">) => {
        if (e.data === "flushed") return rec.flushed?.();
        if (this.rec !== rec) return;
        const more = rec.take.add(e.data);
        this.recordLevel.value = rec.take.level;
        this.showRecording();
        if (!more) void this.stopRecording(record.FULL);
      };
      stream.getAudioTracks()[0]?.addEventListener("ended", () => {
        if (this.rec === rec) void this.stopRecording("The microphone stopped, so the recording ended.");
      });
      this.rec = rec;
      this.recordLevel.min = 0;
      this.recordLevel.max = 1;
      this.recordLevel.value = 0;
      this.recordLevel.hidden = false;
      this.showRecording();
      return true;
    } catch (e) {
      stream?.getTracks().forEach((t) => t.stop());
      void ctx?.close();
      const name = e instanceof DOMException ? e.name : "";
      if (name !== "AbortError") {
        if (!(e instanceof DOMException)) console.error(e);
        const text = name === "NotAllowedError" || name === "SecurityError" ? record.DENIED
          : e instanceof Error && e.message === record.NO_ROOM ? record.NO_ROOM : record.NO_INPUT;
        this.showMessage(text, true);
      }
      return false;
    } finally {
      this.busy = !!this.rec;
      this.update();
    }
  }

  private showRecording(): void {
    if (this.rec) this.showMessage(`Recording… ${ops.formatTime(this.rec.take.seconds)} — press “■ Stop recording” to finish.`);
  }

  /** End the recording and put the take in (one undo step). `now` skips waiting for the last
   *  few milliseconds the microphone still holds (leaving the page). False if nothing came. */
  async stopRecording(note = "", now = false): Promise<boolean> {
    const rec = this.rec;
    if (!rec) return false;
    if (!now) {
      await new Promise<void>((done) => {
        rec.flushed = done;
        rec.node.port.postMessage("flush");
        setTimeout(done, 500); // the worklet never answers if the context already stopped
      });
      if (this.rec !== rec) return false;
    }
    this.rec = null;
    rec.node.port.onmessage = null;
    rec.stream.getTracks().forEach((t) => t.stop());
    void rec.ctx.close();
    this.recordLevel.hidden = true;
    const take = rec.take;
    let samples = take.samples();
    if (!take.frames) {
      this.busy = false;
      this.showMessage(note || "Nothing was recorded.", !!note);
      this.update();
      return false;
    }
    if (!this.doc) this.setDocument(new AudioDoc(take.rate, Array.from({ length: take.channels }, () => new Float32Array(0))), []);
    const doc = this.doc!;
    samples = ops.fitChannels(samples, doc.channels);
    if (take.rate !== doc.rate) {
      this.showMessage("Converting the recording to the sound's sample rate…");
      this.converting = !now;
      try {
        samples = now ? resampleLinear(samples, take.rate, doc.rate) : await resample(samples, take.rate, doc.rate);
      } catch (e) {
        console.error(e);
        samples = resampleLinear(samples, take.rate, doc.rate); // the browser's resampler failed
      } finally {
        this.converting = false;
      }
    }
    this.busy = false;
    if (this.doc === doc && samples[0].length) {
      doc.setSelection(...(doc.frames ? rec.at : [0, 0] as [number, number])); // wherever the cursor went since
      doc.paste(samples, record.LABEL);
      this.clamp();
      this.changed();
    }
    this.showMessage(note);
    this.update();
    return true;
  }

  stop(keepSuspended = false): void {
    const p = this.player;
    if (!p) return;
    this.player = null;
    p.src.onended = null;
    try {
      p.src.stop();
    } catch {
      /* never started */
    }
    if (p.paused && !keepSuspended) void this.ctx?.resume();
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
    if (!doc || this.busy || !this.length()) return;
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
    const states = this.tracks.map((t) => [t.doc, t.doc.state] as const);
    const rev = this.tracksRev;
    const fileName = `${save.name}.${save.format}`;
    const samples = this.mixing ? tracks.mix(this.tracks) : doc.samples;
    const loud = this.mixing ? tracks.peakDb(samples) : -Infinity;
    this.busy = true;
    this.update();
    this.showMessage(this.mixing ? `Saving the mix of ${this.tracks.length} tracks as “${fileName}”…` : `Saving “${fileName}”…`);
    try {
      const wav = writeWav(doc.rate, samples.map((ch) => ch.map((v) => Math.max(-1, Math.min(1, v)))), 32);
      const conv = converterById("audio-file");
      const input: InputFile = { file: new File([wav as BlobPart], `${save.name}.wav`, { type: "audio/wav" }), name: `${save.name}.wav`, ext: "wav", family: "audio" };
      const target = conv.targets([input]).find((t) => t.id === save.format);
      if (!target) throw new Error(`Can't save as ${save.format.toUpperCase()}.`);
      const values = { ...defaults(conv.options), ...(LOSSY.has(save.format) ? { bitrate: save.bitrate } : {}) };
      let [out] = await this.opts.client.run(target, [input], values, (p) => this.showMessage(`Saving “${fileName}”… ${Math.round(p * 100)} %`));
      out = await this.withTags(out, save.format, fileName);
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
      for (const [d, s] of states) d.markSavedAt(s);
      if (this.tracksRev === rev) this.tracksSavedRev = rev;
      if (loud > 0) this.showMessage(`Saved “${fileName}”. ${tracks.LOUD.replace("{db}", `+${loud.toFixed(1)} dB`)}`, true);
      else this.showMessage(`Saved “${fileName}”.`);
    } catch (e) {
      console.error(e);
      this.showMessage(e instanceof Error && e.message ? `Couldn't save: ${e.message}` : "Couldn't save the sound.", true);
    } finally {
      this.busy = false;
      this.update();
    }
  }

  /** `out` with the opened files' song details written in (MP3 / M4A / FLAC / OGG / Opus).
   *  A tag problem never costs the sound: it is then saved without them. */
  private async withTags(out: OutputFile, format: string, fileName: string): Promise<OutputFile> {
    const edit = editFor(this.tags, format);
    if (!edit) return out;
    const song: InputFile = { file: new File([out.data as BlobPart], fileName, { type: out.type }), name: fileName, ext: format, family: "audio" };
    const run = async (cover: File | null) => {
      const [tagged] = await this.opts.client.run(MUSIC_TAGS_TARGET, [song], { edit: JSON.stringify({ values: edit.values }), cover }, () => {});
      return { ...tagged, type: out.type };
    };
    if (edit.cover) {
      const png = edit.cover[0] === 0x89;
      const cover = new File([edit.cover as BlobPart], png ? "cover.png" : "cover.jpg", { type: png ? "image/png" : "image/jpeg" });
      try {
        return await run(cover);
      } catch (e) {
        if (e instanceof JobCancelled) throw e;
        console.warn("Couldn't copy the cover picture; keeping the other details", e);
      }
    }
    if (!Object.keys(edit.values).length) return out;
    try {
      return await run(null);
    } catch (e) {
      if (e instanceof JobCancelled) throw e;
      console.warn("Couldn't copy the song details", e);
      return out;
    }
  }

  // ---------------------------------------------------------------- state
  update(): void {
    const doc = this.doc;
    const ready = !!doc && !this.busy;
    const sel = ready && doc!.hasSelection;
    const has = ready && doc!.frames > 0;
    const playable = ready && this.length() > 0;
    const enabled: Record<string, boolean> = {
      cut: sel, copy: sel, paste: ready && !!this.clipboard, delete: sel, trim: sel, silence: sel,
      fade_in: sel, fade_out: sel, amplify: has, normalize: has, reverse: has, speed: has, learn_noise: sel, reduce_noise: has && !!this.noiseProfile(),
      zoom_in: ready, zoom_out: ready, zoom_sel: sel, fit: ready, spectrogram: !!doc,
      home: playable, end: playable, play: playable, stop: this.playing, record: !!this.rec || !this.busy,
      undo: ready && doc!.canUndo(), redo: ready && doc!.canRedo(),
      save: playable, save_as: playable, open: !this.busy, back: true,
    };
    for (const [key, b] of this.buttons) b.disabled = !enabled[key];
    // switching / adding / removing tracks waits while busy; mixing settings don't
    for (const b of this.tracksEl.querySelectorAll<HTMLButtonElement>("[data-busy-off]")) b.disabled = this.busy;
    this.syncTracks();
    this.buttons.get("play")!.textContent = this.player && !this.player.paused ? "❚❚ Pause" : "▶ Play";
    this.buttons.get("record")!.textContent = this.rec ? "■ Stop recording" : "● Record";
    this.buttons.get("undo")!.title = ready && doc!.canUndo() ? `Undo ${doc!.undoLabel()} (Ctrl+Z)` : "Undo (Ctrl+Z)";
    this.buttons.get("redo")!.title = ready && doc!.canRedo() ? `Redo ${doc!.redoLabel()} (Ctrl+Y)` : "Redo (Ctrl+Y)";
    this.selectionLabel.hidden = !doc;
    for (const box of this.timeBoxes.values()) box.disabled = !ready;
    if (!doc) {
      this.infoLabel.textContent = "";
      this.title.textContent = "";
      this.requestDraw();
      return;
    }
    const t = (f: number) => ops.formatTime(f / doc.rate);
    const [a, b] = doc.selection;
    for (const [key, frames] of [["start", a], ["end", b], ["length", b - a]] as const) {
      const text = t(frames);
      const box = this.timeBoxes.get(key)!;
      // never overwrite what someone is typing
      if (document.activeElement !== box || box.value === this.shownTimes.get(key)) box.value = text;
      this.shownTimes.set(key, text);
    }
    const layout = doc.channels === 1 ? "Mono" : doc.channels === 2 ? "Stereo" : `${doc.channels} channels`;
    this.infoLabel.textContent = `${doc.rate / 1000} kHz · ${layout} · ${ops.formatTime(doc.duration)}${this.tracks.length > 1 ? ` · mix of ${this.tracks.length} tracks: ${t(this.length())}` : ""}${this.dirty ? " · Unsaved changes" : ""}`;
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

  private drawFreqLabels(g: CanvasRenderingContext2D, bottom: number, laneH: number): void {
    const rate = this.doc!.rate;
    g.fillStyle = "rgba(255, 255, 255, 0.75)";
    g.font = "10px system-ui, sans-serif";
    g.textBaseline = "middle";
    for (const hz of spectrum.LABELS) {
      if (hz >= rate / 2) continue;
      const y = bottom - spectrum.rowOf(hz, rate, 1) * laneH;
      g.fillRect(0, Math.round(y), 4, 1);
      g.fillText(hz >= 1000 ? `${hz / 1000} kHz` : `${hz} Hz`, 6, y);
    }
  }

  /** Spectrogram (true) or waveform lanes; remembered for next time unless `remember` is false. */
  setSpectrogram(on: boolean, remember = true): void {
    this.spectrogram = on;
    this.buttons.get("spectrogram")?.setAttribute("aria-pressed", String(on));
    if (remember) {
      try {
        localStorage.setItem(SPECTROGRAM_KEY, on ? "1" : "0");
      } catch {
        /* fine: just not remembered */
      }
    }
    this.draw();
  }

  /** One canvas per channel (columns × rows, lowest pitch at the bottom), kept until the sound,
   *  the view or the size changes. */
  private spectrogramImages(width: number, laneH: number): HTMLCanvasElement[] {
    const doc = this.doc!;
    const rows = Math.max(1, Math.min(spectrum.MAX_ROWS, Math.floor(laneH)));
    const fixed = `${doc.revision}/${width}/${rows}`; // what must be drawn again at once
    const key = `${fixed}/${this.start}/${this.fpp}`;
    if (key === this.spectroKey) return this.spectro;
    // only the view moved: while that happens quickly, stretch the last images and compute once
    // it settles (an edit or a new size is drawn at once)
    if (this.spectro.length && this.spectroKey.startsWith(`${fixed}/`) && performance.now() - this.spectroAt < SETTLE_MS) {
      clearTimeout(this.spectroTimer);
      this.spectroTimer = window.setTimeout(() => this.draw(), SETTLE_MS);
      return this.spectro;
    }
    const step = spectrum.columnStep(width);
    const { k, levels } = spectrum.columns(doc.samples, doc.rate, this.start, this.fpp * step, Math.ceil(width / step), rows);
    this.spectroPlace = { first: this.start, perColumn: this.fpp * step };
    this.spectroAt = performance.now();
    this.spectro = k ? levels.map((lv) => {
      const c = document.createElement("canvas");
      c.width = k;
      c.height = rows;
      const img = new ImageData(k, rows);
      for (let col = 0; col < k; col++) {
        for (let r = 0; r < rows; r++) {
          const t = Math.round(lv[col * rows + r] * 255) * 3;
          const at = ((rows - 1 - r) * k + col) * 4;
          img.data[at] = COLOURS[t]; img.data[at + 1] = COLOURS[t + 1]; img.data[at + 2] = COLOURS[t + 2]; img.data[at + 3] = 255;
        }
      }
      c.getContext("2d")?.putImageData(img, 0, 0);
      return c;
    }) : [];
    this.spectroKey = key;
    return this.spectro;
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
    const images = this.spectrogram ? this.spectrogramImages(Math.ceil(w), laneH) : [];
    for (let c = 0; c < doc.channels; c++) {
      const top = RULER_H + c * (laneH + LANE_GAP);
      g.fillStyle = col.alt;
      g.fillRect(0, top, endX, laneH);
      if (images.length) {
        const { first, perColumn } = this.spectroPlace;
        g.drawImage(images[c], (first - this.start) / this.fpp, top, (images[c].width * perColumn) / this.fpp, laneH);
      }
      if (b > a) {
        g.fillStyle = images.length ? "#ffffff" : col.accent;
        g.globalAlpha = images.length ? 0.24 : 0.18;
        g.fillRect(x0, top, x1 - x0, laneH);
        g.globalAlpha = 1;
      }
      if (images.length) {
        this.drawFreqLabels(g, top + laneH, laneH);
        continue;
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

interface NoiseChoice { reduceDb: number; sensitivityDb: number; smoothing: number }

/** How much to reduce the learned noise; null = cancelled. */
async function noiseDialog(last: NoiseChoice): Promise<NoiseChoice | null> {
  const num = (label: string, value: number, min: number, max: number, step: number) =>
    h("input", { type: "number", value: String(value), min: String(min), max: String(max), step: String(step), ariaLabel: label });
  const reduce = num("Reduce by", last.reduceDb, noise.REDUCE_MIN, noise.REDUCE_MAX, 1);
  const sens = num("Sensitivity", last.sensitivityDb, noise.SENSITIVITY_MIN, noise.SENSITIVITY_MAX, 0.5);
  const smooth = num("Smoothing", last.smoothing, noise.SMOOTHING_MIN, noise.SMOOTHING_MAX, 1);
  const read = (input: HTMLInputElement, fallback: number, min: number, max: number) => {
    const v = input.value.trim() === "" ? NaN : Number(input.value);
    return Math.max(min, Math.min(max, Number.isFinite(v) ? v : fallback));
  };
  const r = await dialog("noise", "Reduce noise", [
    h("label", { class: "field" }, h("span", {}, "Reduce by"), h("span", { class: "with-unit" }, reduce, "dB")),
    h("label", { class: "field" }, h("span", {}, "Sensitivity"), h("span", { class: "with-unit" }, sens, "dB")),
    h("label", { class: "field" }, h("span", {}, "Smoothing"), h("span", { class: "with-unit" }, smooth, "bands")),
    h("div", { class: "hint" }, "More sensitivity removes more noise but can make music sound watery; smoothing softens that."),
  ], "OK", () => ({
    reduceDb: read(reduce, last.reduceDb, noise.REDUCE_MIN, noise.REDUCE_MAX),
    sensitivityDb: read(sens, last.sensitivityDb, noise.SENSITIVITY_MIN, noise.SENSITIVITY_MAX),
    smoothing: Math.trunc(read(smooth, last.smoothing, noise.SMOOTHING_MIN, noise.SMOOTHING_MAX)),
  }));
  return r ? r.value : null;
}

interface SpeedChoice { mode: speed.SpeedMode; value: number }

const SPEED_LABELS: Record<speed.SpeedMode, string> = { speed: "Change speed", tempo: "Change tempo", pitch: "Change pitch" };
const SPEED_MODES: [speed.SpeedMode, string, string][] = [
  ["speed", "Speed", "Faster and higher, or slower and lower (like a record player)"],
  ["tempo", "Tempo", "Faster or slower, the pitch stays"],
  ["pitch", "Pitch", "Higher or lower, the length stays"],
];
const TOO_LONG_RESULT = "The result would be too long for the audio editor in the browser (about 15 minutes of stereo sound). Change a shorter part, or use the desktop app.";

/** Speed (%), tempo (%) or pitch (semitones) of `seconds` of sound; null = cancelled. */
async function speedDialog(seconds: number, rate: number, last: SpeedChoice): Promise<SpeedChoice | null> {
  const mode = h("select", { ariaLabel: "Change" });
  for (const [id, label] of SPEED_MODES) mode.append(h("option", { value: id, selected: id === last.mode }, label));
  const about = h("div", { class: "hint" });
  const input = h("input", { type: "number", ariaLabel: "Amount" });
  const unit = h("span", {});
  const result = h("div", { class: "hint speed-result" });
  const values: Record<speed.SpeedMode, number> = { speed: 100, tempo: 100, pitch: 0 };
  values[last.mode] = last.value;
  let shown = mode.value as speed.SpeedMode;
  const read = (): SpeedChoice => {
    const m = mode.value as speed.SpeedMode;
    const v = input.value.trim() === "" ? NaN : Number(input.value);
    return { mode: m, value: speed.clamp(m, Number.isFinite(v) ? v : values[m]) };
  };
  const show = () => {
    const m = mode.value as speed.SpeedMode;
    if (m !== shown) {
      const v = input.value.trim() === "" ? NaN : Number(input.value); // typed for the mode shown so far
      if (Number.isFinite(v)) values[shown] = speed.clamp(shown, v);
      shown = m;
    }
    const pitch = m === "pitch";
    input.min = String(pitch ? speed.SEMITONES_MIN : speed.PERCENT_MIN);
    input.max = String(pitch ? speed.SEMITONES_MAX : speed.PERCENT_MAX);
    input.step = pitch ? "0.1" : "1";
    input.value = String(values[m]);
    unit.textContent = pitch ? "semitones" : "%";
    about.textContent = SPEED_MODES.find(([id]) => id === m)![2];
    sync();
  };
  const sync = () => {
    const c = read();
    result.textContent = `${ops.formatTime(seconds)} → ${ops.formatTime(speed.newSeconds(c.mode, c.value, seconds, rate))}`;
  };
  mode.addEventListener("change", show);
  input.addEventListener("input", sync);
  show();
  const r = await dialog("speed", "Speed / pitch", [
    h("label", { class: "field" }, h("span", {}, "Change"), mode),
    about,
    h("label", { class: "field" }, h("span", {}, "By"), h("span", { class: "with-unit" }, input, unit)),
    result,
  ], "OK", read);
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

/** The song details all `files` share, read on the page with Mediabunny (files it can't read
 *  count as untagged). */
async function readSongTags(files: InputFile[]): Promise<CarriedTags> {
  const [{ Input, BlobSource, ALL_FORMATS }, music] = await Promise.all([import("mediabunny"), import("../../properties/music")]);
  const details: (MusicDetails | null)[] = [];
  const covers: (Uint8Array | null)[] = [];
  for (const f of files) {
    let d: MusicDetails | null = null;
    let cover: Uint8Array | null = null;
    if (music.canChange(f.ext)) {
      const input = new Input({ source: new BlobSource(f.file), formats: ALL_FORMATS });
      try {
        const tags = await input.getMetadataTags();
        d = music.detailsFromTags(tags);
        const images = tags.images ?? [];
        cover = (images.find((i) => i.kind === "coverFront") ?? images[0])?.data ?? null;
      } catch (e) {
        console.info(`No readable tags in “${f.name}”`, e);
      } finally {
        input.dispose();
      }
    }
    details.push(d);
    covers.push(cover);
  }
  return carriedTags(details, covers);
}

interface Recording {
  take: record.Take;
  at: [number, number]; // the selection when recording started: the take goes there
  stream: MediaStream;
  ctx: AudioContext;
  node: AudioWorkletNode;
  flushed: (() => void) | null;
}

/** Copies what the microphone hands over and posts it every ~2048 frames; "flush" posts the
 *  rest and answers "flushed". */
const RECORDER_WORKLET = `
class UmcRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.parts = [];
    this.frames = 0;
    this.port.onmessage = () => { this.send(); this.port.postMessage("flushed"); };
  }
  send() {
    if (!this.frames) return;
    const channels = this.parts[0].length;
    const out = Array.from({ length: channels }, () => new Float32Array(this.frames));
    let at = 0;
    for (const part of this.parts) {
      for (let c = 0; c < channels; c++) out[c].set(part[c], at);
      at += part[0].length;
    }
    this.port.postMessage(out, out.map((ch) => ch.buffer));
    this.parts = [];
    this.frames = 0;
  }
  process(inputs) {
    const input = inputs[0];
    if (input && input.length && input[0].length) {
      this.parts.push(input.map((ch) => ch.slice()));
      this.frames += input[0].length;
      if (this.frames >= 2048) this.send();
    }
    return true;
  }
}
registerProcessor("umc-recorder", UmcRecorder);
`;

/** The browser's own resampler (an offline audio context); round(frames × to / from) frames. */
async function resample(x: Float32Array[], from: number, to: number): Promise<Float32Array[]> {
  const frames = x[0].length;
  const want = Math.round((frames * to) / from);
  if (want < 1) return x.map(() => new Float32Array(0));
  const off = new OfflineAudioContext(x.length, want, to);
  const buffer = off.createBuffer(x.length, frames, from);
  x.forEach((ch, c) => buffer.copyToChannel(Float32Array.from(ch), c));
  const src = off.createBufferSource();
  src.buffer = buffer;
  src.connect(off.destination);
  src.start();
  const out = await off.startRendering();
  return speed.fitLength(Array.from({ length: x.length }, (_, c) => out.getChannelData(c)), want);
}

/** A quick linear resample for when there's no time to wait (leaving the page). */
function resampleLinear(x: Float32Array[], from: number, to: number): Float32Array[] {
  const frames = x[0].length;
  const want = Math.round((frames * to) / from);
  if (!frames) return x;
  return x.map((ch) => {
    const out = new Float32Array(want);
    for (let i = 0; i < want; i++) {
      const t = (i * from) / to;
      const j = Math.min(frames - 1, Math.floor(t));
      const f = t - j;
      out[i] = ch[j] * (1 - f) + (ch[Math.min(frames - 1, j + 1)] ?? 0) * f;
    }
    return out;
  });
}

const TRACK_NO_ROOM = "There isn't room for another track: the tracks together would be longer than the audio editor allows.";

const gainText = (db: number) => (db === 0 ? "0 dB" : `${db > 0 ? "+" : ""}${db} dB`);
const panText = (pan: number) => (Math.abs(pan) < 0.005 ? "Centre" : `${pan < 0 ? "L" : "R"} ${Math.round(Math.abs(pan) * 100)}`);
