// The single page: drop files → pick a format → (optional) settings → Convert → download.
import { zipSync } from "fflate";
import { detect } from "../core/detect";
import { defaults, type Values } from "../core/options";
import { converterById, targetsFor } from "../core/registry";
import type { InputFile, OutputFile, Target } from "../core/types";
import { ConversionClient, JobCancelled } from "../worker/client";
import { SettingsForm } from "./settings";

const ICON: Record<string, string> = { image: "🖼", video: "🎞", audio: "🎵", document: "📄", model: "🧊" };
const NOUN: Record<string, [string, string]> = { image: ["image", "images"], video: ["video", "videos"], audio: ["audio file", "audio files"], document: ["document", "documents"], model: ["3D model", "3D models"] };

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

const fmtSize = (n: number) => (n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(0)} KB` : `${(n / (1 << 20)).toFixed(1)} MB`);

export function mountApp(root: HTMLElement): void {
  const client = new ConversionClient();
  let files: InputFile[] = [];
  let target: Target | null = null;
  let busy = false;
  let outputs: OutputFile[] = [];
  let objectURLs: string[] = [];
  const remembered: Record<string, Values> = {};

  // ---------------------------------------------------------------- layout
  const picker = h("input", { type: "file", multiple: true, hidden: true });
  const drop = h("div", { class: "drop", tabIndex: 0, role: "button", ariaLabel: "Add files" },
    h("div", { class: "icon" }, "⬆"), h("div", { class: "title" }, "Drop files here"),
    h("div", { class: "sub" }, "or click to browse — images, videos, audio, documents or 3D models"));
  const filesLabel = h("span");
  const clearBtn = h("button", { class: "link" }, "Clear");
  const fileList = h("ul", { class: "file-list" });
  const filesHint = h("div", { class: "hint" }, "Drag to reorder · × removes");
  const filesCard = h("div", { class: "card", hidden: true }, h("div", { class: "files-head" }, filesLabel, clearBtn), fileList, filesHint);
  const note = h("div", { class: "note", hidden: true });
  const formatSection = h("div", { class: "section", hidden: true }, "Convert to");
  const formats = h("div");
  const disclosure = h("button", { class: "disclosure", hidden: true });
  const form = new SettingsForm(() => {
    if (target) remembered[target.converterId] = form.values();
  });
  const resetBtn = h("button", { class: "link" }, "Reset to defaults");
  const settingsCard = h("div", { class: "card", hidden: true }, form.el, h("div", { class: "row" }, h("div", { class: "spacer" }), resetBtn));
  const resultsCard = h("div", { class: "card", hidden: true });

  const progressFill = h("div");
  const progress = h("div", { class: "progress", hidden: true }, progressFill);
  const status = h("div", { class: "status" });
  const zipBtn = h("button", { class: "btn", hidden: true }, "Download all (.zip)");
  const folderBtn = h("button", { class: "btn", hidden: true }, "Save to folder…");
  const convertBtn = h("button", { class: "primary", disabled: true }, "Convert");
  const bar = h("div", { class: "bar" }, h("div", { class: "bar-inner" }, progress, status,
    h("div", { class: "row" }, zipBtn, folderBtn, h("div", { class: "spacer" }), convertBtn)));

  root.append(
    h("main", { class: "app" },
      h("div", { class: "header" },
        h("div", {}, h("h1", {}, "Universal Media Converter"), h("p", { class: "subtitle" }, "Images, video, audio and documents — drop it in, pick a format.")),
        h("span", { class: "badge", title: "Everything is processed in your browser. Nothing is uploaded." }, "🔒 Files never leave your device")),
      picker, drop, filesCard, note, formatSection, formats, disclosure, settingsCard, resultsCard),
    bar,
  );

  // ---------------------------------------------------------------- files
  async function addFiles(list: FileList | File[]) {
    const skipped: string[] = [];
    for (const f of Array.from(list)) {
      const d = await detect(f);
      if (!d) skipped.push(f.name);
      else if (!files.some((x) => x.name === d.name && x.file.size === d.file.size)) files.push(d);
    }
    refresh(skipped.length ? `Skipped unsupported file(s): ${skipped.slice(0, 3).join(", ")}${skipped.length > 3 ? "…" : ""}` : "");
  }

  drop.addEventListener("click", () => !busy && picker.click());
  drop.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && !busy && picker.click());
  picker.addEventListener("change", () => {
    if (picker.files) void addFiles(picker.files);
    picker.value = "";
  });
  for (const el of [document.body]) {
    el.addEventListener("dragover", (e) => {
      if (busy || !e.dataTransfer?.types.includes("Files")) return;
      e.preventDefault();
      drop.classList.add("hover");
    });
    el.addEventListener("dragleave", (e) => {
      if (e.relatedTarget === null) drop.classList.remove("hover");
    });
    el.addEventListener("drop", (e) => {
      drop.classList.remove("hover");
      if (busy || !e.dataTransfer?.files.length) return;
      e.preventDefault();
      void addFiles(e.dataTransfer.files);
    });
  }
  clearBtn.addEventListener("click", () => {
    files = [];
    refresh();
  });

  let dragIndex = -1;
  function renderFiles() {
    fileList.replaceChildren(...files.map((f, i) => {
      const remove = h("button", { class: "icon-btn", title: "Remove", ariaLabel: `Remove ${f.name}` }, "×");
      remove.addEventListener("click", () => {
        files.splice(i, 1);
        refresh();
      });
      const li = h("li", { draggable: true }, ICON[f.family], h("span", { class: "fname", title: f.name }, f.name), h("span", { class: "fsize" }, fmtSize(f.file.size)), remove);
      li.addEventListener("dragstart", (e) => {
        dragIndex = i;
        li.classList.add("dragging");
        e.dataTransfer?.setData("text/plain", String(i));
      });
      li.addEventListener("dragend", () => li.classList.remove("dragging"));
      li.addEventListener("dragover", (e) => {
        if (dragIndex >= 0) e.preventDefault();
      });
      li.addEventListener("drop", (e) => {
        if (dragIndex < 0) return;
        e.preventDefault();
        e.stopPropagation();
        const [moved] = files.splice(dragIndex, 1);
        files.splice(i, 0, moved);
        dragIndex = -1;
        renderFiles();
      });
      return li;
    }));
  }

  // ---------------------------------------------------------------- targets & settings
  function refresh(extraNote = "") {
    if (!busy) {
      clearResults();
      sticky = false;
    }
    drop.classList.toggle("compact", files.length > 0);
    drop.querySelector(".title")!.textContent = files.length ? "+  Add more files" : "Drop files here";
    filesCard.hidden = !files.length;
    filesHint.hidden = files.length < 2;
    const fams = new Set(files.map((f) => f.family));
    filesLabel.textContent = fams.size === 1
      ? `${files.length} ${NOUN[files[0].family][files.length === 1 ? 0 : 1]}`
      : `${files.length} files`;
    renderFiles();
    const { targets, note: why } = targetsFor(files);
    const text = [extraNote, why].filter(Boolean).join(" ");
    note.textContent = text;
    note.hidden = !text;
    formatSection.hidden = !targets.length;
    renderTargets(targets);
  }

  function renderTargets(targets: Target[]) {
    const keep = target?.id ?? localStorageGet("last-target");
    formats.replaceChildren();
    const groups = new Map<string, Target[]>();
    for (const t of targets) groups.set(t.group, [...(groups.get(t.group) ?? []), t]);
    let restored: Target | null = null;
    for (const [name, items] of groups) {
      const pills = h("div", { class: "pills" });
      for (const t of items) {
        const b = h("button", { class: "pill", title: t.hint ?? "" }, t.label);
        b.setAttribute("aria-pressed", String(t.id === keep));
        b.dataset.target = t.id;
        b.addEventListener("click", () => selectTarget(t));
        pills.append(b);
        if (t.id === keep) restored = t;
      }
      formats.append(h("div", { class: "group-heading" }, name), pills);
    }
    selectTarget(restored);
  }

  function selectTarget(t: Target | null) {
    if (!busy && t?.id !== target?.id) {
      clearResults();
      sticky = false;
    }
    target = t;
    for (const b of formats.querySelectorAll<HTMLButtonElement>(".pill")) b.setAttribute("aria-pressed", String(b.dataset.target === t?.id));
    if (t) {
      localStorageSet("last-target", t.id);
      const conv = converterById(t.converterId);
      form.render(conv.options, t.id, remembered[conv.id] ?? defaults(conv.options));
    } else {
      form.render([], "", {});
    }
    updateSettingsVisibility();
    updateButtons();
  }

  let settingsOpen = localStorageGet("settings-open") === "1";
  disclosure.addEventListener("click", () => {
    settingsOpen = !settingsOpen;
    localStorageSet("settings-open", settingsOpen ? "1" : "0");
    updateSettingsVisibility();
  });
  resetBtn.addEventListener("click", () => {
    if (!target) return;
    const conv = converterById(target.converterId);
    delete remembered[conv.id];
    form.render(conv.options, target.id, defaults(conv.options));
  });
  function updateSettingsVisibility() {
    const available = Boolean(target) && form.hasVisibleFields();
    disclosure.hidden = !available;
    disclosure.textContent = `${settingsOpen ? "▾" : "▸"}  Settings`;
    settingsCard.hidden = !(available && settingsOpen);
  }

  // ---------------------------------------------------------------- convert
  // A result/error/cancel message stays until the user changes something.
  let sticky = false;
  function setStatus(text: string, kind: "" | "error" | "success" = "", stick = false) {
    sticky = stick;
    status.textContent = text;
    status.className = `status ${kind}`;
  }

  function updateButtons() {
    convertBtn.disabled = !busy && !(files.length && target);
    convertBtn.textContent = busy ? "Cancel" : "Convert";
    convertBtn.classList.toggle("danger", busy);
    drop.style.pointerEvents = busy ? "none" : "";
    zipBtn.hidden = busy || outputs.length < 2;
    folderBtn.hidden = busy || !outputs.length || !("showDirectoryPicker" in window);
    if (!busy && !sticky) {
      setStatus(!files.length ? "Add files to get started." : !target ? "Choose an output format." : `Ready to convert ${files.length} file${files.length === 1 ? "" : "s"} to ${target.label}.`);
    }
  }

  convertBtn.addEventListener("click", async () => {
    if (busy) {
      client.cancel();
      return;
    }
    if (!target || !files.length) return;
    busy = true;
    clearResults();
    progress.hidden = false;
    progressFill.style.width = "0%";
    setStatus("Starting…");
    updateButtons();
    const conv = converterById(target.converterId);
    const options = { ...defaults(conv.options), ...form.values() };
    const started = performance.now();
    try {
      outputs = await client.run(target, files, options, (f, msg) => {
        progressFill.style.width = `${(f * 100).toFixed(1)}%`;
        if (msg) setStatus(`${msg} — ${Math.round(f * 100)}%`);
      });
      progressFill.style.width = "100%";
      const secs = ((performance.now() - started) / 1000).toFixed(1);
      setStatus(`Done in ${secs} s — ${outputs.length === 1 ? outputs[0].name : `${outputs.length} files`} ready.`, "success", true);
      showResults();
    } catch (e) {
      progress.hidden = true;
      if (e instanceof JobCancelled) setStatus("Cancelled.", "", true);
      else {
        console.error(e, (e as { details?: string }).details);
        setStatus((e as Error).message, "error", true);
      }
    } finally {
      busy = false;
      updateButtons();
    }
  });

  // ---------------------------------------------------------------- results
  function clearResults() {
    for (const u of objectURLs) URL.revokeObjectURL(u);
    objectURLs = [];
    outputs = [];
    resultsCard.hidden = true;
    resultsCard.replaceChildren();
    progress.hidden = true;
    status.classList.remove("error");
  }

  function showResults() {
    const total = outputs.reduce((n, o) => n + o.data.byteLength, 0);
    const list = h("ul", { class: "file-list results" });
    for (const o of outputs.slice(0, 200)) {
      const url = URL.createObjectURL(new Blob([o.data as BlobPart], { type: o.type }));
      objectURLs.push(url);
      const leaf = o.name.split("/").pop()!;
      list.append(h("li", {}, h("a", { href: url, download: leaf, className: "fname" }, o.name), h("span", { class: "fsize" }, fmtSize(o.data.byteLength))));
    }
    const more = outputs.length > 200 ? h("div", { class: "hint" }, `…and ${outputs.length - 200} more — use “Download all”.`) : "";
    resultsCard.replaceChildren(h("div", { class: "files-head" }, `${outputs.length} file${outputs.length === 1 ? "" : "s"} · ${fmtSize(total)}`), list, more);
    resultsCard.hidden = false;
    if (outputs.length === 1) (list.querySelector("a") as HTMLAnchorElement).click(); // single file: download right away
  }

  zipBtn.addEventListener("click", () => {
    const tree: Record<string, Uint8Array> = {};
    for (const o of outputs) tree[o.name] = o.data;
    const zip = zipSync(tree, { level: 0 }); // media is already compressed
    const url = URL.createObjectURL(new Blob([zip as BlobPart], { type: "application/zip" }));
    const first = outputs[0].name.split("/")[0].replace(/\.[^.]+$/, "");
    h("a", { href: url, download: `${first || "converted"}.zip` }).click();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  });

  folderBtn.addEventListener("click", async () => {
    try {
      const dir = await (window as unknown as { showDirectoryPicker: (o: object) => Promise<FileSystemDirectoryHandle> }).showDirectoryPicker({ mode: "readwrite" });
      for (const o of outputs) {
        const parts = o.name.split("/");
        let d = dir;
        for (const p of parts.slice(0, -1)) d = await d.getDirectoryHandle(p, { create: true });
        const fh = await d.getFileHandle(parts[parts.length - 1], { create: true });
        const w = await fh.createWritable();
        await w.write(o.data as BlobPart);
        await w.close();
      }
      setStatus(`Saved ${outputs.length} file${outputs.length === 1 ? "" : "s"} to “${dir.name}”.`, "success", true);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setStatus(`Couldn't save to that folder: ${(e as Error).message}`, "error", true);
    }
  });

  refresh();
}

function localStorageGet(key: string): string | null {
  try {
    return localStorage.getItem(`umc:${key}`);
  } catch {
    return null;
  }
}
function localStorageSet(key: string, value: string): void {
  try {
    localStorage.setItem(`umc:${key}`, value);
  } catch {
    /* private mode etc. */
  }
}
