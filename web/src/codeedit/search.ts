// Find and replace for the code editor — a port of src/umc/codeedit/search.py (same cases:
// tests/fixtures/codeedit_search_cases.json). Patterns and replacements use Python's syntax
// (\1, \g<name>, (?P<name>…)) so both apps behave alike. Positions are JS (UTF-16) indices,
// which is what CodeMirror uses too.

export class SearchError extends Error {}

export interface SearchOptions {
  caseSensitive: boolean;
  wholeWord: boolean;
  regex: boolean;
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const WORD = "[\\p{L}\\p{M}\\p{N}_]";

function pattern(query: string, o: SearchOptions): RegExp {
  let body = o.regex ? query.replace(/\(\?P</g, "(?<").replace(/\(\?P=(\w+)\)/g, "\\k<$1>") : escape(query);
  if (o.wholeWord) body = `(?<!${WORD})(?:${body})(?!${WORD})`;
  try {
    return new RegExp(body, o.caseSensitive ? "gu" : "giu");
  } catch (e) {
    const why = e instanceof Error ? e.message.replace(/^Invalid regular expression: /, "") : "";
    throw new SearchError(`That pattern isn't valid: ${why}`);
  }
}

/** [start, end] of every match; empty matches are skipped. */
export function findAll(text: string, query: string, o: SearchOptions): [number, number][] {
  if (!query) return [];
  const out: [number, number][] = [];
  for (const m of text.matchAll(pattern(query, o))) {
    if (m[0].length) out.push([m.index, m.index + m[0].length]);
  }
  return out;
}

/** A Python-style replacement (\1, \g<1>, \g<name>, \\) filled in from a match. */
function expand(m: RegExpMatchArray, template: string): string {
  return template.replace(/\\(?:g<(\w+)>|(\d{1,2})|(\\))/g, (_all, name: string, num: string, slash: string) => {
    if (slash) return "\\";
    const key = name ?? num;
    const value = /^\d+$/.test(key) ? m[Number(key)] : m.groups?.[key];
    if (value === undefined && !(/^\d+$/.test(key) && Number(key) < m.length) && !(m.groups && key in m.groups)) {
      throw new SearchError(`That replacement isn't valid: there's no group ${key}`);
    }
    return value ?? "";
  });
}

/** The text with every match replaced, and how many were. */
export function replaceAll(text: string, query: string, replacement: string, o: SearchOptions): [string, number] {
  if (!query) return [text, 0];
  let count = 0;
  const out = text.replace(pattern(query, o), (...args) => {
    const whole = args[0] as string;
    if (!whole.length) return whole;
    count++;
    if (!o.regex) return replacement;
    const groupsArg = args[args.length - 1];
    const hasGroups = typeof groupsArg === "object" && groupsArg !== null;
    const caps = args.slice(0, hasGroups ? -3 : -2) as string[];
    const m = Object.assign(caps, { groups: hasGroups ? (groupsArg as Record<string, string>) : undefined }) as unknown as RegExpMatchArray;
    return expand(m, replacement);
  });
  return [out, count];
}

/** [start, end, new text] for the match that starts at `start`; null if none does. */
export function replacementAt(
  text: string,
  query: string,
  replacement: string,
  start: number,
  o: SearchOptions,
): [number, number, string] | null {
  if (!query) return null;
  for (const m of text.matchAll(pattern(query, o))) {
    if (m.index === start && m[0].length) {
      return [m.index, m.index + m[0].length, o.regex ? expand(m, replacement) : replacement];
    }
    if (m.index > start) break;
  }
  return null;
}
