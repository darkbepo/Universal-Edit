// The right column in Properties mode: "About this file" for one of the added files. A picker
// on top when there are several. The facts are read by the hidden "properties-info" converter in
// a worker of its own (so a slow read never holds up a conversion); picking another file stops
// a read still running.
import { DOCUMENT_TAGS_TARGET } from "../converters/documenttags";
import { MUSIC_TAGS_TARGET } from "../converters/musictags";
import { PROPERTIES_TARGET } from "../converters/properties";
import { VIDEO_TAGS_TARGET } from "../converters/videotags";
import type { InputFile } from "../core/types";
import { cleanName, FileError } from "../properties/fileinfo";
import { build, type Facts, type Section } from "../properties/info";
import { fileFacts } from "../properties/read";
import { ConversionClient } from "../worker/client";
import { DOCUMENT_EXTS, DocumentForm } from "./documentform";
import { icon } from "./icons";
import { MusicForm, readMusicDetails } from "./musicform";
import { PhotoForm } from "./photoform";
import { readVideoDetails, VIDEO_EXTS, VideoForm, type VideoRunner } from "./videoform";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export class PropertiesPane {
  readonly el: HTMLElement;
  private readonly picker = h("select", { class: "prop-file", ariaLabel: "File" });
  private readonly status = h("div", { class: "hint prop-status", hidden: true });
  private readonly body = h("div", { class: "prop-cards" });
  private readonly nameInput = h("input", { type: "text", ariaLabel: "File name", spellcheck: false });
  private readonly nameNote = h("p", { class: "hint name-note" });
  private readonly nameCard = h("section", { class: "prop-card name-card", ariaLabel: "Name" });
  private files: InputFile[] = [];
  private current: InputFile | null = null;
  private ticket = 0;
  private reading = false;
  private readonly client = new ConversionClient(); // its worker starts on the first read
  sections: Section[] = [];
  readonly form: PhotoForm;
  readonly musicForm: MusicForm;
  readonly videoForm: VideoForm;
  readonly documentForm: DocumentForm;
  // saving songs has its own worker, so a read cancelled by moving on never stops a save
  private readonly tagClient = new ConversionClient();
  private readonly runVideo: VideoRunner;

  /** ``onChanged``: pictures whose details were changed (their bytes in the queue are new). */
  constructor(private readonly onChanged: (files: InputFile[]) => void = () => {}) {
    const saved = (changed: InputFile[]) => {
      this.onChanged(changed);
      if (this.current) void this.show(this.files.indexOf(this.current), true);
    };
    this.form = new PhotoForm(saved);
    this.musicForm = new MusicForm(
      (f, edit, cover) => this.tagClient.run(MUSIC_TAGS_TARGET, [f], { edit, cover }, () => {}),
      saved,
    );
    this.runVideo = (f, options) => this.tagClient.run(VIDEO_TAGS_TARGET, [f], options, () => {});
    this.videoForm = new VideoForm(this.runVideo, saved);
    this.documentForm = new DocumentForm((f, options) => this.tagClient.run(DOCUMENT_TAGS_TARGET, [f], options, () => {}), saved);
    this.el = h("section", { class: "pane properties-pane", hidden: true, ariaLabel: "Properties" },
      h("div", { class: "pane-head" }, h("span", { class: "pane-icon" }, icon("info-circle", 18)), h("h2", {}, "About this file"), h("div", { class: "spacer" }), this.picker),
      h("div", { class: "pane-body" }, this.status, this.form.el, this.musicForm.el, this.videoForm.el, this.documentForm.el, this.body, this.nameCard));
    this.picker.addEventListener("change", () => void this.show(this.picker.selectedIndex));
    this.buildNameCard();
  }

  /** Keeps showing the same file while it's still there (a file added or removed). */
  setFiles(files: InputFile[]): void {
    this.files = [...files];
    this.picker.replaceChildren(...this.files.map((f) => h("option", {}, f.name)));
    this.picker.hidden = this.files.length < 2;
    if (!this.files.length) {
      this.current = null;
      this.nameCard.hidden = true;
      this.render([]);
      return;
    }
    const keep = this.current ? this.files.indexOf(this.current) : -1;
    const index = keep >= 0 ? keep : 0;
    this.picker.selectedIndex = index;
    if (this.files[index] !== this.current || !this.sections.length) void this.show(index);
  }

  async show(index: number, keepNote = false): Promise<void> {
    const f = this.files[index];
    if (!f) return;
    if (this.current !== f) {
      this.nameInput.value = f.name;
      this.nameNote.textContent = "";
      this.nameNote.classList.remove("error");
    }
    this.current = f;
    this.nameCard.hidden = false;
    const ticket = ++this.ticket;
    void this.fillForm(f, ticket, keepNote);
    const base = fileFacts(f.file, f.name, f.ext, f.family);
    this.render(build({ file: base })); // the file's own facts at once, the rest when read
    this.status.textContent = `Reading ${f.name}…`;
    this.status.hidden = false;
    if (this.reading) this.client.cancel(); // the older read is no use any more
    let facts: Facts = {};
    this.reading = true;
    try {
      const out = await this.client.run(PROPERTIES_TARGET, [f], {}, () => {});
      facts = JSON.parse(new TextDecoder().decode(out[0].data));
    } catch {
      facts = { note: "Some details of this file couldn't be read." };
    } finally {
      if (ticket === this.ticket) this.reading = false;
    }
    if (ticket !== this.ticket) return; // the user moved on
    this.status.hidden = true;
    this.render(build({ ...facts, file: base }));
  }

  /** The "Change details" form: JPG / PNG / WebP pictures, MP3 / M4A / FLAC / OGG / Opus songs, or
   * MP4 / MOV / M4V / MKV / WebM videos, or PDF / Office / OpenDocument files. */
  private async fillForm(f: InputFile, ticket: number, keepNote: boolean): Promise<void> {
    const changeable = (g: InputFile) => ["jpg", "png", "webp"].includes(g.ext);
    const song = (g: InputFile) => ["mp3", "m4a", "flac", "ogg", "opus"].includes(g.ext);
    const clip = (g: InputFile) => VIDEO_EXTS.includes(g.ext);
    if (!song(f)) this.musicForm.set(null, []);
    if (!clip(f)) void this.videoForm.set(null, []);
    const paper = (g: InputFile) => DOCUMENT_EXTS.includes(g.ext);
    if (!paper(f)) void this.documentForm.load(null, []);
    if (paper(f)) {
      this.form.set(null, []);
      const note = keepNote ? this.documentForm.noteText : "";
      await this.documentForm.load(f, this.files.filter(paper), () => ticket === this.ticket);
      if (note && ticket === this.ticket) this.documentForm.keepNote(note);
      return;
    }
    if (clip(f)) {
      this.form.set(null, []);
      const read = async (g: InputFile) => {
        try {
          return { file: g, details: await readVideoDetails(this.runVideo, g) };
        } catch {
          return null;
        }
      };
      const everyone = [];
      for (const g of this.files.filter(clip)) { // one at a time: one FFmpeg in the worker
        const x = await read(g);
        if (ticket !== this.ticket) return;
        if (x) everyone.push(x);
      }
      const note = keepNote ? this.videoForm.noteText : "";
      await this.videoForm.set(everyone.find((x) => x.file === f) ?? null, everyone);
      if (note) this.videoForm.keepNote(note);
      return;
    }
    if (song(f)) {
      this.form.set(null, []);
      const read = async (g: InputFile) => {
        try {
          return { file: g, details: await readMusicDetails(g.file) };
        } catch {
          return null;
        }
      };
      const everyone = (await Promise.all(this.files.filter(song).map(read))).filter((x) => x !== null);
      if (ticket !== this.ticket) return;
      const note = keepNote ? this.musicForm.noteText : "";
      this.musicForm.set(everyone.find((x) => x.file === f) ?? null, everyone);
      if (note) this.musicForm.keepNote(note);
      return;
    }
    if (!changeable(f)) {
      this.form.set(null, []);
      return;
    }
    const photo = await import("../properties/photo");
    const read = async (g: InputFile) => {
      try {
        return { file: g, details: photo.readDetails(new Uint8Array(await g.file.arrayBuffer())) };
      } catch {
        return null;
      }
    };
    const everyone = (await Promise.all(this.files.filter(changeable).map(read))).filter((x) => x !== null);
    if (ticket !== this.ticket) return;
    const note = keepNote ? this.form.noteText : "";
    this.form.set(everyone.find((x) => x.file === f) ?? null, everyone);
    if (note) this.form.keepNote(note);
  }

  /** Name (desktop Properties step 6): a browser can't rename the file on disk or set its date,
   * so the new name is used in the queue and for "Download with this name". */
  private buildNameCard(): void {
    const rename = h("button", { type: "button", className: "btn small", textContent: "Rename" });
    const download = h("button", { type: "button", className: "btn small", textContent: "Download with this name" });
    rename.addEventListener("click", () => this.rename());
    download.addEventListener("click", () => {
      if (this.rename(true) && this.current) {
        const url = URL.createObjectURL(this.current.file);
        const a = h("a", { href: url, download: this.current.name });
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      }
    });
    this.nameInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        this.rename();
      }
    });
    const row = h("div", { class: "row" }, rename, download);
    const limit = h("p", { class: "hint" },
      "Converted files are named after it. The website can't rename the file on your computer or change its date — the desktop app can.");
    this.nameCard.append(h("h3", {}, "Name"), this.nameInput, row, this.nameNote, limit);
    this.nameCard.dataset.title = "Name";
    this.nameCard.hidden = true;
  }

  /** Use the typed name for the shown file (in the queue). False if it can't be used. */
  rename(quiet = false): boolean {
    const f = this.current;
    if (!f) return false;
    let name: string;
    try {
      name = cleanName(this.nameInput.value, f.name);
    } catch (e) {
      if (!(e instanceof FileError)) throw e;
      this.nameNote.textContent = e.message;
      this.nameNote.classList.add("error");
      return false;
    }
    this.nameNote.classList.remove("error");
    this.nameInput.value = name;
    if (name === f.name) {
      if (!quiet) this.nameNote.textContent = "That's already its name.";
      return true;
    }
    f.file = new File([f.file], name, { type: f.file.type, lastModified: f.file.lastModified });
    f.name = name;
    this.nameNote.textContent = `Renamed to “${name}”.`;
    const index = this.files.indexOf(f);
    if (index >= 0 && this.picker.options[index]) this.picker.options[index].textContent = name;
    this.onChanged([f]);
    return true;
  }

  private render(sections: Section[]): void {
    this.sections = sections;
    this.body.replaceChildren(...sections.map((s) => {
      const list = h("dl", {});
      for (const [label, value] of s.rows) {
        if (label) list.append(h("dt", {}, label), h("dd", {}, value));
        else list.append(h("dd", { class: "wide" }, value));
      }
      const card = h("section", { class: "prop-card", ariaLabel: s.title }, h("h3", {}, s.title), list);
      card.dataset.title = s.title;
      return card;
    }));
  }
}
