// Small modal dialogs (<dialog>): resize image, canvas size, quick fix / levels / curves, save as, shortcuts.
import type { OptionSpec, Values } from "../../core/options";
import { MAX_SIDE, type EditorDoc } from "../../editor/document";
import { ANCHORS, type Anchor, type Resample } from "../../editor/ops";
import { asEffect, autoLevels as autoLevelsOf, curveTable, histogram as histogramOf, IDENTITY_CURVE, levelsIdentity, NEUTRAL_LEVELS, type Adjustments, type Channel, type Levels } from "../../editor/adjust";
import type { Effect } from "../../editor/effects";
import * as filters from "../../editor/filters";
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

// ------------------------------------------------------------------ adjustments (Adjust menu)

/** What an adjustment window needs from the page: the pixels it changes (for the histogram and
 *  Auto) and a live preview (null = show the original). */
export interface AdjustHost {
  region: { data: Uint8ClampedArray; width: number; height: number } | null;
  preview(e: Effect | null): void;
}

const CHANNEL_LABELS: [Channel, string][] = [["rgb", "All colours"], ["r", "Red"], ["g", "Green"], ["b", "Blue"]];
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";

/** A modal with live preview, "Hold to see the original" and Reset; resolves to the settings. */
async function effectModal(cls: string, title: string, host: AdjustHost, body: (changed: () => void) => { els: HTMLElement[]; read: () => Effect; reset: () => void }): Promise<Effect | null> {
  let timer = 0;
  let holding = false;
  const changed = () => {
    clearTimeout(timer);
    timer = window.setTimeout(() => {
      if (!holding) {
        const e = built.read();
        host.preview(e.identity ? null : e);
      }
    }, 60);
  };
  const built = body(changed);
  changed(); // show the starting settings (Blur opens at 2 px) right away
  const hold = h("button", { class: "btn small", type: "button" }, "Hold to see the original");
  const show = (orig: boolean) => {
    holding = orig;
    if (orig) host.preview(null);
    else changed();
  };
  hold.addEventListener("pointerdown", () => show(true));
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) hold.addEventListener(ev, () => holding && show(false));
  const reset = h("button", { class: "btn small", type: "button" }, "Reset");
  reset.addEventListener("click", () => {
    built.reset();
    changed();
  });
  const r = await modal(`adjust ${cls}`, title, [...built.els, h("div", { class: "row" }, hold, h("div", { class: "spacer" }), reset)], "Apply", built.read);
  clearTimeout(timer);
  host.preview(null);
  return r && !r.value.identity ? r.value : null;
}

/** An adjustment window: the same as effectModal, built from Adjustments. */
function adjustModal(cls: string, title: string, host: AdjustHost, body: (changed: () => void) => { els: HTMLElement[]; read: () => Adjustments; reset: () => void }): Promise<Effect | null> {
  return effectModal(cls, title, host, (changed) => {
    const b = body(changed);
    return { ...b, read: () => asEffect(b.read()) };
  });
}

const QUICK_FIELDS: [keyof Adjustments, string, number, number, number, string][] = [
  ["exposure", "Exposure", -300, 300, 0.01, "Brighter or darker, as if the photo had been taken that way (stops)"],
  ["brightness", "Brightness", -100, 100, 1, ""],
  ["contrast", "Contrast", -100, 100, 1, ""],
  ["temperature", "Temperature", -100, 100, 1, "Cooler (blue) ↔ warmer (orange)"],
  ["tint", "Tint", -100, 100, 1, "Greener ↔ more magenta"],
  ["saturation", "Saturation", -100, 100, 1, ""],
  ["vibrance", "Vibrance", -100, 100, 1, "Boosts dull colours more than strong ones, and skin tones less"],
];

/** Exposure, brightness / contrast, temperature / tint, saturation, vibrance, Auto tone. */
export function quickFixDialog(host: AdjustHost): Promise<Effect | null> {
  return adjustModal("quick-fix", "Quick fix", host, (changed) => {
    const sliders = QUICK_FIELDS.map(([key, label, min, max, scale, tip]) => {
      const s = h("input", { type: "range", min: String(min), max: String(max), value: "0", ariaLabel: label, title: tip });
      const out = h("output", {}, "0");
      const show = () => {
        const v = +s.value * scale;
        out.textContent = v === 0 ? "0" : `${v > 0 ? "+" : ""}${scale < 1 ? v.toFixed(2) : v}`;
      };
      s.addEventListener("input", () => {
        show();
        changed();
      });
      return { key, s, scale, show, row: h("label", { class: "field" }, h("span", {}, label), h("span", { class: "slider-row" }, s, out)) };
    });
    const auto = h("input", { type: "checkbox" });
    let autoLevels: Levels | null = null;
    auto.addEventListener("change", () => {
      autoLevels = auto.checked && host.region ? autoLevelsOf(host.region.data) : null;
      changed();
    });
    const read = (): Adjustments => {
      const a: Adjustments = {};
      for (const sl of sliders) (a as Record<string, number>)[sl.key] = sl.scale < 1 ? Math.round(+sl.s.value) / 100 : +sl.s.value;
      if (autoLevels) a.levels = { rgb: autoLevels };
      return a;
    };
    const reset = () => {
      for (const sl of sliders) {
        sl.s.value = "0";
        sl.show();
      }
      auto.checked = false;
      autoLevels = null;
    };
    return { els: [...sliders.map((x) => x.row), h("label", { class: "check" }, auto, " Auto tone (stretch a dark or flat photo)")], read, reset };
  });
}

function channelSelect(onChange: () => void) {
  const sel = h("select", { ariaLabel: "Channel" });
  for (const [v, l] of CHANNEL_LABELS) sel.append(h("option", { value: v }, l));
  sel.addEventListener("change", onChange);
  return sel;
}

/** A sized canvas for crisp drawing on high-DPI screens. */
function hiDpiCanvas(cls: string, w: number, hgt: number, label: string) {
  const c = h("canvas", { class: cls, ariaLabel: label });
  const dpr = window.devicePixelRatio || 1;
  c.width = w * dpr;
  c.height = hgt * dpr;
  c.style.width = `${w}px`;
  c.style.height = `${hgt}px`;
  const ctx = c.getContext("2d") as CanvasRenderingContext2D;
  ctx.scale(dpr, dpr);
  return { c, ctx, w, h: hgt };
}

function drawHistogram(ctx: CanvasRenderingContext2D, counts: Uint32Array | undefined, x0: number, y0: number, w: number, hgt: number, color: string) {
  if (!counts) return;
  let top = 0;
  for (const c of counts) top = Math.max(top, Math.sqrt(c));
  if (!top) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let v = 0; v < 256; v++) {
    if (!counts[v]) continue;
    const x = x0 + (w * v) / 255;
    ctx.moveTo(x, y0 + hgt);
    ctx.lineTo(x, y0 + hgt - ((hgt - 4) * Math.sqrt(counts[v])) / top);
  }
  ctx.stroke();
}

/** Histogram with draggable black / mid / white markers, number fields, channels, Auto. */
export function levelsDialog(host: AdjustHost): Promise<Effect | null> {
  return adjustModal("levels", "Levels", host, (changed) => {
    const hist = host.region ? histogramOf(host.region.data) : null;
    const levels = new Map<Channel, Levels>();
    const channel = channelSelect(() => show());
    const current = () => levels.get(channel.value as Channel) ?? { ...NEUTRAL_LEVELS };
    const autoBtn = h("button", { class: "btn small", type: "button", title: "Stretch the tones (ignores the darkest and brightest 0.5 %)" }, "Auto");
    const g = hiDpiCanvas("histogram", 320, 130, "Histogram");
    const PAD = 7;
    const inner = g.w - 2 * PAD;
    const xOf = (v: number) => PAD + (inner * v) / 255;
    const valueOf = (x: number) => Math.min(255, Math.max(0, ((x - PAD) * 255) / inner));
    const field = (label: string, min: number, max: number, step: number) => {
      const input = h("input", { type: "number", min: String(min), max: String(max), step: String(step), ariaLabel: label });
      input.addEventListener("input", () => read());
      return input;
    };
    const black = field("Input black", 0, 254, 1);
    const gamma = field("Mid-tones", 0.1, 9.99, 0.05);
    const white = field("Input white", 1, 255, 1);
    const outBlack = field("Output black", 0, 255, 1);
    const outWhite = field("Output white", 0, 255, 1);
    const markers = (l: Levels) => ({ black: l.black, mid: l.black + (l.white - l.black) * 0.5 ** l.gamma, white: l.white });
    const draw = () => {
      const { ctx } = g;
      const hb = g.h - 14;
      ctx.clearRect(0, 0, g.w, g.h);
      ctx.fillStyle = cssVar("--surface-alt");
      ctx.fillRect(0, 0, g.w, hb);
      drawHistogram(ctx, hist?.[channel.value as Channel], PAD, 0, inner, hb, cssVar("--muted"));
      for (const [name, v] of Object.entries(markers(current()))) {
        const x = xOf(v);
        ctx.fillStyle = { black: "#000", mid: "#808080", white: "#fff" }[name] as string;
        ctx.strokeStyle = cssVar("--text");
        ctx.beginPath();
        ctx.moveTo(x, hb + 1);
        ctx.lineTo(x - 6, hb + 12);
        ctx.lineTo(x + 6, hb + 12);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      }
    };
    const show = () => {
      const l = current();
      black.value = String(l.black);
      white.value = String(l.white);
      gamma.value = l.gamma.toFixed(2);
      outBlack.value = String(l.outBlack);
      outWhite.value = String(l.outWhite);
      draw();
    };
    const set = (l: Levels) => {
      levels.set(channel.value as Channel, l);
      show();
      changed();
    };
    const read = () => {
      const num = (input: HTMLInputElement, empty: number) => (input.value.trim() === "" || Number.isNaN(+input.value) ? empty : +input.value);
      const b = Math.min(254, Math.max(0, Math.round(num(black, 0))));
      const w = Math.max(b + 1, Math.min(255, Math.round(num(white, 255))));
      const gm = Math.min(9.99, Math.max(0.1, num(gamma, 1)));
      const ob = Math.min(255, Math.max(0, Math.round(num(outBlack, 0))));
      const ow = Math.min(255, Math.max(0, Math.round(num(outWhite, 255))));
      if (+white.value !== w && document.activeElement !== white) white.value = String(w); // show what is applied
      levels.set(channel.value as Channel, { black: b, white: w, gamma: Math.round(gm * 100) / 100, outBlack: ob, outWhite: ow });
      draw();
      changed();
    };
    let drag: string | null = null;
    const dragTo = (e: PointerEvent) => {
      const v = valueOf(e.offsetX);
      const l = current();
      if (drag === "black") set({ ...l, black: Math.round(Math.min(v, l.white - 1)) });
      else if (drag === "white") set({ ...l, white: Math.round(Math.max(v, l.black + 1)) });
      else if (drag === "mid") {
        const t = Math.min(0.99, Math.max(0.01, (v - l.black) / Math.max(1, l.white - l.black)));
        set({ ...l, gamma: Math.round((Math.log(t) / Math.log(0.5)) * 100) / 100 });
      }
    };
    g.c.addEventListener("pointerdown", (e) => {
      const m = Object.entries(markers(current()));
      drag = m.reduce((best, cur) => (Math.abs(xOf(cur[1]) - e.offsetX) < Math.abs(xOf(best[1]) - e.offsetX) ? cur : best))[0];
      g.c.setPointerCapture(e.pointerId);
      dragTo(e);
    });
    g.c.addEventListener("pointermove", (e) => drag && dragTo(e));
    g.c.addEventListener("pointerup", () => (drag = null));
    autoBtn.addEventListener("click", () => {
      if (!host.region) return;
      const cur = current();
      set({ ...autoLevelsOf(host.region.data, channel.value as Channel), gamma: cur.gamma, outBlack: cur.outBlack, outWhite: cur.outWhite });
    });
    show();
    const row = (label: string, ...kids: HTMLElement[]) => h("div", { class: "field levels-row" }, h("span", {}, label), h("span", { class: "with-unit" }, ...kids));
    return {
      els: [
        h("div", { class: "row adjust-top" }, h("span", {}, "Channel"), channel, h("div", { class: "spacer" }), autoBtn),
        g.c,
        row("Input", h("small", {}, "Black"), black, h("small", {}, "Mid-tones"), gamma, h("small", {}, "White"), white),
        row("Output", h("small", {}, "Black"), outBlack, h("small", {}, "White"), outWhite),
      ],
      read: () => {
        const out: Partial<Record<Channel, Levels>> = {};
        for (const [ch, l] of levels) if (!levelsIdentity(l)) out[ch] = l;
        return { levels: out };
      },
      reset: () => {
        levels.clear();
        show();
      },
    };
  });
}

/** Click to add a point, drag to move it, drag it off the graph to remove it; channels. */
export function curvesDialog(host: AdjustHost): Promise<Effect | null> {
  return adjustModal("curves", "Curves", host, (changed) => {
    const hist = host.region ? histogramOf(host.region.data) : null;
    const curves = new Map<Channel, [number, number][]>();
    const channel = channelSelect(() => draw());
    const points = () => curves.get(channel.value as Channel) ?? IDENTITY_CURVE.map((p) => [...p] as [number, number]);
    const g = hiDpiCanvas("curve-editor", 300, 300, "Curve");
    const PAD = 8;
    const side = g.w - 2 * PAD;
    const toPx = (x: number, y: number): [number, number] => [PAD + (side * x) / 255, PAD + side * (1 - y / 255)];
    const toCurve = (px: number, py: number): [number, number] => [((px - PAD) * 255) / side, (1 - (py - PAD) / side) * 255];
    const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
    let drag: number | null = null;
    let removing = false;
    const draw = () => {
      const { ctx } = g;
      ctx.clearRect(0, 0, g.w, g.h);
      ctx.fillStyle = cssVar("--surface-alt");
      ctx.fillRect(PAD, PAD, side, side);
      drawHistogram(ctx, hist?.[channel.value as Channel], PAD, PAD, side, side, cssVar("--border"));
      ctx.strokeStyle = cssVar("--border");
      ctx.beginPath();
      for (let i = 1; i < 4; i++) {
        ctx.moveTo(PAD + (side * i) / 4, PAD);
        ctx.lineTo(PAD + (side * i) / 4, PAD + side);
        ctx.moveTo(PAD, PAD + (side * i) / 4);
        ctx.lineTo(PAD + side, PAD + (side * i) / 4);
      }
      ctx.moveTo(...toPx(0, 0));
      ctx.lineTo(...toPx(255, 255));
      ctx.stroke();
      const t = curveTable(points());
      ctx.strokeStyle = cssVar("--accent");
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(...toPx(0, t[0]));
      for (let x = 1; x < 256; x++) ctx.lineTo(...toPx(x, t[x]));
      ctx.stroke();
      ctx.lineWidth = 1;
      points().forEach(([x, y], i) => {
        ctx.fillStyle = cssVar("--surface");
        ctx.strokeStyle = removing && i === drag ? cssVar("--danger") : cssVar("--accent");
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(...toPx(x, y), 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      });
      ctx.lineWidth = 1;
    };
    const setPoints = (pts: [number, number][]) => {
      curves.set(channel.value as Channel, pts);
      draw();
      changed();
    };
    const pointAt = (px: number, py: number) => {
      let best: [number, number] | null = null;
      points().forEach(([x, y], i) => {
        const [qx, qy] = toPx(x, y);
        const d = Math.abs(qx - px) + Math.abs(qy - py);
        if (d <= 14 && (!best || d < best[0])) best = [d, i];
      });
      return best ? (best as [number, number])[1] : null;
    };
    const local = (e: PointerEvent): [number, number] => {
      const r = g.c.getBoundingClientRect();
      return [e.clientX - r.left, e.clientY - r.top];
    };
    g.c.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      const [px, py] = local(e);
      let i = pointAt(px, py);
      if (i === null && (px < PAD || px > PAD + side || py < PAD || py > PAD + side)) return; // beside the graph
      if (i === null) {
        const [x, y] = toCurve(px, py).map(clamp);
        const pts = points().filter((p) => p[0] !== x);
        pts.push([x, y]);
        pts.sort((a, b) => a[0] - b[0]);
        setPoints(pts);
        i = pts.findIndex((p) => p[0] === x);
      }
      drag = i;
      g.c.setPointerCapture(e.pointerId);
    });
    g.c.addEventListener("pointermove", (e) => {
      if (drag === null) return;
      const [px, py] = local(e);
      const out = px < PAD - 24 || px > PAD + side + 24 || py < PAD - 24 || py > PAD + side + 24;
      const pts = points();
      removing = out && drag > 0 && drag < pts.length - 1; // never the two end points
      if (!removing) {
        const [x, y] = toCurve(px, py);
        const lo = drag > 0 ? pts[drag - 1][0] + 1 : 0;
        const hi = drag < pts.length - 1 ? pts[drag + 1][0] - 1 : 255;
        pts[drag] = [Math.max(lo, Math.min(hi, Math.round(x))), clamp(y)];
        setPoints(pts);
      } else draw();
    });
    const up = () => {
      if (drag !== null && removing) {
        const pts = points();
        pts.splice(drag, 1);
        setPoints(pts);
      }
      drag = null;
      removing = false;
      draw();
    };
    g.c.addEventListener("pointerup", up);
    g.c.addEventListener("pointercancel", up);
    draw();
    return {
      els: [
        h("div", { class: "row adjust-top" }, h("span", {}, "Channel"), channel),
        g.c,
        h("div", { class: "hint" }, "Click to add a point · drag to move · drag off the graph to remove"),
      ],
      read: () => {
        const out: Partial<Record<Channel, [number, number][]>> = {};
        for (const [ch, pts] of curves) if (!(pts.length === 2 && pts[0][0] === 0 && pts[0][1] === 0 && pts[1][0] === 255 && pts[1][1] === 255)) out[ch] = pts.map((p) => [...p] as [number, number]);
        return { curves: out };
      },
      reset: () => {
        curves.clear();
        draw();
      },
    };
  });
}

// ------------------------------------------------------------------ filters (Filters menu)

function rangeRow(label: string, min: number, max: number, value: number, scale: number, unit: string, tip: string, changed: () => void) {
  const s = h("input", { type: "range", min: String(min), max: String(max), value: String(Math.round(value / scale)), ariaLabel: label, title: tip });
  const out = h("output");
  const show = () => (out.textContent = `${scale < 1 ? (+s.value * scale).toFixed(1) : +s.value * scale}${unit}`);
  s.addEventListener("input", () => {
    show();
    changed();
  });
  show();
  return {
    value: () => +s.value * scale,
    set: (v: number) => {
      s.value = String(Math.round(v / scale));
      show();
    },
    row: h("label", { class: "field" }, h("span", {}, label), h("span", { class: "slider-row" }, s, out)),
  };
}

export function blurDialog(host: AdjustHost): Promise<Effect | null> {
  return effectModal("blur", "Blur", host, (changed) => {
    const radius = rangeRow("Radius", 0, 500, 2, 0.1, " px", "How far colours spread (px)", changed);
    return { els: [radius.row], read: () => filters.blur(Math.round(radius.value() * 10) / 10), reset: () => radius.set(0) };
  });
}

export function sharpenDialog(host: AdjustHost): Promise<Effect | null> {
  return effectModal("sharpen", "Sharpen", host, (changed) => {
    const amount = rangeRow("Amount", 0, 500, 100, 1, " %", "How much stronger edges get", changed);
    const radius = rangeRow("Radius", 1, 100, 1, 0.1, " px", "How wide the edges are that get sharper (px)", changed);
    const threshold = rangeRow("Threshold", 0, 64, 0, 1, "", "Leave small differences alone (keeps skin and sky smooth)", changed);
    return {
      els: [amount.row, radius.row, threshold.row],
      read: () => filters.sharpen(Math.round(amount.value()), Math.round(radius.value() * 10) / 10, Math.round(threshold.value())),
      reset: () => {
        amount.set(0);
        radius.set(1);
        threshold.set(0);
      },
    };
  });
}

/** One-click looks: a tile per look (a small preview of the picture), plus a strength. */
export function looksDialog(host: AdjustHost): Promise<Effect | null> {
  return effectModal("looks", "Looks", host, (changed) => {
    const TILE = 92;
    // a small copy of the picture for the tiles
    let thumb = { data: new Uint8ClampedArray([128, 128, 128, 255]), width: 1, height: 1 };
    if (host.region) {
      const r = host.region;
      const k = Math.max(1, Math.ceil(Math.max(r.width, r.height) / TILE));
      const w = Math.max(1, Math.floor(r.width / k));
      const ht = Math.max(1, Math.floor(r.height / k));
      const d = new Uint8ClampedArray(w * ht * 4);
      for (let y = 0; y < ht; y++) for (let x = 0; x < w; x++) d.set(r.data.subarray(((y * k) * r.width + x * k) * 4, ((y * k) * r.width + x * k) * 4 + 4), (y * w + x) * 4);
      thumb = { data: d, width: w, height: ht };
    }
    let chosen = "";
    const tiles = new Map<string, HTMLButtonElement>();
    const grid = h("div", { class: "look-grid", role: "radiogroup", ariaLabel: "Looks" });
    for (const [name, def] of Object.entries(filters.LOOKS)) {
      const c = h("canvas", { width: thumb.width, height: thumb.height, class: "look-thumb" });
      (c.getContext("2d") as CanvasRenderingContext2D).putImageData(new ImageData(filters.look(name).apply(thumb.data, thumb.width, thumb.height) as Uint8ClampedArray<ArrayBuffer>, thumb.width, thumb.height), 0, 0);
      const b = h("button", { type: "button", class: "look-tile", role: "radio", ariaLabel: def.label }, c, h("span", {}, def.label));
      b.setAttribute("aria-checked", "false");
      b.addEventListener("click", () => {
        chosen = name;
        for (const [n, t] of tiles) t.setAttribute("aria-checked", String(n === name));
        changed();
      });
      tiles.set(name, b);
      grid.append(b);
    }
    const strength = rangeRow("Strength", 0, 100, 100, 1, " %", "How much of the look", changed);
    return {
      els: [grid, strength.row],
      read: () => filters.look(chosen, Math.round(strength.value()) / 100),
      reset: () => {
        chosen = "";
        for (const t of tiles.values()) t.setAttribute("aria-checked", "false");
        strength.set(100);
      },
    };
  });
}

/** Feather: soften the selection's edge by a radius (px); null = cancelled. */
export async function featherDialog(): Promise<number | null> {
  const r = num("Radius", 5, 1, 250, "px");
  const res = await modal("feather", "Feather selection", [r.row], "Apply", () => Math.max(0.5, +r.input.value || 0));
  return res?.value ?? null;
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

/** The "?" window: every keyboard shortcut, grouped. */
export function shortcutsDialog(tools: { shortcut: string; label: string }[]): Promise<void> {
  return import("./shortcuts").then(({ shortcutRows }) => new Promise<void>((resolve) => {
    const cols = h("div", { class: "shortcut-cols" });
    for (const [group, rows] of shortcutRows(tools)) {
      const table = h("table", { class: "shortcuts" }, h("caption", {}, group));
      for (const [keys, label] of rows) table.append(h("tr", {}, h("td", {}, h("kbd", {}, keys)), h("td", {}, label)));
      cols.append(table);
    }
    const close = h("button", { class: "btn", type: "button" }, "Close");
    const dlg = h("dialog", { class: "editor-dialog shortcuts-dialog", ariaLabel: "Keyboard shortcuts" }, h("h2", {}, "Keyboard shortcuts"), cols, h("div", { class: "row" }, h("div", { class: "spacer" }), close));
    const finish = () => {
      dlg.close();
      dlg.remove();
      resolve();
    };
    close.addEventListener("click", finish);
    dlg.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish();
    });
    dlg.addEventListener("keydown", (e) => e.stopPropagation()); // never reach the editor's shortcuts
    document.body.append(dlg);
    dlg.showModal();
    close.focus();
  }));
}
