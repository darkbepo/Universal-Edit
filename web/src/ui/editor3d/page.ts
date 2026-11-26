// The 3D viewer page (port of the desktop's gui/editor3d/viewer_page.py): top bar (files,
// views, display style, bed grid), a three.js view, an info panel. Models are read in the
// conversion worker (hidden "model3d-view" converter), so STEP / IGES work and the page stays
// responsive. Loaded on demand from ui/app.ts.
import * as THREE from "three";
import { VIEW_TARGET } from "../../converters/model3d";
import type { InputFile } from "../../core/types";
import { OrbitCamera, FOV, type Vec3 } from "../../editor3d/camera";
import { LogoError, MAX_SIDE, defaultMode, type Shape } from "../../editor3d/logo";
import { describe, gridSpec, type ModelStats, type ViewModel } from "../../editor3d/mesh";
import { flatFace, hit, ray } from "../../editor3d/pick";
import {
  arranged, buildLogo, combined, defaultSaveName, History, logoPositions, sameSettings, SAVE_EXTS, sceneStl, scenePositions, emptyScene, withBase, without, withLogo,
  type Logo, type LogoSettings, type Picture, type SaveExt, type Scene,
} from "../../editor3d/scene";
import { JobCancelled, type ConversionClient } from "../../worker/client";
import { backLabel } from "../back";
import { FACE_HINT, LogoPanel } from "./logopanel";
import { ArrangePanel, CheckPanel } from "./printpanels";
import type { CheckAnswer, CheckRequest, RepairAnswer, RepairRequest, TraceAnswer, TraceRequest } from "./traceworker";
import * as A from "../../editor3d/arrange";
import { DENSITY, rows as filamentRows } from "../../editor3d/filament";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export const HINT = "Drag to rotate · right-drag or Shift+drag to pan · wheel to zoom · double-click to fit";
/** The same for touch screens (a phone or tablet without a mouse). */
export const TOUCH_HINT = "Drag to rotate · pinch to zoom · two fingers to pan · double-tap to fit";
const hint = () => (typeof matchMedia === "function" && matchMedia("(pointer: coarse) and (hover: none)").matches ? TOUCH_HINT : HINT);
const VIEW_BUTTONS: [string, string][] = [["iso", "3D"], ["front", "Front"], ["top", "Top"], ["right", "Side"]];
const STYLE_BUTTONS: [Style, string][] = [["shaded", "Shaded"], ["edges", "Edges"], ["wireframe", "Wireframe"]];
const ORBIT_DEG_PER_PX = 0.4;
const CLICK_SLOP_PX = 3; // a press that moves less than this is a click, not a drag
const REBUILD_MS = 250; // after the last setting change
type Style = "shaded" | "edges" | "wireframe";

/** The logo being made or changed (not in the scene until Apply). */
interface Edit {
  index: number | null; // null = a new logo
  picture: Picture | null;
  point: Vec3 | null;
  normal: Vec3 | null;
  faceTris: number[] | null;
  shape: Shape | null;
  shapeKey: string;
  logo: Logo | null;
  preview: Float32Array | null; // the logo's triangles, drawn in the accent colour
  problem: string;
  tracing: number; // id of the trace being waited for (0: none)
  applyWhenTraced: boolean; // Apply was pressed while tracing
}

function loadBed(): A.Bed {
  try {
    const [name, ...dims] = (localStorage.getItem("umc-bed") ?? "").split("|");
    const n = dims.map(Number);
    if (name && n.length === 3 && n.every((v) => Number.isFinite(v) && v >= 10 && v <= 2000)) return [name, n[0], n[1], n[2]];
  } catch {
    // private window
  }
  return A.DEFAULT_BED;
}

const shapeKey = (pic: Picture, s: LogoSettings) => JSON.stringify([pic.name, pic.w, pic.h, s.mode, s.threshold, s.invert, s.detail, s.width]);

/** Read a picture on the page (SVG drawn at MAX_SIDE, others scaled down to it). */
async function readPicture(file: File): Promise<Picture> {
  const svg = /\.svgz?$/i.test(file.name) || file.type === "image/svg+xml";
  let source: CanvasImageSource, w: number, h: number;
  if (svg) {
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      const k = MAX_SIDE / Math.max(img.naturalWidth || 300, img.naturalHeight || 150);
      w = Math.max(1, Math.round((img.naturalWidth || 300) * k));
      h = Math.max(1, Math.round((img.naturalHeight || 150) * k));
      source = img;
      return draw(source, w, h, file.name);
    } finally {
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }
  const bmp = await createImageBitmap(file);
  const k = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
  w = Math.max(1, Math.round(bmp.width * k));
  h = Math.max(1, Math.round(bmp.height * k));
  try {
    return draw(bmp, w, h, file.name);
  } finally {
    bmp.close();
  }
}

function draw(source: CanvasImageSource, w: number, h: number, name: string): Picture {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d", { willReadFrequently: true })!;
  g.drawImage(source, 0, 0, w, h);
  return { name, rgba: g.getImageData(0, 0, w, h).data, w, h };
}

export interface ViewerOptions {
  client: ConversionClient;
  onBack: () => void;
  /** Files picked with "Open…" (the page adds them to the queue and shows them). */
  onOpen?: (files: File[]) => void;
}

const MODEL_ACCEPT = ".stl,.obj,.ply,.glb,.3mf,.step,.stp,.iges,.igs"; // no OFF on the web
const EMPTY_TEXT = "Open a 3D model with “Open…”, or drop one here (STL, OBJ, 3MF, PLY, GLB, STEP, IGES).";

const cssColor = (name: string, fallback: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;

export class ViewerPage {
  readonly el: HTMLElement;
  readonly camera = new OrbitCamera();
  files: InputFile[] = [];
  current: InputFile | null = null;
  style: Style = "shaded";
  showGrid = true;
  failed = "";
  private models = new Map<InputFile, ViewModel | string>(); // string = why it couldn't open
  private loading = new Map<InputFile, Promise<ViewModel | string>>();
  private renderer: THREE.WebGLRenderer | null = null;
  private scene = new THREE.Scene();
  private cam3 = new THREE.PerspectiveCamera(FOV, 1, 0.1, 1000);
  private key = new THREE.DirectionalLight(0xffffff, 2.0);
  private fill = new THREE.DirectionalLight(0xffffff, 0.6);
  private modelGroup = new THREE.Group();
  private gridGroup = new THREE.Group();
  private mesh: THREE.Mesh | null = null;
  private edges: THREE.LineSegments | null = null;
  private canvasHost = h("div", { class: "viewer3d-canvas" });
  private placeholder = h("div", { class: "viewer3d-placeholder", hidden: true });
  private title = h("h2", { class: "viewer3d-title" });
  private info = h("dl", { class: "viewer3d-info" });
  private message = h("div", { class: "msg" });
  private fileBox = h("select", { title: "Which model to show", ariaLabel: "Model" });
  private gridBox = h("input", { type: "checkbox", checked: true });
  private styleButtons = new Map<Style, HTMLButtonElement>();
  private drag: { x: number; y: number; pan: boolean; id: number } | null = null;
  private frame = 0;
  private loadToken = 0;
  private active = false;
  private resizeObserver: ResizeObserver;
  readonly histories = new Map<InputFile | null, History>([[null, new History()]]);
  editing: Edit | null = null;
  saving = false;
  private overlay: THREE.Mesh | null = null;
  private logoPanel: LogoPanel;
  private sideInfo: HTMLElement;
  private logoBox = h("div", { class: "logo-list-box", hidden: true });
  private logoList = h("ul", { class: "logo-list", role: "listbox", ariaLabel: "Logos" });
  private selectedLogo = 0;
  private topButtons: Record<string, HTMLButtonElement> = {};
  private rebuildTimer = 0;
  private press: { x: number; y: number; moved: boolean; button: number } | null = null;
  private grabbing = false;
  private grabOffset: Vec3 = [0, 0, 0]; // logo point − where the drag started on its plane
  private tracer: Worker | null = null;
  private traceId = 0;
  panel: "" | "arrange" | "check" = "";
  layingFlat = false;
  report: import("../../editor3d/meshcheck").Report | null = null;
  private checkedScene: Scene | null = null;
  private checkId = 0;
  private repairId = 0;
  private arrangePanel: ArrangePanel;
  private checkPanel: CheckPanel;
  private highlight: THREE.Mesh | null = null;
  private bedGroup = new THREE.Group();
  bed: A.Bed = loadBed();
  material = "PLA";
  infill = 0.15;
  private materialBox = h("select", { id: "filament-material", title: "For the filament estimate" });
  private infillBox = h("input", { id: "filament-infill", type: "number", min: "0", max: "100", step: "5", title: "Infill (%)" });
  private areaCache = new WeakMap<Float32Array, number>();

  constructor(private opts: ViewerOptions) {
    const btn = (label: string, title: string, fn: () => void, cls = "btn small") => {
      const b = h("button", { class: cls, title, type: "button" }, label);
      b.addEventListener("click", fn);
      return b;
    };
    const views = VIEW_BUTTONS.map(([k, label]) => btn(label, `${label} view`, () => this.setView(k)));
    const styles = h("div", { class: "mode", role: "group", ariaLabel: "Display style" });
    for (const [k, label] of STYLE_BUTTONS) {
      const b = btn(label, `Show the model ${label.toLowerCase()}`, () => this.setStyle(k), "seg");
      b.setAttribute("aria-pressed", String(k === this.style));
      this.styleButtons.set(k, b);
      styles.append(b);
    }
    this.gridBox.addEventListener("change", () => this.setGrid(this.gridBox.checked));
    this.fileBox.addEventListener("change", () => {
      const f = this.files[this.fileBox.selectedIndex];
      if (f) void this.showFile(f);
    });
    const picker = h("input", { type: "file", multiple: true, accept: MODEL_ACCEPT, hidden: true });
    picker.addEventListener("change", () => {
      if (picker.files?.length) this.opts.onOpen?.(Array.from(picker.files));
      picker.value = "";
    });
    this.topButtons.open = btn("Open…", "Open 3D models (STL, OBJ, 3MF, STEP, …)", () => picker.click());
    this.topButtons.logo = btn("Add logo…", "Turn a picture into a raised 3D logo", () => this.startLogo());
    this.topButtons.undo = btn("Undo", "Undo (Ctrl+Z)", () => this.undo());
    this.topButtons.redo = btn("Redo", "Redo (Ctrl+Y)", () => this.redo());
    this.topButtons.arrange = btn("Arrange", "Move, turn, scale, lay flat; your printer's bed", () => this.openPanel("arrange"));
    this.topButtons.check = btn("Check", "Check for holes and other problems; overhangs", () => this.openPanel("check"));
    this.topButtons.save = btn("Save as…", "Save the model with its changes (3MF, STL, …)", () => void this.saveAs());
    const top = h("div", { class: "editor-top" },
      backLabel(btn("← Back to Convert", "Leave the 3D viewer", () => this.back(), "link")),
      h("span", { class: "gap" }),
      this.topButtons.open, picker,
      h("span", { class: "gap" }), this.fileBox,
      h("span", { class: "gap" }), this.topButtons.logo, this.topButtons.arrange, this.topButtons.check, this.topButtons.undo, this.topButtons.redo, this.topButtons.save,
      h("div", { class: "spacer" }),
      btn("Fit", "Show the whole model (double-click the view)", () => this.fit()), ...views,
      h("span", { class: "gap" }), styles,
      h("span", { class: "gap" }),
      h("label", { class: "check", title: "Grid on the print bed (Z = 0); red = X, green = Y" }, this.gridBox, " Bed grid"));
    const change = btn("Change…", "Change the chosen logo", () => this.startLogo(this.selectedLogo));
    const remove = btn("Remove", "Remove the chosen logo", () => this.removeLogo(this.selectedLogo));
    this.logoBox.append(h("p", { class: "muted small" }, "Logos"), this.logoList, h("div", { class: "logo-buttons" }, change, remove));
    try {
      const [mat, inf] = (localStorage.getItem("umc-filament") ?? "").split("|");
      if (mat in DENSITY) this.material = mat;
      if (inf !== undefined && Number.isFinite(Number(inf)) && inf !== "") this.infill = Math.min(1, Math.max(0, Number(inf)));
    } catch {
      // private window: defaults
    }
    for (const mat of Object.keys(DENSITY)) this.materialBox.append(h("option", { value: mat }, mat));
    this.materialBox.value = this.material;
    this.infillBox.value = String(Math.round(this.infill * 100));
    const filamentChanged = () => {
      this.material = this.materialBox.value;
      const v = Number(this.infillBox.value);
      this.infill = Number.isFinite(v) ? Math.min(1, Math.max(0, v / 100)) : this.infill;
      try {
        localStorage.setItem("umc-filament", `${this.material}|${this.infill}`);
      } catch {
        // not kept
      }
      this.showScene();
    };
    this.materialBox.addEventListener("change", filamentChanged);
    this.infillBox.addEventListener("change", filamentChanged);
    const side = h("div", { class: "editor-side viewer3d-side" }, this.title, this.info,
      h("p", { class: "muted small" }, "Sizes are in millimetres, as a slicer will see them. Files without a unit (STL, OBJ, PLY) are taken as mm."),
      h("div", { class: "arr-inline filament-row" }, h("label", { htmlFor: "filament-material", class: "muted small" }, "Filament"), this.materialBox, this.infillBox, h("span", { class: "muted small" }, "% infill")),
      this.logoBox);
    this.arrangePanel = new ArrangePanel({
      rotate: (axis, deg) => this.rotate(axis, deg),
      mirror: (axis) => this.arrange(A.mirror(axis, this.centreNow())),
      scaleBy: (sx, sy, sz) => this.scaleBy(sx, sy, sz),
      sizeTo: (axis, mm, keep) => this.sizeTo(axis, mm, keep),
      moveTo: (x, y) => this.moveTo(x, y),
      centre: () => this.centre(),
      units: (key) => this.scaleBy(A.UNIT_FIXES[key], A.UNIT_FIXES[key], A.UNIT_FIXES[key]),
      layFlat: (on) => this.setLayingFlat(on),
      bedChanged: (bed) => this.setBed(bed),
      done: () => this.closePanel(),
    });
    this.arrangePanel.setBed(this.bed);
    this.checkPanel = new CheckPanel({ repair: () => this.repair(), overhangs: () => this.runCheck(), done: () => this.closePanel() });
    this.sideInfo = side;
    this.logoPanel = new LogoPanel({
      changed: () => this.onSettingsChanged(),
      picture: (f) => void this.loadPicture(f),
      apply: () => this.applyLogo(),
      cancel: () => this.cancelLogo(),
    });
    this.el = h("div", { class: "editor viewer3d", hidden: true },
      top,
      h("div", { class: "viewer3d-body" }, h("div", { class: "viewer3d-view" }, this.canvasHost, this.placeholder), side, this.logoPanel.el, this.arrangePanel.el, this.checkPanel.el),
      h("div", { class: "editor-status" }, this.message, h("div", {}, hint())));

    this.scene.add(this.modelGroup, this.gridGroup, this.bedGroup, this.key, this.fill, new THREE.HemisphereLight(0xffffff, 0x666670, 0.9));
    this.cam3.up.set(0, 0, 1);
    this.buildBed();
    this.installControls();
    this.resizeObserver = new ResizeObserver(() => this.requestRender());
    this.resizeObserver.observe(this.canvasHost);
    window.addEventListener("beforeunload", this.onUnload);
    if (location.search.includes("e2e")) (window as unknown as { __viewer3d: ViewerPage }).__viewer3d = this;
  }

  // ---------------------------------------------------------------- visibility
  show(): void {
    this.active = true;
    this.el.hidden = false;
    requestAnimationFrame(() => this.fit());
  }

  hide(): void {
    this.active = false;
    this.el.hidden = true;
  }

  lastError(): string {
    return this.message.textContent ?? "";
  }

  get dirty(): boolean {
    return [...this.histories.values()].some((hs) => hs.dirty);
  }

  maybeLeave(): boolean {
    this.cancelLogo();
    if (!this.dirty) return true;
    if (!window.confirm("Discard the changes you haven't saved?")) return false;
    for (const hs of this.histories.values()) hs.revert(); // the logos go
    this.showScene();
    return true;
  }

  private readonly onUnload = (e: BeforeUnloadEvent) => {
    if (this.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  };

  private back(): void {
    if (this.maybeLeave()) this.opts.onBack();
  }

  // ---------------------------------------------------------------- opening
  async open(files: InputFile[]): Promise<boolean> {
    if (!files.length) {
      this.message.textContent = "Add a 3D model to view it.";
      return false;
    }
    this.files = [...files];
    for (const f of [...this.models.keys()]) if (!files.includes(f)) this.models.delete(f);
    this.fileBox.replaceChildren(...files.map((f) => h("option", {}, f.name)));
    this.fileBox.hidden = files.length < 2;
    this.fileBox.selectedIndex = 0;
    void this.showFile(files[0]);
    return true;
  }

  /** Started from the editor tiles: nothing to show until a model is opened or dropped. */
  openEmpty(): void {
    this.endEdit();
    this.files = [];
    this.current = null;
    this.loadToken++;
    this.fileBox.replaceChildren();
    this.fileBox.hidden = true;
    this.title.textContent = "";
    this.info.replaceChildren();
    this.message.textContent = "";
    this.message.classList.remove("error");
    this.showPlaceholder(EMPTY_TEXT);
    this.showScene(true);
  }

  async showFile(f: InputFile): Promise<void> {
    if (this.editing) {
      // a model dropped while a logo is being made: it waits in the list
      this.fileBox.selectedIndex = this.current ? this.files.indexOf(this.current) : -1;
      if (f !== this.current) this.message.textContent = `Apply or cancel the logo first, then choose ${f.name} above.`;
      return;
    }
    this.endEdit();
    this.current = f;
    this.fileBox.selectedIndex = this.files.indexOf(f);
    this.title.textContent = f.name;
    let model = this.models.get(f);
    if (model === undefined) {
      const token = ++this.loadToken;
      this.showPlaceholder(`Opening ${f.name}…`);
      this.info.replaceChildren();
      let job = this.loading.get(f); // the same file chosen again while it loads: share the load
      if (!job) {
        job = this.load(f).then(
          (m) => {
            this.models.set(f, m);
            return m;
          },
          // not cached: a cancelled or crashed job is tried again next time
          (e) => (e instanceof JobCancelled ? "Cancelled." : e instanceof Error && e.message ? e.message : `Couldn't open “${f.name}” as a 3D model.`),
        ).finally(() => this.loading.delete(f));
        this.loading.set(f, job);
      }
      model = await job;
      if (token !== this.loadToken || this.current !== f) return;
    }
    if (typeof model === "string") {
      this.showPlaceholder(model);
      this.message.textContent = model;
      this.message.classList.add("error");
      this.syncButtons();
      return;
    }
    if (!this.histories.has(f)) this.histories.set(f, new History(emptyScene(model)));
    this.message.textContent = "";
    this.message.classList.remove("error");
    this.showScene(true);
  }

  private async load(f: InputFile): Promise<ViewModel> {
    const out = await this.opts.client.run(VIEW_TARGET, [f], {}, (_p, m) => m && (this.message.textContent = m));
    const bin = out.find((o) => o.name === "mesh.bin")!.data;
    const stats = JSON.parse(new TextDecoder().decode(out.find((o) => o.name === "info.json")!.data)) as ModelStats;
    const positions = new Float32Array(bin.buffer.slice(bin.byteOffset, bin.byteOffset + bin.byteLength));
    return { ...stats, name: f.name, positions };
  }

  private showPlaceholder(text: string): void {
    this.placeholder.textContent = text;
    this.placeholder.hidden = false;
    this.canvasHost.hidden = true;
  }

  private ensureRenderer(): boolean {
    if (this.renderer) return true;
    if (this.failed) return false;
    try {
      // preserveDrawingBuffer: lets the e2e tests read the picture back
      this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: location.search.includes("e2e") });
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      this.canvasHost.append(this.renderer.domElement);
      this.renderer.domElement.tabIndex = 0;
      this.renderer.domElement.addEventListener("webglcontextlost", (e) => e.preventDefault());
      return true;
    } catch (e) {
      this.failed = e instanceof Error ? e.message : "WebGL couldn't start";
      return false;
    }
  }

  /** Show the current scene (+ the logo preview), fill the info panel, set the buttons. */
  private showScene(fit = false): void {
    const sc = this.shownScene();
    const m = sc ? combined(sc, this.current?.name ?? "Logo") : null;
    this.info.replaceChildren(...(m ? this.infoRowsOf(m) : []).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
    this.fillLogos();
    this.syncButtons();
    if (this.panel === "arrange" && sc && !(sc.base === null && !sc.logos.length)) {
      const pos = scenePositions(sc);
      const [lo, hi] = A.bounds(pos);
      const size: Vec3 = [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]];
      this.arrangePanel.showModel(size, A.centreOf(pos), A.fitNote(size, this.bed.slice(1) as Vec3));
    }
    if (this.panel === "check" && sc !== this.checkedScene) this.runCheck();
    const preview = this.editing?.preview ?? null;
    if (!m && !preview) {
      this.disposeModel();
      this.setOverlay(null);
      if (!this.current) this.showPlaceholder(EMPTY_TEXT);
      return;
    }
    if (!this.ensureRenderer()) {
      this.showPlaceholder("The 3D view needs WebGL, which isn't working in this browser. The model's measurements are on the right.");
      this.message.textContent = this.failed;
      return;
    }
    const first = !this.mesh && !this.overlay;
    this.placeholder.hidden = true;
    this.canvasHost.hidden = false;
    this.disposeModel();
    if (m) this.showModel(m);
    this.setOverlay(preview);
    if (fit || first) this.fit();
    else this.requestRender();
  }

  private showModel(m: ViewModel): void {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(m.positions, 3));
    g.computeVertexNormals(); // non-indexed: flat face normals
    g.computeBoundingBox();
    const accent = new THREE.Color(cssColor("--accent", "#5b5ef0"));
    const hsl = { h: 0, s: 0, l: 0 };
    accent.getHSL(hsl);
    const base = new THREE.Color().setHSL(hsl.h, 0.25, 0.62);
    this.mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: base, roughness: 0.75, metalness: 0.05, side: THREE.DoubleSide, flatShading: true, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
    this.modelGroup.add(this.mesh);
    this.buildGrid(m.size);
    this.applyStyle();
  }

  private setOverlay(pos: Float32Array | null): void {
    if (this.overlay) {
      this.overlay.geometry.dispose();
      (this.overlay.material as THREE.Material).dispose();
      this.overlay.removeFromParent();
      this.overlay = null;
    }
    if (pos?.length && this.renderer) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.computeVertexNormals();
      g.computeBoundingBox();
      const accent = new THREE.Color(cssColor("--accent", "#5b5ef0"));
      this.overlay = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: accent, roughness: 0.6, metalness: 0.05, side: THREE.DoubleSide, flatShading: true }));
      this.modelGroup.add(this.overlay);
      if (!this.mesh) {
        const b = g.boundingBox!;
        this.buildGrid([b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z]);
      }
    }
    this.requestRender();
  }

  private disposeModel(): void {
    for (const o of [this.mesh, this.edges]) {
      if (!o) continue;
      o.geometry.dispose();
      (o.material as THREE.Material).dispose();
      o.removeFromParent();
    }
    this.mesh = this.edges = null;
  }

  private buildGrid(size: [number, number, number]): void {
    for (const c of [...this.gridGroup.children]) {
      const l = c as THREE.LineSegments;
      l.geometry.dispose();
      (l.material as THREE.Material).dispose();
      c.removeFromParent();
    }
    const { step, half } = gridSpec(size);
    const n = Math.round(half / step);
    const minor: number[] = [], major: number[] = [];
    for (let i = -n; i <= n; i++) {
      const v = i * step;
      (i % 10 === 0 ? major : minor).push(v, -half, 0, v, half, 0, -half, v, 0, half, v, 0);
    }
    const muted = new THREE.Color(cssColor("--muted", "#6a7080"));
    const lines = (pts: number[], color: THREE.Color | number, opacity: number) => {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
      return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }));
    };
    const ext = Math.max(...major.map(Math.abs), 50);
    this.gridGroup.add(lines(minor, muted, 0.18), lines(major, muted, 0.4),
      lines([0, 0, 0, ext, 0, 0], 0xdb4d4d, 0.9), lines([0, 0, 0, 0, ext, 0], 0x59b861, 0.9));
    this.gridGroup.visible = this.showGrid;
  }

  // ---------------------------------------------------------------- controls
  setStyle(style: Style): void {
    this.style = style;
    for (const [k, b] of this.styleButtons) b.setAttribute("aria-pressed", String(k === style));
    this.applyStyle();
  }

  private applyStyle(): void {
    if (!this.mesh) return;
    const wantEdges = this.style !== "shaded";
    if (wantEdges && !this.edges) {
      this.edges = new THREE.LineSegments(new THREE.WireframeGeometry(this.mesh.geometry), new THREE.LineBasicMaterial({ transparent: true }));
      this.modelGroup.add(this.edges);
    }
    if (this.edges) {
      this.edges.visible = wantEdges;
      const mat = this.edges.material as THREE.LineBasicMaterial;
      mat.color.set(cssColor(this.style === "wireframe" ? "--text" : "--muted", "#888"));
      mat.opacity = this.style === "wireframe" ? 0.85 : 0.35;
    }
    this.mesh.visible = this.style !== "wireframe";
    this.requestRender();
  }

  setGrid(on: boolean): void {
    this.showGrid = on;
    this.gridBox.checked = on;
    this.gridGroup.visible = on;
    this.requestRender();
  }

  setView(name: string): void {
    this.camera.setView(name);
    this.fit();
  }

  fit(): void {
    let box: THREE.Box3 | null = null;
    for (const o of [this.mesh, this.overlay]) {
      const b = o?.geometry.boundingBox;
      if (b) box = box ? box.clone().union(b) : b.clone();
    }
    if (box) {
      const r = this.canvasHost.getBoundingClientRect();
      this.camera.fit(box.min.toArray() as Vec3, box.max.toArray() as Vec3, r.width / Math.max(r.height, 1) || 1);
    }
    this.requestRender();
  }

  private installControls(): void {
    const el = this.canvasHost;
    el.addEventListener("contextmenu", (e) => e.preventDefault());
    // touch: one finger orbits, two fingers pinch to zoom and move together to pan
    const touches = new Map<number, { x: number; y: number }>();
    let pinch: { dist: number; x: number; y: number } | null = null;
    const pinchState = () => {
      const [a, b] = [...touches.values()];
      return { dist: Math.hypot(a.x - b.x, a.y - b.y) || 1, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    };
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      if (e.pointerType === "touch") {
        touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (touches.size >= 2) {
          this.drag = null; // a second finger: pinch instead of orbiting
          pinch = pinchState();
          return;
        }
      }
      this.press = { x: e.clientX, y: e.clientY, moved: false, button: e.button };
      const [px, py] = this.local(e);
      this.grabbing = e.button === 0 && !e.shiftKey && this.grabTest(px, py);
      this.drag = { x: e.clientX, y: e.clientY, pan: e.button !== 0 || e.shiftKey, id: e.pointerId };
      el.classList.add(this.grabbing ? "grabbing" : "dragging");
      this.renderer?.domElement.focus();
    });
    el.addEventListener("pointermove", (e) => {
      if (touches.has(e.pointerId)) touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pinch && touches.size >= 2) {
        const now = pinchState();
        this.camera.zoom(pinch.dist / now.dist);
        this.camera.pan(now.x - pinch.x, now.y - pinch.y, el.clientHeight);
        pinch = now;
        this.requestRender();
        return;
      }
      if (!this.drag || e.pointerId !== this.drag.id) return;
      if (this.press && !this.press.moved)
        this.press.moved = Math.abs(e.clientX - this.press.x) + Math.abs(e.clientY - this.press.y) >= CLICK_SLOP_PX;
      if (this.grabbing) {
        if (this.press?.moved) {
          const [px, py] = this.local(e);
          this.onGrabMoved(px, py);
        }
        return;
      }
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      this.drag.x = e.clientX;
      this.drag.y = e.clientY;
      if (this.drag.pan) this.camera.pan(dx, dy, el.clientHeight);
      else this.camera.orbit(-dx * ORBIT_DEG_PER_PX, dy * ORBIT_DEG_PER_PX);
      this.requestRender();
    });
    const end = (e: PointerEvent) => {
      touches.delete(e.pointerId);
      if (touches.size < 2) pinch = null;
      this.drag = null;
      this.grabbing = false;
      el.classList.remove("dragging", "grabbing");
    };
    el.addEventListener("pointerup", (e) => {
      const press = this.press, grabbed = this.grabbing;
      this.press = null;
      end(e);
      if (press && !press.moved && press.button === 0 && !grabbed) {
        const [px, py] = this.local(e);
        this.onViewClicked(px, py);
      } else if (press && !press.moved && grabbed) {
        const [px, py] = this.local(e);
        this.onViewClicked(px, py); // a click on the logo itself: place it there
      }
    });
    el.addEventListener("pointercancel", end);
    el.addEventListener("dblclick", () => this.fit());
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.camera.zoom(0.85 ** (-e.deltaY / 100));
      this.requestRender();
    }, { passive: false });
    window.addEventListener("keydown", (e) => {
      if (!this.active || (e.target as HTMLElement).closest("input, select, textarea")) return;
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && !e.altKey && (e.key === "z" || e.key === "Z" || e.key === "y")) {
        e.preventDefault();
        if (e.key === "y" || e.shiftKey) this.redo();
        else this.undo();
        return;
      }
      if (e.key === "Escape" && this.editing) {
        e.preventDefault();
        this.cancelLogo();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const moves: Record<string, () => void> = {
        "+": () => this.camera.zoom(0.85), "=": () => this.camera.zoom(0.85), "-": () => this.camera.zoom(1 / 0.85),
        ArrowLeft: () => this.camera.orbit(-15, 0), ArrowRight: () => this.camera.orbit(15, 0),
        ArrowUp: () => this.camera.orbit(0, 15), ArrowDown: () => this.camera.orbit(0, -15),
        f: () => this.fit(),
      };
      const fn = moves[e.key];
      if (!fn) return;
      e.preventDefault();
      fn();
      this.requestRender();
    });
  }

  // ---------------------------------------------------------------- drawing
  requestRender(): void {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.render();
    });
  }

  /** Draw now (also used by tests). */
  render(): void {
    const r = this.renderer;
    if (!r || !this.active) return;
    const w = Math.max(this.canvasHost.clientWidth, 1), hgt = Math.max(this.canvasHost.clientHeight, 1);
    const size = r.getSize(new THREE.Vector2());
    if (size.x !== w || size.y !== hgt) r.setSize(w, hgt);
    r.setClearColor(new THREE.Color(cssColor("--bg", "#f3f4f7")), 1);
    const c = this.camera;
    const [near, far] = c.clipPlanes();
    Object.assign(this.cam3, { aspect: w / hgt, near, far });
    this.cam3.position.set(...c.eye());
    this.cam3.up.set(...c.up());
    this.cam3.lookAt(...c.target);
    this.cam3.updateProjectionMatrix();
    const dir = c.direction(), up = c.up(), right = c.right();
    const mix = (a: number, b: number, d: number) => new THREE.Vector3(...dir).multiplyScalar(a).add(new THREE.Vector3(...up).multiplyScalar(b)).add(new THREE.Vector3(...right).multiplyScalar(d)).normalize();
    this.key.position.copy(mix(0.8, 0.45, -0.35)).multiplyScalar(c.distance).add(new THREE.Vector3(...c.target));
    this.key.target.position.set(...c.target);
    this.key.target.updateMatrixWorld();
    this.fill.position.copy(mix(0.5, 0, 0.8)).multiplyScalar(c.distance).add(new THREE.Vector3(...c.target));
    this.fill.target.position.set(...c.target);
    this.fill.target.updateMatrixWorld();
    r.render(this.scene, this.cam3);
  }

  // ---------------------------------------------------------------- logos
  private get history(): History | undefined {
    return this.histories.get(this.current);
  }

  /** The current file's model + logos (null while it's still loading). */
  get logoScene(): Scene | null {
    return this.history?.scene ?? null;
  }

  private get base(): ViewModel | null {
    return this.history?.scene.base ?? null;
  }

  private shownScene(): Scene | null {
    const sc = this.logoScene;
    if (!sc) return null;
    return this.editing?.index != null ? without(sc, this.editing.index) : sc;
  }

  private fillLogos(): void {
    const logos = this.logoScene?.logos ?? [];
    this.selectedLogo = Math.min(this.selectedLogo, Math.max(logos.length - 1, 0));
    this.logoList.replaceChildren(...logos.map((lg, i) => {
      const where = lg.settings.where === "badge" ? "badge" : "on the model";
      const li = h("li", { role: "option", tabIndex: 0 }, `${i + 1}. ${lg.picture.name} (${lg.width.toFixed(0)} mm, ${where})`);
      li.setAttribute("aria-selected", String(i === this.selectedLogo));
      li.addEventListener("click", () => {
        this.selectedLogo = i;
        this.fillLogos();
      });
      li.addEventListener("dblclick", () => this.startLogo(i));
      return li;
    }));
    this.logoBox.hidden = !logos.length;
  }

  private syncButtons(): void {
    const hs = this.history;
    const idle = !this.editing && !this.saving;
    const b = this.topButtons;
    b.logo.disabled = !idle || !hs;
    b.undo.disabled = !idle || !hs?.undoStack.length;
    b.redo.disabled = !idle || !hs?.redoStack.length;
    const some = Boolean(hs && (hs.scene.base || hs.scene.logos.length));
    b.save.disabled = !idle || !some;
    b.arrange.disabled = !idle || !some;
    b.check.disabled = !idle || !some;
    b.arrange.setAttribute("aria-pressed", String(this.panel === "arrange"));
    b.check.setAttribute("aria-pressed", String(this.panel === "check"));
    b.open.disabled = Boolean(this.editing);
    this.fileBox.disabled = Boolean(this.editing);
    for (const btn of this.logoBox.querySelectorAll("button")) btn.disabled = !idle;
  }

  private push(sc: Scene): void {
    this.history!.push(sc);
    this.showScene();
  }

  undo(): void {
    if (!this.editing && !this.saving && this.history?.undo()) this.showScene();
  }

  redo(): void {
    if (!this.editing && !this.saving && this.history?.redo()) this.showScene();
  }

  removeLogo(index: number): void {
    const sc = this.logoScene;
    if (!this.editing && sc && index < sc.logos.length) this.push(without(sc, index));
  }

  /** Open the logo panel for a new logo (or to change logo ``index``). */
  startLogo(index: number | null = null): void {
    this.closePanel();
    const hs = this.history;
    if (!hs || this.editing || this.saving) return;
    const e: Edit = { index, picture: null, point: null, normal: null, faceTris: null, shape: null, shapeKey: "", logo: null, preview: null, problem: "", tracing: 0, applyWhenTraced: false };
    const p = this.logoPanel;
    p.setHasModel(Boolean(hs.scene.base));
    if (index === null) {
      const s = p.settings();
      p.setSettings(hs.scene.base ? s : { ...s, where: "badge" });
      p.setPicture("");
      p.setPreview(null);
      p.setSize(0, 0);
      p.title.textContent = "Add a logo";
    } else {
      const lg = hs.scene.logos[index];
      Object.assign(e, { picture: lg.picture, point: lg.point, normal: lg.normal });
      if (lg.point && lg.normal && hs.scene.base) e.faceTris = this.faceAtPoint(lg.point, lg.normal);
      p.setSettings(lg.settings);
      p.setPicture(lg.picture.name);
      p.title.textContent = "Change the logo";
    }
    this.editing = e;
    this.sideInfo.hidden = true;
    p.el.hidden = false;
    p.setNote("Choose a picture: a PNG with a transparent background works best.");
    this.showScene();
    if (e.picture) this.rebuild();
  }

  private faceAtPoint(point: Vec3, normal: Vec3): number[] | null {
    const base = this.base!;
    const o: Vec3 = [point[0] + normal[0] * 0.5, point[1] + normal[1] * 0.5, point[2] + normal[2] * 0.5];
    const found = hit(base.positions, o, [-normal[0], -normal[1], -normal[2]]);
    return found ? flatFace(base.positions, found[0]).triangles : null;
  }

  /** Read a picture file chosen in the panel, then build. */
  async loadPicture(file: File): Promise<void> {
    const e = this.editing;
    if (!e) return;
    this.logoPanel.setNote(`Reading ${file.name}…`);
    let pic: Picture;
    try {
      pic = await readPicture(file);
    } catch {
      if (this.editing === e) this.logoPanel.setNote(`Couldn't read “${file.name}” as a picture.`);
      return;
    }
    if (this.editing !== e) return;
    Object.assign(e, { picture: pic, shape: null, shapeKey: "" });
    this.logoPanel.setPicture(pic.name);
    this.logoPanel.setSettings({ ...this.logoPanel.settings(), mode: defaultMode(pic.rgba, pic.w, pic.h) });
    this.rebuild();
  }

  private onSettingsChanged(): void {
    if (!this.editing) return;
    clearTimeout(this.rebuildTimer);
    this.rebuildTimer = window.setTimeout(() => this.rebuild(), REBUILD_MS);
  }

  /** Trace again if the picture / shape settings changed, else just place again. */
  rebuild(): void {
    clearTimeout(this.rebuildTimer);
    const e = this.editing;
    if (!e?.picture) return;
    const s = this.logoPanel.settings();
    const key = shapeKey(e.picture, s);
    if (e.shape && e.shapeKey === key) {
      this.place();
      return;
    }
    if (e.tracing && e.shapeKey === key) return; // already on its way
    // trace off the page (a busy picture takes a moment)
    const pic = e.picture;
    const rgb = new THREE.Color(cssColor("--text", "#1c1f26"));
    const id = (e.tracing = ++this.traceId);
    this.logoPanel.setNote("Tracing the picture…");
    const worker = this.helper();
    const request: TraceRequest = {
      id, rgba: pic.rgba.slice(), w: pic.w, h: pic.h, mode: s.mode, threshold: s.threshold, invert: s.invert,
      detail: s.detail, width: s.width, colour: [Math.round(rgb.r * 255), Math.round(rgb.g * 255), Math.round(rgb.b * 255)],
    };
    worker.postMessage(request, [request.rgba.buffer]);
    e.shapeKey = key;
    e.shape = null;
  }

  /** The helper worker (tracing, checks, repairs), made on first use. */
  private helper(): Worker {
    if (this.tracer) return this.tracer;
    const w = new Worker(new URL("./traceworker.ts", import.meta.url), { type: "module" });
    w.onmessage = (m: MessageEvent<TraceAnswer | CheckAnswer | RepairAnswer>) => {
      const a = m.data;
      if (a.kind === "check") this.onChecked(a);
      else if (a.kind === "repair") this.onRepaired(a);
      else this.onTraced(a);
    };
    // the worker couldn't load or died: say so, and start a new one next time
    w.onerror = w.onmessageerror = (ev: Event) => {
      ev.preventDefault?.();
      w.terminate();
      if (this.tracer === w) this.tracer = null;
      const ed = this.editing;
      if (ed?.tracing) {
        Object.assign(ed, { tracing: 0, applyWhenTraced: false, shape: null, shapeKey: "", logo: null, preview: null, problem: "Couldn't trace the picture — try again." });
        this.logoPanel.setNote(ed.problem);
        this.setOverlay(null);
      }
      if (this.checkId > 0 && this.panel === "check") this.checkPanel.result.textContent = "Couldn't check this model.";
      this.checkId++;
      if (this.repairId > 0) this.checkPanel.repaired.textContent = "";
      this.repairId++;
      this.checkPanel.repairButton.disabled = false;
    };
    return (this.tracer = w);
  }

  // ---------------------------------------------------------------- arrange / check panels
  /** Show the Arrange or Check panel (again = back to the details). */
  openPanel(name: "arrange" | "check"): void {
    if (this.panel === name) {
      this.closePanel();
      return;
    }
    const sc = this.logoScene;
    if (!sc || (!sc.base && !sc.logos.length) || this.editing || this.saving) return;
    this.closePanel();
    this.panel = name;
    this.sideInfo.hidden = true;
    (name === "arrange" ? this.arrangePanel : this.checkPanel).el.hidden = false;
    if (name === "check") {
      this.checkPanel.repaired.textContent = "";
      this.checkedScene = null;
    }
    this.showScene();
  }

  closePanel(): void {
    if (!this.panel) return;
    this.panel = "";
    this.setLayingFlat(false);
    this.checkId++;
    this.setHighlight(null);
    this.arrangePanel.el.hidden = this.checkPanel.el.hidden = true;
    this.sideInfo.hidden = false;
    this.syncButtons();
  }

  private centreNow(): Vec3 {
    const sc = this.logoScene;
    return sc ? A.centreOf(scenePositions(sc)) : [0, 0, 0];
  }

  /** One undo step: the model and its logos moved by ``m`` (then back on the bed). */
  arrange(m: A.Matrix, settleIt = true): void {
    const sc = this.logoScene;
    if (!sc || (!sc.base && !sc.logos.length) || this.editing || this.saving) return;
    const pos = scenePositions(sc);
    this.push(arranged(sc, settleIt ? A.settle(pos, m) : m));
  }

  rotate(axis: "x" | "y" | "z", degrees: number): void {
    if (degrees % 360) this.arrange(A.rotate(axis, degrees, this.centreNow()));
  }

  scaleBy(sx: number, sy: number, sz: number): void {
    if ((sx !== 1 || sy !== 1 || sz !== 1) && Math.min(sx, sy, sz) > 0) this.arrange(A.scale(sx, sy, sz, this.centreNow()));
  }

  sizeTo(axis: "x" | "y" | "z", mm: number, keep: boolean): void {
    const sc = this.logoScene;
    if (!sc) return;
    const [lo, hi] = A.bounds(scenePositions(sc));
    const i = "xyz".indexOf(axis);
    const now = hi[i] - lo[i];
    if (now <= 0 || Math.abs(mm - now) < 1e-9) return;
    const k = mm / now;
    if (keep) this.scaleBy(k, k, k);
    else this.scaleBy(i === 0 ? k : 1, i === 1 ? k : 1, i === 2 ? k : 1);
  }

  moveTo(x: number, y: number): void {
    const [cx, cy] = this.centreNow();
    if (Math.abs(x - cx) > 1e-9 || Math.abs(y - cy) > 1e-9) this.arrange(A.translate(x - cx, y - cy, 0), false);
  }

  centre(): void {
    const sc = this.logoScene;
    if (sc) this.arrange(A.centred(scenePositions(sc)), false);
  }

  setLayingFlat(on: boolean): void {
    this.layingFlat = on;
    if (!on) this.arrangePanel.stopLayFlat();
    this.message.textContent = on ? "Click the face that should lie on the bed." : "";
  }

  /** Turn so the flat face around triangle ``tri`` (of the shown scene) lies on the bed. */
  layFlatOn(tri: number, viewDir: Vec3 | null = null): void {
    const sc = this.logoScene;
    if (!sc) return;
    const face = flatFace(scenePositions(sc), tri);
    let n = face.normal;
    if (viewDir && n[0] * viewDir[0] + n[1] * viewDir[1] + n[2] * viewDir[2] > 0) n = [-n[0], -n[1], -n[2]];
    this.setLayingFlat(false);
    this.arrange(A.layFlat(n, this.centreNow()));
  }

  setBed(bed: A.Bed): void {
    this.bed = bed;
    try {
      localStorage.setItem("umc-bed", bed.join("|"));
    } catch {
      // not kept
    }
    this.buildBed();
    this.showScene();
  }

  private buildBed(): void {
    for (const c of [...this.bedGroup.children]) {
      const l = c as THREE.LineSegments;
      l.geometry.dispose();
      (l.material as THREE.Material).dispose();
      c.removeFromParent();
    }
    const [, w, d, hgt] = this.bed;
    const x0 = -w / 2, x1 = w / 2, y0 = -d / 2, y1 = d / 2;
    const c = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => [i & 1 ? x1 : x0, i & 2 ? y1 : y0, i & 4 ? hgt : 0]);
    const pairs = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pairs.flatMap(([a, b]) => [...c[a], ...c[b]]), 3));
    this.bedGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: new THREE.Color(cssColor("--accent", "#5b5ef0")), transparent: true, opacity: 0.7 })));
    this.requestRender();
  }

  private infoRowsOf(m: ViewModel): [string, string][] {
    const rows = describe(m);
    rows.push(["Printer bed", A.fitNote(m.size, this.bed.slice(1) as Vec3)]);
    if (m.volumeMm3) {
      let area = this.areaCache.get(m.positions);
      if (area === undefined) {
        area = 0;
        const p = m.positions;
        for (let i = 0; i < p.length; i += 9) {
          const ux = p[i + 3] - p[i], uy = p[i + 4] - p[i + 1], uz = p[i + 5] - p[i + 2];
          const vx = p[i + 6] - p[i], vy = p[i + 7] - p[i + 1], vz = p[i + 8] - p[i + 2];
          area += Math.hypot(uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx) / 2;
        }
        this.areaCache.set(m.positions, area);
      }
      rows.push(...filamentRows(m.volumeMm3, area, this.material, this.infill));
    }
    return rows;
  }

  /** Check what's shown (in the helper worker); the panel and the red overhangs follow. */
  runCheck(): void {
    const sc = this.logoScene;
    if (this.panel !== "check" || !sc) return;
    const m = combined(sc, this.current?.name ?? "Logo");
    if (!m) return;
    this.checkedScene = sc;
    this.checkPanel.checking();
    const request: CheckRequest = { kind: "check", id: ++this.checkId, positions: m.positions.slice(), angle: Number(this.checkPanel.angle.value) || 45 };
    this.checkPositions = m.positions;
    this.helper().postMessage(request, [request.positions.buffer]);
  }

  private checkPositions: Float32Array | null = null;

  private onChecked(a: CheckAnswer): void {
    if (a.id !== this.checkId || this.panel !== "check") return;
    if (!a.report) {
      this.checkPanel.result.textContent = a.error;
      return;
    }
    this.report = a.report;
    this.checkPanel.showReport(a.notes, a.ok, a.report.overhang_area);
    const pos = this.checkPositions;
    if (this.checkPanel.show.checked && pos && a.report.overhang_faces.length) {
      const out = new Float32Array(a.report.overhang_faces.length * 9);
      a.report.overhang_faces.forEach((t, i) => out.set(pos.subarray(t * 9, t * 9 + 9), i * 9));
      this.setHighlight(out);
    } else this.setHighlight(null);
    this.checked++;
  }

  /** How many checks have finished (tests). */
  checked = 0;

  /** One undo step: the model (not the logos) repaired, then checked again. */
  repair(): void {
    const sc = this.logoScene;
    if (!sc?.base || this.saving || this.editing) return;
    this.checkPanel.repairButton.disabled = true;
    this.checkPanel.repaired.textContent = "Repairing…";
    this.repairScene = sc;
    const request: RepairRequest = { kind: "repair", id: ++this.repairId, positions: sc.base.positions.slice() };
    this.helper().postMessage(request, [request.positions.buffer]);
  }

  private repairScene: Scene | null = null;

  private onRepaired(a: RepairAnswer): void {
    if (a.id !== this.repairId) return;
    const sc = this.repairScene;
    this.checkPanel.repaired.textContent = a.error || a.note;
    if (a.changed && a.positions && a.after && sc?.base && this.history?.scene === sc) {
      const after = a.after;
      const [lo, hi] = A.bounds(a.positions);
      const base: ViewModel = {
        ...sc.base, positions: a.positions, triangles: a.positions.length / 9, parts: after.parts,
        size: [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]], watertight: a.closed, volumeMm3: after.volume || null,
      };
      this.push(withBase(sc, base));
    } else this.runCheck();
    this.repaired++;
  }

  /** How many repairs have finished (tests). */
  repaired = 0;

  private setHighlight(pos: Float32Array | null): void {
    if (this.highlight) {
      this.highlight.geometry.dispose();
      (this.highlight.material as THREE.Material).dispose();
      this.highlight.removeFromParent();
      this.highlight = null;
    }
    if (pos?.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.computeVertexNormals();
      this.highlight = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0xdb4040, roughness: 0.7, side: THREE.DoubleSide, flatShading: true }));
      this.modelGroup.add(this.highlight);
    }
    this.requestRender();
  }

  private onTraced(a: TraceAnswer): void {
    const e = this.editing;
    if (!e || a.id !== e.tracing || !e.picture) return; // an older request, or the edit ended
    e.tracing = 0;
    this.logoPanel.setPreview(a.preview, e.picture.w, e.picture.h);
    if (!a.shape) {
      Object.assign(e, { shape: null, shapeKey: "", logo: null, preview: null, problem: a.error, applyWhenTraced: false });
      this.logoPanel.setNote(a.error);
      this.logoPanel.setSize(0, 0);
      this.showScene();
      return;
    }
    e.shape = a.shape;
    this.place();
    if (e.applyWhenTraced) {
      e.applyWhenTraced = false;
      this.applyLogo();
    }
  }

  /** Extrude + place the traced shape (quick: no tracing). */
  private place(): void {
    const e = this.editing!;
    if (!e.shape) return;
    const s = this.logoPanel.settings();
    Object.assign(e, { logo: null, preview: null, problem: "" });
    this.logoPanel.setSize(e.shape!.width, e.shape!.height);
    if (s.where === "face" && !e.point) {
      this.logoPanel.setNote(FACE_HINT);
    } else {
      try {
        e.logo = buildLogo(e.picture!, s, e.point, e.normal, this.base, e.faceTris, e.shape!);
        e.preview = logoPositions([e.logo]);
      } catch (err) {
        if (!(err instanceof LogoError)) throw err;
        e.problem = err.message;
      }
      this.logoPanel.setNote(e.problem || logoNote(e.logo!));
    }
    this.setOverlay(e.preview);
    if (!this.mesh && e.preview) this.fit();
  }

  private local(e: PointerEvent | MouseEvent): [number, number] {
    const r = this.canvasHost.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  private rayAt(x: number, y: number): [Vec3, Vec3] {
    return ray(this.camera, x, y, this.canvasHost.clientWidth, this.canvasHost.clientHeight);
  }

  private onViewClicked(x: number, y: number): void {
    if (this.layingFlat) {
      const sc = this.logoScene;
      if (!sc) return;
      const pos = scenePositions(sc);
      const [o, d] = this.rayAt(x, y);
      const found = hit(pos, o, d);
      if (found) this.layFlatOn(found[0], d);
      return;
    }
    const e = this.editing, base = this.base;
    if (!e || !base || this.logoPanel.settings().where !== "face") return;
    const [o, d] = this.rayAt(x, y);
    const found = hit(base.positions, o, d);
    if (found) this.placeOn(found[0], found[1], d);
  }

  /** Put the logo on the flat face around triangle ``tri`` of the model, at ``point``. */
  placeOn(tri: number, point: Vec3, viewDir: Vec3 | null = null): void {
    const e = this.editing, base = this.base;
    if (!e || !base) return;
    const face = flatFace(base.positions, tri);
    let n = face.normal;
    if (viewDir && n[0] * viewDir[0] + n[1] * viewDir[1] + n[2] * viewDir[2] > 0) n = [-n[0], -n[1], -n[2]];
    Object.assign(e, { point: [...point] as Vec3, normal: n, faceTris: face.triangles });
    if (e.shape) this.place();
    else this.rebuild();
  }

  private grabTest(x: number, y: number): boolean {
    const e = this.editing;
    if (!e?.preview || !e.normal || !e.point) return false;
    const [o, d] = this.rayAt(x, y);
    if (hit(e.preview, o, d) === null) return false;
    const at = this.onPlane(o, d);
    if (!at) return false;
    this.grabOffset = [e.point[0] - at[0], e.point[1] - at[1], e.point[2] - at[2]];
    return true;
  }

  private onGrabMoved(x: number, y: number): void {
    const [o, d] = this.rayAt(x, y);
    this.moveLogoAlongRay(o, d, this.grabOffset);
  }

  /** Where the ray meets the plane of the logo's face (null: parallel or behind). */
  private onPlane(o: Vec3, d: Vec3): Vec3 | null {
    const e = this.editing!;
    const n = e.normal!, p = e.point!;
    const denom = d[0] * n[0] + d[1] * n[1] + d[2] * n[2];
    if (Math.abs(denom) < 1e-6) return null;
    const t = ((p[0] - o[0]) * n[0] + (p[1] - o[1]) * n[1] + (p[2] - o[2]) * n[2]) / denom;
    return t > 0 ? [o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t] : null;
  }

  /** Slide the logo over its face's plane to where the ray meets it (+ ``offset``: the logo
   * keeps the spot it was grabbed by under the pointer). */
  moveLogoAlongRay(o: Vec3, d: Vec3, offset: Vec3 = [0, 0, 0]): void {
    const e = this.editing;
    if (!e?.normal || !e.point || !e.shape) return;
    const at = this.onPlane(o, d);
    if (!at) return;
    e.point = [at[0] + offset[0], at[1] + offset[1], at[2] + offset[2]];
    this.place();
  }

  applyLogo(): void {
    const e = this.editing;
    if (!e) return;
    clearTimeout(this.rebuildTimer);
    if (e.picture) {
      const s = this.logoPanel.settings();
      if (e.shapeKey !== shapeKey(e.picture, s)) this.rebuild();
      if (e.tracing) {
        e.applyWhenTraced = true; // applied as soon as the trace is back
        return;
      }
      if (e.shape && (!e.logo || !sameSettings(e.logo.settings, s))) this.place();
    }
    if (!e.logo) {
      this.logoPanel.setNote(e.problem || (e.picture ? FACE_HINT : "Choose a picture first."));
      return;
    }
    const sc = withLogo(this.history!.scene, e.logo, e.index);
    this.endEdit();
    this.push(sc);
  }

  cancelLogo(): void {
    if (!this.editing) return;
    this.endEdit();
    this.showScene();
  }

  private endEdit(): void {
    clearTimeout(this.rebuildTimer);
    this.traceId++; // answers still on their way are dropped
    if (!this.editing) return;
    this.editing = null;
    this.setOverlay(null);
    this.logoPanel.el.hidden = true;
    this.sideInfo.hidden = false;
  }

  /** Ask for a format, then write the model with its logos (the worker's model writers). */
  async saveAs(ext?: SaveExt): Promise<boolean> {
    const hs = this.history;
    if (!hs || (!hs.scene.base && !hs.scene.logos.length) || this.editing || this.saving) return false;
    ext ??= (await this.askSaveFormat()) ?? undefined;
    if (!ext) return false;
    const sc = hs.scene;
    this.saving = true;
    this.syncButtons();
    const name = defaultSaveName(this.current?.name ?? null, ext, hs.scene.logos.length > 0);
    this.message.classList.remove("error");
    this.message.textContent = `Saving ${name}…`;
    try {
      const file = new File([sceneStl(sc) as BlobPart], "scene.stl", { type: "model/stl" });
      const input: InputFile = { file, name: file.name, ext: "stl", family: "model" };
      const target = { id: ext, label: ext.toUpperCase(), ext, group: "3D model", converterId: "model3d" };
      const opts = { units: "mm", scale_percent: 100, center_on_bed: false, repair: false, merge_parts: true, stl_format: "binary", detail: "normal" };
      const out = await this.opts.client.run(target, [input], opts, () => undefined);
      const url = URL.createObjectURL(new Blob([out[0].data as BlobPart], { type: "application/octet-stream" }));
      const a = h("a", { href: url, download: name });
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      hs.saved = sc;
      this.message.textContent = `Saved “${name}”.`;
      this.lastSaved = { name, data: out[0].data };
      return true;
    } catch (err) {
      this.message.textContent = err instanceof Error && err.message ? err.message : `Couldn't save ${name}.`;
      this.message.classList.add("error");
      return false;
    } finally {
      this.saving = false;
      this.syncButtons();
    }
  }

  /** The last saved file (tests). */
  lastSaved: { name: string; data: Uint8Array } | null = null;

  private askSaveFormat(): Promise<SaveExt | null> {
    return new Promise((resolve) => {
      const select = h("select", { id: "logo-save-format" },
        ...SAVE_EXTS.map((x) => h("option", { value: x }, x === "3mf" ? "3MF (best for slicers)" : x.toUpperCase())));
      const dialog = h("dialog", { class: "logo-save-dialog" },
        h("h2", {}, "Save as"),
        h("label", {}, "Format ", select),
        h("p", { class: "muted small" }, "The model and its logos are saved as one object, in millimetres, centred on the print bed as shown here."));
      const done = (v: SaveExt | null) => {
        dialog.close();
        dialog.remove();
        resolve(v);
      };
      const cancel = h("button", { class: "btn small", type: "button" }, "Cancel");
      cancel.addEventListener("click", () => done(null));
      const ok = h("button", { class: "btn primary", type: "button" }, "Save");
      ok.addEventListener("click", () => done(select.value as SaveExt));
      dialog.addEventListener("cancel", (e) => {
        e.preventDefault();
        done(null);
      });
      dialog.append(h("div", { class: "logo-buttons" }, cancel, ok));
      this.el.append(dialog);
      dialog.showModal();
    });
  }

  /** Where a point of the model is on screen, relative to the page (tests: real mouse clicks). */
  screenOf(p: Vec3): [number, number] {
    this.render();
    const v = new THREE.Vector3(...p).project(this.cam3);
    const r = this.canvasHost.getBoundingClientRect();
    return [r.left + ((v.x + 1) / 2) * r.width, r.top + ((1 - v.y) / 2) * r.height];
  }

  /** {label: value} of the info panel (tests). */
  infoRows(): Record<string, string> {
    const out: Record<string, string> = {};
    const dts = this.info.querySelectorAll("dt");
    dts.forEach((dt) => (out[dt.textContent ?? ""] = (dt.nextElementSibling as HTMLElement).textContent ?? ""));
    return out;
  }
}

function logoNote(lg: Logo): string {
  const notes = [`${lg.width.toFixed(1)} × ${lg.height.toFixed(1)} mm.`];
  if (lg.overhang) notes.push("The logo hangs over the edge of this face — make it smaller or move it.");
  if (lg.dropped) notes.push(`${lg.dropped} tiny ${lg.dropped === 1 ? "bit" : "bits"} left out (too small to print).`);
  if (lg.settings.where === "face") notes.push("Drag the logo to move it, or click another face.");
  return notes.join(" ");
}

export function mountViewer(host: HTMLElement, opts: ViewerOptions): ViewerPage {
  const page = new ViewerPage(opts);
  host.append(page.el);
  return page;
}
