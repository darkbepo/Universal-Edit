// Match files the visitor picked to the sources of an opened project that are still missing.
import type { Source } from "./project";

export interface PickedFile {
  name: string;
  size: number;
}

const baseName = (path: string): string => (path.split(/[\\/]/).pop() ?? "").toLowerCase();

/** For each source still missing, the index of the picked file that belongs to it. */
export function matchSources(sources: Source[], files: PickedFile[]): Map<string, number> {
  const out = new Map<string, number>();
  const used = new Set<number>();
  for (const s of sources) {
    const name = baseName(s.path);
    const same: number[] = [];
    files.forEach((f, i) => {
      if (!used.has(i) && f.name.toLowerCase() === name) same.push(i);
    });
    let hit: number | undefined;
    if (s.size > 0) {
      const exact = same.filter((i) => files[i].size === s.size);
      if (exact.length === 1) hit = exact[0];
    } else if (same.length === 1) hit = same[0];
    if (hit !== undefined) {
      out.set(s.id, hit);
      used.add(hit);
    }
  }
  return out;
}
