// The website's video editor page (port of the desktop's gui/videoedit/): top bar, media list,
// preview, inspector, timeline and a status line. Sources are opened on the page (sources.ts) and
// played by player.ts; export arrives in a later step. Loaded on demand from
// ui/app.ts, only behind the test gate.
import { canonicalExt, PROJECT_EXTS } from "../../core/detect";
import { ConversionError, type InputFile } from "../../core/types";
import { addAt, addSources, addText, applyChanges, relink, remove, removeSource, split, update } from "../../videoedit/edits";
import { History } from "../../videoedit/history";
import { setLimitsForTests, type LIMITS } from "../../videoedit/limits";
import { defaultProjectName, keepsDuration, relinked } from "../../videoedit/locate";
import { projectFromJson, projectToJson } from "../../videoedit/projectfile";
import {
  NO_TRANSITION, find, layout, length, newProject, outputFps, textItem, type Project, type Source, type TextItem,
} from "../../videoedit/project";
import { changeLabel, targetOf } from "../../videoedit/schemas";
import { nextSourceId, projectFrom } from "../../videoedit/start";
import type { ConversionClient } from "../../worker/client";
import { backLabel } from "../back";
import { loadBundledFonts } from "./fonts";
import { Inspector } from "./inspector";
import { locateWindow } from "./locate";
import { MediaList } from "./media";
import { Player } from "./player";
import { startTime } from "./playmath";
import { Preview } from "./preview";
import { actionFor, type ActionId } from "./shortcuts";
import { Scanner } from "./scanner";
import { SoundStore } from "./sound";
import { Abandoned, SourceBank } from "./sources";
import { TimelineView } from "./timeline";
import { frameNumber } from "./timelinemath";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const isProjectFile = (f: File) => PROJECT_EXTS.has(canonicalExt(f.name));
const baseName = (path: string): string => path.split(/[\\/]/).pop() ?? path;

export const DISCARD_QUESTION = "Discard unsaved changes to this video project?";

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

export interface VideoEditorOptions {
  client: ConversionClient;
  onBack: () => void;
}

export class VideoEditorPage {
  readonly el: HTMLElement;
  private active = false;
  private message = h("div", { class: "msg" });
  private error = "";
  private buttons = new Map<string, HTMLButtonElement>();
  private timelineBox = h("div", { class: "video-timeline" });
  private timeline: TimelineView;
  private inspector: Inspector;
  private bank: SourceBank;
  /** The bank's decoded sound for the mixer (one per bank). */
  private sound: SoundStore;
  /** Thumbnails and waveforms for the timeline (one per bank). */
  private scanner: Scanner;
  /** What the scanner has found, for the e2e hook (counts, per source id). */
  private found = { thumbs: {} as Record<string, number>, waves: {} as Record<string, number>, scans: {} as Record<string, number> };
  private media: MediaList;
  private preview: Preview;
  private player: Player;
  /** The project being played (an edit, undo or new project stops it). */
  private playingProject: Project | null = null;
  private history: History | null = null;
  private notes: Map<string, string> = new Map();
  /** Every source id this session has used (the opened project's, every file added since, freed
   *  ones too). The bank decodes by id across the whole undo history, so an id is never given to
   *  a second file: an undone state would show the new file's frames. */
  private usedIds = new Set<string>();
  private playhead = 0;
  private selected: string | null = null;
  /** An inspector edit being made: previewed, pushed as one step once it's finished. */
  private pending: { project: Project; label: string } | null = null;
  /** Opening / adding files: one batch after the other. */
  private work: Promise<unknown> = Promise.resolve();
  /** Bumped by every new project and on leaving, so a batch still probing doesn't fill it. */
  private session = 0;
  /** Bumped on leaving only: jobs queued before it never start. */
  private epoch = 0;
  /** The file "Save project as…" picked: "Save project" (Ctrl+S) writes there again. */
  private handle: FileHandle | null = null;
  private saving = false;

  constructor(private opts: VideoEditorOptions) {
    this.bank = new SourceBank(opts.client);
    this.sound = new SoundStore(this.bank.audio);
    this.scanner = this.newScanner();
    this.preview = new Preview(() => this.bank, {
      step: (n) => this.stepFrames(n),
      play: () => this.togglePlay(),
      loop: (on) => (this.player.loop = on),
      volume: (v) => (this.player.volume = v),
    });
    this.player = new Player(() => this.bank, () => this.sound, {
      frame: (paint) => this.preview.showFrame(paint),
      position: (t) => this.playingAt(t),
      ended: () => this.playFinished(),
      notice: (msg) => this.showMessage(msg),
    });
    this.player.volume = this.preview.volume;
    this.media = new MediaList({
      add: (id) => this.addSourceAtPlayhead(id),
      files: () => this.media.picker.click(),
      text: () => this.addText(),
    });
    this.timeline = new TimelineView({
      edit: (p, label) => this.timelineEdit(p, label),
      seek: (t) => this.seek(t),
      select: (id) => this.select(id),
      drop: (id, t, kind, row) => this.addSource(id, t, kind === "music" ? row : 0),
    });
    this.timelineBox.append(this.timeline.el);
    this.inspector = new Inspector({
      change: (changes) => this.inspectorChanged(changes),
      commit: () => this.flushPending(),
    });
    this.media.picker.addEventListener("change", () => {
      const picked = [...(this.media.picker.files ?? [])];
      this.media.picker.value = "";
      if (picked.length) void this.addFiles(picked);
    });
    const btn = (key: string, label: string, title: string, fn: () => void, cls = "btn small") => {
      const b = h("button", { class: cls, title, type: "button" }, label);
      b.addEventListener("click", fn);
      this.buttons.set(key, b);
      return b;
    };
    const top = h("div", { class: "editor-top" },
      backLabel(btn("back", "← Back to Convert", "Leave the video editor", () => this.back(), "link")),
      btn("undo", "Undo", "Undo (Ctrl+Z)", () => this.undo()),
      btn("redo", "Redo", "Redo (Ctrl+Y)", () => this.redo()),
      h("div", { class: "top-tools" },
        h("span", { class: "spacer" }),
        btn("save", "Save project", "Save the project (Ctrl+S)", () => void this.save()),
        btn("save_as", "Save project as…", "Save the project under a new name (Ctrl+Shift+S)", () => void this.saveAs())),
      btn("export", "Export…", "Export comes soon", () => {}, "primary small"));
    for (const k of ["undo", "redo", "save", "save_as", "export"]) this.buttons.get(k)!.disabled = true;
    this.el = h("div", { class: "editor video-editor", hidden: true },
      top,
      h("div", { class: "video-body" },
        h("div", { class: "video-media", ariaLabel: "Media" }, this.media.el),
        h("div", { class: "video-preview", ariaLabel: "Preview" }, this.preview.el),
        h("div", { class: "video-inspector", ariaLabel: "Inspector" }, this.inspector.el),
        this.timelineBox),
      h("div", { class: "editor-status" }, this.message));
    if (location.search.includes("e2e")) (window as unknown as { __videoeditor: unknown }).__videoeditor = this.hook();
  }

  /** The e2e hook (grows with the later steps). */
  private hook() {
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const page = this;
    return {
      project: (): Project | null => page.history?.project ?? null,
      /** What the page shows: the project with an inspector edit still being made. */
      shown: (): Project | null => page.shownProject(),
      get history() {
        const hi = page.history;
        return { canUndo: hi?.canUndo ?? false, undoLabel: hi?.undoLabel ?? "", dirty: hi?.dirty ?? false, steps: hi?.steps ?? 0 };
      },
      spans: () => {
        const p = page.shownProject();
        return p ? Object.fromEntries(layout(p)) : {};
      },
      view: () => page.timeline.view(),
      rows: () => page.timeline.rows(),
      label: (id: string) => page.timeline.labelOf(id),
      painted: () => page.timeline.itemIds(),
      /** A new project with `n` short texts (for the item limit). */
      loadBig: (n: number) => {
        const texts: TextItem[] = [];
        for (let i = 0; i < n; i++) texts.push(textItem(`big${i}`, i, i + 0.5));
        const base = page.history?.project ?? newProject();
        page.setProject({ ...base, main: [], music_rows: [[]], text_rows: [texts] });
      },
      setLimits: (l: Partial<typeof LIMITS> | null) => setLimitsForTests(l),
      get playhead() {
        return page.playhead;
      },
      get selected() {
        return page.selected;
      },
      select: (id: string | null) => page.select(id),
      seek: (t: number) => page.seek(t),
      /** The page can decode that source (straight or through its copy). */
      decodes: (id: string) => page.bank.has(id),
      /** SourceBank.picture: the size of what it gives and a few of its pixels (null: nothing). */
      picture: async (id: string, time: number, height: number) => {
        const got = await page.bank.picture(id, time, height);
        if (!got) return null;
        const c = new OffscreenCanvas(got.width, got.height);
        const ctx = c.getContext("2d")!;
        ctx.drawImage(got.image, 0, 0);
        const data = ctx.getImageData(0, 0, got.width, got.height).data;
        const samples: number[] = [];
        for (let k = 0; k < 64; k++) {
          const x = Math.floor((((k % 8) + 0.5) * got.width) / 8);
          const y = Math.floor(((Math.floor(k / 8) + 0.5) * got.height) / 8);
          const i = (y * got.width + x) * 4;
          samples.push(data[i], data[i + 1], data[i + 2]);
        }
        return { width: got.width, height: got.height, samples };
      },
      /** Resolves once the still preview has drawn (or dropped) everything asked of it. */
      still: (): Promise<void> => page.preview.idle(),
      /** The preview's pixel at fractions 0…1 of the frame's width / height (null: none shown). */
      pixel: (fx: number, fy: number): [number, number, number, number] | null => page.preview.pixel(fx, fy),
      /** The shown project's length. */
      length: (): number => {
        const p = page.shownProject();
        return p ? length(p) : 0;
      },
      /** Play from the playhead (like ▶ while stopped). */
      play: () => {
        if (!page.player.playing) page.togglePlay();
      },
      stop: () => page.stopPlayback(),
      get playing() {
        return page.player.playing;
      },
      stats: () => page.player.stats(),
      /** What the scanner found: pictures and wave buckets per source, finished scans per source. */
      scanned: () => ({ thumbs: { ...page.found.thumbs }, waves: { ...page.found.waves }, scans: { ...page.found.scans } }),
      /** The source got another file in this bank: drop its scan and ask for it again. */
      rescan: (id: string) => {
        page.dropScan(id);
        page.render(false);
      },
      /** Play as if there were no sound device. */
      forceWallClock: () => page.player.forceWallClock(),
    };
  }

  show(): void {
    if (!this.active) {
      document.addEventListener("keydown", this.onKey);
      window.addEventListener("beforeunload", this.onUnload);
    }
    this.active = true;
    this.el.hidden = false;
    void loadBundledFonts().then(() => this.inspector.updateFontNote(), () => undefined);
    this.render();
  }

  hide(): void {
    this.timeline.endGesture(); // a drag or seek in progress lands nowhere
    this.flushPending();
    document.removeEventListener("keydown", this.onKey);
    window.removeEventListener("beforeunload", this.onUnload);
    this.active = false;
    this.el.hidden = true;
    this.leaveSession(); // leaving drops the project, its decoders and a batch still opening
    this.render();
  }

  /** Start over from outside the work queue (leaving, the empty project): a batch still opening
   *  stops before its next file, its running copy is cancelled, whatever it opened is dropped,
   *  and the next open doesn't wait behind it. */
  private leaveSession(): void {
    this.session++;
    this.epoch++;
    this.stopPlayback(false);
    this.player.dispose(); // its AudioContext goes with the session
    this.preview.clear(); // a still being made for the old project never paints
    this.freshBank();
    this.work = Promise.resolve();
    this.found.scans = {}; // a new visit counts from 0
    this.history = null;
    this.pending = null;
    this.selected = null;
    this.notes = new Map();
    this.usedIds = new Set();
    this.handle = null;
  }

  private newScanner(): Scanner {
    return new Scanner(() => this.bank, {
      thumbs: (id, images) => {
        this.found.thumbs[id] = images.length;
        this.timeline.setThumbnails(id, images);
      },
      wave: (id, peaks) => {
        this.found.waves[id] = peaks.length;
        this.timeline.setWaveform(id, peaks);
      },
      done: (id) => (this.found.scans[id] = (this.found.scans[id] ?? 0) + 1),
    });
  }

  /** A source id got another file: its scanned pictures go (timeline first, then the scanner). */
  private dropScan(id: string): void {
    this.timeline.forgetSource(id);
    this.scanner.forget(id);
    delete this.found.thumbs[id];
    delete this.found.waves[id];
  }

  /** A source's decoders and scanned pictures go (relink / remove). */
  private forgetSource(id: string): void {
    this.bank.forget(id);
    this.sound.forget(id);
    this.dropScan(id);
  }

  private freshBank(): void {
    this.stopPlayback(false); // its streams read the old bank
    this.timeline.forgetAllSources();
    this.scanner.dispose();
    this.found = { thumbs: {}, waves: {}, scans: this.found.scans };
    this.bank.abandon();
    this.sound.dispose();
    this.bank = new SourceBank(this.opts.client);
    this.sound = new SoundStore(this.bank.audio);
    this.scanner = this.newScanner();
  }

  get shown(): boolean {
    return this.active;
  }

  /** Unsaved changes: in the history, or an inspector edit still being made. */
  private get unsaved(): boolean {
    return !!this.history && (this.history.dirty || this.pending !== null);
  }

  /** Closing / reloading the tab with unsaved changes asks first (like the other editors). */
  private readonly onUnload = (e: BeforeUnloadEvent): void => {
    if (this.active && this.unsaved) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  /** False when the visitor wants to keep an unsaved project (the audio editor's question). */
  maybeLeave(): boolean {
    this.flushPending();
    if (!this.history?.dirty) return true;
    return window.confirm(DISCARD_QUESTION);
  }

  openEmpty(): void {
    this.leaveSession();
    this.setProject(newProject());
    this.showMessage("");
  }

  /** A new project from the files (ids s1…); false when none of them can be used
   *  (true when it was superseded by leaving: the caller must not react to it). */
  open(files: InputFile[]): Promise<boolean> {
    return this.queue(() => this.openFiles(files.map((f) => f.file)));
  }

  /** Dropped / picked files: into the open project as one "Add files" step, else a new project
   *  (a lone project file replaces the open project, asking first when it has unsaved changes). */
  addFiles(files: File[]): Promise<boolean> {
    return this.queue(() => {
      if (files.length === 1 && isProjectFile(files[0])) return this.openProject(files[0]);
      return this.history ? this.addToProject(files) : this.openFiles(files);
    });
  }

  private queue(job: () => Promise<boolean>): Promise<boolean> {
    const epoch = this.epoch;
    const start = () => (epoch === this.epoch ? job() : Promise.resolve(true));
    const run = this.work.then(start, start);
    this.work = run.catch(() => undefined);
    return run;
  }

  private async openFiles(files: File[]): Promise<boolean> {
    if (files.length === 1 && isProjectFile(files[0])) return this.openProject(files[0]);
    if (files.some(isProjectFile)) {
      this.showMessage("Open a video project on its own, without other files.", true);
      return false;
    }
    // a job of the queue: nothing else of this page is running, so the old bank only has decoders
    const session = ++this.session;
    this.freshBank();
    this.history = null;
    this.pending = null;
    this.notes = new Map();
    this.usedIds = new Set();
    this.handle = null;
    this.render();
    const { sources, messages } = await this.probe(files, session);
    if (session !== this.session) return true; // superseded (left meanwhile): nothing to report, the bank was abandoned
    if (!sources.length) {
      this.showMessage(messages.join(" ") || "Nothing to edit.", true);
      return false;
    }
    let project: Project;
    try {
      project = projectFrom(sources);
    } catch (e) {
      this.showMessage(e instanceof Error ? e.message : String(e), true);
      return false;
    }
    this.setProject(project);
    this.showMessage(messages.join(" "), messages.length > 0);
    return true;
  }

  /** A saved project (editor_page._open_project_file): every source is picked again in the locate
   *  window; the file's project is loaded first, the fixes are one "Fix missing files" step.
   *  Cancel (or an unreadable file) leaves the page as it was. */
  private async openProject(file: File): Promise<boolean> {
    const session = this.session;
    let project: Project;
    try {
      project = projectFromJson(await file.text());
    } catch (e) {
      this.showMessage(e instanceof ConversionError ? e.message : `Couldn't read “${file.name}”.`, true);
      return false;
    }
    if (session !== this.session) return true;
    if (this.history && !this.maybeLeave()) return false;
    let picks = new Map<string, File | null>();
    if (project.sources.length) {
      const got = await locateWindow(project.sources);
      if (session !== this.session) return true; // left meanwhile
      if (!got) {
        this.showMessage("Opening the project was cancelled.", true);
        return false;
      }
      picks = got;
    }
    const mine = ++this.session;
    this.freshBank();
    this.history = null;
    this.pending = null;
    this.notes = new Map();
    this.usedIds = new Set(project.sources.map((s) => s.id));
    this.handle = null;
    this.render();
    const bank = this.bank;
    let fixed = project;
    const messages: string[] = [];
    for (const s of project.sources) {
      if (mine !== this.session) return true;
      const name = baseName(s.path);
      const f = picks.get(s.id) ?? null;
      if (f) {
        const opening = `Opening “${f.name}”…`;
        this.showMessage(opening);
        try {
          const opened = await bank.open(f, s.id, (fr, msg) => this.showMessage(`${msg || opening} ${Math.round(fr * 100)} %`));
          if (mine !== this.session) return true;
          if (opened.source.kind === s.kind && keepsDuration(s, opened.source)) {
            fixed = relink(fixed, s.id, relinked(s, opened.source));
            this.dropScan(s.id); // the id has a new file: its old thumbnails / wave go
            if (opened.note) this.notes = new Map(this.notes).set(s.id, opened.note);
            continue;
          }
          this.forgetSource(s.id);
          messages.push(`Couldn't use “${f.name}” for “${name}”.`);
        } catch (e) {
          if (e instanceof Abandoned || mine !== this.session) return true;
          if (!(e instanceof ConversionError)) console.error(`Couldn't open ${f.name}`, e);
          const reason = e instanceof ConversionError ? ` ${e.message}` : "";
          messages.push(`Couldn't use “${f.name}” for “${name}”.${reason}`);
        }
      } else messages.push(`Removed “${name}” from the project.`);
      fixed = removeSource(fixed, s.id);
    }
    this.history = new History(project);
    if (fixed !== project) this.history.push(fixed, "Fix missing files");
    this.playhead = 0;
    this.selected = null;
    this.render();
    this.showMessage(messages.join(" "), messages.length > 0);
    return true;
  }

  // ---------------------------------------------------------------- saving
  /** To the file picked before, else like "Save project as…". */
  async save(): Promise<boolean> {
    this.flushPending();
    if (!this.history || this.saving) return false;
    if (!this.handle) return this.saveAs();
    return this.write(this.handle, this.handle.name ?? defaultProjectName(this.history.project));
  }

  /** The browser's save window where there is one (remembered for Ctrl+S), else a download. */
  async saveAs(): Promise<boolean> {
    this.flushPending();
    const hi = this.history;
    if (!hi || this.saving) return false;
    const name = defaultProjectName(hi.project);
    const w = window as unknown as { showSaveFilePicker?: (o: object) => Promise<FileHandle> };
    if (typeof w.showSaveFilePicker !== "function") return this.write(null, name);
    let handle: FileHandle;
    try {
      handle = await w.showSaveFilePicker({
        suggestedName: name,
        types: [{ description: "Video project", accept: { "application/json": [".umcvideo"] } }],
      });
    } catch (err) {
      if ((err as { name?: string } | null)?.name !== "AbortError") {
        console.error("The save window couldn't be opened", err);
        this.showMessage("The browser couldn't open its save window. Try again.", true);
      }
      return false;
    }
    if (hi !== this.history) return false; // the project was left meanwhile
    this.handle = handle;
    return this.write(handle, handle.name ?? name);
  }

  private async write(handle: FileHandle | null, name: string): Promise<boolean> {
    const hi = this.history!;
    const project = hi.project;
    const blob = new Blob([projectToJson(project)], { type: "application/json" });
    this.saving = true;
    try {
      if (handle) {
        const out = await handle.createWritable();
        await out.write(blob);
        await out.close();
      } else {
        const url = URL.createObjectURL(blob);
        const a = h("a", { href: url, download: name });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
    } catch (err) {
      console.error("Saving the project failed", err);
      this.showMessage(`Couldn't save “${name}”.`, true);
      return false;
    } finally {
      this.saving = false;
    }
    if (hi === this.history && hi.project === project) hi.markSaved();
    this.showMessage(`Saved “${name}”.`);
    return true;
  }

  private async addToProject(files: File[]): Promise<boolean> {
    const session = this.session;
    const { sources, messages } = await this.probe(files, session);
    if (session !== this.session || !this.history) return false; // left: that bank was abandoned
    if (sources.length) this.apply((p) => addSources(p, sources), "Add files");
    if (messages.length) this.showMessage(messages.join(" "), true);
    return sources.length > 0;
  }

  /** Probe one file after the other; messages for those that can't be used. Stops between files
   *  once `session` is over (the bank it used is abandoned then and keeps nothing). */
  private async probe(files: File[], session: number): Promise<{ sources: Source[]; messages: string[] }> {
    const sources: Source[] = [];
    const messages: string[] = [];
    const bank = this.bank;
    const used = this.usedIds; // this session's (a new session gets a new set)
    for (const file of files) {
      if (session !== this.session) break;
      if (isProjectFile(file)) {
        messages.push(`Open “${file.name}” on its own to edit that project.`);
        continue;
      }
      // never one the current project, any undo / redo state or a file tried before has had
      const id = nextSourceId(this.history?.project ?? newProject(), used);
      used.add(id);
      const opening = `Opening “${file.name}”…`;
      this.showMessage(opening);
      try {
        const opened = await bank.open(file, id, (f, msg) => this.showMessage(`${msg || opening} ${Math.round(f * 100)} %`));
        if (session !== this.session) break;
        sources.push(opened.source);
        if (opened.note) this.notes = new Map(this.notes).set(id, opened.note);
      } catch (e) {
        if (e instanceof Abandoned || session !== this.session) break;
        messages.push(e instanceof ConversionError ? e.message : `Couldn't open “${file.name}”.`);
      }
    }
    return { sources, messages };
  }

  private setProject(project: Project): void {
    this.history = new History(project);
    this.pending = null;
    this.playhead = 0;
    this.selected = null;
    this.render();
  }

  // ---------------------------------------------------------------- history (editor_page._apply)
  /** One undo step from `fn` (an inspector edit still being made goes in first); an edit the
   *  project refuses is shown in the status line. Returns the new item's id, if any. */
  private apply(fn: (p: Project) => Project | [Project, string], label: string): string | null {
    if (!this.history) return null;
    this.flushPending();
    let got: Project | [Project, string];
    try {
      got = fn(this.history.project);
    } catch (e) {
      if (!(e instanceof ConversionError)) throw e;
      this.showMessage(e.message, true);
      return null;
    }
    const [project, newId] = Array.isArray(got) ? got : [got, null];
    this.showMessage("");
    this.history.push(project, label);
    if (newId) this.selected = newId;
    this.render();
    return newId;
  }

  /** The inspector edit being made, as its own step (commit, selection change, undo, save, leaving). */
  private flushPending(): void {
    const pending = this.pending;
    this.pending = null;
    if (!pending || !this.history) return;
    this.showMessage("");
    this.history.push(pending.project, pending.label);
    this.render();
  }

  private inspectorChanged(changes: Record<string, unknown>): void {
    const hi = this.history;
    if (!hi) return;
    const base = hi.project;
    const id = this.inspector.selectionId;
    if (!Object.keys(changes).length) {
      this.pending = null;
      this.render(false);
      return;
    }
    try {
      const project = applyChanges(base, id, changes);
      this.pending = { project, label: changeLabel(targetOf(base, id).kind, changes) };
    } catch (e) {
      this.pending = null;
      if (!(e instanceof ConversionError)) console.error("An inspector change couldn't be applied", e);
      this.showMessage(e instanceof ConversionError ? e.message : "That item no longer exists.", true);
      this.inspector.show(base, this.selected);
      this.render(false);
      return;
    }
    this.render(false);
  }

  /** A timeline drag or "+" row, computed from what it shows (which includes a pending edit:
   *  that goes in first, as its own step). */
  private timelineEdit(project: Project, label: string): void {
    if (!this.history) return;
    this.flushPending();
    this.showMessage("");
    this.history.push(project, label);
    this.render();
  }

  // ---------------------------------------------------------------- editing actions
  private addSourceAtPlayhead(id: string): void {
    this.addSource(id, this.playhead, 0);
  }

  private addSource(id: string, t: number, row: number): void {
    const src = this.history?.project.sources.find((s) => s.id === id);
    if (!src) return;
    this.apply((p) => addAt(p, id, t, row), src.kind === "audio" ? "Add music" : "Add clip");
  }

  private addText(): void {
    this.apply((p) => addText(p, this.playhead), "Add text");
  }

  private splitTarget(): string | null {
    const sel = this.selected;
    if (sel !== null && !sel.startsWith("tr:")) return sel;
    if (!this.history) return null;
    for (const [id, span] of layout(this.history.project)) {
      if (span.row_kind === "main" && span.start < this.playhead && this.playhead < span.end) return id;
    }
    return null;
  }

  private split(): void {
    if (!this.history) return;
    this.flushPending();
    const target = this.splitTarget();
    if (target === null) {
      this.showMessage("Put the playhead inside a clip to split it.");
      return;
    }
    this.apply((p) => split(p, target, this.playhead), "Split clip");
  }

  private deleteSelected(): void {
    const sel = this.selected;
    if (sel === null || !this.history) return;
    this.flushPending();
    if (sel.startsWith("tr:")) {
      this.apply((p) => update(p, sel.slice(3), { transition_in: NO_TRANSITION }), "Remove transition");
      return;
    }
    let type: string;
    try {
      type = find(this.history.project, sel).item.type;
    } catch {
      return;
    }
    this.apply((p) => remove(p, sel), type === "text" ? "Delete text" : type === "audio" ? "Delete music" : "Delete clip");
  }

  private undo(): void {
    this.flushPending();
    if (!this.history?.canUndo) return;
    this.history.undo();
    this.render();
  }

  private redo(): void {
    this.flushPending();
    if (!this.history?.canRedo) return;
    this.history.redo();
    this.render();
  }

  private select(id: string | null): void {
    this.flushPending();
    this.selected = id;
    this.render();
  }

  // ---------------------------------------------------------------- playhead
  /** The project as the page shows it: with an inspector edit still being made. */
  private shownProject(): Project | null {
    return this.pending?.project ?? this.history?.project ?? null;
  }

  private seek(t: number): void {
    this.stopPlayback(false); // the still at the seek follows
    const p = this.shownProject();
    const end = p ? length(p) : 0;
    this.playhead = Math.max(0, Math.min(t, end));
    this.timeline.setPlayhead(this.playhead);
    this.viewChanged();
  }

  private stepFrames(n: number): void {
    const p = this.shownProject();
    if (!p) return;
    this.seek((frameNumber(p, this.playhead) + n) / outputFps(p));
  }

  // ---------------------------------------------------------------- playback (editor_page._play_toggled …)
  private togglePlay(): void {
    if (this.player.playing) {
      this.stopPlayback();
      return;
    }
    this.flushPending();
    const p = this.shownProject();
    if (!p || !this.active || length(p) <= 0) return;
    const size = this.preview.playSize(p);
    if (!size) return; // the preview isn't laid out
    this.player.play(p, startTime(this.playhead, length(p)), size);
    if (!this.player.playing) return;
    this.playingProject = p;
    this.preview.setPlaying(true);
  }

  /** Stop playing; `still` shows the frame at the playhead (callers that redraw anyway skip it). */
  private stopPlayback(still = true): void {
    const was = this.player.playing;
    this.player.stop();
    this.playingProject = null;
    this.preview.setPlaying(false);
    if (was && still) this.viewChanged();
  }

  private playingAt(t: number): void {
    this.playhead = t;
    this.timeline.setPlayhead(t, true);
    const p = this.playingProject;
    if (p) this.preview.setTime(t, length(p));
  }

  private playFinished(): void {
    const p = this.playingProject;
    this.playingProject = null;
    this.preview.setPlaying(false);
    if (p) this.playhead = length(p);
    this.timeline.setPlayhead(this.playhead);
    this.viewChanged();
  }

  /** The shown project or the playhead changed: the still preview follows (no project: blank). */
  private viewChanged(): void {
    const p = this.shownProject();
    if (!p || !this.active) {
      this.preview.clear();
      return;
    }
    this.preview.show(p, this.playhead);
    this.preview.setTime(this.playhead, length(p));
  }

  // ---------------------------------------------------------------- keys
  private onKey = (e: KeyboardEvent): void => {
    if (!this.active || e.defaultPrevented) return;
    const t = e.target as HTMLElement | null;
    const inField = !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
    const id = actionFor(e, inField);
    if (!id) return;
    // Space on a button presses it; leave that to the browser
    if (id === "play" && t?.tagName === "BUTTON") return;
    e.preventDefault();
    this.run(id);
  };

  private run(id: ActionId): void {
    switch (id) {
      case "split": return this.split();
      case "delete": return this.deleteSelected();
      case "prev-frame": return this.stepFrames(-1);
      case "next-frame": return this.stepFrames(1);
      case "back-second": return this.seek(this.playhead - 1);
      case "forward-second": return this.seek(this.playhead + 1);
      case "home": return this.seek(0);
      case "end": return this.seek(Number.MAX_VALUE);
      case "undo": return this.undo();
      case "redo": return this.redo();
      case "save":
        return void this.save();
      case "save-as":
        return void this.saveAs();
      case "zoom-in": return this.timeline.zoomIn();
      case "zoom-out": return this.timeline.zoomOut();
      case "play": return this.togglePlay();
      case "loop": return this.preview.toggleLoop();
      case "export":
        return; // export comes with a later step
    }
  }

  /** Bring everything in line with the history (and a pending edit); `inspector` false while an
   *  inspector edit is being made (its fields already show it). */
  private render(inspector = true): void {
    const hi = this.history;
    const shown = this.shownProject();
    // an edit, undo / redo, opening or leaving changed the project: playback stops (the still follows)
    if (this.player.playing && shown !== this.playingProject) this.stopPlayback(false);
    if (shown && this.selected !== null) {
      const id = this.selected.startsWith("tr:") ? this.selected.slice(3) : this.selected;
      if (!layout(shown).has(id)) this.selected = null;
    }
    this.timeline.setProject(shown); // null clears it (left, or new files still opening)
    if (shown) this.playhead = Math.min(this.playhead, length(shown));
    else {
      this.selected = null;
      this.playhead = 0;
    }
    this.timeline.setSelected(this.selected);
    this.timeline.setPlayhead(this.playhead);
    if (inspector) this.inspector.show(hi?.project ?? null, this.selected);
    this.media.set(hi?.project.sources ?? [], this.notes);
    if (hi) this.scanner.want(hi.project.sources);
    const undo = this.buttons.get("undo")!;
    const redo = this.buttons.get("redo")!;
    this.buttons.get("save")!.disabled = !hi;
    this.buttons.get("save_as")!.disabled = !hi;
    undo.disabled = !hi?.canUndo;
    redo.disabled = !hi?.canRedo;
    undo.title = hi?.canUndo ? `Undo ${hi.undoLabel} (Ctrl+Z)` : "Undo (Ctrl+Z)";
    redo.title = hi?.canRedo ? `Redo ${hi.redoLabel} (Ctrl+Y)` : "Redo (Ctrl+Y)";
    this.viewChanged();
  }

  lastError(): string {
    return this.error;
  }

  showMessage(text: string, isError = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", isError);
    this.error = isError ? text : "";
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }
}

export function mountVideoEditor(host: HTMLElement, opts: VideoEditorOptions): VideoEditorPage {
  const page = new VideoEditorPage(opts);
  host.append(page.el);
  return page;
}
