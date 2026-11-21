// A project from dropped files without probing: port of media.new_project (the page probes the files itself).
import { addAt, addSources, insertMain } from "./edits";
import { newProject, type Project, type Source } from "./project";

export function projectFrom(sources: Source[]): Project {
  let p = addSources(newProject(), sources);
  let musicEnd = 0.0;
  for (const src of sources) {
    if (src.kind === "audio") {
      [p] = addAt(p, src.id, musicEnd, 0);
      musicEnd += src.duration;
    } else {
      [p] = insertMain(p, p.main.length, src.id);
    }
  }
  return p;
}

/** "s<n>", the first one not taken by `p` nor in `also` (the desktop starts counting at the number
 *  of sources, so it would give s4 for s1, s3). The page passes every id it used this session in
 *  `also`: its decoders are keyed by id across the undo history, so an id is never handed out twice. */
export function nextSourceId(p: Project, also: ReadonlySet<string> = new Set()): string {
  const taken = new Set(p.sources.map((s) => s.id));
  let n = 1;
  while (taken.has(`s${n}`) || also.has(`s${n}`)) n++;
  return `s${n}`;
}
