// Pure edits on a list of cues — a port of src/umc/subedit/edits.py. Each returns new cues
// (the input is never changed); times are ms and never go below 0.

import { findAll, replaceAll as replaceInText, type SearchOptions } from "../codeedit/search";
import { cue, extraGet, type Cue } from "./formats";

export const NEW_CUE_MS = 2000; // length of an added cue when there is room
export const MIN_SPLIT_MS = 1; // each half of a split keeps at least this

const round = (x: number) => Math.floor(x + 0.5);
const swap = (cues: Cue[], i: number, c: Cue) => [...cues.slice(0, i), c, ...cues.slice(i + 1)];

export const setText = (cues: Cue[], i: number, text: string) => swap(cues, i, { ...cues[i], text });

/** A new start keeps the end (pushed along when passed); a new end keeps the start (pulled
 * back when the end goes before it). */
export function setTimes(cues: Cue[], i: number, start: number | null = null, end: number | null = null): Cue[] {
  const c = cues[i];
  let s = c.start, e = c.end;
  if (start !== null) {
    s = Math.max(0, start);
    e = Math.max(e, s);
  }
  if (end !== null) {
    e = Math.max(0, end);
    s = Math.min(s, e);
  }
  return swap(cues, i, { ...c, start: s, end: e });
}

export const setLength = (cues: Cue[], i: number, length: number) => setTimes(cues, i, null, cues[i].start + Math.max(0, length));

/** Move cues (all, or `indices`) by `delta` ms; moving earlier stops when the earliest of them
 * reaches 0, so every cue keeps its length and the gaps between them. */
export function shift(cues: Cue[], delta: number, indices: Iterable<number> | null = null): Cue[] {
  const chosen = new Set(indices ?? cues.keys());
  if (!chosen.size) return cues;
  const earliest = Math.min(...[...chosen].map((i) => cues[i].start));
  delta = Math.max(delta, -earliest);
  if (delta === 0) return cues;
  return cues.map((c, i) => (chosen.has(i) ? { ...c, start: c.start + delta, end: c.end + delta } : c));
}

/** Stretch every cue so cue `a` starts at `aTime` and cue `b` at `bTime`. Throws
 * Error("same") when the two start together, Error("order") when they would turn round. */
export function sync(cues: Cue[], a: number, aTime: number, b: number, bTime: number): Cue[] {
  const sa = cues[a].start, sb = cues[b].start;
  if (sa === sb) throw new Error("same");
  const factor = (bTime - aTime) / (sb - sa);
  if (factor <= 0) throw new Error("order");
  const at = (t: number) => Math.max(0, round(aTime + (t - sa) * factor));
  return cues.map((c) => ({ ...c, start: at(c.start), end: at(c.end) }));
}

/** An empty cue after cue `after` (-1 = at the very start); see edits.py. */
export function add(cues: Cue[], after: number, length = NEW_CUE_MS): [Cue[], number] {
  const prev = after >= 0 && after < cues.length ? cues[after] : null;
  const nxt = after + 1 < cues.length ? cues[after + 1] : null;
  const start = prev ? prev.end : 0;
  let end = start + length;
  if (nxt && start < nxt.start && nxt.start < end && nxt.start - start >= Math.floor(length / 2)) end = nxt.start;
  let extra = prev ? prev.extra.filter(([k]) => k !== "id") : [];
  if (extra.length && extraGet({ ...cue(0, 0, ""), extra }, "kind") === "Comment") {
    extra = extra.map(([k, v]) => (k === "kind" ? ["kind", "Dialogue"] : [k, v]));
  }
  const at = after + 1;
  return [[...cues.slice(0, at), cue(start, end, "", extra), ...cues.slice(at)], at];
}

export function remove(cues: Cue[], indices: Iterable<number>): Cue[] {
  const gone = new Set(indices);
  return cues.filter((_, i) => !gone.has(i));
}

function splitPoint(text: string): number {
  const breaks: number[] = [];
  const spaces: number[] = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "\n") breaks.push(i);
    else if (text[i] === " ") spaces.push(i);
  }
  if (breaks.length) return breaks[Math.floor((breaks.length - 1) / 2)];
  if (!spaces.length) return -1;
  const middle = text.length / 2;
  let best = spaces[0];
  for (const i of spaces) if (Math.abs(i - middle) < Math.abs(best - middle)) best = i;
  return best;
}

const pyStrip = (s: string) => s.trim();

/** Cut cue `i` in two at text position `at` (else in the middle); time shared by characters. */
export function split(cues: Cue[], i: number, at: number | null = null): Cue[] {
  const c = cues[i];
  const pos = at !== null && at > 0 && at < c.text.length ? at : splitPoint(c.text);
  const [first, second] = pos < 0 ? [c.text, ""] : [pyStrip(c.text.slice(0, pos)), pyStrip(c.text.slice(pos))];
  const total = first.length + second.length;
  const share = !first || !second ? 0.5 : first.length / total;
  const span = c.end - c.start;
  let cut = c.start + round(span * share);
  if (span >= 2 * MIN_SPLIT_MS) cut = Math.min(Math.max(cut, c.start + MIN_SPLIT_MS), c.end - MIN_SPLIT_MS);
  const a = { ...c, end: cut, text: first };
  const b = cue(cut, c.end, second, c.extra.filter(([k]) => k !== "id"));
  return [...cues.slice(0, i), a, b, ...cues.slice(i + 1)];
}

/** One cue from the chosen ones; returns the cues and the joined cue's index. */
export function join(cues: Cue[], indices: Iterable<number>): [Cue[], number] {
  const chosen = [...new Set(indices)].sort((x, y) => x - y);
  if (chosen.length < 2) return [cues, chosen.length ? chosen[0] : -1];
  const parts = chosen.map((i) => cues[i]);
  const text = parts.map((p) => pyStrip(p.text)).filter(Boolean).join("\n");
  const joined = {
    ...parts[0],
    start: Math.min(...parts.map((p) => p.start)),
    end: Math.max(...parts.map((p) => p.end)),
    text,
  };
  const drop = new Set(chosen.slice(1));
  const out = cues.filter((_, n) => !drop.has(n));
  out[chosen[0]] = joined;
  return [out, chosen[0]];
}

/** By start time (then end); equal ones keep their order. */
export const sort = (cues: Cue[]) => [...cues].sort((a, b) => a.start - b.start || a.end - b.end);

export const isSorted = (cues: Cue[]) =>
  cues.every((c, i) => i === 0 || cues[i - 1].start < c.start || (cues[i - 1].start === c.start && cues[i - 1].end <= c.end));

/** [cue, start, end] of every match. Throws SearchError. */
export function find(cues: Cue[], query: string, o: SearchOptions): [number, number, number][] {
  const out: [number, number, number][] = [];
  cues.forEach((c, i) => {
    for (const [a, b] of findAll(c.text, query, o)) out.push([i, a, b]);
  });
  return out;
}

export function replaceAll(cues: Cue[], query: string, replacement: string, o: SearchOptions): [Cue[], number] {
  let count = 0;
  const out = cues.map((c) => {
    const [text, n] = replaceInText(c.text, query, replacement, o);
    count += n;
    return n ? { ...c, text } : c;
  });
  return [count ? out : cues, count];
}

/** Indices of the cues shown at `ms` (start ≤ ms < end), ASS comment lines left out. */
export const cueAt = (cues: Cue[], ms: number) =>
  cues.flatMap((c, i) => (c.start <= ms && ms < c.end && extraGet(c, "kind") !== "Comment" ? [i] : []));
