// The website's video editor page (port of the desktop's gui/videoedit/): top bar, media list,
// preview, inspector, timeline and a status line. Sources are opened on the page (sources.ts);
// the timeline, inspector, preview and player arrive in the next steps. Loaded on demand from
// ui/app.ts, only behind the test gate.
import { canonicalExt, PROJECT_EXTS } from "../../core/detect";
import { ConversionError, type InputFile } from "../../core/types";
import { addAt, addSources, addText, applyChanges, remove, split, update } from "../../videoedit/edits";
import { History } from "../../videoedit/history";
import { setLimitsForTests, type LIMITS } from "../../videoedit/limits";
import {
  NO_TRANSITION, find, layout, length, newProject, outputFps, textItem, type Project, type Source, type TextItem,
} from "../../videoedit/project";
import { changeLabel, targetOf } from "../../videoedit/schemas";
import { projectFrom } from "../../videoedit/start";
import type { ConversionClient } from "../../worker/client";
import { backLabel } from "../back";
import { loadBundledFonts } from "./fonts";
import { Inspector } from "./inspector";
import { MediaList } from "./media";
import { actionFor, type ActionId } from "./shortcuts";
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
  private media: MediaList;
  private history: History | null = null;
  private notes: Map<string, string> = new Map();
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

  constructor(private opts: VideoEditorOptions) {
    this.bank = new SourceBank(opts.client);
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
        btn("save", "Save project", "Save the project (Ctrl+S)", () => {}),
        btn("save_as", "Save project as…", "Save the project under a new name (Ctrl+Shift+S)", () => {})),
      btn("export", "Export…", "Export comes soon", () => {}, "primary small"));
    for (const k of ["undo", "redo", "save", "save_as", "export"]) this.buttons.get(k)!.disabled = true;
    this.el = h("div", { class: "editor video-editor", hidden: true },
      top,
      h("div", { class: "video-body" },
        h("div", { class: "video-media", ariaLabel: "Media" }, this.media.el),
        h("div", { class: "video-preview", ariaLabel: "Preview" }),
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
      still: async (): Promise<void> => {}, // the still preview comes with step 3's preview task
      pixel: (_x: number, _y: number): [number, number, number, number] | null => null,
    };
  }

  show(): void {
    if (!this.active) document.addEventListener("keydown", this.onKey);
    this.active = true;
    this.el.hidden = false;
    void loadBundledFonts().then(() => this.inspector.updateFontNote(), () => undefined);
    this.render();
  }

  hide(): void {
    this.flushPending();
    document.removeEventListener("keydown", this.onKey);
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
    this.freshBank();
    this.work = Promise.resolve();
    this.history = null;
    this.pending = null;
    this.selected = null;
    this.notes = new Map();
  }

  private freshBank(): void {
    this.bank.abandon();
    this.bank = new SourceBank(this.opts.client);
  }

  get shown(): boolean {
    return this.active;
  }

  /** Nothing can be saved yet (saving projects comes later), so there's nothing to ask. */
  maybeLeave(): boolean {
    return true;
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

  /** Dropped / picked files: into the open project as one "Add files" step, else a new project. */
  addFiles(files: File[]): Promise<boolean> {
    return this.queue(() => (this.history ? this.addToProject(files) : this.openFiles(files)));
  }

  private queue(job: () => Promise<boolean>): Promise<boolean> {
    const epoch = this.epoch;
    const start = () => (epoch === this.epoch ? job() : Promise.resolve(true));
    const run = this.work.then(start, start);
    this.work = run.catch(() => undefined);
    return run;
  }

  private async openFiles(files: File[]): Promise<boolean> {
    if (files.some(isProjectFile)) {
      // opening a saved project comes with the locate window (a later task)
      this.showMessage(files.length === 1 ? "Opening video projects isn't ready yet." : "Open a video project on its own, without other files.", true);
      return false;
    }
    // a job of the queue: nothing else of this page is running, so the old bank only has decoders
    const session = ++this.session;
    this.freshBank();
    this.history = null;
    this.pending = null;
    this.notes = new Map();
    this.render();
    const { sources, messages } = await this.probe(files, new Set(), session);
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

  private async addToProject(files: File[]): Promise<boolean> {
    const session = this.session;
    const taken = new Set(this.history!.project.sources.map((s) => s.id));
    const { sources, messages } = await this.probe(files, taken, session);
    if (session !== this.session || !this.history) return false; // left: that bank was abandoned
    if (sources.length) this.apply((p) => addSources(p, sources), "Add files");
    if (messages.length) this.showMessage(messages.join(" "), true);
    return sources.length > 0;
  }

  /** Probe one file after the other; messages for those that can't be used. Stops between files
   *  once `session` is over (the bank it used is abandoned then and keeps nothing). */
  private async probe(files: File[], taken: Set<string>, session: number): Promise<{ sources: Source[]; messages: string[] }> {
    const sources: Source[] = [];
    const messages: string[] = [];
    const bank = this.bank;
    let n = 1;
    for (const file of files) {
      if (session !== this.session) break;
      if (isProjectFile(file)) {
        messages.push(`Open “${file.name}” on its own to edit that project.`);
        continue;
      }
      while (taken.has(`s${n}`)) n++;
      const id = `s${n}`;
      const opening = `Opening “${file.name}”…`;
      this.showMessage(opening);
      try {
        const opened = await bank.open(file, id, (f, msg) => this.showMessage(`${msg || opening} ${Math.round(f * 100)} %`));
        if (session !== this.session) break;
        sources.push(opened.source);
        taken.add(id);
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

  /** The shown project or the playhead changed (the still preview hooks in here). */
  private viewChanged(): void {}

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
      case "save-as":
        return this.flushPending(); // saving projects comes with a later task
      case "zoom-in": return this.timeline.zoomIn();
      case "zoom-out": return this.timeline.zoomOut();
      case "play":
      case "export":
      case "loop":
        return; // playback and export come with later steps
    }
  }

  /** Bring everything in line with the history (and a pending edit); `inspector` false while an
   *  inspector edit is being made (its fields already show it). */
  private render(inspector = true): void {
    const hi = this.history;
    const shown = this.shownProject();
    if (shown && this.selected !== null) {
      const id = this.selected.startsWith("tr:") ? this.selected.slice(3) : this.selected;
      if (!layout(shown).has(id)) this.selected = null;
    }
    if (shown) {
      this.timeline.setProject(shown);
      this.playhead = Math.min(this.playhead, length(shown));
    }
    this.timeline.setSelected(this.selected);
    this.timeline.setPlayhead(this.playhead);
    if (inspector) this.inspector.show(hi?.project ?? null, this.selected);
    this.media.set(hi?.project.sources ?? [], this.notes);
    const undo = this.buttons.get("undo")!;
    const redo = this.buttons.get("redo")!;
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
