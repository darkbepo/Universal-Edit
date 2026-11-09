// Option schemas: each converter declares its settings as a list of OptionSpec; the settings
// panel renders them automatically (same idea as the desktop app's opt()).
import { ConversionError } from "./types";

export type Kind = "slider" | "int" | "float" | "choice" | "bool" | "text" | "color" | "file";
export type Value = string | number | boolean | File | null;
export type Values = Record<string, Value>;

export interface OptionSpec {
  name: string;
  label: string;
  kind: Kind;
  default: Value;
  min?: number;
  max?: number;
  step?: number;
  decimals?: number;
  unit?: string;
  help?: string;
  choices?: [Value, string][];
  /** Only shown for these target ids. */
  targets?: string[];
  /** Only shown when this returns true for the current values. */
  showIf?: (v: Values) => boolean;
  group?: string;
  accept?: string; // for kind="file"
}

type Extra = Omit<OptionSpec, "name" | "label" | "kind" | "default">;

export const opt = {
  slider: (name: string, label: string, def: number, min: number, max: number, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "slider", default: def, min, max, ...x }),
  int: (name: string, label: string, def: number, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "int", default: def, ...x }),
  choice: (name: string, label: string, def: Value, choices: [Value, string][], x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "choice", default: def, choices, ...x }),
  bool: (name: string, label: string, def: boolean, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "bool", default: def, ...x }),
  text: (name: string, label: string, def: string, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "text", default: def, ...x }),
  color: (name: string, label: string, def: string, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "color", default: def, ...x }),
  file: (name: string, label: string, x: Extra = {}): OptionSpec =>
    ({ name, label, kind: "file", default: null, ...x }),
};

export function defaults(specs: OptionSpec[]): Values {
  return Object.fromEntries(specs.map((s) => [s.name, s.default]));
}

export function visible(spec: OptionSpec, values: Values, targetId: string): boolean {
  if (spec.targets && !spec.targets.includes(targetId)) return false;
  return spec.showIf ? spec.showIf(values) : true;
}

/** "90", "1:30", "00:01:30.5" → seconds. Empty → 0. */
export function parseTime(text: Value): number {
  if (typeof text === "number") return text;
  const t = String(text ?? "").trim();
  if (!t) return 0;
  const parts = t.split(":").map(Number);
  if (parts.some((p) => Number.isNaN(p))) {
    throw new ConversionError(`“${t}” isn't a valid time. Use seconds or mm:ss.`);
  }
  return parts.reduce((acc, p) => acc * 60 + p, 0);
}

/** "1-3, 5, 8-" → zero-based page indexes. Empty / "all" → every page. */
export function parsePageRange(text: Value, pageCount: number): number[] {
  const t = String(text ?? "").trim().toLowerCase();
  if (!t || t === "all" || t === "*") return [...Array(pageCount).keys()];
  const pages: number[] = [];
  for (const raw of t.replaceAll(";", ",").split(",")) {
    const part = raw.trim();
    if (!part) continue;
    const m = /^(\d*)\s*(-?)\s*(\d*)$/.exec(part);
    if (!m || (!m[1] && !m[3])) {
      throw new ConversionError(`“${t}” isn't a valid page range. Use something like 1-3, 5, 8-`);
    }
    const start = m[1] ? Number(m[1]) : 1;
    const end = m[2] ? (m[3] ? Number(m[3]) : pageCount) : start;
    if (start < 1 || end < start) throw new ConversionError(`“${part}” isn't a valid page range.`);
    for (let p = start; p <= Math.min(end, pageCount); p++) pages.push(p - 1);
  }
  if (!pages.length) {
    throw new ConversionError(`The page range doesn't include any page (document has ${pageCount}).`);
  }
  return pages;
}

/** "1-3, 4-10, 11-" → one page list per comma-separated group. */
export function parsePageGroups(text: Value, pageCount: number): number[][] {
  const groups = String(text ?? "")
    .split(",")
    .filter((p) => p.trim())
    .map((p) => parsePageRange(p, pageCount));
  if (!groups.length) throw new ConversionError("Enter at least one page range, e.g. 1-3, 4-10, 11-");
  return groups;
}
