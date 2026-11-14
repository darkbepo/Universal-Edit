// The single page: drop files → queue → pick a format (or open Settings) → Convert → download.
// Wide screens: files on the left, "Convert to" / "Settings" sideways tabs on the right, a bottom
// bar with status, progress and Convert. Narrow screens stack the same parts in one column.
import { zipSync } from "fflate";
import { detect } from "../core/detect";
import { defaults, type Values } from "../core/options";
import { fileFractions } from "../core/progress";
import { converterById, targetsFor } from "../core/registry";
import type { InputFile, OutputFile, Target } from "../core/types";
import { MAX_FILE_BYTES, WEB_EDITABLE_DOCUMENT_EXTS } from "../doceditor/types";
import codeLanguages from "../codeedit/languages.json";
import { ConversionClient, JobCancelled } from "../worker/client";
import { editorHub, newPictureDialog } from "./hub";
import { groupIcon, icon } from "./icons";
import { Orbit } from "./orbit";
import { PropertiesPane } from "./properties";
import { fmtSize, Queue } from "./queue";
import { SettingsForm } from "./settings";
import { themeButton } from "./theme";

const NOUN: Record<string, [string, string]> = { image: ["image", "images"], video: ["video", "videos"], audio: ["audio file", "audio files"], document: ["document", "documents"], model: ["3D model", "3D models"], archive: ["archive", "archives"], subtitle: ["subtitle file", "subtitle files"], font: ["font", "fonts"], vector: ["vector graphic", "vector graphics"], cad: ["CAD drawing", "CAD drawings"], code: ["code file", "code files"] };

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

// Video encoding in the browser is capped at a few threads (engines/threads.ts), so point out
// that the desktop app is much faster for these.
const SLOW_IN_BROWSER = new Set(["video", "gif", "frames", "slideshow", "subtitles-video"]);
const DESKTOP_URL = "https://github.com/darkbepo/Universal-Edit/releases";
const MAX_RESULTS = 200;

export function mountApp(root: HTMLElement): void {
  const client = new ConversionClient();
  let files: InputFile[] = [];
  let target: Target | null = null;
  let busy = false;
  let outputs: OutputFile[] = [];
  let objectURLs: string[] = [];
  const remembered: Record<string, Values> = {};
  const folded = new Set<string>(readFolded());

  // ---------------------------------------------------------------- top bar
  const picker = h("input", { type: "file", multiple: true, hidden: true });
  // "Convert mode" as its accessible name, so it never collides with the Convert action button.
  const convertMode = h("button", { class: "seg", type: "button", ariaLabel: "Convert mode" }, "Convert");
  const editMode = h("button", { class: "seg", type: "button" }, "Edit");
  // ▾ after Edit: the other editor for files two editors can open (TXT / MD / HTML)
  const editMore = h("button", { class: "seg edit-more", type: "button", ariaLabel: "Other editors", title: "Other editors", hidden: true }, "▾");
  const editMenu = h("div", { class: "edit-menu", role: "menu", hidden: true });
  editMore.setAttribute("aria-haspopup", "menu");
  const propertiesMode = h("button", { class: "seg", type: "button", title: "See the details of the files" }, "Properties");
  const modeBar = h("div", { class: "mode", role: "group", ariaLabel: "Mode" }, convertMode, editMode, editMore, propertiesMode, editMenu);
  convertMode.setAttribute("aria-pressed", "true");
  editMode.setAttribute("aria-pressed", "false");
  propertiesMode.setAttribute("aria-pressed", "false");
  let mode: "convert" | "properties" = "convert"; // on the converter page
  let editor: import("./editor/page").EditorPage | null = null;
  let viewer: import("./editor3d/page").ViewerPage | null = null;
  let audio: import("./audioeditor/page").AudioEditorPage | null = null;
  let docEditor: import("./doceditor/page").DocEditorPage | null = null;
  let codeEditor: import("./codeeditor/page").CodeEditorPage | null = null;
  let editing = false;
  // Three columns (badge + theme | logo + name | Convert/Edit): the outer two are equally wide, so the
  // name stays centred on the page.
  const topbar = h("header", { class: "topbar" },
    h("div", { class: "topbar-start" },
      h("span", { class: "badge", title: "Everything is processed in your browser. Nothing is uploaded." }, icon("lock", 14), "Files never leave your device"),
      themeButton()),
    h("div", { class: "brand" }, h("img", { class: "logo", src: "./icon.svg", alt: "" }), h("h1", {}, "Universal Edit")),
    h("div", { class: "topbar-end" }, modeBar));

  // ---------------------------------------------------------------- left: drop zone, results, queue
  const dropTitle = h("div", { class: "title" }, "Drop files here or click to browse");
  const drop = h("div", { class: "drop", tabIndex: 0, role: "button", ariaLabel: "Add files" },
    h("div", { class: "icon" }, icon("upload", 34)), dropTitle,
    h("div", { class: "sub" }, "Images, video, audio, documents, 3D models, fonts and more"));
  const note = h("div", { class: "note", hidden: true });
  // file-type badges orbiting the text while the box is empty
  const orbit = new Orbit(drop, [...drop.querySelectorAll<HTMLElement>(".icon, .title, .sub")]);

  const resultsLabel = h("span");
  const zipBtn = h("button", { class: "link", hidden: true }, icon("download", 16), "Download all (.zip)");
  const folderBtn = h("button", { class: "link", hidden: true }, icon("folder", 16), "Save to folder…");
  const resultsList = h("ul", { class: "file-list results" });
  const resultsMore = h("div", { class: "hint", hidden: true });
  const resultsCard = h("section", { class: "card results-card", hidden: true, ariaLabel: "Results" },
    h("div", { class: "results-head" }, resultsLabel, h("span", { class: "actions" }, zipBtn, folderBtn)), resultsList, resultsMore);

  const filesLabel = h("span");
  const clearBtn = h("button", { class: "link" }, "Clear");
  const queue = new Queue();
  const filesHint = h("div", { class: "hint" }, "Drag to reorder · × removes");
  const filesCard = h("section", { class: "card queue-card", hidden: true, ariaLabel: "Queue" },
    h("div", { class: "files-head" }, filesLabel, clearBtn), queue.el, filesHint);
  const hub = editorHub((id) => void openEditor(id)); // editor tiles, only while no files are added
  const left = h("div", { class: "left" }, drop, hub, note, resultsCard, filesCard);

  // ---------------------------------------------------------------- right: Convert to | Settings
  const formats = h("div", { class: "cards" });
  const speedTip = h("div", { class: "hint speed-tip", hidden: true },
    "Video is slower in the browser. The ",
    h("a", { href: DESKTOP_URL, target: "_blank", rel: "noopener" }, "Windows desktop app"),
    " uses your whole processor and converts video much faster.");
  const cog = h("button", { class: "icon-btn cog", type: "button", ariaLabel: "Settings", title: "Settings for this format" }, icon("settings", 18));
  const convertPane = h("section", { class: "pane convert-pane", ariaLabel: "Convert to" },
    h("div", { class: "pane-head" }, h("span", { class: "pane-icon" }, icon("transform", 18)), h("h2", {}, "Convert to"), h("div", { class: "spacer" }), cog),
    h("div", { class: "pane-body" }, speedTip, formats));
  const stripLabel = h("span", { class: "strip-label" }, "Convert to");
  const strip = h("button", { class: "strip", type: "button", hidden: true, ariaLabel: "Convert to", title: "Back to the output formats" }, icon("transform", 20), stripLabel);
  const form = new SettingsForm(() => {
    if (target) remembered[target.converterId] = form.values();
  });
  const settingsTitle = h("h2", {}, "Settings");
  const resetBtn = h("button", { class: "link", title: "Reset every setting to its default" }, "Reset");
  const settingsPane = h("section", { class: "pane settings-pane", hidden: true, ariaLabel: "Settings" },
    h("div", { class: "pane-head" }, h("span", { class: "pane-icon" }, icon("settings", 18)), settingsTitle, h("div", { class: "spacer" }), resetBtn),
    h("div", { class: "pane-body" }, form.el));
  const properties = new PropertiesPane(() => queue.set(files)); // changed pictures: new previews
  const right = h("div", { class: "right", hidden: true }, strip, convertPane, settingsPane, properties.el);

  const workspace = h("div", { class: "workspace empty" }, left, right);

  // ---------------------------------------------------------------- bottom bar
  const progressFill = h("div");
  const progress = h("div", { class: "progress", hidden: true }, progressFill);
  const status = h("div", { class: "status" });
  const convertBtn = h("button", { class: "primary", disabled: true }, "Convert");
  const bar = h("div", { class: "bar" }, h("div", { class: "bar-inner" }, status, progress, convertBtn));

  root.append(h("main", { class: "app" }, picker, topbar, workspace), bar);

  // ---------------------------------------------------------------- files
  async function addFiles(list: FileList | File[]) {
    const skipped: string[] = [];
    if (editing && docEditor && !docEditor.el.hidden) {
      // the document editor opens one dropped document instead (it asks about unsaved changes)
      const found = await Promise.all(Array.from(list, (f) => detect(f).catch(() => null)));
      await docEditor.dropFiles(found.every((f) => f !== null) ? (found as InputFile[]) : []);
      return;
    }
    if (editing && audio && !audio.el.hidden) {
      await audio.openFiles(Array.from(list)); // audio files replace the sound (asks first)
      return;
    }
    if (editing && viewer && !viewer.el.hidden) {
      // In the 3D viewer new models join the queue and are shown.
      const added: InputFile[] = [];
      for (const f of Array.from(list)) {
        const d = await detect(f);
        if (d?.family === "model" && !files.some((x) => x.name === d.name && x.file.size === d.file.size)) added.push(d);
      }
      if (!added.length) return;
      files.push(...added);
      refresh();
      await viewer.open(files);
      await viewer.showFile(added[0]);
      return;
    }
    if (editing && editor) {
      // In Edit mode new images become layers of the open picture.
      const images: InputFile[] = [];
      for (const f of Array.from(list)) {
        const d = await detect(f);
        if (d?.family === "image") images.push(d);
      }
      if (images.length) await editor.addFiles(images);
      return;
    }
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
  document.body.addEventListener("dragover", (e) => {
    if (busy || !e.dataTransfer?.types.includes("Files")) return;
    e.preventDefault();
    drop.classList.add("hover");
    orbit.setPull(true);
  });
  document.body.addEventListener("dragleave", (e) => {
    if (e.relatedTarget === null) {
      drop.classList.remove("hover");
      orbit.setPull(false);
    }
  });
  document.body.addEventListener("drop", (e) => {
    drop.classList.remove("hover");
    orbit.setPull(false);
    if (busy || !e.dataTransfer?.files.length) return;
    e.preventDefault();
    const dropped = [...e.dataTransfer.files];
    // on the empty page the badges burst outward first, then the file list appears
    if (!files.length && !editing) void orbit.burst().then(() => addFiles(dropped));
    else void addFiles(dropped);
  });

  queue.onRemove = (i) => {
    files.splice(i, 1);
    refresh();
  };
  queue.onReorder = (from, to) => {
    const [moved] = files.splice(from, 1);
    files.splice(to, 0, moved);
    refresh();
  };
  clearBtn.addEventListener("click", () => {
    if (busy) return;
    files = [];
    refresh();
  });

  // ---------------------------------------------------------------- Convert | Edit
  const appMain = root.querySelector("main.app") as HTMLElement;
  // A lone .zip is a document when it is a Markdown ZIP the website saved (checked once per file).
  const markdownZips = new WeakMap<InputFile, boolean>();
  type EditKind = "image" | "model" | "audio" | "document" | "code";
  // files the code editor opens (the language table's extensions; "" = text with none)
  const CODE_EDITABLE_EXTS = new Set(["", ...codeLanguages.languages.flatMap((l) => l.exts)]);
  /** Every editor that can open the files, the one Edit opens first (the desktop's
   * MainWindow.edit_choices): TXT / MD → document then code, HTML → code then document. */
  const editChoices = (): EditKind[] => {
    if (!files.length) return [];
    if (files.every((f) => f.family === "image")) return ["image"];
    if (files.every((f) => f.family === "model")) return ["model"];
    if (files.every((f) => f.family === "audio")) return ["audio"];
    if (files.length === 1) {
      const f = files[0];
      if (f.ext === "txt" || f.ext === "md") return ["document", "code"];
      if (f.ext === "html") return ["code", "document"];
      if (f.family === "document" && WEB_EDITABLE_DOCUMENT_EXTS.includes(f.ext)) return ["document"];
      if (markdownZips.get(f)) return ["document"];
    }
    const textLike = new Set(["document", "vector", "subtitle"]);
    if (files.every((f) => f.family === "code" || (textLike.has(f.family) && CODE_EDITABLE_EXTS.has(f.ext)))) return ["code"];
    return [];
  };
  const editKind = (): EditKind | null => editChoices()[0] ?? null;
  /** Check a lone .zip in the queue (its reader is loaded only then), then update Edit. */
  async function checkMarkdownZip() {
    const f = files.length === 1 ? files[0] : null;
    if (!f || f.ext !== "zip" || markdownZips.has(f)) return;
    markdownZips.set(f, false);
    if (f.file.size > MAX_FILE_BYTES) return;
    try {
      const [{ isEditableMarkdownZip }, bytes] = await Promise.all([import("../doceditor/mdzip"), f.file.arrayBuffer()]);
      markdownZips.set(f, isEditableMarkdownZip(new Uint8Array(bytes)));
    } catch (e) {
      console.error(`Couldn't look inside ${f.name}`, e);
    }
    updateEditSwitch();
  }
  function updateEditSwitch() {
    if (!editing) editMode.disabled = !canEdit();
    editMore.hidden = editChoices().length < 2;
    editMenu.hidden = true;
    const kind = editKind();
    editMode.title = kind === "code" ? "Open the files in the code editor" : kind === "document" ? "Open the document in the document editor" : kind === "model" ? "Open the 3D models in the 3D viewer" : kind === "image" ? "Open the images in the image editor (each image becomes a layer)" : kind === "audio" ? "Open the sound in the audio editor (several files are joined)" : "Add images, audio, code, one text document, or 3D models to edit them";
  }
  const canEdit = () => editKind() !== null;
  function showConverter() {
    editing = false;
    // a document still opening is left to finish hidden (docRun ignores it); a tile still
    // opening keeps `opening`, so a second tile can't start meanwhile
    if (docOpening) opening = docOpening = false;
    docRun++;
    editMode.disabled = !canEdit();
    editor?.hide();
    viewer?.hide();
    audio?.hide();
    docEditor?.hide();
    codeEditor?.hide();
    appMain.hidden = false;
    bar.hidden = false;
    orbit.setActive(!files.length); // back on screen: the badges move again
    setMode("convert");
  }
  /** Convert or Properties, on the converter page. */
  function setMode(next: "convert" | "properties") {
    mode = next;
    convertMode.setAttribute("aria-pressed", String(next === "convert"));
    editMode.setAttribute("aria-pressed", "false");
    propertiesMode.setAttribute("aria-pressed", String(next === "properties"));
    applyMode();
  }
  // In Properties mode the Convert to / Settings panes are hidden by CSS (.properties-mode), so
  // they keep which of them was open.
  function applyMode() {
    const props = mode === "properties";
    right.classList.toggle("properties-mode", props);
    properties.el.hidden = !props;
    convertBtn.hidden = props;
    if (props) {
      progress.hidden = true;
      properties.setFiles(files);
    }
    updateButtons();
  }
  function enterProperties() {
    if (busy || !files.length) return;
    if (editing) {
      if (editor && !editor.el.hidden && !editor.maybeLeave()) return;
      if (docEditor && !docEditor.el.hidden && !docEditor.maybeLeave()) return;
      if (audio && !audio.el.hidden && !audio.maybeLeave()) return;
      if (codeEditor && !codeEditor.el.hidden && !codeEditor.maybeLeave()) return;
      showConverter();
    }
    setMode("properties");
  }
  // One mount each, even when a second click comes while the page's code is still loading.
  let viewerP: Promise<import("./editor3d/page").ViewerPage> | null = null;
  let editorP: Promise<import("./editor/page").EditorPage> | null = null;
  const ensureViewer = () => (viewerP ??= import("./editor3d/page").then(({ mountViewer }) =>
    (viewer = mountViewer(root, { client, onBack: showConverter, onOpen: (list) => void addFiles(list) }))));
  let audioP: Promise<import("./audioeditor/page").AudioEditorPage> | null = null;
  const ensureAudio = () => (audioP ??= import("./audioeditor/page").then(({ mountAudioEditor }) =>
    (audio = mountAudioEditor(root, { client, onBack: showConverter }))));
  let docEditorP: Promise<import("./doceditor/page").DocEditorPage> | null = null;
  const ensureDocEditor = () => (docEditorP ??= import("./doceditor/page").then(({ mountDocEditor }) =>
    (docEditor = mountDocEditor(root, { client, onBack: showConverter }))));
  let codeEditorP: Promise<import("./codeeditor/page").CodeEditorPage> | null = null;
  const ensureCodeEditor = () => (codeEditorP ??= import("./codeeditor/page").then(({ mountCodeEditor }) =>
    (codeEditor = mountCodeEditor(root, { onBack: showConverter }))).catch((e) => {
    codeEditorP = null; // try again next time
    throw e;
  }));
  async function loadCodeEditor(): Promise<import("./codeeditor/page").CodeEditorPage | null> {
    try {
      return await ensureCodeEditor();
    } catch (e) {
      console.error("Couldn't load the code editor", e);
      note.textContent = "Couldn't load the code editor. Check the internet connection and try again.";
      note.hidden = false;
      return null;
    }
  }
  const ensureEditor = () => (editorP ??= import("./editor/page").then(({ mountEditor }) =>
    (editor = mountEditor(root, { client, onBack: showConverter }))));
  let opening = false; // an editor tile is being opened (New picture window, code loading)
  let docRun = 0; // counts document opens from Edit; leaving the page ends the one running
  let docOpening = false; // the `opening` flag is held by a document open (docRun)
  function showPage(page: { show(): void }) {
    editing = true;
    appMain.hidden = true;
    bar.hidden = true;
    orbit.setActive(false); // no animation behind an editor
    convertMode.setAttribute("aria-pressed", "false");
    editMode.setAttribute("aria-pressed", "true");
    propertiesMode.setAttribute("aria-pressed", "false");
    page.show();
  }
  async function enterEdit(choice?: EditKind) {
    if (editing || busy || opening || !canEdit()) return;
    const kind = choice && editChoices().includes(choice) ? choice : editKind();
    if (kind === "code") {
      opening = true;
      editMode.disabled = true;
      try {
        const c = await loadCodeEditor();
        if (!c) return;
        if (await c.open(files)) showPage(c);
        else {
          note.textContent = c.lastError();
          note.hidden = false;
        }
      } finally {
        opening = false;
        editMode.disabled = !canEdit();
      }
      return;
    }
    if (kind === "model") {
      const v = await ensureViewer();
      await v.open(files);
      showPage(v);
      return;
    }
    if (kind === "document") {
      // the page shows straight away, with "Opening…" and the converter's download progress
      const f = files[0];
      const run = ++docRun;
      opening = docOpening = true;
      editMode.disabled = true;
      try {
        let d: import("./doceditor/page").DocEditorPage;
        try {
          d = await ensureDocEditor();
        } catch (e) {
          console.error("Couldn't load the document editor", e);
          docEditorP = null; // try again next time
          note.textContent = "Couldn't load the document editor. Check the internet connection and try again.";
          note.hidden = false;
          return;
        }
        showPage(d);
        // null: a later open (a drop) took over, so the page stays
        if ((await d.open(f)) === false && run === docRun) {
          showConverter();
          note.textContent = d.lastError();
          note.hidden = false;
        }
      } finally {
        if (run === docRun) {
          opening = docOpening = false;
          editMode.disabled = !canEdit();
        }
      }
      return;
    }
    if (kind === "audio") {
      const a = await ensureAudio();
      showPage(a); // shows "Opening…" while the worker decodes
      editMode.disabled = true;
      const ok = await a.open(files);
      editMode.disabled = !canEdit();
      if (!ok && !a.doc) {
        showConverter();
        note.textContent = a.lastError();
        note.hidden = false;
      }
      return;
    }
    const ed = await ensureEditor();
    editMode.disabled = true;
    const ok = await ed.open(files);
    editMode.disabled = !canEdit();
    if (!ok) {
      note.textContent = ed.lastError();
      note.hidden = false;
      return;
    }
    showPage(ed);
  }
  /** An editor tile: that editor without files (the image editor asks for a picture size first). */
  async function openEditor(id: string) {
    if (editing || busy || opening || files.length) return;
    opening = true;
    try {
      if (id === "model") {
        const v = await ensureViewer();
        v.openEmpty();
        showPage(v);
        return;
      }
      if (id === "audio") {
        const a = await ensureAudio();
        a.openEmpty();
        showPage(a);
        return;
      }
      if (id === "document") {
        let d: import("./doceditor/page").DocEditorPage;
        try {
          d = await ensureDocEditor();
        } catch (e) {
          console.error("Couldn't load the document editor", e);
          docEditorP = null; // try again next time
          note.textContent = "Couldn't load the document editor. Check the internet connection and try again.";
          note.hidden = false;
          return;
        }
        d.openEmpty();
        showPage(d);
        return;
      }
      if (id === "code") {
        const c = await loadCodeEditor();
        if (!c) return;
        c.openEmpty();
        showPage(c);
        return;
      }
      if (id !== "image") return; // not available yet
      const pick = await newPictureDialog();
      if (!pick) return;
      const [{ blankDocument }, ed] = await Promise.all([import("../editor/blank"), ensureEditor()]);
      try {
        ed.openBlank(blankDocument(pick.width, pick.height, pick.background));
      } catch (e) {
        note.textContent = e instanceof Error ? e.message : "Couldn't make the picture.";
        note.hidden = false;
        return;
      }
      showPage(ed);
    } finally {
      opening = false;
    }
  }
  editMode.addEventListener("click", () => void enterEdit());
  const labels: Partial<Record<EditKind, string>> = { code: "Edit as code", document: "Edit as document" };
  editMore.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!editMenu.hidden) {
      editMenu.hidden = true;
      return;
    }
    editMenu.replaceChildren(
      ...editChoices().map((k) => {
        const item = h("button", { type: "button", role: "menuitem" }, labels[k] ?? k);
        item.addEventListener("click", () => {
          editMenu.hidden = true;
          void enterEdit(k);
        });
        return item;
      }),
    );
    editMenu.hidden = false;
    (editMenu.firstElementChild as HTMLElement | null)?.focus();
  });
  document.addEventListener("click", () => (editMenu.hidden = true));
  editMenu.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      editMenu.hidden = true;
      editMore.focus();
    }
  });
  propertiesMode.addEventListener("click", () => {
    enterProperties();
    // one column (phones, narrow windows): the panel is below the queue, so bring it on screen
    if (mode === "properties" && matchMedia("(max-width: 900px)").matches) {
      requestAnimationFrame(() => properties.el.scrollIntoView({ block: "start", behavior: "smooth" }));
    }
  });
  convertMode.addEventListener("click", () => {
    if (editing && editor && !editor.el.hidden && !editor.maybeLeave()) return;
    if (editing && audio && !audio.el.hidden && !audio.maybeLeave()) return;
    if (editing && docEditor && !docEditor.el.hidden && !docEditor.maybeLeave()) return;
    showConverter();
  });

  // ---------------------------------------------------------------- targets & settings
  function refresh(extraNote = "") {
    if (!busy) {
      clearResults();
      sticky = false;
    }
    const has = files.length > 0;
    workspace.classList.toggle("empty", !has);
    drop.classList.toggle("compact", has);
    hub.hidden = has;
    orbit.setActive(!has && !editing);
    dropTitle.textContent = has ? "Add more files" : "Drop files here or click to browse";
    drop.querySelector(".icon")!.replaceChildren(icon(has ? "plus" : "upload", has ? 18 : 34));
    filesCard.hidden = !has;
    filesHint.hidden = files.length < 2;
    right.hidden = !has;
    editMode.disabled = !canEdit();
    updateEditSwitch();
    void checkMarkdownZip();
    const fams = new Set(files.map((f) => f.family));
    filesLabel.textContent = fams.size === 1
      ? `Queue · ${files.length} ${NOUN[files[0].family][files.length === 1 ? 0 : 1]}`
      : `Queue · ${files.length} files`;
    queue.set(files);
    const { targets, note: why } = targetsFor(files);
    const text = [extraNote, why].filter(Boolean).join(" ");
    note.textContent = text;
    note.hidden = !text;
    right.classList.toggle("no-targets", !targets.length);
    renderTargets(targets);
    if (mode === "properties") {
      if (has) applyMode();
      else setMode("convert");
    }
  }

  function renderTargets(targets: Target[]) {
    const keep = target?.id ?? localStorageGet("last-target");
    formats.replaceChildren();
    const groups = new Map<string, Target[]>();
    for (const t of targets) groups.set(t.group, [...(groups.get(t.group) ?? []), t]);
    let restored: Target | null = null;
    for (const [name, items] of groups) {
      const tiles = h("div", { class: "tiles" });
      for (const t of items) {
        const b = h("button", { class: "pill tile", title: t.hint ?? t.label, type: "button" }, icon(groupIcon(t.group), 22), h("span", {}, t.label));
        b.setAttribute("aria-pressed", String(t.id === keep));
        b.dataset.target = t.id;
        b.addEventListener("click", () => selectTarget(t));
        tiles.append(b);
        if (t.id === keep) restored = t;
      }
      const badge = h("span", { class: "badge-pill" });
      const head = h("button", { class: "card-head", type: "button" }, icon("chevron", 16, "ico chev"), h("span", { class: "card-title" }, name));
      const card = h("div", { class: "cat-card" }, h("div", { class: "card-row" }, head, badge), tiles);
      card.dataset.group = name;
      const open = !folded.has(name) || items.some((t) => t.id === keep);
      setCardOpen(card, open);
      head.addEventListener("click", () => {
        const now = card.classList.contains("closed");
        setCardOpen(card, now);
        if (now) folded.delete(name);
        else folded.add(name);
        localStorageSet("folded-groups", JSON.stringify([...folded]));
      });
      formats.append(card);
    }
    selectTarget(restored);
  }

  function setCardOpen(card: HTMLElement, open: boolean) {
    card.classList.toggle("closed", !open);
    card.querySelector(".card-head")!.setAttribute("aria-expanded", String(open));
    updateBadge(card);
  }

  function updateBadge(card: HTMLElement) {
    const badge = card.querySelector(".badge-pill") as HTMLElement;
    const chosen = card.querySelector<HTMLButtonElement>('.tile[aria-pressed="true"]');
    const count = card.querySelectorAll(".tile").length;
    badge.textContent = chosen ? chosen.textContent ?? "" : card.classList.contains("closed") ? String(count) : "";
    badge.classList.toggle("count", !chosen);
    badge.hidden = !badge.textContent;
  }

  function selectTarget(t: Target | null) {
    if (!busy && t?.id !== target?.id) {
      clearResults();
      sticky = false;
    }
    target = t;
    speedTip.hidden = !(t && SLOW_IN_BROWSER.has(t.converterId));
    for (const b of formats.querySelectorAll<HTMLButtonElement>(".tile")) b.setAttribute("aria-pressed", String(b.dataset.target === t?.id));
    for (const c of formats.querySelectorAll<HTMLElement>(".cat-card")) updateBadge(c);
    if (t) {
      localStorageSet("last-target", t.id);
      const conv = converterById(t.converterId);
      form.render(conv.options, t.id, remembered[conv.id] ?? defaults(conv.options));
    } else {
      form.render([], "", {});
    }
    const available = Boolean(t) && form.hasVisibleFields();
    cog.hidden = !available;
    stripLabel.textContent = t?.label ?? "Convert to";
    settingsTitle.textContent = t ? `Settings · ${t.label}` : "Settings";
    if (!available) showFormats();
    updateButtons();
  }

  function showSettings() {
    if (cog.hidden) return;
    convertPane.hidden = true;
    strip.hidden = false;
    settingsPane.hidden = false;
    right.classList.add("settings-open");
  }
  function showFormats() {
    settingsPane.hidden = true;
    strip.hidden = true;
    convertPane.hidden = false;
    right.classList.remove("settings-open");
  }
  cog.addEventListener("click", showSettings);
  strip.addEventListener("click", showFormats);
  resetBtn.addEventListener("click", () => {
    if (!target) return;
    const conv = converterById(target.converterId);
    delete remembered[conv.id];
    form.render(conv.options, target.id, defaults(conv.options));
  });

  // ---------------------------------------------------------------- convert
  // A result/error/cancel message stays until the user changes something.
  let sticky = false;
  function setStatus(text: string, kind: "" | "error" | "success" = "", stick = false) {
    sticky = stick;
    status.textContent = text;
    status.className = `status ${kind}`;
  }

  function updateButtons() {
    const ready = files.length > 0 && target !== null;
    convertBtn.disabled = !busy && !ready;
    convertBtn.textContent = busy ? "Cancel" : "Convert";
    convertBtn.classList.toggle("danger", busy);
    drop.style.pointerEvents = busy ? "none" : "";
    queue.locked = busy;
    workspace.classList.toggle("busy", busy);
    zipBtn.hidden = busy || outputs.length < 2;
    folderBtn.hidden = busy || !outputs.length || !("showDirectoryPicker" in window);
    propertiesMode.disabled = busy || !files.length;
    if (mode === "properties" && !busy) {
      setStatus(files.length > 1 ? `${files.length} files · choose one at the top to see its details` : "");
    } else if (!busy && !sticky) {
      if (!files.length) setStatus("Add files to get started.");
      else if (!target) setStatus("Choose an output format.");
      else {
        const fams = new Set(files.map((f) => f.family));
        const what = fams.size === 1 ? `${files.length} ${NOUN[files[0].family][files.length === 1 ? 0 : 1]}` : `${files.length} files`;
        setStatus(`${what} → ${target.label}`);
      }
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
    queue.setFractions(fileFractions(0, files.length));
    setStatus("Starting…");
    updateButtons();
    const conv = converterById(target.converterId);
    const options = { ...defaults(conv.options), ...form.values() };
    const started = performance.now();
    try {
      outputs = await client.run(target, files, options, (f, msg) => {
        progressFill.style.width = `${(f * 100).toFixed(1)}%`;
        queue.setFractions(fileFractions(f, files.length));
        if (msg) setStatus(`${msg} — ${Math.round(f * 100)}%`);
      });
      progressFill.style.width = "100%";
      queue.markDone();
      const secs = ((performance.now() - started) / 1000).toFixed(1);
      setStatus(`Done in ${secs} s — ${outputs.length === 1 ? outputs[0].name : `${outputs.length} files`} ready.`, "success", true);
      showResults();
    } catch (e) {
      progress.hidden = true;
      if (e instanceof JobCancelled) {
        queue.markCancelled();
        setStatus("Cancelled.", "", true);
      } else {
        console.error(e, (e as { details?: string }).details);
        queue.markFailed((e as Error).message);
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
    resultsList.replaceChildren();
    progress.hidden = true;
    status.classList.remove("error");
  }

  function showResults() {
    const total = outputs.reduce((n, o) => n + o.data.byteLength, 0);
    resultsList.replaceChildren();
    for (const o of outputs.slice(0, MAX_RESULTS)) {
      const url = URL.createObjectURL(new Blob([o.data as BlobPart], { type: o.type }));
      objectURLs.push(url);
      const leaf = o.name.split("/").pop()!;
      resultsList.append(h("li", {}, icon("check", 18, "ico ok"),
        h("a", { href: url, download: leaf, className: "fname", title: `Download ${leaf}` }, o.name),
        h("span", { class: "fsize" }, fmtSize(o.data.byteLength)),
        h("span", { class: "dl", ariaHidden: "true" }, icon("download", 16))));
    }
    resultsMore.textContent = outputs.length > MAX_RESULTS ? `…and ${outputs.length - MAX_RESULTS} more — use “Download all”.` : "";
    resultsMore.hidden = outputs.length <= MAX_RESULTS;
    resultsLabel.textContent = `Results · ${outputs.length} file${outputs.length === 1 ? "" : "s"} · ${fmtSize(total)}`;
    resultsCard.hidden = false;
    if (outputs.length === 1) (resultsList.querySelector("a") as HTMLAnchorElement).click(); // single file: download right away
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

function readFolded(): string[] {
  try {
    const v: unknown = JSON.parse(localStorageGet("folded-groups") ?? "[]");
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
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
