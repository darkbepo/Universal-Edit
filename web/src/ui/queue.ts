// The queue: one row per input file — thumbnail, name, size (+ pixel size) or its own progress.
import { rowStates, type RowState } from "../core/progress";
import type { InputFile } from "../core/types";
import { FAMILY_ICON, icon } from "./icons";

// Formats every current browser can decode in an <img>; others get the family icon.
const PREVIEWABLE = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "avif", "ico", "svg"]);
const PREVIEW_MAX_BYTES = 40 * 1024 * 1024;

export const fmtSize = (n: number) =>
  n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(0)} KB` : n < 1 << 30 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${(n / (1 << 30)).toFixed(2)} GB`;

interface Row {
  li: HTMLLIElement;
  detail: HTMLElement;
  bar: HTMLElement;
  fill: HTMLElement;
  state: HTMLElement;
  remove: HTMLButtonElement;
}

export class Queue {
  readonly el: HTMLUListElement;
  onRemove: (index: number) => void = () => {};
  onReorder: (from: number, to: number) => void = () => {};
  locked = false;
  private rows: Row[] = [];
  private thumbs = new Map<File, string>();
  private dims = new Map<File, string>();
  private dragIndex = -1;

  constructor() {
    this.el = document.createElement("ul");
    this.el.className = "queue";
  }

  set(files: InputFile[]): void {
    // Drop previews of files that left the queue.
    const keep = new Set(files.map((f) => f.file));
    for (const [file, url] of this.thumbs) {
      if (!keep.has(file)) {
        URL.revokeObjectURL(url);
        this.thumbs.delete(file);
        this.dims.delete(file);
      }
    }
    this.rows = files.map((f, i) => this.row(f, i));
    this.el.replaceChildren(...this.rows.map((r) => r.li));
  }

  private row(f: InputFile, i: number): Row {
    const thumb = el("div", "thumb");
    const ext = f.ext.toLowerCase();
    if (f.family === "image" && PREVIEWABLE.has(ext) && f.file.size <= PREVIEW_MAX_BYTES) {
      let url = this.thumbs.get(f.file);
      if (!url) {
        url = URL.createObjectURL(f.file);
        this.thumbs.set(f.file, url);
      }
      const img = document.createElement("img");
      img.alt = "";
      img.decoding = "async";
      img.loading = "lazy";
      img.src = url;
      img.addEventListener("load", () => {
        if (img.naturalWidth && !this.dims.has(f.file)) {
          this.dims.set(f.file, `${img.naturalWidth}×${img.naturalHeight}`);
          detail.textContent = this.detailText(f);
        }
      });
      img.addEventListener("error", () => img.replaceWith(icon(FAMILY_ICON[f.family] ?? "file", 20)));
      thumb.append(img);
    } else {
      thumb.append(icon(FAMILY_ICON[f.family] ?? "file", 20));
    }
    const name = el("span", "fname", f.name);
    name.title = f.name;
    const detail = el("span", "detail", this.detailText(f));
    const fill = el("div", "");
    const bar = el("div", "row-progress", fill);
    bar.hidden = true;
    const text = el("div", "text", name, detail, bar);
    const remove = document.createElement("button");
    remove.className = "icon-btn remove";
    remove.title = "Remove";
    remove.ariaLabel = `Remove ${f.name}`;
    remove.append(icon("close", 16));
    remove.addEventListener("click", () => !this.locked && this.onRemove(i));
    const state = el("div", "state", remove);
    const li = el("li", "", thumb, text, state) as HTMLLIElement;
    li.dataset.state = "idle";
    li.draggable = true;
    li.addEventListener("dragstart", (e) => {
      if (this.locked) return e.preventDefault();
      this.dragIndex = i;
      li.classList.add("dragging");
      e.dataTransfer?.setData("text/plain", String(i));
    });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("dragover", (e) => {
      if (this.dragIndex >= 0) e.preventDefault();
    });
    li.addEventListener("drop", (e) => {
      if (this.dragIndex < 0) return;
      e.preventDefault();
      e.stopPropagation();
      const from = this.dragIndex;
      this.dragIndex = -1;
      if (from !== i) this.onReorder(from, i);
    });
    return { li, detail, bar, fill, state, remove };
  }

  private detailText(f: InputFile): string {
    return [fmtSize(f.file.size), this.dims.get(f.file)].filter(Boolean).join(" · ");
  }

  states(): RowState[] {
    return this.rows.map((r) => r.li.dataset.state as RowState);
  }

  private setRow(r: Row, state: RowState, fraction: number | null, message = ""): void {
    r.li.dataset.state = state;
    const showBar = state === "running" || state === "done" || state === "error";
    r.bar.hidden = !showBar;
    r.detail.hidden = showBar;
    r.fill.style.width = `${((state === "done" ? 1 : fraction ?? 0) * 100).toFixed(1)}%`;
    r.li.title = message;
    const label =
      state === "done" ? icon("check", 18, "ico ok")
        : state === "error" ? icon("alert", 18, "ico bad")
          : state === "running" ? el("span", "pct", `${Math.round((fraction ?? 0) * 100)} %`)
            : state === "waiting" ? el("span", "pct", "Waiting")
              : null;
    r.state.replaceChildren(label ?? r.remove);
  }

  setFractions(fractions: number[]): void {
    const states = rowStates(fractions);
    this.rows.forEach((r, i) => this.setRow(r, states[i] ?? "idle", fractions[i] ?? 0));
  }

  markDone(): void {
    this.rows.forEach((r) => this.setRow(r, "done", 1));
  }

  markFailed(message: string): void {
    const i = this.rows.findIndex((r) => r.li.dataset.state !== "done");
    if (i < 0) return;
    this.setRow(this.rows[i], "error", Number.parseFloat(this.rows[i].fill.style.width) / 100 || 0, message);
    for (const r of this.rows.slice(i + 1)) if (r.li.dataset.state === "waiting") this.reset1(r);
  }

  markCancelled(): void {
    for (const r of this.rows) if (r.li.dataset.state === "running" || r.li.dataset.state === "waiting") this.reset1(r);
  }

  private reset1(r: Row): void {
    this.setRow(r, "idle", null);
  }
}

function el(tag: string, cls: string, ...children: (Node | string)[]): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  e.append(...children);
  return e;
}
