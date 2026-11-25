// Audio editor projects: the tracks saved separately — a port of src/umc/audioedit/project.py.
// A .umcaudio file is a ZIP: project.json (format, version, sample rate, the chosen track and
// each track's name, volume, left / right, mute, solo, start and file) and one 32-bit float WAV
// per track in tracks/ — lossless. Both apps read and write the same layout.
import { strFromU8, strToU8, unzipSync, zipSync, type Zippable } from "fflate";
import { AudioDoc } from "./document";
import * as tr from "./tracks";
import { readWav, writeWav } from "./wav";

export const EXT = "umcaudio";
export const FORMAT = "universal-edit-audio-project";
export const VERSION = 1;
export const MAX_TRACKS = 64;

export class ProjectError extends Error {}

export function toBytes(tracks: tr.Track[], active = 0): Uint8Array {
  if (!tracks.length) throw new ProjectError("There are no tracks to save.");
  const rate = tracks[0].doc.rate;
  const files: Zippable = {};
  const entries = tracks.map((t, i) => {
    const name = `tracks/${i + 1}.wav`;
    files[name] = writeWav(rate, t.doc.samples, 32);
    return { name: t.name, file: name, gain_db: t.gainDb, pan: t.pan, mute: t.mute, solo: t.solo, offset: t.offset };
  });
  const meta = { format: FORMAT, version: VERSION, rate, active: Math.max(0, Math.min(active, tracks.length - 1)), tracks: entries };
  files["project.json"] = strToU8(JSON.stringify(meta, null, 1));
  return zipSync(files);
}

const num = (v: unknown, lo: number, hi: number, fallback: number) =>
  typeof v === "number" && Number.isFinite(v) ? Math.max(lo, Math.min(hi, v)) : fallback;

/** The tracks and the chosen one. Throws ProjectError in plain words. */
export function fromBytes(data: Uint8Array, maxSamples: number): { tracks: tr.Track[]; active: number } {
  let files: Record<string, Uint8Array>;
  let meta: Record<string, unknown>;
  try {
    files = unzipSync(data, { filter: (f) => f.name === "project.json" });
    meta = JSON.parse(strFromU8(files["project.json"]));
  } catch {
    throw new ProjectError("This isn't an audio editor project (or it is damaged).");
  }
  if (!meta || typeof meta !== "object" || meta.format !== FORMAT) throw new ProjectError("This isn't an audio editor project.");
  if (typeof meta.version !== "number" || !Number.isInteger(meta.version) || meta.version > VERSION) {
    throw new ProjectError("This project was made by a newer version of Universal Edit.");
  }
  const entries = meta.tracks;
  if (!Array.isArray(entries) || !entries.length || entries.length > MAX_TRACKS) throw new ProjectError("This project has no tracks (or too many).");
  const wanted = new Set(entries.map((e) => (e && typeof e === "object" ? (e as { file?: unknown }).file : null)).filter((f): f is string => typeof f === "string"));
  let packed = 0;
  files = unzipSync(data, {
    filter: (f) => {
      if (!wanted.has(f.name)) return false;
      packed += f.originalSize;
      if (packed > maxSamples * 4 + entries.length * 1024) throw new ProjectError("This project is too long for the editor.");
      return true;
    },
  });
  let rate: number | null = null;
  const out: tr.Track[] = [];
  entries.forEach((e: unknown, i: number) => {
    if (!e || typeof e !== "object" || typeof (e as { file?: unknown }).file !== "string") throw new ProjectError("This project's track list is damaged.");
    const entry = e as Record<string, unknown>;
    const bytes = files[entry.file as string];
    if (!bytes) throw new ProjectError(`Track ${i + 1}'s sound is missing from the project.`);
    const wav = readWav(bytes);
    rate ??= wav.rate;
    if (wav.rate !== rate) throw new ProjectError("The project's tracks have different sample rates.");
    const name = typeof entry.name === "string" && entry.name.trim() ? entry.name : `Track ${i + 1}`;
    out.push({
      doc: new AudioDoc(wav.rate, wav.samples),
      name,
      gainDb: num(entry.gain_db, tr.MIN_GAIN_DB, tr.MAX_GAIN_DB, 0),
      pan: num(entry.pan, -1, 1, 0),
      mute: entry.mute === true,
      solo: entry.solo === true,
      offset: Math.trunc(num(entry.offset, 0, 2 ** 40, 0)),
    });
  });
  const total = out.reduce((n, t) => n + t.doc.samples.length * (t.doc.samples[0]?.length ?? 0), 0);
  if (total > maxSamples) throw new ProjectError("This project is too long for the editor.");
  const active = typeof meta.active === "number" && Number.isInteger(meta.active) && meta.active >= 0 && meta.active < out.length ? meta.active : 0;
  return { tracks: out, active };
}
