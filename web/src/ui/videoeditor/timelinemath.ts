// The timeline's geometry, hit testing, snapping, drag, zoom and ruler (port of
// src/umc/gui/videoedit/timeline.py minus painting). No DOM: the canvas view only paints and
// forwards pointer events. Same numbers and tie rules as the desktop.

import * as edits from "../../videoedit/edits";
import { outputFps, type Project, type Span } from "../../videoedit/project";

export const SNAP_PX = 8;
export const DRAG_PX = 3;
export const MIN_PPS = 5.0;
export const MAX_PPS = 800.0;
export const DEFAULT_PPS = 80.0;
export const ZOOM_STEP = 1.25;
export const FOLLOW_LEAD = 0.05;

export interface Metrics { header: number; ruler: number; textH: number; mainH: number; musicH: number; edge: number; handle: number; handleH: number }
export const DESKTOP: Metrics = { header: 80, ruler: 24, textH: 28, mainH: 64, musicH: 40, edge: 6, handle: 12, handleH: 20 };

export interface Rect { x: number; y: number; w: number; h: number }
export interface Row { kind: "text" | "main" | "music"; row: number; y: number; h: number }
export interface View { pps: number; scroll: number; width: number; playhead: number; m: Metrics }
export type Part = "body" | "left" | "right" | "transition";
export interface Hit { itemId: string; part: Part }
export interface Drag { id: string; part: Part; span: Span; ox: number; oy: number; x: number; y: number; moved: boolean; alt: boolean }

// ---- geometry ----------------------------------------------------------------------------
export function rows(p: Project, m: Metrics): Row[] {
  const out: Row[] = [];
  let y = m.ruler;
  for (let r = 0; r < p.text_rows.length; r++) { out.push({ kind: "text", row: r, y, h: m.textH }); y += m.textH; }
  out.push({ kind: "main", row: 0, y, h: m.mainH });
  y += m.mainH;
  for (let r = 0; r < p.music_rows.length; r++) { out.push({ kind: "music", row: r, y, h: m.musicH }); y += m.musicH; }
  return out;
}

export function totalHeight(rs: Row[]): number {
  const r = rs[rs.length - 1];
  return r.y + r.h;
}

export function xFor(v: View, t: number): number {
  return Math.round((t - v.scroll) * v.pps + v.m.header);
}
export function tFor(v: View, x: number): number {
  return (x - v.m.header) / v.pps + v.scroll;
}

export function rowAt(rs: Row[], y: number): Row | null {
  for (const r of rs) if (r.y <= y && y < r.y + r.h) return r;
  return null;
}

function rowY(rs: Row[], kind: string, row: number): number {
  for (const r of rs) if (r.kind === kind && r.row === row) return r.y;
  throw new Error(`${kind} ${row}`);
}

export function spanRect(v: View, rs: Row[], s: Span): Rect {
  const y = rowY(rs, s.row_kind, s.row);
  const h = { main: v.m.mainH, text: v.m.textH, music: v.m.musicH }[s.row_kind];
  const x0 = xFor(v, s.start);
  const x1 = xFor(v, s.end);
  return { x: x0, y: y + 2, w: Math.max(2, x1 - x0), h: h - 4 };
}

export function addRowRect(rs: Row[], kind: "text" | "music", m: Metrics): Rect {
  return { x: m.header - 22, y: rowY(rs, kind, 0) + 4, w: 18, h: 18 };
}

// ---- hit testing -------------------------------------------------------------------------
/** Pixel x of the boundary before main clip `index` (centre of any overlap). */
export function boundaryX(v: View, p: Project, spans: Map<string, Span>, index: number): number | null {
  if (index <= 0 || index >= p.main.length) return null;
  const cur = spans.get(p.main[index].id)!;
  const prev = spans.get(p.main[index - 1].id)!;
  return xFor(v, (cur.start + prev.end) / 2);
}

export function hitTest(v: View, p: Project, spans: Map<string, Span>, rs: Row[], x: number, y: number): Hit | null {
  if (x < v.m.header) return null;
  const row = rowAt(rs, y);
  if (!row) return null;
  if (row.kind === "main" && y - row.y < v.m.handleH) {
    for (let i = 0; i < p.main.length; i++) {
      const bx = boundaryX(v, p, spans, i);
      if (bx !== null && Math.abs(x - bx) <= Math.floor(v.m.handle / 2)) return { itemId: p.main[i].id, part: "transition" };
    }
  }
  // Edges first, over every item in the row, nearest wins; a tie goes to the earlier clip's
  // right edge. Only then do bodies count (the last one is painted on top).
  let best: { dist: number; hit: Hit } | null = null;
  const bodies: string[] = [];
  for (const [id, s] of spans) {
    if (s.row_kind !== row.kind || s.row !== row.row) continue;
    const x0 = xFor(v, s.start);
    const x1 = xFor(v, s.end);
    const edge = Math.min(v.m.edge, Math.floor((x1 - x0) / 3));
    const cands: [number, Part][] = [
      [Math.abs(x - x1) + (x > x1 ? 0.5 : 0), "right"],
      [Math.abs(x - x0) + (x < x0 ? 0.5 : 0), "left"],
    ];
    for (const [dist, part] of cands) {
      if (dist <= edge && (best === null || dist < best.dist)) best = { dist, hit: { itemId: id, part } };
    }
    if (x0 <= x && x <= x1) bodies.push(id);
  }
  if (best) return best.hit;
  return bodies.length ? { itemId: bodies[bodies.length - 1], part: "body" } : null;
}

export function selectionFor(hit: Hit | null): string | null {
  if (!hit) return null;
  return hit.part === "transition" ? `tr:${hit.itemId}` : hit.itemId;
}

// ---- snapping ----------------------------------------------------------------------------
export function snap(v: View, spans: Map<string, Span>, t: number, exclude: string, alt: boolean): number {
  if (alt) return t;
  let best = t;
  let dist = SNAP_PX / v.pps;
  const points = [v.playhead, 0.0];
  for (const [id, s] of spans) if (id !== exclude) points.push(s.start, s.end);
  for (const q of points) {
    if (Math.abs(q - t) <= dist) { best = q; dist = Math.abs(q - t); }
  }
  return best;
}

/** Snap the start or the end of the moved item, whichever is nearer to a snap point. */
export function snapMove(v: View, spans: Map<string, Span>, start: number, dur: number, exclude: string, alt: boolean): number {
  const a = snap(v, spans, start, exclude, alt) - start;
  const b = snap(v, spans, start + dur, exclude, alt) - (start + dur);
  if (a === 0 && b === 0) return start;
  if (a === 0 || (b !== 0 && Math.abs(b) < Math.abs(a))) return start + b;
  return start + a;
}

// ---- drag --------------------------------------------------------------------------------
export function dragMoved(d: Drag): boolean {
  return Math.abs(d.x - d.ox) + Math.abs(d.y - d.oy) >= DRAG_PX;
}

const dt = (v: View, d: Drag) => (d.x - d.ox) / v.pps;

/** Where the dragged main clip would land: after every other clip centred before it. */
export function mainIndex(v: View, p: Project, spans: Map<string, Span>, d: Drag): number {
  const t = tFor(v, d.x);
  let index = 0;
  for (const m of p.main) {
    if (m.id === d.id) continue;
    const s = spans.get(m.id)!;
    if ((s.start + s.end) / 2 < t) index++;
  }
  return index;
}

export function dragResult(v: View, p: Project, spans: Map<string, Span>, rs: Row[], d: Drag): { project: Project; label: string } | null {
  const span = d.span;
  const delta = dt(v, d);
  let out: { project: Project; label: string };
  try {
    if (d.part === "left" || d.part === "right") {
      const edge = d.part === "left" ? "start" : "end";
      const cur = d.part === "left" ? span.start : span.end;
      const target = snap(v, spans, cur + delta, d.id, d.alt);
      out = { project: edits.trim(p, d.id, edge, target - cur), label: "Trim clip" };
    } else if (d.part === "transition") {
      return null;
    } else if (span.row_kind === "main") {
      out = { project: edits.moveMain(p, d.id, mainIndex(v, p, spans, d)), label: "Move clip" };
    } else {
      const row = rowAt(rs, d.y);
      const newRow = row && row.kind === span.row_kind ? row.row : span.row;
      const start = snapMove(v, spans, Math.max(0, span.start + delta), span.end - span.start, d.id, d.alt);
      const label = span.row_kind === "text" ? "Move text" : "Move music";
      out = { project: edits.moveItem(p, d.id, start, newRow), label };
    }
  } catch (e) {
    if (e instanceof edits.EditError) return null; // refused: cancel the drag silently
    throw e;
  }
  // a drag that changes nothing makes no history entry
  return JSON.stringify(out.project) === JSON.stringify(p) ? null : out;
}

/** The dashed rectangle and (for a main clip) the landing line, as `_paint_ghost` computes them. */
export function ghost(v: View, p: Project, spans: Map<string, Span>, rs: Row[], d: Drag): { rect: Rect; insertX: number | null } {
  const span = d.span;
  const rect = spanRect(v, rs, span);
  const delta = dt(v, d);
  let insertX: number | null = null;
  if (d.part === "left") {
    const newLeft = xFor(v, snap(v, spans, span.start + delta, d.id, d.alt));
    if (span.row_kind === "main") {
      rect.w -= newLeft - rect.x; // trimming the start keeps the clip's place
    } else {
      rect.w = rect.x + rect.w - newLeft;
      rect.x = newLeft;
    }
  } else if (d.part === "right") {
    rect.w = xFor(v, snap(v, spans, span.end + delta, d.id, d.alt)) - rect.x;
  } else if (span.row_kind === "main") {
    const index = mainIndex(v, p, spans, d);
    const others = p.main.filter((m) => m.id !== d.id);
    if (index < others.length) insertX = xFor(v, spans.get(others[index].id)!.start);
    else if (others.length) insertX = xFor(v, spans.get(others[others.length - 1].id)!.end);
    else insertX = xFor(v, 0);
    rect.x += d.x - d.ox;
  } else {
    const start = snapMove(v, spans, Math.max(0, span.start + delta), span.end - span.start, d.id, d.alt);
    rect.x = xFor(v, start);
    const row = rowAt(rs, d.y);
    if (row && row.kind === span.row_kind) rect.y = row.y + 2;
  }
  rect.w = Math.max(0, rect.w);
  return { rect, insertX };
}

// ---- zoom, scroll, follow -----------------------------------------------------------------
export function zoom(v: View, factor: number, anchorX?: number): { pps: number; scroll: number } {
  const ax = anchorX ?? v.m.header + (v.width - v.m.header) / 2;
  const at = tFor(v, ax);
  const pps = Math.max(MIN_PPS, Math.min(MAX_PPS, v.pps * factor));
  return { pps, scroll: Math.max(0, at - (ax - v.m.header) / pps) };
}

export function wheel(v: View, deltaY: number, ctrl: boolean, x: number): { pps: number; scroll: number } {
  if (ctrl) return zoom(v, deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP, x);
  const shift = ((deltaY / 120) * 60) / v.pps;
  return { pps: v.pps, scroll: Math.max(0, v.scroll - shift) };
}

/** New scroll for playback: the view pages when the playhead leaves it. */
export function follow(v: View, t: number): number {
  const ph = Math.max(0, t);
  const span = Math.max(0, (v.width - v.m.header) / v.pps);
  if (!(v.scroll <= ph && ph < v.scroll + span)) return Math.max(0, ph - span * FOLLOW_LEAD);
  return v.scroll;
}

// ---- ruler and time text --------------------------------------------------------------------
export function rulerTicks(v: View): { x: number; label: string }[] {
  let step = 600;
  for (const c of [0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 300, 600]) {
    if (c * v.pps >= 60) { step = c; break; }
  }
  const end = tFor(v, v.width);
  const out: { x: number; label: string }[] = [];
  for (let k = Math.floor(v.scroll / step + 1e-9); k * step <= end; k++) {
    const t = k * step;
    const m = Math.floor(t / 60);
    const s = t - m * 60;
    const label = step < 1 ? `${m}:${s.toFixed(1).padStart(4, "0")}` : `${m}:${String(Math.floor(s + 1e-9)).padStart(2, "0")}`;
    out.push({ x: xFor(v, t), label });
  }
  return out;
}

/** `m:ss.cc` (hundredths), e.g. `1:05.40`. */
export function formatTime(t: number): string {
  const cs = Math.max(0, Math.round(t * 100));
  const minutes = Math.floor(cs / 6000);
  const rest = cs % 6000;
  return `${minutes}:${String(Math.floor(rest / 100)).padStart(2, "0")}.${String(rest % 100).padStart(2, "0")}`;
}

export function frameNumber(p: Project, t: number): number {
  return Math.floor(Math.max(0, t) * outputFps(p) + 1e-6);
}
