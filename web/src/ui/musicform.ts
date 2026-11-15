// The "Change details" card for MP3 / M4A / FLAC / OGG / Opus songs in the Properties pane — the
// web side of the desktop's gui/properties/music_form.py (same fields and rules). The browser
// can't write into the original file, so the button downloads the changed song (a ZIP for
// several); the queue then holds the changed song, so later conversions use it.
import type { InputFile, OutputFile } from "../core/types";
import type { Field, MusicDetails, MusicEdit } from "../properties/music";
import { uniqueName } from "./photoform";

// The same list as properties/music.ts (a test checks), kept here so the page doesn't load the
// music code until a song's details are shown.
export const FIELDS: readonly Field[] = ["title", "artist", "album", "album_artist", "track", "disc", "year", "genre"];
const NO_COVER_EXTS = ["ogg", "opus"];

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const LABELS: Record<Field, string> = {
  title: "Title", artist: "Artist", album: "Album", album_artist: "Album artist",
  track: "Track", disc: "Disc", year: "Year", genre: "Genre",
};
const HINTS: Partial<Record<Field, string>> = { track: "Like 3 or 3/12", disc: "Like 1 or 1/2", year: "Like 2026" };
const SEVERAL = "Several values";

const loadMusic = () => import("../properties/music");

/** Each song's tags, read on the page with Mediabunny (throws for files it can't read). */
export async function readMusicDetails(file: File): Promise<MusicDetails> {
  const [{ Input, BlobSource, ALL_FORMATS }, music] = await Promise.all([import("mediabunny"), loadMusic()]);
  const input = new Input({ source: new BlobSource(file), formats: ALL_FORMATS });
  try {
    return music.detailsFromTags(await input.getMetadataTags());
  } finally {
    input.dispose();
  }
}

export type TagRunner = (file: InputFile, edit: string, cover: File | null) => Promise<OutputFile[]>;
type Song = { file: InputFile; details: MusicDetails };

export class MusicForm {
  readonly el: HTMLElement;
  private readonly inputs = {} as Record<Field, HTMLInputElement>;
  private readonly coverStatus = h("span", { class: "mf-cover-status" });
  private readonly coverPick = h("input", { type: "file", accept: "image/jpeg,image/png,.jpg,.jpeg,.png", hidden: true, id: "mf-cover-file" });
  private readonly coverButton = h("button", { type: "button", title: "Use a JPG or PNG picture as the cover" }, "Choose picture…");
  private readonly removeButton = h("button", { type: "button", title: "Take the cover picture out" }, "Remove");
  private readonly applyAll = h("input", { type: "checkbox", id: "mf-apply-all" });
  private readonly applyAllRow: HTMLElement;
  private readonly applyAllText = h("span");
  private readonly note = h("div", { class: "hint ph-note mf-note" });
  private readonly save = h("button", { class: "primary", type: "button", disabled: true, title: "Download the song with the new details (the original on your device isn't changed)" }, "Download with changes");
  private start = {} as Record<Field, string | null>;
  private cover: File | null = null;
  private removeCover = false;
  private busy = false;
  private one: Song | null = null;
  private all: Song[] = [];

  constructor(private readonly runTags: TagRunner, private readonly onSaved: (changed: InputFile[]) => void) {
    const rows = h("div", { class: "ph-grid" });
    for (const name of FIELDS) {
      const input = h("input", { type: "text", id: `mf-${name}`, ariaLabel: LABELS[name] });
      input.addEventListener("input", () => this.changed());
      this.inputs[name] = input;
      rows.append(h("label", { htmlFor: input.id }, LABELS[name]), input);
    }
    this.coverButton.addEventListener("click", () => this.coverPick.click());
    this.coverPick.addEventListener("change", () => {
      const file = this.coverPick.files?.[0];
      this.coverPick.value = "";
      if (file) void this.setCover(file);
    });
    this.removeButton.addEventListener("click", () => {
      this.cover = null;
      this.removeCover = true;
      this.showCover();
      this.changed();
    });
    rows.append(h("label", {}, "Cover"), h("div", { class: "mf-cover" }, this.coverStatus, this.coverButton, this.removeButton, this.coverPick));
    this.applyAll.addEventListener("change", () => this.reload());
    this.applyAllRow = h("label", { class: "ph-check" }, this.applyAll, this.applyAllText);
    this.save.addEventListener("click", () => void this.download());
    this.el = h("section", { class: "prop-card mf-form", ariaLabel: "Change details", hidden: true },
      h("h3", {}, "Change details"), rows, this.applyAllRow,
      h("div", { class: "hint mf-limit" }, "In the browser only these details are kept for sure — others, like lyrics or loudness info, may be lost. The desktop app keeps them."),
      this.note, h("div", { class: "ph-actions" }, this.save));
    this.el.dataset.title = "Change details";
  }

  /** The shown song and every changeable song (for "Apply to all"); null hides the form. */
  set(current: Song | null, everyone: Song[]): void {
    this.one = current;
    this.all = everyone;
    this.el.hidden = !current;
    if (!current) return;
    this.applyAllText.textContent = `Apply to all ${everyone.length} songs`;
    this.applyAllRow.hidden = everyone.length < 2;
    if (everyone.length < 2) this.applyAll.checked = false;
    this.reload();
  }

  private get many(): boolean { return this.applyAll.checked && this.all.length > 1; }
  private targets(): Song[] { return this.many ? this.all : this.one ? [this.one] : []; }
  private get coverWorks(): boolean { return !this.targets().some((t) => NO_COVER_EXTS.includes(t.file.ext)); }

  private reload(): void {
    const details = this.targets().map((t) => t.details);
    if (!details.length) return;
    const start: Record<string, string | null> = {};
    for (const name of FIELDS) {
      const values = new Set(details.map((d) => d[name]));
      start[name] = values.size === 1 ? [...values][0] : null;
    }
    this.start = start as Record<Field, string | null>;
    for (const name of FIELDS) {
      const value = this.start[name];
      this.inputs[name].value = value ?? "";
      this.inputs[name].placeholder = value === null ? SEVERAL : HINTS[name] ?? "";
    }
    this.cover = null;
    this.removeCover = false;
    this.note.textContent = "";
    this.showCover();
    this.changed();
  }

  private showCover(): void {
    const details = this.targets().map((t) => t.details);
    let text: string;
    if (this.cover) text = `New cover: ${this.cover.name}`;
    else if (this.removeCover) text = "The cover will be removed";
    else if (details.length && details.every((d) => d.has_cover)) text = details.length === 1 ? "Has a cover picture" : "All have a cover picture";
    else if (details.some((d) => d.has_cover)) text = "Some have a cover picture";
    else text = "No cover picture";
    if (!this.coverWorks && !this.cover && !this.removeCover) text += " (the browser can't change OGG / Opus covers)";
    this.coverStatus.textContent = text;
    this.coverButton.hidden = !this.coverWorks;
    this.coverButton.disabled = this.busy;
    this.removeButton.disabled = this.busy || !(this.cover || (!this.removeCover && details.some((d) => d.has_cover)));
  }

  /** Use ``file`` as the new cover (checked first). */
  async setCover(file: File): Promise<void> {
    const music = await loadMusic();
    try {
      music.coverMime(new Uint8Array(await file.slice(0, 16).arrayBuffer()));
      if (file.size > music.MAX_COVER_BYTES) music.coverMime(new Uint8Array(music.MAX_COVER_BYTES + 1));
    } catch (e) {
      this.message(e instanceof music.MusicError ? e.message : "That picture couldn't be read.", true);
      return;
    }
    this.cover = file;
    this.removeCover = false;
    this.note.textContent = "";
    this.showCover();
    this.changed();
  }

  edit(): MusicEdit {
    const values: Partial<Record<Field, string>> = {};
    for (const name of FIELDS) {
      const start = this.start[name];
      const text = this.inputs[name].value;
      if (start === null && text === "") continue; // "Several values" left alone
      if (text.trim() !== (start ?? "").trim()) values[name] = text;
    }
    return { values, remove_cover: this.removeCover && !this.cover };
  }

  private changed(): void {
    const e = this.edit();
    const dirty = Object.keys(e.values).length > 0 || this.cover !== null || Boolean(e.remove_cover);
    this.save.disabled = !dirty || this.busy;
    this.save.textContent = this.many ? "Download changed songs (.zip)" : "Download with changes";
    void loadMusic().then((m) => {
      for (const name of ["track", "disc", "year"] as const) {
        let ok = true;
        try { m.cleanValues({ [name]: this.inputs[name].value }); } catch { ok = false; }
        this.inputs[name].classList.toggle("invalid", !ok);
      }
    });
  }

  private message(text: string, error = false): void {
    this.note.textContent = text;
    this.note.classList.toggle("error", error);
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    for (const input of Object.values(this.inputs)) input.disabled = busy;
    this.applyAll.disabled = busy;
    this.showCover();
    this.changed();
  }

  private async download(): Promise<void> {
    const music = await loadMusic();
    const edit = this.edit();
    try {
      music.cleanValues(edit.values);
    } catch (e) {
      this.message(e instanceof music.MusicError ? e.message : "The details couldn't be changed.", true);
      return;
    }
    const targets = this.targets();
    const json = JSON.stringify({ values: edit.values, remove_cover: edit.remove_cover });
    const outputs: { target: Song; data: Uint8Array }[] = [];
    this.setBusy(true);
    this.message("Saving…");
    try {
      for (const t of targets) {
        const [out] = await this.runTags(t.file, json, this.cover);
        outputs.push({ target: t, data: out.data });
      }
    } catch (e) {
      this.setBusy(false);
      this.message(e instanceof Error && e.message ? e.message : "The details couldn't be changed.", true);
      return;
    }
    this.setBusy(false);
    let blob: Blob, name: string;
    if (outputs.length === 1) {
      const f = outputs[0].target.file;
      blob = new Blob([outputs[0].data as BlobPart], { type: f.file.type });
      name = f.name;
    } else {
      const { zipSync } = await import("fflate");
      const tree: Record<string, Uint8Array> = {};
      for (const o of outputs) tree[uniqueName(tree, o.target.file.name)] = o.data;
      blob = new Blob([zipSync(tree, { level: 0 }) as BlobPart], { type: "application/zip" });
      name = "songs (changed details).zip";
    }
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    // the queue now holds the changed songs, so a conversion afterwards uses them
    for (const o of outputs) {
      const f = o.target.file;
      f.file = new File([o.data as BlobPart], f.file.name, { type: f.file.type, lastModified: f.file.lastModified });
    }
    const n = outputs.length;
    this.message(n === 1 ? `Downloaded “${name}” with the new details.` : `Downloaded ${n} songs in “${name}”.`);
    this.onSaved(outputs.map((o) => o.target.file));
  }

  /** After a save the form is filled again; this puts the "Downloaded …" message back. */
  keepNote(text: string): void { this.message(text); }

  /** (tests) the note under the form */
  get noteText(): string { return this.note.textContent ?? ""; }
}
