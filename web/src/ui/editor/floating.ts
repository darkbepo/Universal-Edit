// Floating windows inside the editor (port of gui/editor/floating.py): movable by their title
// bar, minimised back into their rail icon, closed with ×. Several can be open at once; each
// remembers where it was put (localStorage). The window element is the <dialog> the dialogs
// build (shown with show(), not showModal(), so the picture stays usable behind it).
import { ICONS as TABLER } from "../icon_data";

export type PanelState = "open" | "minimised" | "closed";

interface Panel {
  el: HTMLElement;
  closable: boolean;
  onClose?: () => void;
}

const MARGIN = 8;
const storeKey = (key: string) => `umc-panel-${key}`;

function iconButton(name: string, label: string, fn: () => void): HTMLButtonElement {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "panel-button";
  b.title = label;
  b.setAttribute("aria-label", label);
  b.innerHTML = `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${(TABLER as Record<string, string>)[name]}</svg>`;
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    fn();
  });
  return b;
}

export class FloatingHost {
  private panels = new Map<string, Panel>();
  private top = 10;
  /** Told when a window opens, is minimised or closes (the rail marks its icon). */
  onState: (key: string, state: PanelState) => void = () => {};
  private perKey = new Map<string, (state: PanelState) => void>();
  /** Kept in the top-right corner until moved by hand (re-placed when the editor resizes). */
  private pinned = new Set<string>();
  /** Saved positions waiting for the editor to have a real size (it opens hidden). */
  private pending = new Map<string, [number, number]>();

  /** Listen to one window's state (null stops). */
  onStateFor(key: string, fn: ((state: PanelState) => void) | null): void {
    if (fn) this.perKey.set(key, fn);
    else this.perKey.delete(key);
  }

  private tell(key: string, state: PanelState): void {
    this.onState(key, state);
    this.perKey.get(key)?.(state);
  }

  constructor(private area: HTMLElement) {
    // also covers the editor becoming visible (its size goes from 0 to real)
    new ResizeObserver(() => {
      if (area.clientWidth > 100) {
        for (const [key, pos] of this.pending) {
          const el = this.panels.get(key)?.el;
          if (el) this.place(el, pos);
        }
        this.pending.clear();
      }
      for (const key of this.pinned) this.toCorner(key);
    }).observe(area);
  }

  private toCorner(key: string): void {
    const el = this.panels.get(key)?.el;
    if (el) this.place(el, [this.area.clientWidth - el.offsetWidth - MARGIN, MARGIN]);
  }

  state(key: string): PanelState {
    const p = this.panels.get(key);
    return !p ? "closed" : p.el.hidden ? "minimised" : "open";
  }

  element(key: string): HTMLElement | null {
    return this.panels.get(key)?.el ?? null;
  }

  /** Turn `el` (its first <h2> becomes the title bar) into a window over the editor area. */
  open(key: string, title: string, el: HTMLElement, opts: { closable?: boolean; onClose?: () => void; right?: boolean } = {}): void {
    if (this.panels.has(key)) {
      this.restore(key);
      return;
    }
    const closable = opts.closable ?? true;
    el.querySelector(":scope > h2")?.remove();
    el.classList.add("floating");
    el.dataset.panel = key;
    const label = document.createElement("span");
    label.className = "panel-title-text";
    label.textContent = title;
    const bar = document.createElement("div");
    bar.className = "panel-title";
    bar.append(label, iconButton("minus", "Minimise (back into its icon)", () => this.minimise(key)));
    if (closable) bar.append(iconButton("close", "Close", () => this.close(key)));
    el.prepend(bar);
    this.drag(bar, el, key);
    el.addEventListener("pointerdown", () => this.raise(el), true);
    this.area.append(el);
    if (el instanceof HTMLDialogElement) el.show();
    this.panels.set(key, { el, closable, onClose: opts.onClose });
    let pos: [number, number] | null = null;
    try {
      const saved = localStorage.getItem(storeKey(key));
      if (saved) pos = JSON.parse(saved) as [number, number];
    } catch {
      /* storage blocked: cascade instead */
    }
    const n = this.panels.size - 1;
    if (pos) {
      this.place(el, pos);
      if (this.area.clientWidth <= 100) this.pending.set(key, pos); // place it again once shown
    }
    else if (opts.right) {
      this.pinned.add(key);
      this.toCorner(key);
    } else this.place(el, [MARGIN + 24 * (n % 8), MARGIN + 24 * (n % 8)]);
    this.raise(el);
    this.tell(key, "open");
  }

  setTitle(key: string, title: string): void {
    const t = this.panels.get(key)?.el.querySelector(".panel-title-text");
    if (t) t.textContent = title;
  }

  minimise(key: string): void {
    const p = this.panels.get(key);
    if (!p || p.el.hidden) return;
    p.el.hidden = true;
    this.tell(key, "minimised");
  }

  restore(key: string): void {
    const p = this.panels.get(key);
    if (!p) return;
    p.el.hidden = false;
    if (this.pinned.has(key)) this.toCorner(key);
    else this.place(p.el, [p.el.offsetLeft, p.el.offsetTop]); // the editor may have shrunk meanwhile
    this.raise(p.el);
    this.tell(key, "open");
  }

  /** An icon clicked again: minimise an open window, bring back a minimised one. False when
   *  there is no window under `key` (the caller opens one). */
  toggle(key: string): boolean {
    const s = this.state(key);
    if (s === "open") this.minimise(key);
    else if (s === "minimised") this.restore(key);
    return s !== "closed";
  }

  /** Close a window (× or its own OK / Cancel). The tool options only minimise. */
  close(key: string): void {
    const p = this.panels.get(key);
    if (!p) return;
    if (!p.closable) return this.minimise(key);
    this.panels.delete(key);
    this.remember(key, p.el);
    if (p.el instanceof HTMLDialogElement && p.el.open) p.el.close();
    p.el.remove();
    this.tell(key, "closed");
    p.onClose?.();
  }

  closeAll(keep: string[] = []): void {
    for (const key of [...this.panels.keys()]) if (!keep.includes(key)) this.close(key);
  }

  private raise(el: HTMLElement): void {
    el.style.zIndex = String(++this.top);
  }

  private place(el: HTMLElement, [x, y]: [number, number]): void {
    const w = this.area.clientWidth;
    const h = this.area.clientHeight;
    el.style.left = `${Math.max(MARGIN, Math.min(x, w - el.offsetWidth - MARGIN))}px`;
    el.style.top = `${Math.max(MARGIN, Math.min(y, h - 40))}px`;
  }

  private remember(key: string, el: HTMLElement): void {
    try {
      localStorage.setItem(storeKey(key), JSON.stringify([el.offsetLeft, el.offsetTop]));
    } catch {
      /* fine: just not remembered */
    }
  }

  private drag(bar: HTMLElement, el: HTMLElement, key: string): void {
    let grab: [number, number] | null = null;
    bar.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || (e.target as HTMLElement).closest("button")) return;
      e.preventDefault();
      grab = [e.clientX - el.offsetLeft, e.clientY - el.offsetTop];
      bar.setPointerCapture(e.pointerId);
      bar.classList.add("dragging");
    });
    bar.addEventListener("pointermove", (e) => {
      if (grab) this.place(el, [e.clientX - grab[0], e.clientY - grab[1]]);
    });
    const end = () => {
      if (!grab) return;
      grab = null;
      bar.classList.remove("dragging");
      this.pinned.delete(key); // placed by hand: stays there
      this.remember(key, el);
    };
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
  }
}
