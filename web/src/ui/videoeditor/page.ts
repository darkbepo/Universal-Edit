// The website's video editor page (port of the desktop's gui/videoedit/): top bar, media list,
// preview, inspector, timeline and a status line. Sources are opened on the page (sources.ts);
// the timeline, inspector, preview and player arrive in the next steps. Loaded on demand from
// ui/app.ts, only behind the test gate.
import { canonicalExt, PROJECT_EXTS } from "../../core/detect";
import { ConversionError, type InputFile } from "../../core/types";
import { addAt, addSources, addText } from "../../videoedit/edits";
import { History } from "../../videoedit/history";
import { setLimitsForTests, type LIMITS } from "../../videoedit/limits";
import { length, newProject, type Project, type Source } from "../../videoedit/project";
import { projectFrom } from "../../videoedit/start";
import type { ConversionClient } from "../../worker/client";
import { backLabel } from "../back";
import { MediaList } from "./media";
import { SourceBank } from "./sources";

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
  private timeline = h("div", { class: "video-timeline", ariaLabel: "Timeline" });
  private bank: SourceBank;
  private media: MediaList;
  private history: History | null = null;
  private notes: Map<string, string> = new Map();
  private playhead = 0;
  private selected: string | null = null;
  /** Opening / adding files: one batch after the other. */
  private work: Promise<unknown> = Promise.resolve();
  /** Bumped on leaving, so a batch still probing doesn't fill a page that was left. */
  private session = 0;

  constructor(private opts: VideoEditorOptions) {
    this.bank = new SourceBank(opts.client);
    this.media = new MediaList({
      add: (id) => this.addSourceAtPlayhead(id),
      files: () => this.media.picker.click(),
      text: () => this.apply((p) => addText(p, this.playhead), "Add text"),
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
        h("div", { class: "video-inspector", ariaLabel: "Inspector" }),
        this.timeline),
      h("div", { class: "editor-status" }, this.message));
    if (location.search.includes("e2e")) (window as unknown as { __videoeditor: unknown }).__videoeditor = this.hook();
  }

  /** The e2e hook (grows with the later steps). */
  private hook() {
    // eslint-disable-next-line @typescript-eslint/no-this-alias
    const page = this;
    return {
      project: (): Project | null => page.history?.project ?? null,
      get history() {
        const hi = page.history;
        return { canUndo: hi?.canUndo ?? false, undoLabel: hi?.undoLabel ?? "", dirty: hi?.dirty ?? false };
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
    this.active = true;
    this.el.hidden = false;
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
    this.session++;
    this.history = null; // leaving drops the project and its decoders
    this.notes = new Map();
    this.bank.dispose();
    this.render();
  }

  get shown(): boolean {
    return this.active;
  }

  /** Nothing can be saved yet (saving projects comes later), so there's nothing to ask. */
  maybeLeave(): boolean {
    return true;
  }

  openEmpty(): void {
    this.session++;
    this.bank.dispose();
    this.notes = new Map();
    this.setProject(newProject());
    this.showMessage("");
  }

  /** A new project from the files (ids s1…); false when none of them can be used. */
  open(files: InputFile[]): Promise<boolean> {
    return this.queue(() => this.openFiles(files.map((f) => f.file)));
  }

  /** Dropped / picked files: into the open project as one "Add files" step, else a new project. */
  addFiles(files: File[]): Promise<boolean> {
    return this.queue(() => (this.history ? this.addToProject(files) : this.openFiles(files)));
  }

  private queue(job: () => Promise<boolean>): Promise<boolean> {
    const run = this.work.then(job, job);
    this.work = run.catch(() => undefined);
    return run;
  }

  private async openFiles(files: File[]): Promise<boolean> {
    if (files.some(isProjectFile)) {
      // opening a saved project comes with the locate window (a later task)
      this.showMessage(files.length === 1 ? "Opening video projects isn't ready yet." : "Open a video project on its own, without other files.", true);
      return false;
    }
    const session = ++this.session;
    this.bank.dispose();
    this.history = null;
    this.notes = new Map();
    this.render();
    const { sources, messages } = await this.probe(files, new Set());
    if (session !== this.session) return false;
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
    const { sources, messages } = await this.probe(files, taken);
    if (session !== this.session || !this.history) {
      for (const s of sources) this.bank.forget(s.id);
      return false;
    }
    if (sources.length) this.apply((p) => addSources(p, sources), "Add files");
    if (messages.length) this.showMessage(messages.join(" "), true);
    return sources.length > 0;
  }

  /** Probe one file after the other; messages for those that can't be used. */
  private async probe(files: File[], taken: Set<string>): Promise<{ sources: Source[]; messages: string[] }> {
    const sources: Source[] = [];
    const messages: string[] = [];
    let n = 1;
    for (const file of files) {
      if (isProjectFile(file)) {
        messages.push(`Open “${file.name}” on its own to edit that project.`);
        continue;
      }
      while (taken.has(`s${n}`)) n++;
      const id = `s${n}`;
      const opening = `Opening “${file.name}”…`;
      this.showMessage(opening);
      try {
        const opened = await this.bank.open(file, id, (f, msg) => this.showMessage(`${msg || opening} ${Math.round(f * 100)} %`));
        sources.push(opened.source);
        taken.add(id);
        if (opened.note) this.notes = new Map(this.notes).set(id, opened.note);
      } catch (e) {
        messages.push(e instanceof ConversionError ? e.message : `Couldn't open “${file.name}”.`);
      }
    }
    return { sources, messages };
  }

  private setProject(project: Project): void {
    this.history = new History(project);
    this.playhead = 0;
    this.selected = null;
    this.render();
  }

  /** One undo step from `fn`; an edit the project refuses is shown in the status line. */
  private apply(fn: (p: Project) => Project | [Project, string], label: string): void {
    if (!this.history) return;
    try {
      const got = fn(this.history.project);
      const [project, newId] = Array.isArray(got) ? got : [got, null];
      this.history.push(project, label);
      if (newId) this.selected = newId;
      this.showMessage("");
    } catch (e) {
      if (!(e instanceof ConversionError)) throw e;
      this.showMessage(e.message, true);
    }
    this.render();
  }

  private addSourceAtPlayhead(id: string): void {
    const src = this.history?.project.sources.find((s) => s.id === id);
    if (!src) return;
    this.apply((p) => addAt(p, id, this.playhead, 0), src.kind === "audio" ? "Add music" : "Add clip");
  }

  private undo(): void {
    this.history?.undo();
    this.render();
  }

  private redo(): void {
    this.history?.redo();
    this.render();
  }

  private select(id: string | null): void {
    this.selected = id;
  }

  private seek(t: number): void {
    const end = this.history ? length(this.history.project) : 0;
    this.playhead = Math.max(0, Math.min(t, end));
  }

  private render(): void {
    const hi = this.history;
    this.media.set(hi?.project.sources ?? [], this.notes);
    const undo = this.buttons.get("undo")!;
    const redo = this.buttons.get("redo")!;
    undo.disabled = !hi?.canUndo;
    redo.disabled = !hi?.canRedo;
    undo.title = hi?.canUndo ? `Undo ${hi.undoLabel} (Ctrl+Z)` : "Undo (Ctrl+Z)";
    redo.title = hi?.canRedo ? `Redo ${hi.redoLabel} (Ctrl+Y)` : "Redo (Ctrl+Y)";
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
