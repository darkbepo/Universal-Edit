// Small fixes to Office (OPC zip) files before Pandoc reads them.
import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";

/**
 * Office files may point to their parts with absolute paths (Target="/xl/worksheets/sheet1.xml",
 * as openpyxl / pandas write them — valid OPC). Pandoc's readers join those onto the folder
 * ("xl//xl/worksheets/…") and fail with "Entry not found", so make them relative first.
 * Returns the input unchanged when there is nothing to fix (or it isn't a zip).
 */
export function relativeOfficeRels(input: Uint8Array): Uint8Array {
  let entries: Record<string, Uint8Array>;
  try {
    entries = unzipSync(input);
  } catch {
    return input; // not a zip: let Pandoc report it
  }
  let changed = false;
  for (const [path, data] of Object.entries(entries)) {
    const m = /^(.+\/)_rels\/[^/]+\.rels$/.exec(path);
    if (!m) continue; // only part-level rels (xl/_rels/…, ppt/_rels/…, word/_rels/…)
    const base = m[1]; // e.g. "xl/"
    const text = strFromU8(data);
    const fixed = text.replace(/Target="\/([^"]+)"/g, (all, target: string) =>
      target.startsWith(base) ? `Target="${target.slice(base.length)}"` : all,
    );
    if (fixed !== text) {
      entries[path] = strToU8(fixed);
      changed = true;
    }
  }
  return changed ? zipSync(entries) : input;
}
