// The "Change details" card for PDF, Word / Excel / PowerPoint and OpenDocument files in the
// Properties pane — the web side of the desktop's gui/properties/document_form.py (same fields
// and rules). A locked PDF shows a password box first (the password is kept for this page only);
// a signed PDF gets a note. The browser can't write into the original file, so the button
// downloads the changed document (a ZIP for several); the queue then holds the changed file.
import type { Values } from "../core/options";
import type { InputFile, OutputFile } from "../core/types";
import { NEEDS_PASSWORD } from "../converters/documenttags";
import type { DocumentDetails, Field } from "../properties/document";
import { uniqueName } from "./photoform";

export const DOCUMENT_EXTS = ["pdf", "docx", "docm", "dotx", "xlsx", "xlsm", "pptx", "pptm", "ppsx", "odt", "ods", "odp", "odg"];

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const SEVERAL = "Several values";
const LABELS: [Field, string][] = [["title", "Title"], ["subject", "Subject"], ["author", "Author"], ["keywords", "Keywords"]];
const LOCKED = "This PDF is locked with a password. Type it to see and change the details.";
const SIGNED = "This PDF is signed. The signature stays, but PDF readers will show that the file was changed after signing.";
const loadModel = () => import("../properties/document");

/** Runs the hidden "document-tags" converter: ``read`` gives details.json, ``save`` the new file. */
export type DocumentRunner = (file: InputFile, options: Values) => Promise<OutputFile[]>;
type Item = { file: InputFile; details: DocumentDetails };

const passwordMessage = (e: unknown): string | null =>
  e instanceof Error && e.message.startsWith(NEEDS_PASSWORD) ? e.message.slice(NEEDS_PASSWORD.length) : null;

export class DocumentForm {
  readonly el: HTMLElement;
  private readonly boxes = new Map<Field, HTMLInputElement>();
  private readonly lockBox = h("div", { class: "df-lock", hidden: true });
  private readonly lockText = h("div", { class: "hint" }, LOCKED);
  private readonly password = h("input", { type: "password", id: "df-password", ariaLabel: "Password", placeholder: "Password" });
  private readonly open = h("button", { type: "button", title: "Open the PDF with this password (it isn't stored)" }, "Open");
  private readonly fields: HTMLElement;
  private readonly signed = h("div", { class: "hint df-signed", hidden: true }, SIGNED);
  private readonly applyAll = h("input", { type: "checkbox", id: "df-apply-all" });
  private readonly applyAllRow: HTMLElement;
  private readonly applyAllText = h("span");
  private readonly note = h("div", { class: "hint ph-note df-note" });
  private readonly save = h("button", { class: "primary", type: "button", disabled: true, title: "Download the document with the new details (the original on your device isn't changed)" }, "Download with changes");
  private readonly passwords = new Map<InputFile, string>(); // locked PDFs opened on this page
  private start: Record<string, string | null> = {};
  private busy = false;
  private one: Item | null = null;
  private all: Item[] = [];
  private shown: InputFile | null = null;
  private files: InputFile[] = [];
  private loading = 0;
  private model: Awaited<ReturnType<typeof loadModel>> | null = null;

  constructor(private readonly runTags: DocumentRunner, private readonly onSaved: (changed: InputFile[]) => void) {
    const grid = h("div", { class: "ph-grid" });
    for (const [name, label] of LABELS) {
      const box = h("input", { type: "text", id: `df-${name}`, ariaLabel: label, maxLength: 2000 });
      box.addEventListener("input", () => this.changed());
      this.boxes.set(name, box);
      grid.append(h("label", { htmlFor: box.id }, label), box);
    }
    this.boxes.get("keywords")!.placeholder = "Separate them with commas";
    this.applyAll.addEventListener("change", () => this.reload());
    this.applyAllRow = h("label", { class: "ph-check" }, this.applyAll, this.applyAllText);
    this.password.addEventListener("keydown", (e) => { if (e.key === "Enter") this.unlock(); });
    this.open.addEventListener("click", () => this.unlock());
    this.lockBox.append(this.lockText, h("div", { class: "df-unlock" }, this.password, this.open));
    this.fields = h("div", {}, grid, this.signed, this.applyAllRow);
    this.save.addEventListener("click", () => void this.download());
    this.el = h("section", { class: "prop-card df-form", ariaLabel: "Change details", hidden: true },
      h("h3", {}, "Change details"), this.lockBox, this.fields, this.note,
      h("div", { class: "ph-actions" }, this.save));
    this.el.dataset.title = "Change details";
  }

  /** Read the shown document and every other changeable one (for "Apply to all"), one at a
   * time; ``current`` says whether the pane still shows ``f``. null hides the form. */
  async load(f: InputFile | null, files: InputFile[], current: () => boolean = () => true): Promise<void> {
    this.shown = f;
    this.files = files;
    const mine = ++this.loading;
    if (!f) {
      this.el.hidden = true;
      return;
    }
    this.model ??= await loadModel();
    const everyone: Item[] = [];
    let locked: string | null = null;
    for (const g of files) {
      const pw = this.passwords.get(g);
      try {
        const [out] = await this.runTags(g, pw ? { mode: "read", password: pw } : { mode: "read" });
        everyone.push({ file: g, details: JSON.parse(new TextDecoder().decode(out.data)) as DocumentDetails });
      } catch (e) {
        const message = passwordMessage(e);
        if (message !== null && g === f) {
          locked = message;
          this.passwords.delete(g); // it didn't open the file
        }
      }
      if (!current() || mine !== this.loading) return;
    }
    const one = everyone.find((x) => x.file === f) ?? null;
    if (locked !== null) {
      this.showLocked(locked);
      return;
    }
    this.one = one;
    this.all = everyone;
    this.el.hidden = !one;
    if (!one) return;
    this.lockBox.hidden = true;
    this.fields.hidden = this.save.hidden = false;
    this.applyAllText.textContent = `Apply to all ${everyone.length} documents`;
    this.applyAllRow.hidden = everyone.length < 2;
    if (everyone.length < 2) this.applyAll.checked = false;
    this.reload();
  }

  private showLocked(message: string): void {
    this.one = null;
    this.all = [];
    this.el.hidden = false;
    this.lockText.textContent = message;
    this.lockBox.hidden = false;
    this.fields.hidden = this.save.hidden = true;
    this.password.value = "";
    this.password.disabled = this.open.disabled = false;
    this.note.textContent = "";
  }

  private unlock(): void {
    const text = this.password.value;
    if (!text || !this.shown) return;
    this.passwords.set(this.shown, text);
    this.password.disabled = this.open.disabled = true;
    void this.load(this.shown, this.files);
  }

  private get many(): boolean { return this.applyAll.checked && this.all.length > 1; }
  private targets(): Item[] { return this.many ? this.all : this.one ? [this.one] : []; }

  private reload(): void {
    const D = this.model;
    const details = this.targets().map((t) => t.details);
    if (!D || !details.length) return;
    this.start = D.common(details);
    for (const [name, box] of this.boxes) {
      box.value = this.start[name] ?? "";
      box.placeholder = this.start[name] === null ? SEVERAL : name === "keywords" ? "Separate them with commas" : "";
    }
    this.signed.hidden = !details.some((d) => d.signed);
    this.note.textContent = "";
    this.changed();
  }

  edit(): Partial<Record<Field, string>> {
    const values: Partial<Record<Field, string>> = {};
    for (const [name, box] of this.boxes) {
      const start = this.start[name];
      if (start === null && box.value === "") continue; // "Several values" left alone
      if (box.value.trim() !== (start ?? "").trim()) values[name] = box.value;
    }
    return values;
  }

  private changed(): void {
    this.save.disabled = !Object.keys(this.edit()).length || this.busy;
    this.save.textContent = this.many ? "Download changed documents (.zip)" : "Download with changes";
  }

  private message(text: string, error = false): void {
    this.note.textContent = text;
    this.note.classList.toggle("error", error);
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    for (const el of [...this.boxes.values(), this.applyAll]) el.disabled = busy;
    this.changed();
  }

  private async download(): Promise<void> {
    const D = this.model!;
    const values = this.edit();
    try {
      D.cleanValues(values);
    } catch (e) {
      this.message(e instanceof D.DocumentError ? e.message : "The details couldn't be changed.", true);
      return;
    }
    const json = JSON.stringify(values);
    const outputs: { target: Item; data: Uint8Array }[] = [];
    this.setBusy(true);
    this.message("Saving…");
    try {
      for (const t of this.targets()) {
        const pw = this.passwords.get(t.file);
        const [out] = await this.runTags(t.file, { mode: "save", values: json, ...(pw ? { password: pw } : {}) });
        outputs.push({ target: t, data: out.data });
      }
    } catch (e) {
      this.setBusy(false);
      const text = passwordMessage(e) ?? (e instanceof Error && e.message ? e.message : "The details couldn't be changed.");
      this.message(text, true);
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
      name = "documents (changed details).zip";
    }
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: name });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    for (const o of outputs) {
      const f = o.target.file;
      f.file = new File([o.data as BlobPart], f.file.name, { type: f.file.type, lastModified: f.file.lastModified });
    }
    const n = outputs.length;
    this.message(n === 1 ? `Downloaded “${name}” with the new details.` : `Downloaded ${n} documents in “${name}”.`);
    this.onSaved(outputs.map((o) => o.target.file));
  }

  keepNote(text: string): void { this.message(text); }
  get noteText(): string { return this.note.textContent ?? ""; }
}
