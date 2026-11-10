// Small modal dialogs (<dialog>): resize image, canvas size, brightness / contrast, save as.
import type { OptionSpec, Values } from "../../core/options";
import { MAX_SIDE, type EditorDoc } from "../../editor/document";
import { ANCHORS, type Anchor, type Resample } from "../../editor/ops";
import { SettingsForm } from "../settings";
import { h } from "./panels";

function modal<T>(cls: string, title: string, body: HTMLElement[], okLabel: string, read: () => T, extra: HTMLButtonElement[] = []): Promise<{ value: T; button: string } | null> {
  return new Promise((resolve) => {
    const ok = h("button", { class: "primary small", type: "button" }, okLabel);
    const cancel = h("button", { class: "btn", type: "button" }, "Cancel");
    const dlg = h("dialog", { class: `editor-dialog ${cls}` }, h("h2", {}, title), ...body, h("div", { class: "row" }, h("div", { class: "spacer" }), cancel, ...extra, ok));
    let done = false;
    const finish = (button: string | null) => {
      if (done) return;
      done = true;
      dlg.close();
      dlg.remove();
      resolve(button ? { value: read(), button } : null);
    };
    ok.addEventListener("click", () => finish(okLabel));
    for (const b of extra) b.addEventListener("click", () => finish(b.textContent ?? ""));
    cancel.addEventListener("click", () => finish(null));
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(null);
    });
    dlg.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.target as HTMLElement).tagName !== "SELECT") {
        e.preventDefault();
        finish(okLabel);
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") e.preventDefault(); // not the browser's "Save page"
      e.stopPropagation(); // never reach the editor's shortcuts
    });
    document.body.append(dlg);
    dlg.showModal();
  });
}

const num = (label: string, value: number, min: number, max: number, suffix: string) => {
  const input = h("input", { type: "number", value: String(value), min: String(min), max: String(max), step: "1", ariaLabel: label });
  return { input, row: h("label", { class: "field" }, h("span", {}, label), h("span", { class: "with-unit" }, input, suffix)) };
};

function sizeFields(doc: EditorDoc, keepDefault: boolean) {
  const w = num("Width", doc.width, 1, MAX_SIDE, "px");
  const hgt = num("Height", doc.height, 1, MAX_SIDE, "px");
  const keep = h("input", { type: "checkbox", checked: keepDefault });
  let syncing = false;
  const link = (fromWidth: boolean) => {
    if (syncing || !keep.checked) return;
    syncing = true;
    if (fromWidth) hgt.input.value = String(Math.max(1, Math.round((+w.input.value * doc.height) / doc.width)));
    else w.input.value = String(Math.max(1, Math.round((+hgt.input.value * doc.width) / doc.height)));
    syncing = false;
  };
  w.input.addEventListener("input", () => link(true));
  hgt.input.addEventListener("input", () => link(false));
  return { w: w.input, hgt: hgt.input, rows: [w.row, hgt.row, h("label", { class: "check" }, keep, " Keep proportions")] };
}

export async function resizeDialog(doc: EditorDoc): Promise<[number, number, Resample] | null> {
  const f = sizeFields(doc, true);
  const pct = num("Scale", 100, 1, 1000, "%");
  pct.input.addEventListener("input", () => {
    f.w.value = String(Math.max(1, Math.round((doc.width * +pct.input.value) / 100)));
    f.hgt.value = String(Math.max(1, Math.round((doc.height * +pct.input.value) / 100)));
  });
  const resample = h("select", { ariaLabel: "Resampling" });
  for (const [v, l] of [["lanczos", "Smooth (best quality)"], ["bilinear", "Bilinear (faster)"], ["nearest", "Nearest neighbour (pixel art)"]]) resample.append(h("option", { value: v }, l));
  const r = await modal("resize", "Resize image", [pct.row, ...f.rows, h("label", { class: "field" }, h("span", {}, "Resampling"), resample)], "Resize", () => [Math.round(+f.w.value), Math.round(+f.hgt.value), resample.value as Resample] as [number, number, Resample]);
  return r?.value ?? null;
}

export async function canvasSizeDialog(doc: EditorDoc): Promise<[number, number, Anchor] | null> {
  const f = sizeFields(doc, false);
  const anchor = h("select", { ariaLabel: "Anchor" });
  for (const a of ANCHORS) anchor.append(h("option", { value: a, selected: a === "center" }, a.replace("-", " ")));
  const r = await modal("canvas-size", "Canvas size", [...f.rows, h("label", { class: "field" }, h("span", {}, "Anchor"), anchor)], "Apply", () => [Math.round(+f.w.value), Math.round(+f.hgt.value), anchor.value as Anchor] as [number, number, Anchor]);
  return r?.value ?? null;
}

export async function brightnessDialog(onPreview: (b: number, c: number) => void): Promise<[number, number] | null> {
  const slider = (label: string) => {
    const s = h("input", { type: "range", min: "-100", max: "100", value: "0", ariaLabel: label });
    const out = h("output", {}, "0");
    let timer = 0;
    s.addEventListener("input", () => {
      out.textContent = s.value;
      clearTimeout(timer);
      timer = window.setTimeout(() => onPreview(+b.s.value, +c.s.value), 60);
    });
    return { s, row: h("label", { class: "field" }, h("span", {}, label), h("span", { class: "slider-row" }, s, out)) };
  };
  const b = slider("Brightness");
  const c = slider("Contrast");
  const r = await modal("brightness", "Brightness / contrast", [b.row, c.row], "Apply", () => [+b.s.value, +c.s.value] as [number, number]);
  return r?.value ?? null;
}

export interface SaveChoice {
  name: string;
  format: string;
  values: Values;
  toFile: boolean; // File System Access picker instead of a download
}

export async function saveDialog(defaultName: string, formats: [string, string][], defaultFormat: string, optionsFor: (format: string) => OptionSpec[], remembered: Map<string, Values>): Promise<SaveChoice | null> {
  const name = h("input", { type: "text", value: defaultName, ariaLabel: "File name" });
  const format = h("select", { ariaLabel: "Format" });
  for (const [id, label] of formats) format.append(h("option", { value: id, selected: id === defaultFormat }, label));
  const form = new SettingsForm(() => remembered.set(format.value, form.values()));
  const render = () => {
    const specs = optionsFor(format.value);
    form.render(specs, format.value, remembered.get(format.value) ?? Object.fromEntries(specs.map((s) => [s.name, s.default])));
    form.el.hidden = !form.hasVisibleFields();
  };
  format.addEventListener("change", render);
  render();
  const extra: HTMLButtonElement[] = [];
  if ("showSaveFilePicker" in window) extra.push(h("button", { class: "btn", type: "button", title: "Choose where to save; Save (Ctrl+S) then writes to the same file" }, "Save to a file…"));
  const r = await modal(
    "save",
    "Save image",
    [h("label", { class: "field" }, h("span", {}, "File name"), name), h("label", { class: "field" }, h("span", {}, "Format"), format), form.el],
    "Save",
    () => ({ name: name.value.trim() || defaultName, format: format.value, values: form.values() }),
    extra,
  );
  if (!r) return null;
  return { ...r.value, toFile: r.button !== "Save" };
}
