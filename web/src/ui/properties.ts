// The right column in Properties mode: "About this file" for one of the added files. A picker
// on top when there are several. The facts are read by the hidden "properties-info" converter in
// a worker of its own (so a slow read never holds up a conversion); picking another file stops
// a read still running.
import { PROPERTIES_TARGET } from "../converters/properties";
import type { InputFile } from "../core/types";
import { build, type Facts, type Section } from "../properties/info";
import { fileFacts } from "../properties/read";
import { ConversionClient } from "../worker/client";
import { icon } from "./icons";
import { PhotoForm } from "./photoform";

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
  private files: InputFile[] = [];
  private current: InputFile | null = null;
  private ticket = 0;
  private reading = false;
  private readonly client = new ConversionClient(); // its worker starts on the first read
  sections: Section[] = [];
  readonly form: PhotoForm;

  /** ``onChanged``: pictures whose details were changed (their bytes in the queue are new). */
  constructor(private readonly onChanged: (files: InputFile[]) => void = () => {}) {
    this.form = new PhotoForm((changed) => {
      this.onChanged(changed);
      if (this.current) void this.show(this.files.indexOf(this.current), true);
    });
    this.el = h("section", { class: "pane properties-pane", hidden: true, ariaLabel: "Properties" },
      h("div", { class: "pane-head" }, h("span", { class: "pane-icon" }, icon("info-circle", 18)), h("h2", {}, "About this file"), h("div", { class: "spacer" }), this.picker),
      h("div", { class: "pane-body" }, this.status, this.form.el, this.body));
    this.picker.addEventListener("change", () => void this.show(this.picker.selectedIndex));
  }

  /** Keeps showing the same file while it's still there (a file added or removed). */
  setFiles(files: InputFile[]): void {
    this.files = [...files];
    this.picker.replaceChildren(...this.files.map((f) => h("option", {}, f.name)));
    this.picker.hidden = this.files.length < 2;
    if (!this.files.length) {
      this.current = null;
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
    this.current = f;
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

  /** The "Change details" form, for JPG / PNG / WebP pictures. */
  private async fillForm(f: InputFile, ticket: number, keepNote: boolean): Promise<void> {
    const changeable = (g: InputFile) => ["jpg", "png", "webp"].includes(g.ext);
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
