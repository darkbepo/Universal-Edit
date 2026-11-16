// The "Change details" card for MP4 / MOV / M4V / MKV / WebM videos in the Properties pane — the
// web side of the desktop's gui/properties/video_form.py (same fields and rules). The browser
// can't write into the original file, so the button downloads the changed video (a ZIP for
// several); the queue then holds the changed video, so later conversions use it.
import type { Values } from "../core/options";
import type { InputFile, OutputFile } from "../core/types";
import type { Field, Track, TrackKind, VideoDetails, VideoEdit } from "../properties/video";
import { uniqueName } from "./photoform";

export const VIDEO_EXTS = ["mp4", "mov", "m4v", "mkv", "webm"];
const ROTATING = ["mp4", "mov", "m4v"];

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const SEVERAL = "Several values";
const ROTATION_LABELS: [number, string][] = [[0, "Not turned"], [90, "Turned 90° clockwise"], [180, "Upside down"], [270, "Turned 90° anticlockwise"]];
const KIND_LABELS: Record<TrackKind, string> = { audio: "Sound", subtitle: "Subtitles" };
const loadVideo = () => import("../properties/video");

/** Runs the hidden "video-tags" converter: ``read`` gives details.json, ``save`` the new video. */
export type VideoRunner = (file: InputFile, options: Values) => Promise<OutputFile[]>;
type Clip = { file: InputFile; details: VideoDetails };

export async function readVideoDetails(run: VideoRunner, file: InputFile): Promise<VideoDetails> {
  const [out] = await run(file, { mode: "read" });
  return JSON.parse(new TextDecoder().decode(out.data)) as VideoDetails;
}

const trackLabel = (t: Track, n: number) => `${KIND_LABELS[t.kind]} ${n}${t.title ? ` · ${t.title}` : ""}`;

export class VideoForm {
  readonly el: HTMLElement;
  private readonly title = h("input", { type: "text", id: "vf-title", ariaLabel: "Title" });
  private readonly description = h("textarea", { id: "vf-description", ariaLabel: "Description", rows: 3 });
  private readonly date = h("input", { type: "text", id: "vf-date", ariaLabel: "Date recorded", title: "Your device's local time; it's stored as UTC in the file" });
  private readonly rotation = h("select", { id: "vf-rotation", ariaLabel: "Rotation" });
  private readonly rotationLabel = h("label", { htmlFor: "vf-rotation" }, "Rotation");
  private readonly tracks = h("div", { class: "ph-grid vf-tracks" });
  private readonly applyAll = h("input", { type: "checkbox", id: "vf-apply-all" });
  private readonly applyAllRow: HTMLElement;
  private readonly applyAllText = h("span");
  private readonly lost = h("div", { class: "hint vf-lost" });
  private readonly note = h("div", { class: "hint ph-note vf-note" });
  private readonly progress = h("progress", { class: "vf-progress", max: 1, value: 0, hidden: true });
  private readonly save = h("button", { class: "primary", type: "button", disabled: true, title: "Download the video with the new details (the original on your device isn't changed)" }, "Download with changes");
  private start: Record<string, string | null> = {};
  private startTracks = new Map<number, string>();
  private startDefaults = new Map<TrackKind, number | null>();
  private languages = new Map<number, HTMLSelectElement>();
  private defaults = new Map<TrackKind, HTMLSelectElement>();
  private busy = false;
  private one: Clip | null = null;
  private all: Clip[] = [];
  private video: Awaited<ReturnType<typeof loadVideo>> | null = null;

  constructor(private readonly runTags: VideoRunner, private readonly onSaved: (changed: InputFile[]) => void) {
    const rows = h("div", { class: "ph-grid" },
      h("label", { htmlFor: "vf-title" }, "Title"), this.title,
      h("label", { htmlFor: "vf-description" }, "Description"), this.description,
      h("label", { htmlFor: "vf-date" }, "Date recorded"), this.date,
      this.rotationLabel, this.rotation);
    for (const el of [this.title, this.description, this.date]) el.addEventListener("input", () => this.changed());
    this.rotation.addEventListener("change", () => this.changed());
    this.applyAll.addEventListener("change", () => this.reload());
    this.applyAllRow = h("label", { class: "ph-check" }, this.applyAll, this.applyAllText);
    this.save.addEventListener("click", () => void this.download());
    this.el = h("section", { class: "prop-card vf-form", ariaLabel: "Change details", hidden: true },
      h("h3", {}, "Change details"), rows, this.tracks, this.applyAllRow, this.lost,
      h("div", { class: "hint vf-limit" }, "Every track is copied as it is, so nothing is re-encoded. Videos over 1.5 GB need the desktop app."),
      this.note, this.progress, h("div", { class: "ph-actions" }, this.save));
    this.el.dataset.title = "Change details";
  }

  /** The shown video and every changeable video (for "Apply to all"); null hides the form. */
  async set(current: Clip | null, everyone: Clip[]): Promise<void> {
    this.one = current;
    this.all = everyone;
    this.el.hidden = !current;
    if (!current) return;
    this.video ??= await loadVideo();
    this.applyAllText.textContent = `Apply to all ${everyone.length} videos`;
    this.applyAllRow.hidden = everyone.length < 2;
    if (everyone.length < 2) this.applyAll.checked = false;
    this.reload();
  }

  private get many(): boolean { return this.applyAll.checked && this.all.length > 1; }
  private targets(): Clip[] { return this.many ? this.all : this.one ? [this.one] : []; }

  private reload(): void {
    const v = this.video;
    const details = this.targets().map((t) => t.details);
    if (!v || !details.length) return;
    const common = v.common(details);
    this.start = { ...common };
    if (common.date) this.start.date = v.shownDate(common.date);
    this.title.value = this.start.title ?? "";
    this.title.placeholder = this.start.title === null ? SEVERAL : "";
    this.description.value = this.start.description ?? "";
    this.description.placeholder = this.start.description === null ? SEVERAL : "";
    this.date.value = this.start.date ?? "";
    this.date.placeholder = this.start.date === null ? SEVERAL : "Like 2026-11-15 14:30";
    const options = ROTATION_LABELS.map(([turn, label]) => h("option", { value: String(turn) }, label));
    if (common.rotation === null) options.unshift(h("option", { value: "" }, SEVERAL));
    this.rotation.replaceChildren(...options);
    this.rotation.value = common.rotation ?? "";
    const rotating = details.every((d) => ROTATING.includes(d.container));
    this.rotation.hidden = this.rotationLabel.hidden = !rotating;
    this.fillTracks(this.many ? null : details[0]);
    const lost = [...new Set(details.flatMap((d) => d.lost))].sort();
    this.lost.textContent = lost.length ? `Saving can't keep ${lost.join(", ")}.` : "";
    this.lost.hidden = !lost.length;
    this.note.textContent = "";
    this.changed();
  }

  private fillTracks(one: VideoDetails | null): void {
    const v = this.video!;
    this.tracks.replaceChildren();
    this.languages.clear();
    this.defaults.clear();
    this.startTracks.clear();
    this.startDefaults.clear();
    const tracks = one?.tracks ?? [];
    this.tracks.hidden = !tracks.length;
    const counts: Record<TrackKind, number> = { audio: 0, subtitle: 0 };
    const numbers = new Map<number, number>();
    for (const t of tracks) {
      numbers.set(t.index, ++counts[t.kind]);
      const label = trackLabel(t, counts[t.kind]);
      const box = h("select", { id: `vf-language-${t.index}`, ariaLabel: `${label} language` });
      box.append(h("option", { value: "" }, "Not set"), ...v.LANGUAGES.map(([code, name]) => h("option", { value: code }, name)));
      if (t.language && !v.LANGUAGES.some(([c]) => c === t.language)) box.append(h("option", { value: t.language }, t.language));
      box.value = t.language;
      box.addEventListener("change", () => this.changed());
      this.languages.set(t.index, box);
      this.startTracks.set(t.index, t.language);
      this.tracks.append(h("label", { htmlFor: box.id }, t.codec ? `${label} (${t.codec})` : label), box);
    }
    for (const kind of v.TRACK_KINDS) {
      const ofKind = tracks.filter((t) => t.kind === kind);
      if (!ofKind.length) continue;
      const marked = ofKind.find((t) => t.default)?.index ?? null;
      const name = kind === "audio" ? "Default sound" : "Default subtitles";
      const box = h("select", { id: `vf-default-${kind}`, ariaLabel: name });
      if (marked === null || v.allowsNoDefault(one!.container)) box.append(h("option", { value: "" }, "None"));
      box.append(...ofKind.map((t) => h("option", { value: String(t.index) }, trackLabel(t, numbers.get(t.index)!))));
      box.value = marked === null ? "" : String(marked);
      box.addEventListener("change", () => this.changed());
      this.defaults.set(kind, box);
      this.startDefaults.set(kind, marked);
      this.tracks.append(h("label", { htmlFor: box.id }, name), box);
    }
  }

  edit(): VideoEdit {
    const values: Partial<Record<Field, string>> = {};
    const texts: [Field, string][] = [["title", this.title.value], ["description", this.description.value], ["date", this.date.value]];
    for (const [name, text] of texts) {
      const start = this.start[name];
      if (start === null && text === "") continue; // "Several values" left alone
      if (text.trim() !== (start ?? "").trim()) values[name] = text;
    }
    const turn = this.rotation.value;
    if (!this.rotation.hidden && turn !== "" && turn !== this.start.rotation) values.rotation = turn;
    const languages: Record<number, string> = {};
    for (const [index, box] of this.languages) if (box.value !== this.startTracks.get(index)) languages[index] = box.value;
    const defaults: Partial<Record<TrackKind, number | null>> = {};
    for (const [kind, box] of this.defaults) {
      const chosen = box.value === "" ? null : Number(box.value);
      if (chosen !== this.startDefaults.get(kind)) defaults[kind] = chosen;
    }
    return { values, languages, defaults };
  }

  private changed(): void {
    const v = this.video;
    if (!v) return;
    this.save.disabled = v.isEmpty(this.edit()) || this.busy;
    this.save.textContent = this.many ? "Download changed videos (.zip)" : "Download with changes";
    let ok = true;
    try { v.cleanValues({ date: this.date.value }); } catch { ok = false; }
    this.date.classList.toggle("invalid", !ok);
  }

  private message(text: string, error = false): void {
    this.note.textContent = text;
    this.note.classList.toggle("error", error);
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    for (const el of [this.title, this.description, this.date, this.rotation, this.applyAll, ...this.languages.values(), ...this.defaults.values()]) el.disabled = busy;
    this.progress.hidden = !busy;
    this.progress.value = 0;
    this.changed();
  }

  private async download(): Promise<void> {
    const v = this.video!;
    const edit = this.edit();
    let offset: number | null = null;
    try {
      v.cleanValues(edit.values);
      const parts = edit.values.date ? v.parseDate(edit.values.date) : null;
      if (parts) offset = v.localOffset(parts);
    } catch (e) {
      this.message(e instanceof v.VideoError ? e.message : "The details couldn't be changed.", true);
      return;
    }
    const targets = this.targets();
    const json = JSON.stringify(edit);
    const outputs: { target: Clip; data: Uint8Array }[] = [];
    this.setBusy(true);
    this.message("Saving…");
    try {
      for (const [i, t] of targets.entries()) {
        const [out] = await this.runTags(t.file, { mode: "save", edit: json, offset: offset ?? "" });
        outputs.push({ target: t, data: out.data });
        this.progress.value = (i + 1) / targets.length;
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
      name = "videos (changed details).zip";
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
    this.message(n === 1 ? `Downloaded “${name}” with the new details.` : `Downloaded ${n} videos in “${name}”.`);
    this.onSaved(outputs.map((o) => o.target.file));
  }

  keepNote(text: string): void { this.message(text); }
  get noteText(): string { return this.note.textContent ?? ""; }
}
