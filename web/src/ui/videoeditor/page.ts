// The website's video editor page (port of the desktop's gui/videoedit/): top bar, media list,
// preview, inspector, timeline and a status line. This is the shell; the project, timeline and
// player arrive in the next steps. Loaded on demand from ui/app.ts, only behind the test gate.
import type { InputFile } from "../../core/types";
import type { ConversionClient } from "../../worker/client";
import { backLabel } from "../back";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

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

  constructor(private opts: VideoEditorOptions) {
    const btn = (key: string, label: string, title: string, fn: () => void, cls = "btn small") => {
      const b = h("button", { class: cls, title, type: "button" }, label);
      b.addEventListener("click", fn);
      this.buttons.set(key, b);
      return b;
    };
    const top = h("div", { class: "editor-top" },
      backLabel(btn("back", "← Back to Convert", "Leave the video editor", () => this.back(), "link")),
      btn("undo", "Undo", "Undo (Ctrl+Z)", () => {}),
      btn("redo", "Redo", "Redo (Ctrl+Y)", () => {}),
      h("div", { class: "top-tools" },
        h("span", { class: "spacer" }),
        btn("save", "Save project", "Save the project (Ctrl+S)", () => {}),
        btn("save_as", "Save project as…", "Save the project under a new name (Ctrl+Shift+S)", () => {})),
      btn("export", "Export…", "Export comes soon", () => {}, "primary small"));
    for (const k of ["undo", "redo", "save", "save_as", "export"]) this.buttons.get(k)!.disabled = true;
    this.el = h("div", { class: "editor video-editor", hidden: true },
      top,
      h("div", { class: "video-body" },
        h("div", { class: "video-media", ariaLabel: "Media" }),
        h("div", { class: "video-preview", ariaLabel: "Preview" }),
        h("div", { class: "video-inspector", ariaLabel: "Inspector" }),
        this.timeline),
      h("div", { class: "editor-status" }, this.message));
    if (location.search.includes("e2e")) (window as unknown as { __videoeditor: VideoEditorPage }).__videoeditor = this;
  }

  show(): void {
    this.active = true;
    this.el.hidden = false;
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
  }

  get shown(): boolean {
    return this.active;
  }

  /** Nothing can be unsaved yet. */
  maybeLeave(): boolean {
    return true;
  }

  openEmpty(): void {
    this.showMessage("");
  }

  async open(files: InputFile[]): Promise<boolean> {
    this.showMessage(files.length ? `${files.length} file${files.length === 1 ? "" : "s"} ready` : "");
    return true;
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
