// Problems worth a look in a subtitle list — a port of src/umc/subedit/checks.py.

import { extraGet, plainText, type Cue } from "./formats";

export const MIN_MS = 700;
export const MAX_CPS = 21;
export const MAX_LINE = 42;
export const MAX_LINES = 2;

export interface Problem {
  index: number;
  kind: string; // empty, backwards, overlap, short, fast, long, lines
  message: string;
}

function seconds(ms: number): string {
  const tenths = Math.floor((ms + 50) / 100);
  return `${Math.floor(tenths / 10)}.${tenths % 10} s`;
}

/** Python's len(): code points, not UTF-16 units. */
const pyLen = (s: string) => [...s].length;

export function check(cues: Cue[], fmt: string): Problem[] {
  const out: Problem[] = [];
  let prev: [number, Cue] | null = null;
  cues.forEach((c, i) => {
    if (extraGet(c, "kind") === "Comment") return;
    const lines = plainText(c.text, fmt).split("\n").map((l) => l.trim()).filter(Boolean);
    const span = c.end - c.start;
    if (!lines.length) out.push({ index: i, kind: "empty", message: "No text" });
    if (span <= 0) out.push({ index: i, kind: "backwards", message: "Ends before it starts" });
    else if (span < MIN_MS) out.push({ index: i, kind: "short", message: `Shown for only ${seconds(span)}` });
    if (prev && c.start < prev[1].end) out.push({ index: i, kind: "overlap", message: `Starts before line ${prev[0] + 1} ends` });
    const chars = lines.reduce((n, l) => n + pyLen(l), 0);
    if (span > 0 && lines.length) {
      const cps = (chars * 1000) / span;
      if (cps > MAX_CPS) out.push({ index: i, kind: "fast", message: `Too fast to read (${Math.floor(cps + 0.5)} characters a second)` });
    }
    const longest = Math.max(0, ...lines.map(pyLen));
    if (longest > MAX_LINE) out.push({ index: i, kind: "long", message: `A line is ${longest} characters long` });
    if (lines.length > MAX_LINES) out.push({ index: i, kind: "lines", message: `${lines.length} lines (two fit best)` });
    if (!prev || c.end >= prev[1].end) prev = [i, c];
  });
  return out;
}
