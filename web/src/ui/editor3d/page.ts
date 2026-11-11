// The 3D viewer page (port of the desktop's gui/editor3d/viewer_page.py): top bar (files,
// views, display style, bed grid), a three.js view, an info panel. Models are read in the
// conversion worker (hidden "model3d-view" converter), so STEP / IGES work and the page stays
// responsive. Loaded on demand from ui/app.ts.
import * as THREE from "three";
import { VIEW_TARGET } from "../../converters/model3d";
import type { InputFile } from "../../core/types";
import { OrbitCamera, FOV, type Vec3 } from "../../editor3d/camera";
import { describe, gridSpec, type ModelStats, type ViewModel } from "../../editor3d/mesh";
import type { ConversionClient } from "../../worker/client";

const h = <K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> & { class?: string } = {}, ...children: (Node | string)[]) => {
  const el = document.createElement(tag);
  const { class: cls, ...rest } = props;
  Object.assign(el, rest);
  if (cls) el.className = cls;
  el.append(...children);
  return el;
};

export const HINT = "Drag to rotate · right-drag or Shift+drag to pan · wheel to zoom · double-click to fit";
const VIEW_BUTTONS: [string, string][] = [["iso", "3D"], ["front", "Front"], ["top", "Top"], ["right", "Side"]];
const STYLE_BUTTONS: [Style, string][] = [["shaded", "Shaded"], ["edges", "Edges"], ["wireframe", "Wireframe"]];
const ORBIT_DEG_PER_PX = 0.4;
type Style = "shaded" | "edges" | "wireframe";

export interface ViewerOptions {
  client: ConversionClient;
  onBack: () => void;
}

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
    const top = h("div", { class: "editor-top" },
      btn("← Back to Convert", "Leave the 3D viewer", () => this.opts.onBack(), "link"),
      h("span", { class: "gap" }), this.fileBox,
      h("div", { class: "spacer" }),
      btn("Fit", "Show the whole model (double-click the view)", () => this.fit()), ...views,
      h("span", { class: "gap" }), styles,
      h("span", { class: "gap" }),
      h("label", { class: "check", title: "Grid on the print bed (Z = 0); red = X, green = Y" }, this.gridBox, " Bed grid"));
    const side = h("div", { class: "editor-side viewer3d-side" }, this.title, this.info,
      h("p", { class: "muted small" }, "Sizes are in millimetres, as a slicer will see them. Files without a unit (STL, OBJ, PLY) are taken as mm."));
    this.el = h("div", { class: "editor viewer3d", hidden: true },
      top,
      h("div", { class: "viewer3d-body" }, h("div", { class: "viewer3d-view" }, this.canvasHost, this.placeholder), side),
      h("div", { class: "editor-status" }, this.message, h("div", {}, HINT)));

    this.scene.add(this.modelGroup, this.gridGroup, this.key, this.fill, new THREE.HemisphereLight(0xffffff, 0x666670, 0.9));
    this.cam3.up.set(0, 0, 1);
    this.installControls();
    this.resizeObserver = new ResizeObserver(() => this.requestRender());
    this.resizeObserver.observe(this.canvasHost);
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

  maybeLeave(): boolean {
    return true; // the viewer changes nothing
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

  async showFile(f: InputFile): Promise<void> {
    this.current = f;
    this.fileBox.selectedIndex = this.files.indexOf(f);
    this.title.textContent = f.name;
    let model = this.models.get(f);
    if (model === undefined) {
      const token = ++this.loadToken;
      this.showPlaceholder(`Opening ${f.name}…`);
      this.info.replaceChildren();
      try {
        model = await this.load(f);
      } catch (e) {
        model = e instanceof Error && e.message ? e.message : `Couldn't open “${f.name}” as a 3D model.`;
      }
      this.models.set(f, model);
      if (token !== this.loadToken || this.current !== f) return;
    }
    if (typeof model === "string") {
      this.showPlaceholder(model);
      this.message.textContent = model;
      this.message.classList.add("error");
      return;
    }
    this.showModel(model);
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

  private showModel(m: ViewModel): void {
    this.message.textContent = "";
    this.message.classList.remove("error");
    this.info.replaceChildren(...describe(m).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
    if (!this.ensureRenderer()) {
      this.showPlaceholder("The 3D view needs WebGL, which isn't working in this browser. The model's measurements are on the right.");
      this.message.textContent = this.failed;
      return;
    }
    this.placeholder.hidden = true;
    this.canvasHost.hidden = false;
    this.disposeModel();
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
    this.fit();
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
    const box = this.mesh?.geometry.boundingBox;
    if (box) {
      const r = this.canvasHost.getBoundingClientRect();
      this.camera.fit(box.min.toArray() as Vec3, box.max.toArray() as Vec3, r.width / Math.max(r.height, 1) || 1);
    }
    this.requestRender();
  }

  private installControls(): void {
    const el = this.canvasHost;
    el.addEventListener("contextmenu", (e) => e.preventDefault());
    el.addEventListener("pointerdown", (e) => {
      el.setPointerCapture(e.pointerId);
      this.drag = { x: e.clientX, y: e.clientY, pan: e.button !== 0 || e.shiftKey, id: e.pointerId };
      el.classList.add("dragging");
      this.renderer?.domElement.focus();
    });
    el.addEventListener("pointermove", (e) => {
      if (!this.drag || e.pointerId !== this.drag.id) return;
      const dx = e.clientX - this.drag.x, dy = e.clientY - this.drag.y;
      this.drag.x = e.clientX;
      this.drag.y = e.clientY;
      if (this.drag.pan) this.camera.pan(dx, dy, el.clientHeight);
      else this.camera.orbit(-dx * ORBIT_DEG_PER_PX, dy * ORBIT_DEG_PER_PX);
      this.requestRender();
    });
    const end = () => {
      this.drag = null;
      el.classList.remove("dragging");
    };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
    el.addEventListener("dblclick", () => this.fit());
    el.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.camera.zoom(0.85 ** (-e.deltaY / 100));
      this.requestRender();
    }, { passive: false });
    window.addEventListener("keydown", (e) => {
      if (!this.active || (e.target as HTMLElement).closest("input, select, textarea")) return;
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

  /** {label: value} of the info panel (tests). */
  infoRows(): Record<string, string> {
    const out: Record<string, string> = {};
    const dts = this.info.querySelectorAll("dt");
    dts.forEach((dt) => (out[dt.textContent ?? ""] = (dt.nextElementSibling as HTMLElement).textContent ?? ""));
    return out;
  }
}

export function mountViewer(host: HTMLElement, opts: ViewerOptions): ViewerPage {
  const page = new ViewerPage(opts);
  host.append(page.el);
  return page;
}
