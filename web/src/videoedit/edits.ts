// Editing operations on a video editor project: a line-by-line port of src/umc/videoedit/edits.py.
// Every function returns a new Project that went through `normalize` and never changes its input.
import { ConversionError } from "../core/types";
import {
  NO_TRANSITION, audioClip, clipDuration, find, layout, length, newId, pictureClip, textItem, videoClip,
  type AudioClip, type MainItem, type Project, type ProjectSettings, type Source, type TextItem, type Transition,
} from "./project";

export const MIN_DURATION = 0.1;
export const MAX_LENGTH = 4 * 3600;
export const MAX_ITEMS = 500;
export const PICTURE_SECONDS = 3.0;
export const TEXT_SECONDS = 3.0;
const TOO_LARGE = "This project is too big to edit (max 4 hours and 500 items).";

export class EditError extends ConversionError {}
export class ProjectTooLarge extends EditError {
  constructor() {
    super(TOO_LARGE);
  }
}

type Item = MainItem | TextItem | AudioClip;
type Kind = "main" | "text" | "music";

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v));
const round6 = (x: number): number => Math.round(x * 1e6) / 1e6;

function sourceDuration(p: Project, id: string): number {
  return p.sources.find((s) => s.id === id)?.duration ?? 0.0;
}

function clampRange(in_: number, out: number, duration: number, minLen: number): [number, number] {
  in_ = Math.max(0.0, in_);
  if (duration > 0) {
    in_ = Math.min(in_, Math.max(0.0, duration - minLen));
    out = clamp(out, in_ + minLen, Math.max(duration, in_ + minLen));
  } else {
    out = Math.max(out, in_ + minLen);
  }
  return [in_, out];
}

function normalizeMain(p: Project, item: MainItem): MainItem {
  const fade_in = clamp(item.fade_in, 0.0, 5.0);
  const fade_out = clamp(item.fade_out, 0.0, 5.0);
  if (item.type === "picture") return { ...item, duration: clamp(item.duration, 0.1, 60.0), fade_in, fade_out };
  const speed = clamp(item.speed, 0.25, 4.0);
  const [in_, out] = clampRange(item.in_, item.out, sourceDuration(p, item.source), MIN_DURATION * speed);
  return { ...item, in_, out, speed, volume: Math.trunc(clamp(item.volume, 0, 200)), fade_in, fade_out };
}

function normalizeAudio(p: Project, a: AudioClip): AudioClip {
  const [in_, out] = clampRange(a.in_, a.out, sourceDuration(p, a.source), MIN_DURATION);
  const start = Math.max(0.0, a.start);
  let loop = a.loop_until;
  if (loop > 0) loop = Math.max(loop, start + MIN_DURATION);
  return {
    ...a, start, in_, out, loop_until: loop, volume: Math.trunc(clamp(a.volume, 0, 200)),
    fade_in: clamp(a.fade_in, 0.0, 5.0), fade_out: clamp(a.fade_out, 0.0, 5.0),
  };
}

function normalizeText(t: TextItem): TextItem {
  const start = Math.max(0.0, t.start);
  return {
    ...t, start, end: Math.max(t.end, start + MIN_DURATION),
    box_opacity: Math.trunc(clamp(t.box_opacity, 0, 100)),
    fade_in: clamp(t.fade_in, 0.0, 5.0), fade_out: clamp(t.fade_out, 0.0, 5.0),
  };
}

const sameTransition = (a: Transition, b: Transition): boolean => a.kind === b.kind && a.duration === b.duration;

function normalizeTransitions(main: readonly MainItem[]): MainItem[] {
  return main.map((item, i) => {
    let tr: Transition = item.transition_in;
    if (i === 0) {
      tr = NO_TRANSITION;
    } else if (tr.kind !== "none") {
      const shorter = Math.min(clipDuration(main[i - 1]), clipDuration(item));
      const cap = Math.min(3.0, shorter / 2);
      tr = cap < 0.1 ? NO_TRANSITION : { ...tr, duration: round6(clamp(tr.duration, 0.1, cap)) };
    }
    return sameTransition(tr, item.transition_in) ? item : { ...item, transition_in: tr };
  });
}

/** Bring every value into its allowed range; refuse projects that are too big. */
export function normalize(p: Project): Project {
  const count =
    p.main.length + p.text_rows.reduce((n, r) => n + r.length, 0) + p.music_rows.reduce((n, r) => n + r.length, 0);
  if (count > MAX_ITEMS) throw new ProjectTooLarge();
  const result: Project = {
    ...p,
    main: normalizeTransitions(p.main.map((m) => normalizeMain(p, m))),
    text_rows: p.text_rows.map((row) => row.map(normalizeText)),
    music_rows: p.music_rows.map((row) => row.map((a) => normalizeAudio(p, a))),
  };
  if (length(result) > MAX_LENGTH) throw new ProjectTooLarge();
  return result;
}

// ---- helpers ----

/** Swap an item for `repl` (several items, or none to remove it). */
function replaceItem(p: Project, itemId: string, repl: Item[]): Project {
  const { kind, row, index } = find(p, itemId);
  if (kind === "main") {
    return { ...p, main: [...p.main.slice(0, index), ...(repl as MainItem[]), ...p.main.slice(index + 1)] };
  }
  const rows = (kind === "text" ? p.text_rows : p.music_rows) as readonly (readonly Item[])[];
  const newRow = [...rows[row].slice(0, index), ...repl, ...rows[row].slice(index + 1)];
  const out = [...rows.slice(0, row), newRow, ...rows.slice(row + 1)];
  return kind === "text" ? { ...p, text_rows: out as TextItem[][] } : { ...p, music_rows: out as AudioClip[][] };
}

function getSource(p: Project, id: string): Source {
  const s = p.sources.find((x) => x.id === id);
  if (!s) throw new EditError("That file is not part of the project.");
  return s;
}

function findItem(p: Project, id: string): { kind: Kind; row: number; index: number; item: Item } {
  try {
    return find(p, id);
  } catch {
    throw new EditError("That item no longer exists.");
  }
}

function newMain(source: Source): MainItem {
  if (source.kind === "image") return pictureClip(newId(), source.id, PICTURE_SECONDS);
  return videoClip(newId(), source.id, 0.0, source.duration);
}

function withRow<T>(rows: readonly (readonly T[])[], row: number): (readonly T[])[] {
  const out = [...rows];
  while (out.length <= row) out.push([]);
  return out;
}

// ---- edits ----

export function addSources(p: Project, sources: Source[]): Project {
  return normalize({ ...p, sources: [...p.sources, ...sources] });
}

export function insertMain(p: Project, index: number, sourceId: string): [Project, string] {
  const source = getSource(p, sourceId);
  if (source.kind === "audio") throw new EditError("Audio can only go on a music row.");
  const item = newMain(source);
  index = Math.max(0, Math.min(index, p.main.length));
  const main = [...p.main.slice(0, index), item, ...p.main.slice(index)];
  return [normalize({ ...p, main }), item.id];
}

function freeSpot(row: readonly { id: string }[], spans: ReturnType<typeof layout>, start: number, dur: number): number {
  const spots = row.map((x) => [spans.get(x.id)!.start, spans.get(x.id)!.end] as [number, number]);
  spots.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  let moved = true;
  while (moved) {
    moved = false;
    for (const [s, e] of spots) {
      if (start < e && start + dur > s) {
        start = e;
        moved = true;
      }
    }
  }
  return start;
}

export function addAt(p: Project, sourceId: string, t: number, row = 0): [Project, string] {
  const source = getSource(p, sourceId);
  if (source.kind !== "audio") {
    const spans = layout(p);
    const bounds = p.main.map((m) => spans.get(m.id)!.start);
    bounds.push(p.main.length ? spans.get(p.main[p.main.length - 1].id)!.end : 0.0);
    let index = 0;
    for (let i = 1; i < bounds.length; i++) if (Math.abs(bounds[i] - t) < Math.abs(bounds[index] - t)) index = i;
    return insertMain(p, index, sourceId);
  }
  row = Math.max(0, row);
  const rows = withRow(p.music_rows, row);
  const dur = source.duration;
  const start = freeSpot(rows[row], layout(p), Math.max(0.0, t), dur);
  const clip = audioClip(newId(), sourceId, start, 0.0, dur);
  rows[row] = [...rows[row], clip];
  return [normalize({ ...p, music_rows: rows as AudioClip[][] }), clip.id];
}

export function addText(p: Project, t: number, row = 0): [Project, string] {
  row = Math.max(0, row);
  const rows = withRow(p.text_rows, row);
  const start = Math.max(0.0, t);
  const item = textItem(newId(), start, start + TEXT_SECONDS);
  rows[row] = [...rows[row], item];
  return [normalize({ ...p, text_rows: rows as TextItem[][] }), item.id];
}

export function remove(p: Project, itemId: string): Project {
  findItem(p, itemId);
  return normalize(replaceItem(p, itemId, []));
}

export function moveMain(p: Project, itemId: string, newIndex: number): Project {
  const { kind, index, item } = findItem(p, itemId);
  if (kind !== "main") throw new EditError("Only clips on the main track can be reordered.");
  const rest = [...p.main.slice(0, index), ...p.main.slice(index + 1)];
  newIndex = Math.max(0, Math.min(newIndex, rest.length));
  return normalize({ ...p, main: [...rest.slice(0, newIndex), item as MainItem, ...rest.slice(newIndex)] });
}

const EPS = 1e-6;

function itemEnd(kind: Kind, item: Item): number {
  return kind === "music" ? (item as AudioClip).start + clipDuration(item as AudioClip) : (item as TextItem).end;
}

/** Throw unless [start, end) is free in `row` (touching items are fine). */
function ensureFree(row: readonly Item[], itemId: string, kind: Kind, start: number, end: number): void {
  for (const other of row) {
    if (other.id === itemId) continue;
    if (start < itemEnd(kind, other) - EPS && end > (other as TextItem | AudioClip).start + EPS) {
      throw new EditError("That spot is taken.");
    }
  }
}

export function moveItem(p: Project, itemId: string, start: number, row: number): Project {
  const { kind, item } = findItem(p, itemId);
  if (kind === "main") throw new EditError("Use move_main for clips on the main track.");
  const rows = (kind === "text" ? p.text_rows : p.music_rows) as readonly (readonly Item[])[];
  if (!(row >= 0 && row < rows.length)) throw new EditError("That row doesn't exist.");
  start = Math.max(0.0, start);
  const dur = itemEnd(kind, item) - (item as TextItem | AudioClip).start;
  ensureFree(rows[row], itemId, kind, start, start + dur);
  let moved: Item;
  if (kind === "text") {
    moved = { ...(item as TextItem), start, end: start + dur };
  } else {
    const a = item as AudioClip;
    moved = { ...a, start };
    if (a.loop_until > 0) moved = { ...moved, loop_until: start + dur };
  }
  const removed = replaceItem(p, itemId, []);
  const rows2 = (kind === "text" ? removed.text_rows : removed.music_rows) as readonly (readonly Item[])[];
  const out = [...rows2.slice(0, row), [...rows2[row], moved], ...rows2.slice(row + 1)];
  return normalize(
    kind === "text" ? { ...removed, text_rows: out as TextItem[][] } : { ...removed, music_rows: out as AudioClip[][] },
  );
}

export function trim(p: Project, itemId: string, edge: "start" | "end", delta: number): Project {
  const { item } = findItem(p, itemId);
  const startEdge = edge === "start";
  let next: Item;
  if (item.type === "video") {
    const d = delta * item.speed;
    const minLen = MIN_DURATION * item.speed;
    const duration = sourceDuration(p, item.source);
    if (startEdge) {
      next = { ...item, in_: clamp(item.in_ + d, 0.0, Math.max(0.0, item.out - minLen)) };
    } else {
      const high = duration > 0 ? duration : Infinity;
      next = { ...item, out: clamp(item.out + d, item.in_ + minLen, Math.max(high, item.in_ + minLen)) };
    }
  } else if (item.type === "picture") {
    next = { ...item, duration: item.duration + (startEdge ? -delta : delta) };
  } else if (item.type === "audio") {
    if (startEdge) {
      delta = Math.max(delta, -item.start, -item.in_);
      delta = Math.min(delta, clipDuration(item) - MIN_DURATION, item.out - item.in_ - MIN_DURATION);
      next = { ...item, start: item.start + delta, in_: item.in_ + delta };
    } else if (item.loop_until > 0) {
      next = { ...item, loop_until: item.loop_until + delta };
    } else {
      next = { ...item, out: item.out + delta };
    }
  } else if (startEdge) {
    delta = Math.min(delta, item.end - item.start - MIN_DURATION);
    next = { ...item, start: item.start + delta };
  } else {
    next = { ...item, end: item.end + delta };
  }
  return normalize(replaceItem(p, itemId, [next]));
}

export function split(p: Project, itemId: string, t: number): Project {
  const { item } = findItem(p, itemId);
  const span = layout(p).get(itemId)!;
  if (!(span.start + 1e-9 < t && t < span.end - 1e-9)) throw new EditError("Put the playhead inside the clip to split it.");
  const rel = t - span.start;
  let a: Item;
  let b: Item;
  if (item.type === "video") {
    const cut = item.in_ + rel * item.speed;
    a = { ...item, id: newId(), out: cut, fade_out: 0.0 };
    b = { ...item, id: newId(), in_: cut, fade_in: 0.0, transition_in: NO_TRANSITION };
  } else if (item.type === "picture") {
    a = { ...item, id: newId(), duration: rel, fade_out: 0.0 };
    b = { ...item, id: newId(), duration: item.duration - rel, fade_in: 0.0, transition_in: NO_TRANSITION };
  } else if (item.type === "audio") {
    let a2: AudioClip = { ...item, id: newId(), fade_out: 0.0 };
    let b2: AudioClip = { ...item, id: newId(), start: t, fade_in: 0.0 };
    if (item.loop_until > 0) {
      a2 = { ...a2, loop_until: t };
    } else {
      a2 = { ...a2, out: item.in_ + rel };
      b2 = { ...b2, in_: item.in_ + rel };
    }
    a = a2;
    b = b2;
  } else {
    a = { ...item, id: newId(), end: t, fade_out: 0.0 };
    b = { ...item, id: newId(), start: t, fade_in: 0.0 };
  }
  return normalize(replaceItem(p, itemId, [a, b]));
}

function unknownCheck(changes: Record<string, unknown>, valid: Set<string>): void {
  const unknown = Object.keys(changes).filter((k) => !valid.has(k));
  if (unknown.length) throw new EditError(`Unknown setting: ${unknown.sort().join(", ")}.`);
}

export function update(p: Project, itemId: string, changes: Record<string, unknown>): Project {
  const { item } = findItem(p, itemId);
  unknownCheck(changes, new Set(Object.keys(item).filter((k) => k !== "id" && k !== "source" && k !== "type")));
  const result = normalize(replaceItem(p, itemId, [{ ...item, ...changes } as Item]));
  const { kind, row, item: nw } = find(result, itemId);
  if ((kind === "text" || kind === "music") && ["start", "end", "in_", "out", "loop_until"].some((k) => k in changes)) {
    const rows = (kind === "text" ? result.text_rows : result.music_rows) as readonly (readonly Item[])[];
    ensureFree(rows[row], itemId, kind, (nw as TextItem | AudioClip).start, itemEnd(kind, nw));
  }
  return result;
}

export function setSettings(p: Project, settings: ProjectSettings): Project {
  return normalize({ ...p, settings });
}

export function addRow(p: Project, kind: "text" | "music"): Project {
  return kind === "text" ? { ...p, text_rows: [...p.text_rows, []] } : { ...p, music_rows: [...p.music_rows, []] };
}

/** Point a source at another file; `probed` supplies its path, size and media facts. */
export function relink(p: Project, sourceId: string, probed: Source): Project {
  const next: Source = { ...probed, id: sourceId };
  return normalize({ ...p, sources: p.sources.map((s) => (s.id === sourceId ? next : s)) });
}

export function removeSource(p: Project, sourceId: string): Project {
  return normalize({
    ...p,
    sources: p.sources.filter((s) => s.id !== sourceId),
    main: p.main.filter((m) => m.source !== sourceId),
    music_rows: p.music_rows.map((row) => row.filter((a) => a.source !== sourceId)),
  });
}

/** Settings changed in the inspector, for the project (null), a transition ("tr:<clip id>") or an item. */
export function applyChanges(p: Project, selectionId: string | null, changes: Record<string, unknown>): Project {
  if (selectionId === null) {
    unknownCheck(changes, new Set(Object.keys(p.settings)));
    return setSettings(p, { ...p.settings, ...changes } as ProjectSettings);
  }
  if (selectionId.startsWith("tr:")) {
    const clipId = selectionId.slice(3);
    const { item } = findItem(p, clipId);
    if (item.type !== "video" && item.type !== "picture") throw new EditError("Only clips on the main track have transitions.");
    unknownCheck(changes, new Set(["kind", "duration"]));
    return update(p, clipId, { transition_in: { ...item.transition_in, ...changes } });
  }
  return update(p, selectionId, changes);
}
