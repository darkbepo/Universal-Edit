// Opening a saved project on the website: every source has to be picked again (the browser can't
// read paths). The DOM-free part of the locate window (ui/videoeditor/locate.ts).
import { matchSources, type PickedFile } from "./match";
import type { Project, Source } from "./project";

/** The picked file (index into the window's files) a missing source will use; null = remove it. */
export type Choice = { file: number } | null;

/** Seconds a name-only match may differ from the project's duration. */
export const DURATION_SLACK = 0.5;

/** `choices` with every source still null matched against `files` (name + size; name alone
 *  when the project has no size). A source already chosen — and the file it took — stay as they are. */
export function applyPicks(sources: readonly Source[], files: PickedFile[], choices: Map<string, Choice>): Map<string, Choice> {
  const out = new Map(choices);
  const taken = new Set<number>();
  for (const c of choices.values()) if (c) taken.add(c.file);
  const open = sources.filter((s) => !out.get(s.id));
  const free = files.map((f, i) => (taken.has(i) ? { name: "", size: -1 } : f));
  for (const [id, i] of matchSources(open, free)) out.set(id, { file: i });
  return out;
}

/** A file matched by name alone must last as long as the project says (0 = unknown → fine). */
export function keepsDuration(original: Source, probed: Source): boolean {
  if (original.size > 0) return true;
  if (!original.duration || !probed.duration) return true;
  return Math.abs(original.duration - probed.duration) <= DURATION_SLACK;
}

/** The probed file's facts under the project's own id, path and relpath (kept for re-saving). */
export function relinked(original: Source, probed: Source): Source {
  return { ...probed, id: original.id, path: original.path, relpath: original.relpath ?? null };
}

const stem = (path: string): string => {
  const name = path.split(/[\\/]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
};

/** "<first source's name>.umcvideo", else "video.umcvideo". */
export function defaultProjectName(p: Project): string {
  const first = p.sources.length ? stem(p.sources[0].path) : "";
  return `${first || "video"}.umcvideo`;
}
