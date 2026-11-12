// File-type badges orbiting the empty drop zone — a port of the desktop's core/orbit.py (same
// badges, rings and maths from orbit.json; both test suites check tests/fixtures/orbit_cases.json).
// A canvas behind the drop zone's text; pointer events pass through it. Dragging a file over
// pulls the badges in and speeds them up; a drop bursts them outward before the files are added.
// With "reduce motion" on they stand still (and a drop doesn't wait for the burst).
import data from "./orbit.json";
import { ICONS } from "./icon_data";

export interface Spot { x: number; y: number; scale: number; alpha: number; z: number }

const rad = (deg: number) => (deg * Math.PI) / 180;

export const speed = (pull: number) => 1 + data.pullSpeed * pull;

/** Where each badge is at orbit time ``tau`` (seconds): core/orbit.py ``layout``, line by line. */
export function layout(tau: number, width: number, height: number, textW: number, textH: number, pull = 0, burst = 0): Spot[] {
  const sx = Math.max(width / 2 - data.pad, 1);
  const sy = Math.max(height / 2 - data.pad, 1);
  const out = 1 + data.burstOut * (1 - (1 - burst) ** 2);
  const fade = (1 - burst) ** 2;
  const shrink = 1 - data.pullIn * pull;
  const spots: Spot[] = [];
  for (const ring of data.rings) {
    const tilt = rad(ring.tilt), roll = rad(ring.roll);
    for (let k = 0; k < ring.count; k++) {
      const turn = ring.phase + k / ring.count + (ring.direction * tau) / ring.period;
      const a = 2 * Math.PI * turn;
      const r = ring.radius * shrink * out;
      const x0 = r * Math.cos(a), y0 = r * Math.sin(a);
      const y1 = y0 * Math.cos(tilt);
      const x = x0 * Math.cos(roll) - y1 * Math.sin(roll);
      const y = x0 * Math.sin(roll) + y1 * Math.cos(roll);
      const depth = Math.sin(a);
      const px = x * sx, py = y * sy * data.stretch;
      const hw = Math.max(textW / 2, 1), hh = Math.max(textH / 2, 1);
      const d = Math.hypot(px / hw, py / hh);
      const nearText = Math.min(Math.max((d - 1) / data.clear, 0), 1);
      const alpha = (0.5 + 0.25 * (depth + 1)) * nearText * fade;
      spots.push({ x: px, y: py, scale: 0.82 + 0.18 * depth, alpha, z: depth });
    }
  }
  return spots;
}

const DIM = 0.62; // "lively but dimmed": the brightest badge is this opaque
const BADGE_H = 30;

function svgImage(name: string, colour: string): Promise<HTMLImageElement> {
  const inner = (ICONS as Record<string, string>)[name] ?? "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="${colour}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  const img = new Image();
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
  return img.decode().then(() => img, () => img);
}

/** One badge drawn once (icon + label in a soft pill of its colour), at the screen's density. */
async function renderBadge(icon: string, label: string, colour: string, dpr: number): Promise<HTMLCanvasElement> {
  const font = "600 12px system-ui, -apple-system, 'Segoe UI', sans-serif";
  const probe = document.createElement("canvas").getContext("2d")!;
  probe.font = font;
  const w = Math.ceil(10 + 16 + 6 + probe.measureText(label).width + 12);
  const c = document.createElement("canvas");
  c.width = Math.ceil(w * dpr);
  c.height = Math.ceil(BADGE_H * dpr);
  const g = c.getContext("2d")!;
  g.scale(dpr, dpr);
  const r = BADGE_H / 2;
  g.beginPath();
  g.roundRect(0.5, 0.5, w - 1, BADGE_H - 1, r);
  g.fillStyle = `${colour}26`; // ~15 %
  g.fill();
  g.strokeStyle = `${colour}66`; // ~40 %
  g.lineWidth = 1;
  g.stroke();
  g.drawImage(await svgImage(icon, colour), 10, (BADGE_H - 16) / 2, 16, 16);
  g.font = font;
  g.fillStyle = colour;
  g.textBaseline = "middle";
  g.fillText(label, 32, BADGE_H / 2 + 0.5);
  return c;
}

export class Orbit {
  readonly canvas = document.createElement("canvas");
  private badges: HTMLCanvasElement[] = [];
  private tau = 0;
  private pull = 0;
  private pullTarget = 0;
  private burstStart: number | null = null;
  private last = 0;
  private frame = 0;
  private active = true;
  private readonly still = matchMedia("(prefers-reduced-motion: reduce)");

  constructor(private readonly host: HTMLElement, private readonly text: HTMLElement[]) {
    this.canvas.className = "orbit";
    this.canvas.setAttribute("aria-hidden", "true");
    host.prepend(this.canvas);
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 3));
    void Promise.all(data.badges.map(([icon, label, colour]) => renderBadge(icon, label, colour, dpr))).then((b) => {
      this.badges = b;
      this.kick();
    });
    new ResizeObserver(() => this.kick()).observe(host);
    document.addEventListener("visibilitychange", () => this.kick());
    this.still.addEventListener?.("change", () => this.kick());
    this.kick();
  }

  /** Badges only while the big empty drop zone shows. */
  setActive(on: boolean): void {
    this.active = on;
    this.canvas.hidden = !on;
    this.kick();
  }

  /** A file dragged over the box (or not any more). */
  setPull(on: boolean): void {
    this.pullTarget = on ? 1 : 0;
    this.kick();
  }

  /** Animating: active, on screen (not behind an editor page), tab visible, motion allowed. */
  get moving(): boolean {
    return this.active && !this.still.matches && document.visibilityState === "visible" && this.host.clientWidth > 0;
  }

  /** The drop burst; resolves when it's over (at once when nothing moves). */
  burst(): Promise<void> {
    if (!this.moving) return Promise.resolve();
    this.burstStart = performance.now();
    this.canvas.dataset.burst = "true";
    this.kick();
    return new Promise((done) => setTimeout(() => {
      this.burstStart = null; // back in orbit if the drop added nothing
      delete this.canvas.dataset.burst;
      done();
    }, data.burstMs));
  }

  private kick(): void {
    this.canvas.dataset.moving = String(this.moving);
    if (this.frame) return;
    this.last = performance.now();
    this.frame = requestAnimationFrame((t) => this.step(t));
  }

  private step(now: number): void {
    this.frame = 0;
    const moving = this.moving;
    this.canvas.dataset.moving = String(moving);
    if (!this.active) return;
    if (!this.badges.length) {
      if (moving) this.frame = requestAnimationFrame((t) => this.step(t)); // still drawing them
      return;
    }
    const dt = Math.min((now - this.last) / 1000, 0.05);
    this.last = now;
    if (moving) {
      this.pull += (this.pullTarget - this.pull) * Math.min(1, dt * data.pullEase);
      this.tau += dt * speed(this.pull);
    } else {
      this.pull = 0;
    }
    const burst = this.burstStart === null ? 0 : Math.min((now - this.burstStart) / data.burstMs, 1);
    this.draw(burst);
    if (moving) this.frame = requestAnimationFrame((t) => this.step(t));
  }

  private draw(burst: number): void {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, 3));
    if (this.canvas.width !== Math.round(w * dpr) || this.canvas.height !== Math.round(h * dpr)) {
      this.canvas.width = Math.round(w * dpr);
      this.canvas.height = Math.round(h * dpr);
    }
    const g = this.canvas.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const box = this.host.getBoundingClientRect();
    let tw = 0, th = 0;
    for (const el of this.text) {
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      tw = Math.max(tw, 2 * Math.max(Math.abs(r.left - box.left - w / 2), Math.abs(r.right - box.left - w / 2)));
      th = Math.max(th, 2 * Math.max(Math.abs(r.top - box.top - h / 2), Math.abs(r.bottom - box.top - h / 2)));
    }
    const spots = layout(this.tau, w, h, tw, th, this.pull, burst).map((s, i) => ({ ...s, i }));
    spots.sort((a, b) => a.z - b.z); // far side first
    for (const s of spots) {
      const img = this.badges[s.i];
      if (!img || s.alpha <= 0.01) continue;
      const bw = (img.width / dpr) * s.scale, bh = (img.height / dpr) * s.scale;
      g.globalAlpha = s.alpha * DIM;
      g.drawImage(img, w / 2 + s.x - bw / 2, h / 2 + s.y - bh / 2, bw, bh);
    }
    g.globalAlpha = 1;
  }
}
