/**
 * The website's subtitle editor page (the desktop's gui/subtitleeditor/subtitle_editor_page.py):
 * a table of subtitles (only the rows on screen are drawn, so 100 000 stay quick), an edit box for
 * the chosen one, an optional video with the subtitle over the picture, find & replace, shift /
 * sync, undo. The model is subedit/ (shared cases with the desktop). Loaded on demand.
 *
 * Save downloads a file; "Save to a file…" (Chrome / Edge) writes to a picked file, and Save
 * (Ctrl+S) then writes there again.
 */
import { parseTime } from "../../audioedit/ops";
import { SearchError, replacementAt, type SearchOptions } from "../../codeedit/search";
import { SUBTITLE_EXTS, VIDEO_EXTS } from "../../core/detect";
import type { InputFile } from "../../core/types";
import { check, type Problem } from "../../subedit/checks";
import * as edits from "../../subedit/edits";
import { clock, convertMarkup, extraGet, lostParts, plainText, saveFormats, SubtitleError, WRITE_FORMATS, type Cue, type SubDoc } from "../../subedit/formats";
import { History } from "../../subedit/history";
import { CHARSETS, newDocument, openBytes, toBytes, type Opened } from "../../subedit/io";
import { backLabel } from "../back";
import { SHORTCUTS, keyName, showKey } from "./shortcuts";

export const ROW_PX = 28;
const FORMAT_NAMES: Record<string, string> = { srt: "SRT", vtt: "WebVTT", ass: "ASS", ssa: "SSA", lrc: "LRC", smi: "SAMI" };
const MIME: Record<string, string> = { srt: "application/x-subrip", vtt: "text/vtt", ass: "text/x-ssa", ssa: "text/x-ssa", lrc: "text/plain" };

interface FileHandle {
  name?: string;
  createWritable(): Promise<{ write(data: Blob): Promise<void>; close(): Promise<void> }>;
}

export interface SubtitleEditorOptions {
  onBack: () => void;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls = "", text = ""): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

const extOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
};
const baseOf = (name: string) => {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
};

/** Seconds typed → ms (null for anything else). */
export function parseMs(text: string): number | null {
  const s = parseTime(text);
  return s === null ? null : Math.round(s * 1000);
}

export function lengthText(ms: number): string {
  const sign = ms < 0 ? "-" : "";
  ms = Math.abs(ms);
  return `${sign}${Math.floor(ms / 1000)}.${String(ms % 1000).padStart(3, "0")}`;
}

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** The shown subtitles as safe HTML: only italic / bold / underline / strike kept (desktop
 * preview.caption_html). */
export function captionHtml(texts: string[], fmt: string): string {
  return texts
    .map((t) => {
      let safe = escapeHtml(convertMarkup(t, fmt, "srt"));
      for (const tag of ["i", "b", "u", "s"]) safe = safe.replaceAll(`&lt;${tag}&gt;`, `<${tag}>`).replaceAll(`&lt;/${tag}&gt;`, `</${tag}>`);
      return safe.replaceAll("\n", "<br>");
    })
    .join("<br>");
}

export class SubtitleEditorPage {
  readonly el: HTMLElement = el("div", "editor subtitle-editor");
  opened: Opened = newDocument("srt");
  history = new History(this.opened.doc);
  problems: Problem[] = [];
  /** The cue in the edit box (-1: none). */
  row = -1;
  /** Chosen rows (always includes `row` when there is one). */
  chosen = new Set<number>();
  private sourceName: string | null = null; // the file opened (title)
  private sourceBytes: Uint8Array | null = null; // for "open again with another encoding"
  private saveName: string | null = null; // where Save writes (null: ask first)
  private handle: FileHandle | undefined;
  private active = false;
  private saving = false;
  private videoUrl: string | null = null;
  private raf = 0;
  private checkTimer: ReturnType<typeof setTimeout> | undefined;
  private anchor = -1; // Shift+click range start

  private readonly title = el("span", "sub-title");
  private readonly message = el("div", "msg");
  private readonly counts = el("span", "sub-counts");
  private readonly problemBtn: HTMLButtonElement;
  private readonly formatEl = el("span", "sub-format");
  private readonly encodingSel = el("select");
  private readonly list = el("div", "sub-list");
  private readonly rowsEl = el("div", "sub-rows");
  private readonly textBox = el("textarea", "sub-text");
  private readonly startBox = el("input", "sub-time");
  private readonly endBox = el("input", "sub-time");
  private readonly lengthBox = el("input", "sub-time");
  private readonly note = el("div", "sub-note");
  private readonly sortBtn: HTMLButtonElement;
  private readonly deleteBtn: HTMLButtonElement;
  private readonly splitBtn: HTMLButtonElement;
  private readonly joinBtn: HTMLButtonElement;
  private readonly setStartBtn: HTMLButtonElement;
  private readonly setEndBtn: HTMLButtonElement;
  private readonly shiftBtn: HTMLButtonElement;
  private readonly syncBtn: HTMLButtonElement;
  private readonly videoBtn: HTMLButtonElement;
  private readonly saveBtn: HTMLButtonElement;
  private readonly saveAsBtn: HTMLButtonElement;
  readonly video = el("video", "sub-video");
  private readonly caption = el("div", "sub-caption");
  private readonly videoPane = el("div", "sub-video-pane");
  private readonly videoNote = el("div", "sub-video-note");
  private readonly findEl = el("div", "sub-find");
  private readonly findInput = el("input");
  private readonly replaceInput = el("input");
  private readonly findCount = el("span", "sub-find-count");
  private readonly findCase = el("input");
  private readonly findWord = el("input");
  private readonly findRegex = el("input");
  private readonly picker = el("input");
  private readonly videoPicker = el("input");
  private readonly helpDialog: HTMLDialogElement;
  private readonly saveDialog: HTMLDialogElement;
  private readonly shiftDialog: HTMLDialogElement;
  private readonly syncDialog: HTMLDialogElement;
  private readonly actions: Record<string, () => void>;

  constructor(private readonly opts: SubtitleEditorOptions) {
    this.el.hidden = true;
    const btn = (label: string, title: string, run: () => void, cls = "btn small") => {
      const b = el("button", cls, label);
      b.type = "button";
      b.title = title;
      b.addEventListener("click", run);
      return b;
    };
    this.saveAsBtn = btn("Save as…", "Save as (Ctrl+Shift+S)", () => void this.saveAs());
    this.saveBtn = btn("Save", "Save (Ctrl+S)", () => void this.save(), "primary small");
    this.videoBtn = btn("Video…", "Show a video next to the subtitles", () => this.videoPicker.click());
    this.shiftBtn = btn("Shift times…", "Move subtitles earlier or later", () => this.askShift());
    this.syncBtn = btn("Sync…", "Fix subtitles that drift: give two subtitles their right times", () => this.askSync());
    const tools = el("div", "top-tools");
    tools.append(
      btn("Open…", "Open subtitles (Ctrl+O)", () => this.picker.click()),
      btn("New", "New subtitles (Alt+N)", () => void this.openEmpty()),
      this.title,
      el("div", "spacer"),
      this.videoBtn,
      this.shiftBtn,
      this.syncBtn,
      btn("Find", "Find and replace (Ctrl+F, Ctrl+H)", () => this.openFind(false)),
      btn("?", "Keyboard shortcuts (F1)", () => this.helpDialog.showModal()),
    );
    const top = el("div", "editor-top");
    top.append(backLabel(btn("← Back to Convert", "Leave the editor", () => this.back(), "link")), el("span", "gap"), tools, this.saveAsBtn, this.saveBtn);

    // find bar
    this.findInput.type = "search";
    this.findInput.placeholder = "Find in the subtitles";
    this.findInput.setAttribute("aria-label", "Find");
    this.replaceInput.type = "text";
    this.replaceInput.placeholder = "Replace with";
    this.replaceInput.setAttribute("aria-label", "Replace with");
    const check = (box: HTMLInputElement, label: string) => {
      box.type = "checkbox";
      box.addEventListener("change", () => this.countMatches());
      const l = el("label", "check");
      l.append(box, label);
      return l;
    };
    const findRow = el("div", "sub-find-row");
    findRow.append(
      this.findInput,
      btn("↑", "Previous match (Shift+F3)", () => this.find(-1)),
      btn("↓", "Next match (F3)", () => this.find(1)),
      this.replaceInput,
      btn("Replace", "Replace this match", () => this.replaceOne()),
      btn("Replace all", "Replace every match", () => this.replaceAll()),
      check(this.findCase, "Match case"),
      check(this.findWord, "Whole word"),
      check(this.findRegex, "Regex"),
      this.findCount,
      btn("×", "Close (Esc)", () => (this.findEl.hidden = true)),
    );
    this.findEl.append(findRow);
    this.findEl.hidden = true;
    this.findInput.addEventListener("input", () => this.countMatches());
    this.findInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.find(e.shiftKey ? -1 : 1);
      } else if (e.key === "Escape") this.findEl.hidden = true;
    });
    this.replaceInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.replaceOne();
      } else if (e.key === "Escape") this.findEl.hidden = true;
    });

    // the table
    const head = el("div", "sub-row sub-head");
    for (const t of ["#", "Start", "End", "Length", "Text"]) head.append(el("span", "", t));
    this.list.setAttribute("role", "listbox");
    this.list.setAttribute("aria-label", "Subtitles");
    this.list.setAttribute("aria-multiselectable", "true");
    this.list.tabIndex = 0;
    this.list.append(this.rowsEl);
    this.list.addEventListener("scroll", () => this.renderRows());
    this.list.addEventListener("click", (e) => this.rowClicked(e));
    this.list.addEventListener("dblclick", () => this.textBox.focus());
    this.list.addEventListener("keydown", (e) => this.listKey(e));
    new ResizeObserver(() => this.renderRows()).observe(this.list);

    // the edit panel
    const panel = el("div", "sub-panel");
    const row = el("div", "sub-panel-row");
    this.deleteBtn = btn("Delete", "Delete the chosen subtitles (Alt+Delete)", () => this.remove());
    this.splitBtn = btn("Split", "Split at the cursor, or in the middle (Alt+S)", () => this.split());
    this.joinBtn = btn("Join", "Join the chosen subtitles, or with the next (Alt+J)", () => this.join());
    this.sortBtn = btn("Sort by time", "Put the subtitles in time order", () => this.sort());
    this.setStartBtn = btn("⇤", "Start = the video's position (Ctrl+[)", () => this.setFromVideo("start"));
    this.setStartBtn.setAttribute("aria-label", "Start from the video");
    this.setEndBtn = btn("⇥", "End = the video's position (Ctrl+])", () => this.setFromVideo("end"));
    this.setEndBtn.setAttribute("aria-label", "End from the video");
    const times = el("div", "sub-times");
    for (const [box, label] of [[this.startBox, "Start"], [this.endBox, "End"], [this.lengthBox, "Length"]] as const) {
      box.type = "text";
      box.setAttribute("aria-label", label);
      box.title = `${label} — type a time like 1:05.250 and press Enter`;
      box.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          this.typedTime(label.toLowerCase() as "start" | "end" | "length", box.value);
        } else if (e.key === "Escape") {
          this.fillBox();
          box.blur();
        }
      });
      box.addEventListener("change", () => this.typedTime(label.toLowerCase() as "start" | "end" | "length", box.value));
    }
    times.append(this.setStartBtn, el("span", "muted", "Start"), this.startBox, el("span", "muted", "End"), this.endBox, this.setEndBtn, el("span", "muted", "Length"), this.lengthBox);
    row.append(btn("+ Add", "Add a subtitle after this one (Ctrl+Enter)", () => this.add()), this.deleteBtn, this.splitBtn, this.joinBtn, this.sortBtn, el("div", "spacer"), times);
    this.textBox.setAttribute("aria-label", "Subtitle text");
    this.textBox.placeholder = "The subtitle's text (Enter starts a new line)";
    this.textBox.rows = 3;
    this.textBox.addEventListener("input", () => this.textTyped()); // Ctrl+Z: the page's undo (onKey)
    this.note.setAttribute("aria-live", "polite");
    panel.append(row, this.textBox, this.note);

    // the video
    this.video.controls = true;
    this.video.playsInline = true;
    this.video.preload = "auto";
    const stage = el("div", "sub-stage");
    stage.append(this.video, this.caption);
    const closeVideo = btn("Close video", "Take the video away (the subtitles stay)", () => this.closeVideo(), "link");
    this.videoPane.append(stage, this.videoNote, closeVideo);
    this.videoPane.hidden = true;
    this.videoNote.hidden = true;
    this.video.addEventListener("play", () => this.tick());
    this.video.addEventListener("seeked", () => this.playMoved());
    this.video.addEventListener("timeupdate", () => this.playMoved());
    this.video.addEventListener("error", () => {
      this.videoNote.textContent = "This browser can't play this video (the desktop app can). The subtitles still work.";
      this.videoNote.hidden = false;
      this.updateButtons();
    });

    const left = el("div", "sub-left");
    left.append(head, this.list, panel);
    const body = el("div", "sub-body");
    body.append(left, this.videoPane);
    const main = el("div", "sub-main");
    main.append(this.findEl, body);

    const status = el("div", "editor-status sub-status");
    this.problemBtn = btn("", "Go to the next subtitle with a problem (F8)", () => this.nextProblem(), "link");
    this.encodingSel.setAttribute("aria-label", "Encoding");
    this.encodingSel.title = "Garbled letters? Open the file again with another encoding";
    this.encodingSel.append(new Option("Detect (UTF-8 / Windows-1252)", ""));
    for (const [code, label] of CHARSETS) this.encodingSel.append(new Option(label, code));
    this.encodingSel.addEventListener("change", () => this.reopen(this.encodingSel.value || null));
    const right = el("div", "sub-status-right");
    right.append(this.counts, this.problemBtn, this.formatEl, this.encodingSel);
    status.append(this.message, right);
    this.message.setAttribute("aria-live", "polite");

    this.picker.type = "file";
    this.picker.multiple = true;
    this.picker.hidden = true;
    this.picker.accept = [...SUBTITLE_EXTS, ...VIDEO_EXTS].map((e) => "." + e).join(",");
    this.picker.addEventListener("change", () => {
      const list = Array.from(this.picker.files ?? []);
      this.picker.value = "";
      void this.openFiles(list);
    });
    this.videoPicker.type = "file";
    this.videoPicker.hidden = true;
    this.videoPicker.accept = "video/*," + [...VIDEO_EXTS].map((e) => "." + e).join(",");
    this.videoPicker.addEventListener("change", () => {
      const f = this.videoPicker.files?.[0];
      this.videoPicker.value = "";
      if (f) this.openVideo(f);
    });
    this.helpDialog = this.buildHelp();
    this.saveDialog = this.buildSaveDialog();
    this.shiftDialog = this.buildShiftDialog();
    this.syncDialog = this.buildSyncDialog();
    this.el.append(top, main, status, this.picker, this.videoPicker, this.helpDialog, this.saveDialog, this.shiftDialog, this.syncDialog);

    this.actions = {
      new: () => void this.openEmpty(),
      open: () => this.picker.click(),
      save: () => void this.save(),
      save_as: () => void this.saveAs(),
      undo: () => this.undo(),
      redo: () => this.redo(),
      add: () => this.add(),
      delete: () => this.remove(),
      split: () => this.split(),
      join: () => this.join(),
      prev: () => this.step(-1),
      next: () => this.step(1),
      next_problem: () => this.nextProblem(),
      find: () => this.openFind(false),
      replace: () => this.openFind(true),
      find_next: () => this.find(1),
      find_prev: () => this.find(-1),
      play: () => this.togglePlay(),
      set_start: () => this.setFromVideo("start"),
      set_end: () => this.setFromVideo("end"),
      help: () => this.helpDialog.showModal(),
    };
    window.addEventListener("keydown", this.onKey, true);
    window.addEventListener("beforeunload", this.onUnload);
    this.el.addEventListener("dragover", (e) => {
      if (!e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
    });
    this.el.addEventListener("drop", (e) => {
      const list = Array.from(e.dataTransfer?.files ?? []);
      if (!list.length) return;
      e.preventDefault();
      e.stopPropagation();
      void this.openFiles(list);
    });
    this.showDoc(true);
    if (typeof location !== "undefined" && location.search.includes("e2e")) this.installTestHook();
  }

  // ---------------------------------------------------------------- test hook
  private installTestHook(): void {
    const page = this;
    (window as unknown as { __subeditor: object }).__subeditor = {
      cues: () => page.doc.cues.map((c) => ({ start: c.start, end: c.end, text: c.text })),
      get format() {
        return page.doc.format;
      },
      get dirty() {
        return page.history.dirty;
      },
      get row() {
        return page.row;
      },
      get caption() {
        return page.caption.innerHTML;
      },
      select: (i: number) => page.selectRow(i),
      /** The video's position as the page sees it (headless browsers may not decode it). */
      showAt: (ms: number) => page.playMoved(ms),
    };
  }

  // ---------------------------------------------------------------- state
  get doc(): SubDoc {
    return this.history.doc;
  }

  get dirty(): boolean {
    return this.history.dirty;
  }

  show(): void {
    this.active = true;
    this.el.hidden = false;
    this.list.focus();
    this.renderRows();
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
    this.video.pause();
  }

  lastError(): string {
    return this.message.classList.contains("error") ? (this.message.textContent ?? "") : "";
  }

  private showMessage(text: string, error = false): void {
    this.message.textContent = text;
    this.message.classList.toggle("error", error);
  }

  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.active && this.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  private readonly onKey = (e: KeyboardEvent) => {
    if (!this.active || this.el.hidden) return;
    if (this.el.querySelector("dialog[open]")) return;
    const name = keyName(e);
    const s = SHORTCUTS.find((x) => x.keys.includes(name));
    if (!s) return;
    // in a one-line box (times, find) its own keys win: only saving and moving between matches
    const inBox = e.target instanceof HTMLInputElement && e.target.type !== "checkbox" && e.target.type !== "radio";
    if (inBox && !["save", "save_as", "find_next", "find_prev"].includes(s.id)) return;
    e.preventDefault();
    e.stopPropagation();
    this.actions[s.id]();
  };

  // ---------------------------------------------------------------- opening
  /** Open the first subtitle file in `list` (a video among them goes to the preview). */
  async openFiles(list: File[]): Promise<boolean> {
    const sub = list.find((f) => SUBTITLE_EXTS.has(extOf(f.name)));
    const video = list.find((f) => VIDEO_EXTS.has(extOf(f.name)));
    if (!sub && !video) {
      this.showMessage("Choose a subtitle file (SRT, VTT, ASS, SSA, SMI or LRC).", true);
      return false;
    }
    if (sub) {
      if (this.saving || !this.discardOk()) return false;
      if (!(await this.read(sub, null))) return false;
    }
    if (video) this.openVideo(video);
    return true;
  }

  /** From the converter page: one subtitle file, maybe with one video. */
  open(files: InputFile[]): Promise<boolean> {
    return this.openFiles(files.map((f) => f.file));
  }

  private async read(file: File, charset: string | null, bytes?: Uint8Array): Promise<boolean> {
    let data: Uint8Array;
    try {
      data = bytes ?? new Uint8Array(await file.arrayBuffer());
    } catch {
      this.showMessage(`“${file.name}” couldn't be read.`, true);
      return false;
    }
    let opened: Opened;
    try {
      opened = openBytes(data, extOf(file.name), charset);
    } catch (e) {
      this.showMessage(e instanceof SubtitleError ? `“${file.name}”: ${e.message}` : `“${file.name}” couldn't be opened.`, true);
      if (!(e instanceof SubtitleError)) console.error(e);
      return false;
    }
    this.load(file.name, opened, data);
    return true;
  }

  openEmpty(): boolean {
    if (this.saving || !this.discardOk()) return false;
    this.load(null, newDocument("srt"), null);
    return true;
  }

  private reopen(charset: string | null): void {
    if (!this.sourceName || !this.sourceBytes || !this.discardOk()) {
      this.encodingSel.value = this.opened.charset ?? "";
      return;
    }
    void this.read(new File([this.sourceBytes as BlobPart], this.sourceName), charset, this.sourceBytes);
  }

  private discardOk(): boolean {
    return !this.dirty || window.confirm("Discard the unsaved changes to the subtitles?");
  }

  private load(name: string | null, opened: Opened, bytes: Uint8Array | null): void {
    this.opened = opened;
    this.history = new History(opened.doc);
    this.sourceName = name;
    this.sourceBytes = bytes;
    this.saveName = name && WRITE_FORMATS.includes(opened.doc.format) ? name : null;
    this.handle = undefined;
    this.row = -1;
    this.chosen.clear();
    this.list.scrollTop = 0;
    this.showDoc(true);
    if (opened.doc.cues.length) this.selectRow(0);
    const n = opened.doc.cues.length;
    let note = name ? `Opened “${name}”: ${n} subtitle${n === 1 ? "" : "s"}.` : "";
    if (name && !WRITE_FORMATS.includes(opened.doc.format)) note += " SAMI files can't be written: Save asks for another format.";
    if (opened.charset) note += " It will be saved as UTF-8.";
    this.showMessage(note);
    this.encodingSel.value = opened.charset ?? "";
  }

  /** True when the editor may close: nothing unsaved, or the user agreed to drop it. */
  maybeLeave(): boolean {
    if (!this.discardOk()) return false;
    this.history.markSaved();
    this.video.pause();
    return true;
  }

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- the video
  openVideo(file: File): void {
    this.closeVideo();
    this.videoUrl = URL.createObjectURL(file);
    this.video.src = this.videoUrl;
    this.videoPane.hidden = false;
    this.videoNote.hidden = true;
    this.el.classList.add("with-video");
    this.playMoved();
    this.updateButtons();
  }

  closeVideo(): void {
    if (!this.videoUrl) return;
    cancelAnimationFrame(this.raf);
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    URL.revokeObjectURL(this.videoUrl);
    this.videoUrl = null;
    this.videoPane.hidden = true;
    this.el.classList.remove("with-video");
    this.playMoved(0);
    this.updateButtons();
  }

  get hasVideo(): boolean {
    return this.videoUrl !== null && !this.video.error;
  }

  private positionMs(): number {
    return Math.round(this.video.currentTime * 1000);
  }

  private togglePlay(): void {
    if (!this.hasVideo) return;
    if (this.video.paused) void this.video.play().catch(() => undefined);
    else this.video.pause();
  }

  private tick = (): void => {
    cancelAnimationFrame(this.raf);
    const loop = () => {
      this.playMoved();
      if (!this.video.paused && !this.video.ended) this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  };

  private playing = new Set<number>();

  /** The subtitles shown at `ms` (default: the video's position): bold rows + the caption. */
  playMoved(ms = this.videoUrl ? this.positionMs() : -1): void {
    const rows = ms < 0 ? [] : edits.cueAt(this.doc.cues, ms);
    const next = new Set(rows);
    if (next.size !== this.playing.size || rows.some((r) => !this.playing.has(r))) {
      this.playing = next;
      this.renderRows();
    }
    const html = captionHtml(rows.map((r) => this.doc.cues[r].text), this.doc.format);
    if (this.caption.innerHTML !== html) this.caption.innerHTML = html;
    this.caption.hidden = !html;
  }

  private setFromVideo(which: "start" | "end"): void {
    if (!this.hasVideo || !this.hasRow()) return;
    const ms = this.positionMs();
    this.push(which === "start" ? edits.setTimes(this.doc.cues, this.row, ms) : edits.setTimes(this.doc.cues, this.row, null, ms), which === "start" ? "Set start" : "Set end");
  }

  // ---------------------------------------------------------------- showing the document
  private hasRow(): boolean {
    return this.row >= 0 && this.row < this.doc.cues.length;
  }

  private push(cues: Cue[], label: string, merge: string | null = null, row: number | null = null, select: number[] | null = null): void {
    this.history.push({ ...this.doc, cues }, label, merge);
    this.showDoc(false, row, select);
  }

  private showDoc(reset: boolean, row: number | null = null, select: number[] | null = null): void {
    const n = this.doc.cues.length;
    this.rowsEl.style.height = `${n * ROW_PX}px`;
    if (reset || select) this.chosen.clear();
    if (n) {
      const target = Math.min(Math.max(row ?? this.row, 0), n - 1);
      if (reset || row !== null || select) this.selectRow(target, select ?? []);
      else this.fillBox();
    } else {
      this.row = -1;
      this.chosen.clear();
      this.fillBox();
    }
    for (const r of [...this.chosen]) if (r >= n) this.chosen.delete(r);
    clearTimeout(this.checkTimer);
    this.checkTimer = setTimeout(() => this.runCheck(), 300);
    this.playMoved();
    this.renderRows();
    this.updateTitle();
    this.updateButtons();
  }

  selectRow(row: number, also: number[] = []): void {
    if (row < 0 || row >= this.doc.cues.length) return;
    if (row !== this.row) this.history.endMerge();
    this.row = row;
    this.anchor = row;
    this.chosen = new Set([row, ...also.filter((r) => r >= 0 && r < this.doc.cues.length)]);
    this.scrollToRow(row);
    this.fillBox();
    this.renderRows();
    if (this.hasVideo && this.video.paused) {
      this.video.currentTime = this.doc.cues[row].start / 1000;
      this.playMoved(this.doc.cues[row].start);
    }
    this.updateButtons();
  }

  private scrollToRow(row: number): void {
    const top = row * ROW_PX;
    const h = this.list.clientHeight || ROW_PX * 10;
    if (top < this.list.scrollTop) this.list.scrollTop = top;
    else if (top + ROW_PX > this.list.scrollTop + h) this.list.scrollTop = top + ROW_PX - h;
  }

  private renderRows(): void {
    const cues = this.doc.cues;
    const h = this.list.clientHeight || ROW_PX * 20;
    const first = Math.max(0, Math.floor(this.list.scrollTop / ROW_PX) - 5);
    const last = Math.min(cues.length, Math.ceil((this.list.scrollTop + h) / ROW_PX) + 5);
    const bad = new Map<number, string[]>();
    for (const p of this.problems) bad.set(p.index, [...(bad.get(p.index) ?? []), p.message]);
    const out: HTMLElement[] = [];
    for (let i = first; i < last; i++) {
      const c = cues[i];
      const r = el("div", "sub-row");
      r.dataset.index = String(i);
      r.style.top = `${i * ROW_PX}px`;
      r.setAttribute("role", "option");
      r.setAttribute("aria-selected", String(this.chosen.has(i)));
      r.classList.toggle("chosen", this.chosen.has(i));
      r.classList.toggle("current", i === this.row);
      r.classList.toggle("playing", this.playing.has(i));
      const comment = extraGet(c, "kind") === "Comment";
      r.classList.toggle("comment", comment);
      const notes = bad.get(i);
      if (notes) {
        r.classList.add("problem");
        r.title = notes.join("\n");
      } else if (comment) r.title = "A comment line: never shown";
      const text = (comment ? "(comment) " : "") + plainText(c.text, this.doc.format).replaceAll("\n", " ⏎ ");
      r.append(el("span", "num", String(i + 1)), el("span", "", clock(c.start)), el("span", "", clock(c.end)), el("span", "", lengthText(c.end - c.start)), el("span", "txt", text));
      out.push(r);
    }
    this.rowsEl.replaceChildren(...out);
  }

  private rowClicked(e: MouseEvent): void {
    const r = (e.target as HTMLElement).closest<HTMLElement>(".sub-row[data-index]");
    if (!r) return;
    const i = Number(r.dataset.index);
    if (e.shiftKey && this.anchor >= 0) {
      const [a, b] = [Math.min(this.anchor, i), Math.max(this.anchor, i)];
      const anchor = this.anchor;
      this.selectRow(i, Array.from({ length: b - a + 1 }, (_, k) => a + k));
      this.anchor = anchor;
    } else if (e.ctrlKey || e.metaKey) {
      const keep = new Set(this.chosen);
      if (keep.has(i) && keep.size > 1) {
        keep.delete(i);
        this.selectRow([...keep][0], [...keep]);
      } else this.selectRow(i, [...keep]);
    } else this.selectRow(i);
  }

  private listKey(e: KeyboardEvent): void {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const n = this.doc.cues.length;
    const page = Math.max(1, Math.floor(this.list.clientHeight / ROW_PX) - 1);
    const go: Record<string, number> = { ArrowUp: -1, ArrowDown: 1, PageUp: -page, PageDown: page, Home: -n, End: n };
    if (e.key in go && n) {
      e.preventDefault();
      this.selectRow(Math.min(Math.max(this.row + go[e.key], 0), n - 1));
    } else if (e.key === " ") {
      e.preventDefault();
      this.togglePlay();
    } else if (e.key === "Delete") {
      e.preventDefault();
      this.remove();
    } else if (e.key === "Enter") {
      e.preventDefault();
      this.textBox.focus();
    }
  }

  private fillBox(): void {
    const has = this.hasRow();
    const c = has ? this.doc.cues[this.row] : null;
    const text = c?.text ?? "";
    if (this.textBox.value !== text) {
      const pos = this.textBox.selectionStart;
      this.textBox.value = text;
      this.textBox.setSelectionRange(Math.min(pos, text.length), Math.min(pos, text.length));
    }
    this.textBox.disabled = !has;
    const values: [HTMLInputElement, number | null][] = [[this.startBox, c?.start ?? null], [this.endBox, c?.end ?? null], [this.lengthBox, c ? c.end - c.start : null]];
    for (const [box, v] of values) {
      box.disabled = !has;
      if (document.activeElement !== box) box.value = v === null ? "" : clock(Math.max(0, v));
    }
    const notes = this.problems.filter((p) => p.index === this.row).map((p) => p.message);
    this.note.textContent = notes.join(" · ");
    this.note.hidden = !notes.length;
  }

  private updateTitle(): void {
    this.title.textContent = (this.dirty ? "● " : "") + (this.sourceName ?? "New subtitles");
    this.formatEl.textContent = FORMAT_NAMES[this.doc.format] ?? this.doc.format.toUpperCase();
    this.encodingSel.hidden = !this.sourceName;
  }

  private updateButtons(): void {
    const has = this.hasRow();
    const n = this.doc.cues.length;
    this.deleteBtn.disabled = !has;
    this.splitBtn.disabled = !has;
    this.joinBtn.disabled = !has || (this.chosen.size < 2 && this.row + 1 >= n);
    this.sortBtn.hidden = edits.isSorted(this.doc.cues);
    this.setStartBtn.disabled = this.setEndBtn.disabled = !has || !this.hasVideo;
    this.shiftBtn.disabled = !n;
    this.syncBtn.disabled = n < 2;
    this.saveBtn.disabled = this.saveAsBtn.disabled = this.saving;
    this.videoBtn.textContent = this.videoUrl ? "Change video…" : "Video…";
  }

  runCheck(): void {
    this.problems = check(this.doc.cues, this.doc.format);
    const n = this.doc.cues.length;
    const rows = new Set(this.problems.map((p) => p.index)).size;
    this.counts.textContent = `${n} subtitle${n === 1 ? "" : "s"}`;
    this.problemBtn.textContent = rows ? `${rows} with problems` : "";
    this.problemBtn.hidden = !rows;
    this.renderRows();
    this.fillBox();
  }

  // ---------------------------------------------------------------- editing
  private textTyped(): void {
    if (!this.hasRow()) return;
    const text = this.textBox.value;
    if (text === this.doc.cues[this.row].text) return;
    this.push(edits.setText(this.doc.cues, this.row, text), "Edit text", `text:${this.row}`);
  }

  private typedTime(which: "start" | "end" | "length", text: string): void {
    if (!this.hasRow()) return;
    const c = this.doc.cues[this.row];
    const shown = clock(Math.max(0, which === "start" ? c.start : which === "end" ? c.end : c.end - c.start));
    if (text.trim() === shown) return;
    const ms = parseMs(text);
    if (ms === null) {
      this.showMessage("Type a time like 1:05.250 or 65.25.", true);
      return;
    }
    const cues = this.doc.cues;
    const next = which === "start" ? edits.setTimes(cues, this.row, ms) : which === "end" ? edits.setTimes(cues, this.row, null, ms) : edits.setLength(cues, this.row, ms);
    this.showMessage("");
    this.push(next, "Change time");
    const box = which === "start" ? this.startBox : which === "end" ? this.endBox : this.lengthBox;
    box.blur();
    this.fillBox();
  }

  chosenRows(): number[] {
    const rows = [...this.chosen].filter((r) => r < this.doc.cues.length).sort((a, b) => a - b);
    return rows.length || !this.hasRow() ? rows : [this.row];
  }

  add(): void {
    const after = this.doc.cues.length ? this.row : -1;
    const [cues, at] = edits.add(this.doc.cues, after);
    this.push(cues, "Add subtitle", null, at);
    this.textBox.focus();
  }

  remove(): void {
    const rows = this.chosenRows();
    if (!rows.length) return;
    this.push(edits.remove(this.doc.cues, rows), "Delete", null, rows[0]);
  }

  split(): void {
    if (!this.hasRow()) return;
    const at = document.activeElement === this.textBox ? this.textBox.selectionStart : null;
    this.push(edits.split(this.doc.cues, this.row, at), "Split", null, this.row, [this.row, this.row + 1]);
  }

  join(): void {
    let rows = this.chosenRows();
    if (rows.length === 1 && rows[0] + 1 < this.doc.cues.length) rows = [rows[0], rows[0] + 1];
    if (rows.length < 2) return;
    const [cues, at] = edits.join(this.doc.cues, rows);
    this.push(cues, "Join", null, at);
  }

  sort(): void {
    const current = this.hasRow() ? this.doc.cues[this.row] : null;
    const cues = edits.sort(this.doc.cues);
    this.push(cues, "Sort by time", null, Math.max(0, cues.indexOf(current as Cue)));
  }

  shiftTimes(delta: number, chosenOnly: boolean): void {
    const rows = chosenOnly ? this.chosenRows() : null;
    this.push(edits.shift(this.doc.cues, delta, rows), "Shift times", null, null, rows);
  }

  syncTimes(a: number, aTime: number, b: number, bTime: number): boolean {
    let cues: Cue[];
    try {
      cues = edits.sync(this.doc.cues, a, aTime, b, bTime);
    } catch (e) {
      const why = (e as Error).message === "same" ? "Those two subtitles start at the same time: pick two further apart." : "The second subtitle must start after the first.";
      this.showMessage(why, true);
      return false;
    }
    this.push(cues, "Sync");
    this.showMessage("Synced: every subtitle was moved and stretched.");
    return true;
  }

  undo(): void {
    const label = this.history.undo();
    if (label) {
      this.showDoc(true, this.row);
      this.showMessage(`Undid: ${label}`);
    }
  }

  redo(): void {
    const label = this.history.redo();
    if (label) {
      this.showDoc(true, this.row);
      this.showMessage(`Redid: ${label}`);
    }
  }

  private step(delta: number): void {
    const n = this.doc.cues.length;
    if (n) this.selectRow(Math.min(Math.max(this.row + delta, 0), n - 1));
  }

  private nextProblem(): void {
    const rows = [...new Set(this.problems.map((p) => p.index))].sort((a, b) => a - b);
    if (rows.length) this.selectRow(rows.find((r) => r > this.row) ?? rows[0]);
  }

  // ---------------------------------------------------------------- find
  private openFind(replace: boolean): void {
    this.findEl.hidden = false;
    const target = replace && this.findInput.value ? this.replaceInput : this.findInput;
    target.focus();
    target.select();
    this.countMatches();
  }

  private options(): SearchOptions {
    return { caseSensitive: this.findCase.checked, wholeWord: this.findWord.checked, regex: this.findRegex.checked };
  }

  private matches(): [number, number, number][] | null {
    try {
      return edits.find(this.doc.cues, this.findInput.value, this.options());
    } catch (e) {
      if (!(e instanceof SearchError)) throw e;
      this.findCount.textContent = e.message;
      return null;
    }
  }

  private countMatches(): void {
    const found = this.matches();
    if (found) this.findCount.textContent = this.findInput.value ? `${found.length} found` : "";
  }

  find(direction: number): boolean {
    const found = this.matches();
    if (!found?.length) return false;
    const at: [number, number] = [this.row, this.textBox.selectionStart];
    const cmp = (m: [number, number, number]) => (m[0] - at[0]) || (m[1] - at[1]);
    const hit = direction > 0 ? (found.find((m) => cmp(m) > 0) ?? found[0]) : ([...found].reverse().find((m) => cmp(m) < 0) ?? found[found.length - 1]);
    const [row, a, b] = hit;
    this.selectRow(row);
    this.textBox.focus();
    this.textBox.setSelectionRange(a, b);
    this.findCount.textContent = `${found.indexOf(hit) + 1} of ${found.length}`;
    return true;
  }

  private replaceOne(): void {
    if (!this.hasRow()) return;
    const start = this.textBox.selectionStart, end = this.textBox.selectionEnd;
    let hit: [number, number, string] | null;
    try {
      hit = replacementAt(this.doc.cues[this.row].text, this.findInput.value, this.replaceInput.value, start, this.options());
    } catch (e) {
      if (!(e instanceof SearchError)) throw e;
      this.findCount.textContent = e.message;
      return;
    }
    if (hit && hit[1] === end) {
      const [a, b, text] = hit;
      const old = this.doc.cues[this.row].text;
      this.push(edits.setText(this.doc.cues, this.row, old.slice(0, a) + text + old.slice(b)), "Replace");
      this.textBox.setSelectionRange(a + text.length, a + text.length);
    }
    this.find(1);
    this.countMatches();
  }

  private replaceAll(): void {
    let cues: Cue[], n: number;
    try {
      [cues, n] = edits.replaceAll(this.doc.cues, this.findInput.value, this.replaceInput.value, this.options());
    } catch (e) {
      if (!(e instanceof SearchError)) throw e;
      this.findCount.textContent = e.message;
      return;
    }
    if (n) this.push(cues, "Replace all");
    this.findCount.textContent = `Replaced ${n}`;
  }

  // ---------------------------------------------------------------- saving
  async save(): Promise<boolean> {
    if (this.saving) return false;
    if (!this.saveName && !this.handle) return this.saveAs();
    return this.write(this.handle?.name ?? this.saveName!, this.doc.format);
  }

  async saveAs(): Promise<boolean> {
    if (this.saving) return false;
    const fmts = saveFormats(this.doc);
    const choice = await this.askSave(`${this.sourceName ? baseOf(this.sourceName) : "Subtitles"}.${fmts[0]}`, fmts);
    if (!choice) return false;
    let fmt = extOf(choice.name);
    let name = choice.name;
    if (!fmts.includes(fmt)) {
      fmt = choice.format;
      name = `${baseOf(name)}.${fmt}`;
    }
    const lost = lostParts(this.doc, fmt);
    if (lost.length && !window.confirm(`${FORMAT_NAMES[fmt]} files can't keep: ${lost.join(", ")}. Save anyway?`)) return false;
    if (choice.toFile) {
      try {
        this.handle = await (window as unknown as { showSaveFilePicker: (o: object) => Promise<FileHandle> }).showSaveFilePicker({ suggestedName: name.replace(/[\\/:*?"<>|]/g, "_") });
      } catch (err) {
        if ((err as { name?: string } | null)?.name !== "AbortError") {
          console.error("The save window couldn't be opened", err);
          this.showMessage("The browser couldn't open its save window. Try again, or use Save as without “Save to a file…”.", true);
        }
        return false;
      }
      name = this.handle.name ?? name;
    } else this.handle = undefined;
    return this.write(name, fmt);
  }

  private async write(name: string, fmt: string): Promise<boolean> {
    const doc = this.doc;
    let saved: { data: Uint8Array; utf8: boolean };
    try {
      saved = toBytes(this.opened, doc, fmt);
    } catch (e) {
      this.showMessage(e instanceof Error ? e.message : "Couldn't save.", true);
      return false;
    }
    this.saving = true;
    this.updateButtons();
    try {
      const blob = new Blob([saved.data as BlobPart], { type: MIME[fmt] ?? "application/octet-stream" });
      if (this.handle) {
        const w = await this.handle.createWritable();
        await w.write(blob);
        await w.close();
      } else {
        const url = URL.createObjectURL(blob);
        const a = el("a");
        a.href = url;
        a.download = name;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
      const note = saved.utf8 ? " (as UTF-8)" : "";
      const reopened = openBytes(saved.data, fmt);
      if (fmt !== doc.format) {
        const handle = this.handle;
        this.load(name, reopened, saved.data); // now editing the new file: its own markup and fields
        this.handle = handle;
        this.showMessage(`Saved “${name}”${note}. Now editing the .${fmt} file.`);
      } else {
        this.opened = reopened;
        this.sourceName = this.saveName = name;
        this.sourceBytes = saved.data;
        this.history.markSaved(doc);
        this.updateTitle();
        this.showMessage(`Saved “${name}”${note}.`);
      }
      return true;
    } catch (err) {
      console.error(`Couldn't save ${name}`, err);
      this.showMessage(`Couldn't save “${name}”.`, true);
      return false;
    } finally {
      this.saving = false;
      this.updateButtons();
    }
  }

  // ---------------------------------------------------------------- dialogs
  private dialog(cls: string, title: string): [HTMLDialogElement, HTMLElement] {
    const d = el("dialog", `editor-dialog ${cls}`);
    d.setAttribute("aria-label", title);
    const buttons = el("div", "row");
    d.append(el("h2", "", title), buttons);
    return [d, buttons];
  }

  private buildSaveDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("sub-save-dialog", "Save as");
    const name = el("input");
    name.type = "text";
    name.setAttribute("aria-label", "File name");
    const format = el("select");
    format.setAttribute("aria-label", "Format");
    format.addEventListener("change", () => (name.value = `${baseOf(name.value)}.${format.value}`));
    const row = el("label", "field");
    row.append(el("span", "", "File name"), name);
    const row2 = el("label", "field");
    row2.append(el("span", "", "Format"), format);
    const button = (text: string, cls: string, value: string) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.dataset.value = value;
      return b;
    };
    const toFile = button("Save to a file…", "btn", "file");
    toFile.title = "Choose where to save; Save (Ctrl+S) then writes to the same file";
    buttons.append(el("div", "spacer"), button("Cancel", "btn", ""), toFile, button("Save", "primary small", "save"));
    dialog.insertBefore(row, buttons);
    dialog.insertBefore(row2, buttons);
    return dialog;
  }

  /** Asks the file name and format (and whether to pick a file); replaceable in tests. */
  askSave = (suggested: string, fmts: string[]): Promise<{ name: string; format: string; toFile: boolean } | null> => {
    const dialog = this.saveDialog;
    const name = dialog.querySelector("input") as HTMLInputElement;
    const format = dialog.querySelector("select") as HTMLSelectElement;
    const toFile = dialog.querySelector('[data-value="file"]') as HTMLButtonElement;
    toFile.hidden = !("showSaveFilePicker" in window);
    format.replaceChildren(...fmts.map((f) => new Option(`${FORMAT_NAMES[f]} (.${f})`, f)));
    name.value = suggested;
    return new Promise((resolve) => {
      const finish = (value: string) => {
        dialog.removeEventListener("click", clicked);
        dialog.removeEventListener("cancel", cancelled);
        dialog.removeEventListener("keydown", keyed);
        dialog.close();
        resolve(value ? { name: name.value.trim() || suggested, format: format.value, toFile: value === "file" } : null);
      };
      const clicked = (e: Event) => {
        const b = (e.target as HTMLElement).closest<HTMLButtonElement>("button[data-value]");
        if (b) finish(b.dataset.value ?? "");
      };
      const cancelled = (e: Event) => {
        e.preventDefault();
        finish("");
      };
      const keyed = (e: KeyboardEvent) => {
        if (e.key === "Enter" && e.target === name) {
          e.preventDefault();
          finish("save");
        }
      };
      dialog.addEventListener("click", clicked);
      dialog.addEventListener("cancel", cancelled);
      dialog.addEventListener("keydown", keyed);
      dialog.showModal();
      name.focus();
      const dot = name.value.lastIndexOf(".");
      name.setSelectionRange(0, dot > 0 ? dot : name.value.length);
    });
  };

  private buildShiftDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("sub-shift-dialog", "Shift times");
    const amount = el("input");
    amount.type = "number";
    amount.step = "0.1";
    amount.value = "0";
    amount.setAttribute("aria-label", "Seconds");
    const field = el("label", "field");
    field.append(el("span", "", "Move by (seconds; negative = earlier)"), amount);
    const all = el("input"), only = el("input");
    for (const r of [all, only]) {
      r.type = "radio";
      r.name = "sub-shift-which";
    }
    all.checked = true;
    const l1 = el("label", "check"), l2 = el("label", "check");
    l1.append(all, "All subtitles");
    l2.append(only, el("span", "only-label", "Only the chosen ones"));
    const cancel = el("button", "btn", "Cancel"), ok = el("button", "primary small", "Shift");
    cancel.type = ok.type = "button";
    cancel.addEventListener("click", () => dialog.close());
    ok.addEventListener("click", () => {
      const s = Number(amount.value);
      dialog.close();
      if (Number.isFinite(s) && s) this.shiftTimes(Math.round(s * 1000), only.checked);
    });
    buttons.append(el("div", "spacer"), cancel, ok);
    for (const w of [field, l1, l2]) dialog.insertBefore(w, buttons);
    return dialog;
  }

  private askShift(): void {
    const n = this.chosenRows().length;
    const only = this.shiftDialog.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1];
    only.disabled = !n;
    this.shiftDialog.querySelector(".only-label")!.textContent = n > 1 ? `Only the ${n} chosen` : "Only the chosen one";
    this.shiftDialog.showModal();
  }

  private buildSyncDialog(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("sub-sync-dialog", "Sync with two subtitles");
    const about = el("p", "muted", "Fixes subtitles that drift out of step. Pick a subtitle near the start and one near the end, and type when each should really start; every subtitle is moved and stretched to match.");
    dialog.insertBefore(about, buttons);
    for (const which of ["First", "Last"]) {
      const row = el("div", "sub-sync-row");
      const num = el("input");
      num.type = "number";
      num.min = "1";
      num.setAttribute("aria-label", `${which} subtitle number`);
      const time = el("input");
      time.type = "text";
      time.setAttribute("aria-label", `${which} subtitle's new start`);
      const now = el("div", "muted sub-sync-now");
      num.addEventListener("input", () => this.fillSync(row));
      const use = el("button", "btn small", "Video position");
      use.type = "button";
      use.title = "Use where the video is now";
      use.addEventListener("click", () => (time.value = clock(this.positionMs())));
      row.append(el("span", "", `${which} subtitle`), num, el("span", "", "should start at"), time, use, now);
      dialog.insertBefore(row, buttons);
    }
    const problem = el("div", "error sub-sync-problem");
    dialog.insertBefore(problem, buttons);
    const cancel = el("button", "btn", "Cancel"), ok = el("button", "primary small", "Sync");
    cancel.type = ok.type = "button";
    cancel.addEventListener("click", () => dialog.close());
    ok.addEventListener("click", () => {
      const rows = [...dialog.querySelectorAll<HTMLElement>(".sub-sync-row")];
      const [a, b] = rows.map((r) => {
        const [num, time] = r.querySelectorAll("input");
        return [Number(num.value) - 1, parseMs(time.value)] as const;
      });
      const n = this.doc.cues.length;
      if (a[1] === null || b[1] === null || !(a[0] >= 0 && a[0] < n && b[0] >= 0 && b[0] < n)) {
        problem.textContent = "Type the times like 1:05.250 or 65.25.";
        return;
      }
      if (this.syncTimes(a[0], a[1], b[0], b[1])) dialog.close();
      else problem.textContent = this.message.textContent ?? "";
    });
    buttons.append(el("div", "spacer"), cancel, ok);
    return dialog;
  }

  private fillSync(row: HTMLElement): void {
    const [num, time] = row.querySelectorAll("input");
    const c = this.doc.cues[Number(num.value) - 1];
    if (!c) return;
    time.value = clock(c.start);
    row.querySelector(".sub-sync-now")!.textContent = `now ${clock(c.start)}: ${c.text.replaceAll("\n", " ").slice(0, 60)}`;
  }

  private askSync(): void {
    const n = this.doc.cues.length;
    if (n < 2) return;
    const rows = this.chosenRows();
    const [first, last] = rows.length > 1 ? [rows[0], rows[rows.length - 1]] : [0, n - 1];
    const dialog = this.syncDialog;
    dialog.querySelector(".sub-sync-problem")!.textContent = "";
    dialog.querySelectorAll<HTMLElement>(".sub-sync-row").forEach((row, k) => {
      const [num] = row.querySelectorAll("input");
      num.max = String(n);
      num.value = String((k === 0 ? first : last) + 1);
      row.querySelector("button")!.hidden = !this.hasVideo;
      this.fillSync(row);
    });
    dialog.showModal();
  }

  private buildHelp(): HTMLDialogElement {
    const [dialog, buttons] = this.dialog("code-help", "Keyboard shortcuts");
    const table = el("div", "code-help-rows");
    let group = "";
    for (const s of SHORTCUTS) {
      if (s.group !== group) {
        group = s.group;
        table.append(el("h3", "", group));
      }
      const row = el("div", "code-help-row");
      row.append(el("kbd", "", s.keys.map(showKey).join(" / ")), el("span", "", s.label));
      table.append(row);
    }
    const row = el("div", "code-help-row");
    row.append(el("kbd", "", "Space (in the list)"), el("span", "", "Play / pause the video"));
    table.append(row);
    const close = el("button", "primary small", "Close");
    close.type = "button";
    close.addEventListener("click", () => dialog.close());
    buttons.append(el("div", "spacer"), close);
    dialog.insertBefore(table, buttons);
    return dialog;
  }
}

export function mountSubtitleEditor(host: HTMLElement, opts: SubtitleEditorOptions): SubtitleEditorPage {
  const page = new SubtitleEditorPage(opts);
  host.append(page.el);
  return page;
}
