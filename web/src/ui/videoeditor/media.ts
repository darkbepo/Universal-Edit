// The project's files (port of gui/videoedit/media_list.py): one row per source with its kind,
// name and length. Drag a row onto the timeline, or double-click / Enter / "Add" to put it at the
// playhead (touch screens have no drag and drop). "+ Add files" and "+ Text" sit under the list.
import type { Source } from "../../videoedit/project";
import { icon } from "../icons";
import { formatTime } from "./timelinemath";

/** dataTransfer type of a dragged source row (its value is the source id). */
export const SOURCE_MIME = "application/x-umc-source";
export const ACCEPT = "video/*,image/*,audio/*,.umcvideo";

const KIND_ICON = { video: "movie", image: "photo", audio: "music" } as const;

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export function detailOf(src: Source): string {
  if (src.kind === "image") return "Picture";
  if (src.kind === "audio") return `Music · ${formatTime(src.duration)}`;
  return formatTime(src.duration);
}

const baseName = (path: string) => path.split(/[\\/]/).pop() ?? path;

export class MediaList {
  readonly el: HTMLElement;
  /** The file picker of "+ Add files" (the page reads its files). */
  readonly picker: HTMLInputElement;
  private list = h("ul", { class: "ve-media-list", ariaLabel: "Project files" });
  private shown: readonly Source[] = [];
  private shownNotes: ReadonlyMap<string, string> = new Map();

  constructor(private on: { add(id: string): void; files(): void; text(): void }) {
    this.picker = h("input", { type: "file", multiple: true, accept: ACCEPT, hidden: true });
    const addFiles = h("button", { class: "btn small", type: "button", title: "Add videos, pictures or music to the project" }, "+ Add files");
    addFiles.addEventListener("click", () => on.files());
    const addText = h("button", { class: "btn small", type: "button", title: "Add a text at the playhead" }, "+ Text");
    addText.addEventListener("click", () => on.text());
    this.el = h("div", { class: "ve-media" }, this.list, h("div", { class: "ve-media-buttons" }, addFiles, addText), this.picker);
  }

  set(sources: readonly Source[], notes: ReadonlyMap<string, string>): void {
    if (sources === this.shown && notes === this.shownNotes) return;
    this.shown = sources;
    this.shownNotes = notes;
    this.list.replaceChildren(...sources.map((src) => this.row(src, notes.get(src.id) ?? null)));
  }

  private row(src: Source, note: string | null): HTMLElement {
    const name = baseName(src.path);
    const add = h("button", { class: "btn small ve-media-add", type: "button", title: `Add “${name}” at the playhead` }, "Add");
    add.addEventListener("click", (e) => {
      e.stopPropagation();
      this.on.add(src.id);
    });
    const text = h("div", { class: "ve-media-text" },
      h("div", { class: "ve-media-name", title: src.path }, name),
      h("div", { class: "ve-media-detail" }, detailOf(src)));
    if (note) text.append(h("div", { class: "ve-media-note" }, note));
    const li = h("li", { class: `ve-media-row ${src.kind}`, tabIndex: 0, draggable: true },
      h("span", { class: "ve-media-icon" }, icon(KIND_ICON[src.kind], 20)), text, add);
    li.dataset.source = src.id;
    li.addEventListener("dblclick", () => this.on.add(src.id));
    li.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && e.target === li) {
        e.preventDefault();
        this.on.add(src.id);
      }
    });
    li.addEventListener("dragstart", (e) => {
      e.dataTransfer?.setData(SOURCE_MIME, src.id);
      if (e.dataTransfer) e.dataTransfer.effectAllowed = "copy";
    });
    return li;
  }
}
