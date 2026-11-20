// What one frame of a video project shows: which clips, where, how strongly. Pure maths, no DOM —
// draw.ts carries a Frame out on a Canvas 2D context. The reference is the desktop's FFmpeg recipe
// (src/umc/videoedit/render.py: _geometry, _fit, _video_chain, _fade_filters, _xfade); every number
// here is checked against colours FFmpeg itself made (tests/fixtures/videoedit_cases.json).
import { clipDuration, layout, outputFps, outputSize, type MainItem, type Project } from "./project";

/** FFmpeg `eq` numbers: brightness b/200, contrast 1 + c/100, saturation 1 + s/100. */
export interface Grade {
  brightness: number;
  contrast: number;
  saturation: number;
}
export interface Layer {
  itemId: string;
  sourceId: string;
  kind: "video" | "picture";
  /** Source seconds (video: in_ + u · speed, kept within [in_, out]); 0 for pictures. */
  time: number;
  /** Fractions 0…1 cut off each side before rotating (pictures: all 0). */
  crop: { left: number; top: number; right: number; bottom: number };
  rotate: 0 | 90 | 180 | 270;
  flipH: boolean;
  flipV: boolean;
  fit: string;
  /** null when brightness, contrast and saturation are all 0. */
  grade: Grade | null;
  /** 0…1: how much the item's own fades cover it with the background. */
  fade: number;
}
/** A layer is a whole frame: background, the picture placed by `layerRect`, then the fade. */
export interface Placed {
  layer: Layer;
  /** "over": alpha over what is below; "add": added onto black. */
  weight: number;
  /** Only the columns [from, to) of the frame show this layer (wipes). */
  clipX: [number, number] | null;
  /** The layer is drawn shifted this many pixels to the right (slides). */
  offsetX: number;
}
export interface Frame {
  width: number;
  height: number;
  background: string;
  mode: "over" | "add";
  /** Levels taken off every channel after the layers (fadeblack, see `mixWeights`), clamped at 0. */
  lift: number;
  layers: Placed[];
}
export interface Rect {
  sx: number;
  sy: number;
  sw: number;
  sh: number;
  dx: number;
  dy: number;
  dw: number;
  dh: number;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const noNegZero = (v: number) => v + 0;

/** FFmpeg counts every time and duration in whole frames, rounding halves up (av_rescale_q):
 *  a 0.5 s fade at 25 fps lasts 13 frames, a fade-out from 1.5 s starts at frame 38. */
const snap = (seconds: number, fps: number) => Math.floor(seconds * fps + 0.5 + 1e-9) / fps;

/** fadeblack's black is Y = 0 (vf_xfade fills black with 0, not video black 16), which in RGB is
 *  16 · 255 / 219 levels below black: a mix towards it darkens by that much. */
const BLACK_LIFT = (16 * 255) / 219;

function smoothstep(e0: number, e1: number, x: number): number {
  const s = clamp((x - e0) / (e1 - e0), 0, 1);
  return s * s * (3 - 2 * s);
}

/** How the two clips of a transition mix; `progress` P runs 1 → 0 (FFmpeg xfade). For "fadeblack"
 *  (vf_xfade: mix(mix(a, black, ss(0.8, 1, P)), mix(black, b, ss(0.2, 1, P)), P), mix(x, y, m) =
 *  x·m + y·(1 − m)) the weights are a = P · ss(0.8, 1, P), b = (1 − P)(1 − ss(0.2, 1, P)) and the
 *  rest (1 − a − b) goes to FFmpeg's black, which is `lift` levels below RGB black. Matched to the
 *  fixture within 4 levels; without the lift it misses by up to 24. */
export function mixWeights(kind: string, progress: number): { mode: "over" | "add"; a: number; b: number; lift: number } {
  const p = clamp(progress, 0, 1);
  if (kind === "fadeblack") {
    const a = p * smoothstep(0.8, 1, p);
    const b = (1 - p) * (1 - smoothstep(0.2, 1, p));
    return { mode: "add", a, b, lift: noNegZero(BLACK_LIFT * (1 - a - b)) };
  }
  if (kind === "fade") return { mode: "over", a: 1, b: noNegZero(1 - p), lift: 0 };
  return { mode: "over", a: 1, b: 1, lift: 0 }; // wipes and slides: both whole, placed apart
}

/** The item's eq numbers, or null when it has no grade. */
export function gradeGrade(item: MainItem): Grade | null {
  if (!item.brightness && !item.contrast && !item.saturation) return null;
  return { brightness: item.brightness / 200, contrast: 1 + item.contrast / 100, saturation: 1 + item.saturation / 100 };
}

/** One plane of FFmpeg's eq filter (vf_eq.c process_c): its values pass av_clipf, so they are
 *  32-bit floats (brightness −0.2 → 79, not 80, below), and the maths is integer. */
function eqPlane(v: number, contrast: number, brightness: number): number {
  const c = Math.fround(contrast);
  const b = Math.fround(brightness);
  if (c === 1 && b === 0) return v;
  const c4 = Math.trunc(c * 256 * 16);
  const br = Math.trunc((Math.trunc(100 * b + 100) * 511) / 200) - 128 - Math.trunc(c4 / 32);
  return clamp(((v * c4) >> 12) + br, 0, 255);
}

/** A colour through FFmpeg's eq on yuv420p: RGB → BT.601 limited-range YUV (rounded), eq on Y
 *  (contrast, brightness) and on U / V (saturation as their contrast), back to RGB. Within 2
 *  levels of every fixture case (swscale's own conversions round a little differently). */
export function gradeRgb(rgb: [number, number, number], g: Grade): [number, number, number] {
  const [r, gr, b] = rgb;
  let y = Math.round(16 + (65.481 * r + 128.553 * gr + 24.966 * b) / 255);
  let u = Math.round(128 + (-37.797 * r - 74.203 * gr + 112 * b) / 255);
  let v = Math.round(128 + (112 * r - 93.786 * gr - 18.214 * b) / 255);
  y = eqPlane(y, g.contrast, g.brightness);
  u = eqPlane(u, g.saturation, 0);
  v = eqPlane(v, g.saturation, 0);
  const c = 1.164383 * (y - 16);
  const out = [c + 1.596027 * (v - 128), c - 0.391762 * (u - 128) - 0.812968 * (v - 128), c + 2.017232 * (u - 128)];
  return out.map((x) => clamp(Math.round(x), 0, 255)) as [number, number, number];
}

/** Where a srcW × srcH picture goes in an outW × outH frame (render.py `_fit`). letterbox: fit
 *  inside, centred, sizes rounded like FFmpeg's scale; crop: fill and cut the overflow, centred;
 *  stretch: the whole frame. Source rect (s*) in picture pixels, destination (d*) in frame pixels. */
export function fitRect(srcW: number, srcH: number, outW: number, outH: number, fit: string): Rect {
  if (fit === "stretch") return { sx: 0, sy: 0, sw: srcW, sh: srcH, dx: 0, dy: 0, dw: outW, dh: outH };
  if (fit === "crop") {
    const scale = Math.max(outW / srcW, outH / srcH);
    const sw = outW / scale;
    const sh = outH / scale;
    return { sx: (srcW - sw) / 2, sy: (srcH - sh) / 2, sw, sh, dx: 0, dy: 0, dw: outW, dh: outH };
  }
  const scale = Math.min(outW / srcW, outH / srcH);
  const dw = Math.min(outW, Math.round(srcW * scale));
  const dh = Math.min(outH, Math.round(srcH * scale));
  return { sx: 0, sy: 0, sw: srcW, sh: srcH, dx: Math.floor((outW - dw) / 2), dy: Math.floor((outH - dh) / 2), dw, dh };
}

/** `fitRect` for a layer whose source is srcW × srcH: the crop is cut first, then a 90° / 270°
 *  rotation swaps the sides (the order of `_geometry`). The source rect is in that cropped and
 *  turned picture. */
export function layerRect(layer: Layer, srcW: number, srcH: number, outW: number, outH: number): Rect {
  const { left, top, right, bottom } = layer.crop;
  const w = srcW * (1 - left - right);
  const h = srcH * (1 - top - bottom);
  const turned = layer.rotate === 90 || layer.rotate === 270;
  return fitRect(turned ? h : w, turned ? w : h, outW, outH, layer.fit);
}

/** 1 − kin · kout: kin rises over fade_in from the clip's start, kout falls over fade_out up to
 *  its end; each 1 when its fade is 0. Times snapped to frames like FFmpeg's fade filter. */
function fadeAmount(u: number, dur: number, fadeIn: number, fadeOut: number, fps: number): number {
  const kin = fadeIn > 0 ? clamp(u / snap(fadeIn, fps), 0, 1) : 1;
  const kout = fadeOut > 0 ? 1 - clamp((u - snap(dur - fadeOut, fps)) / snap(fadeOut, fps), 0, 1) : 1;
  return noNegZero(1 - kin * kout);
}

function layerOf(item: MainItem, u: number, fps: number): Layer {
  const video = item.type === "video";
  return {
    itemId: item.id,
    sourceId: item.source,
    kind: video ? "video" : "picture",
    time: video ? clamp(item.in_ + u * item.speed, item.in_, item.out) : 0,
    crop: video
      ? { left: item.crop_left / 100, top: item.crop_top / 100, right: item.crop_right / 100, bottom: item.crop_bottom / 100 }
      : { left: 0, top: 0, right: 0, bottom: 0 },
    rotate: ([0, 90, 180, 270].includes(item.rotate) ? item.rotate : 0) as Layer["rotate"],
    flipH: item.flip_h,
    flipV: item.flip_v,
    fit: item.fit,
    grade: gradeGrade(item),
    fade: fadeAmount(u, clipDuration(item), item.fade_in, item.fade_out, fps),
  };
}

/** What the frame at project time `t` shows, drawn first-clip-first. */
export function frameAt(p: Project, t: number, size?: [number, number]): Frame {
  const [width, height] = size ?? outputSize(p);
  const frame: Frame = { width, height, background: p.settings.background, mode: "over", lift: 0, layers: [] };
  const main = p.main;
  if (!main.length || t < 0) return frame;
  const spans = layout(p);
  const span = (k: number) => spans.get(main[k].id)!;
  if (t >= span(main.length - 1).end) return frame;
  const fps = outputFps(p);

  let i = 0;
  for (let k = 1; k < main.length; k++) if (span(k).start <= t) i = k;
  const item = main[i];
  const tr = item.transition_in;
  const start = snap(span(i).start, fps);
  const d = snap(tr.duration, fps);
  // FFmpeg's window [start, start + d] in whole frames, which can reach a frame past the first clip
  const inTransition = i > 0 && tr.kind !== "none" && t <= Math.max(span(i - 1).end, start + d);
  if (!inTransition) {
    frame.layers.push({ layer: layerOf(item, t - span(i).start, fps), weight: 1, clipX: null, offsetX: 0 });
    return frame;
  }

  const progress = clamp(1 - (t - start) / d, 0, 1);
  const mix = mixWeights(tr.kind, progress);
  frame.mode = mix.mode;
  frame.lift = mix.lift;
  const first: Placed = { layer: layerOf(main[i - 1], t - span(i - 1).start, fps), weight: mix.a, clipX: null, offsetX: 0 };
  const second: Placed = { layer: layerOf(item, t - span(i).start, fps), weight: mix.b, clipX: null, offsetX: 0 };
  if (tr.kind === "wipeleft") second.clipX = [width * progress, width];
  else if (tr.kind === "wiperight") second.clipX = [0, width * (1 - progress)];
  else if (tr.kind === "slideleft") {
    first.offsetX = noNegZero(-width * (1 - progress));
    second.offsetX = width * progress;
  } else if (tr.kind === "slideright") {
    first.offsetX = width * (1 - progress);
    second.offsetX = noNegZero(-width * progress);
  }
  frame.layers.push(first, second);
  return frame;
}
