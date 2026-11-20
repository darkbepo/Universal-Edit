// Saving and loading .umcvideo project files (JSON): port of src/umc/videoedit/projectfile.py.
import { ConversionError } from "../core/types";
import {
  DEFAULT_SETTINGS, NO_TRANSITION, audioClip, pictureClip, textItem, videoClip,
  type AudioClip, type MainItem, type PictureClip, type Project, type ProjectSettings, type Source,
  type TextItem, type Transition, type VideoClip,
} from "./project";

export const FORMAT = "umcvideo";
export const VERSION = 1;

const INVALID = "This isn't a valid video project file.";
const bad = (): never => {
  throw new ConversionError(INVALID);
};

type Dict = Record<string, unknown>;
const isDict = (x: unknown): x is Dict => typeof x === "object" && x !== null && !Array.isArray(x);

/** Defaults plus the known keys of `data` (unknown keys dropped), like `_build`. */
function build<T extends object>(defaults: T, data: Dict): T {
  const out: Dict = { ...(defaults as Dict) };
  for (const k of Object.keys(defaults)) {
    if (!(k in data)) continue;
    const want = (defaults as Dict)[k];
    const got = data[k];
    if (want === null ? got !== null && typeof got !== "string" : typeof got !== typeof want) return bad();
    if (typeof got === "number" && !Number.isFinite(got)) return bad();
    out[k] = got;
  }
  return out as T;
}

function itemFromJson(data: unknown): MainItem | TextItem | AudioClip {
  if (!isDict(data) || typeof data.id !== "string") return bad();
  if ("transition_in" in data && !isDict(data.transition_in)) return bad();
  if (data.type !== "text" && typeof data.source !== "string") return bad();
  if ((data.type === "video" || data.type === "audio") && (typeof data.in_ !== "number" || typeof data.out !== "number")) {
    return bad();
  }
  const tr = isDict(data.transition_in) ? build<Transition>(NO_TRANSITION, data.transition_in) : undefined;
  const id = data.id;
  let item: MainItem | TextItem | AudioClip;
  switch (data.type) {
    case "video": item = build<VideoClip>(videoClip(id, "", 0, 0), data); break;
    case "picture": item = build<PictureClip>(pictureClip(id, ""), data); break;
    case "audio": item = build<AudioClip>(audioClip(id, "", 0, 0, 0), data); break;
    case "text": item = build<TextItem>(textItem(id, 0, 3), data); break;
    default: return bad();
  }
  return tr && "transition_in" in item ? { ...item, transition_in: tr } : item;
}

/** One entry of a project file's "sources" list. */
export function sourceFromJson(entry: unknown): Source {
  if (!isDict(entry) || typeof entry.id !== "string" || typeof entry.path !== "string") return bad();
  if (entry.kind !== "video" && entry.kind !== "image" && entry.kind !== "audio") return bad();
  const base: Source = {
    id: entry.id, path: entry.path, kind: entry.kind, duration: 0, width: 0, height: 0, fps: 0,
    has_audio: false, size: 0, relpath: null,
  };
  return build<Source>(base, entry);
}

type Kind = "video" | "picture" | "audio" | "text";
function placed(item: MainItem | TextItem | AudioClip, allowed: Kind[]): void {
  if (!allowed.includes(item.type as Kind)) bad();
}

function list(x: unknown): unknown[] {
  return Array.isArray(x) ? x : bad();
}
const rows = <T>(x: unknown): T[][] => list(x).map((r) => list(r).map((i) => itemFromJson(i) as T));

export function projectFromJson(text: string): Project {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return bad();
  }
  if (!isDict(data) || data.format !== FORMAT || typeof data.version !== "number" || !Number.isInteger(data.version)) {
    return bad();
  }
  if (data.version > VERSION) throw new ConversionError("This project was made by a newer version of the app.");
  if (!isDict(data.settings)) return bad();
  const project: Project = {
    settings: build<ProjectSettings>(DEFAULT_SETTINGS, data.settings),
    sources: list(data.sources).map(sourceFromJson),
    main: list(data.main).map((m) => itemFromJson(m) as MainItem),
    text_rows: rows<TextItem>(data.text_rows),
    music_rows: rows<AudioClip>(data.music_rows),
  };
  const known = new Set(project.sources.map((s) => s.id));
  if (known.size !== project.sources.length) bad();
  const seen = new Set<string>();
  const check = (items: readonly (MainItem | TextItem | AudioClip)[], allowed: Kind[]) => {
    for (const item of items) {
      placed(item, allowed);
      if (seen.has(item.id)) bad();
      seen.add(item.id);
      if (item.type !== "text" && !known.has(item.source)) bad();
    }
  };
  check(project.main, ["video", "picture"]);
  for (const r of project.text_rows) check(r, ["text"]);
  for (const r of project.music_rows) check(r, ["audio"]);
  return project;
}

const basename = (path: string): string => path.split(/[\\/]/).pop() ?? path;

/** Like `save_project`: indent 1, no trailing newline. */
export function projectToJson(p: Project): string {
  const dict = {
    format: FORMAT,
    version: VERSION,
    settings: p.settings,
    sources: p.sources.map((s) => ({ ...s, relpath: s.relpath ?? basename(s.path) })),
    main: p.main,
    text_rows: p.text_rows,
    music_rows: p.music_rows,
  };
  return JSON.stringify(dict, null, 1);
}
