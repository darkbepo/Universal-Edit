// Changing a song's tags on the website — the same fields and rules as the desktop's
// properties/music.py (cleanValues / common are line-by-line ports; both suites check
// tests/fixtures/music_cases.json). The desktop writes the tags with mutagen; here FFmpeg copies
// the sound unchanged (-c copy) and writes new tags, so the result reads the same in both apps.
// OGG / Opus covers live in a tag FFmpeg can't write back, so the browser can't change those
// (canChangeCover) and refuses a change that would lose one.
import { wellFormed, wsTrim } from "./exif";

export const WRITABLE_EXTS = new Set(["mp3", "m4a", "flac", "ogg", "opus"]);
export const FIELDS = ["title", "artist", "album", "album_artist", "track", "disc", "year", "genre"] as const;
export type Field = (typeof FIELDS)[number];
export const MAX_COVER_BYTES = 20 * 1024 * 1024;

export class MusicError extends Error {}

export interface MusicDetails {
  title: string; artist: string; album: string; album_artist: string;
  track: string; disc: string; year: string; genre: string; has_cover: boolean;
}

export interface MusicEdit {
  /** Only what the user changed ("" clears it). */
  values: Partial<Record<Field, string>>;
  /** New cover picture bytes (JPG / PNG). */
  cover?: Uint8Array | null;
  remove_cover?: boolean;
}

export const canChange = (ext: string) => WRITABLE_EXTS.has(ext.toLowerCase());
/** FFmpeg can't write OGG / Opus cover pictures. */
export const canChangeCover = (ext: string) => !["ogg", "opus"].includes(ext.toLowerCase());
export const isEmpty = (e: MusicEdit) => Object.keys(e.values).length === 0 && !e.cover && !e.remove_cover;

const NUMBER = /^(\d{1,4})(?:[ \t\n\r\f\v]*\/[ \t\n\r\f\v]*(\d{1,4}))?$/;
const NUMBER_TEXT: Partial<Record<Field, [string, string, string]>> = {
  track: [
    "Write the track number like 3, or 3/12 with the number of tracks.",
    "The track number can't be 0.",
    "The track number can't be bigger than the number of tracks.",
  ],
  disc: [
    "Write the disc number like 1, or 1/2 with the number of discs.",
    "The disc number can't be 0.",
    "The disc number can't be bigger than the number of discs.",
  ],
};
export const YEAR_TEXT = "Write the year with four digits, like 2026.";

/** "3", "3/12", "03 / 12" → [3, null] / [3, 12]; anything else → null. */
export function numberPair(text: string): [number, number | null] | null {
  const m = NUMBER.exec(wsTrim(text));
  if (!m) return null;
  return [Number(m[1]), m[2] !== undefined ? Number(m[2]) : null];
}

/** The user's text → what's written ("" = remove the tag); throws MusicError. */
export function cleanValues(values: Partial<Record<Field, string>>): Partial<Record<Field, string>> {
  const out: Partial<Record<Field, string>> = {};
  for (const name of FIELDS) {
    if (!(name in values)) continue;
    let text = wsTrim(wellFormed(values[name] ?? ""));
    const texts = NUMBER_TEXT[name];
    if (texts && text) {
      const pair = numberPair(text);
      const [shape, zero, bigger] = texts;
      if (pair === null) throw new MusicError(shape);
      const [n, total] = pair;
      if (n === 0 || total === 0) throw new MusicError(zero);
      if (total !== null && n > total) throw new MusicError(bigger);
      text = total !== null ? `${n}/${total}` : String(n);
    } else if (name === "year" && text && !/^\d{4}$/.test(text)) {
      throw new MusicError(YEAR_TEXT);
    }
    out[name] = text;
  }
  return out;
}

/** "image/jpeg" / "image/png" from the bytes; throws MusicError for anything else. */
export function coverMime(data: Uint8Array): string {
  if (data.length > MAX_COVER_BYTES) throw new MusicError("That picture is too big for a cover (over 20 MB).");
  if (data[0] === 0xff && data[1] === 0xd8 && data[2] === 0xff) return "image/jpeg";
  const png = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (data.length >= 8 && png.every((b, i) => data[i] === b)) return "image/png";
  throw new MusicError("The cover has to be a JPG or PNG picture.");
}

/** Each field's value when every song has the same one, else null ("Several values"). */
export function common(details: MusicDetails[]): Record<Field, string | null> {
  const out = {} as Record<Field, string | null>;
  for (const name of FIELDS) {
    const values = new Set(details.map((d) => String(d[name])));
    out[name] = values.size === 1 ? [...values][0] : null;
  }
  return out;
}

/** Mediabunny's MetadataTags (the parts we use) → the form's values. */
export interface TagsLike {
  title?: string; artist?: string; album?: string; albumArtist?: string; genre?: string;
  trackNumber?: number; tracksTotal?: number; discNumber?: number; discsTotal?: number;
  date?: Date; images?: unknown[]; raw?: Record<string, unknown>;
}

const numberText = (n?: number, total?: number) => (n && n > 0 ? (total && total > 0 ? `${n}/${total}` : String(n)) : "");

export function detailsFromTags(tags: TagsLike): MusicDetails {
  const raw = tags.raw ?? {};
  // a year that isn't a whole date ("1959") may not become a Date; take it from the raw tags
  const rawYear = ["TYER", "TDRC", "©day", "DATE", "YEAR", "date"].map((k) => raw[k]).find((v) => typeof v === "string") as string | undefined;
  const year = tags.date && !Number.isNaN(tags.date.getTime()) ? String(tags.date.getUTCFullYear()).padStart(4, "0") : /\d{4}/.exec(rawYear ?? "")?.[0] ?? "";
  return {
    title: tags.title ?? "", artist: tags.artist ?? "", album: tags.album ?? "", album_artist: tags.albumArtist ?? "",
    track: numberText(tags.trackNumber, tags.tracksTotal), disc: numberText(tags.discNumber, tags.discsTotal),
    year, genre: tags.genre ?? "", has_cover: Boolean(tags.images?.length),
  };
}

const VORBIS = new Set(["flac", "ogg", "opus"]);
// FFmpeg's own names (it writes each format's real tag: TIT2 / ©nam / TITLE …)
const KEYS: Record<Field, string> = {
  title: "title", artist: "artist", album: "album", album_artist: "album_artist",
  track: "track", disc: "disc", year: "date", genre: "genre",
};
// Vorbis comments: other spellings the desktop removes too, and where the totals go
const VORBIS_ALSO: Partial<Record<Field, string[]>> = { album_artist: ["ALBUM ARTIST"], year: ["YEAR"] };
const VORBIS_TOTALS: Partial<Record<Field, [string, string]>> = { track: ["TRACKTOTAL", "TOTALTRACKS"], disc: ["DISCTOTAL", "TOTALDISCS"] };

/**
 * FFmpeg arguments that copy ``input`` to ``output`` with ``edit`` applied. ``cover`` is the
 * mounted new cover picture (or null); ``hasCover``: the file has one now. Throws MusicError.
 */
export function ffmpegArgs(ext: string, input: string, output: string, edit: MusicEdit, cover: string | null, hasCover: boolean): string[] {
  ext = ext.toLowerCase();
  if (!canChange(ext)) throw new MusicError("Only MP3, M4A, FLAC, OGG and Opus files can be changed here.");
  const values = cleanValues(edit.values);
  if (edit.cover) coverMime(edit.cover);
  const vorbis = VORBIS.has(ext);
  if (!canChangeCover(ext)) {
    if (edit.cover) throw new MusicError("The browser can't add a cover to OGG or Opus files — the desktop app can.");
    if (hasCover && !edit.remove_cover) {
      throw new MusicError("This file's cover picture would be lost in the browser. The desktop app can change it and keep the cover — or remove the cover here.");
    }
  }
  const args = ["-i", input];
  if (cover) args.push("-i", cover);
  args.push("-map", "0:a");
  if (cover) {
    args.push("-map", "1", "-disposition:v:0", "attached_pic", "-metadata:s:v:0", "comment=Cover (front)", "-metadata:s:v:0", "title=Cover");
  } else if (!edit.remove_cover && canChangeCover(ext)) {
    args.push("-map", "0:v?");
  }
  args.push("-c", "copy", "-map_metadata", "0", "-fflags", "+bitexact");
  if (ext === "mp3") args.push("-id3v2_version", "3");
  // OGG / Opus keep their comments on the sound stream, the others on the file
  const where = ext === "ogg" || ext === "opus" ? "-metadata:s:a:0" : "-metadata";
  const set = (key: string, value: string) => args.push(where, `${key}=${value}`);
  for (const name of FIELDS) {
    const text = values[name];
    if (text === undefined) continue;
    const totals = VORBIS_TOTALS[name];
    if (vorbis && totals) {
      const pair = text ? numberPair(text) : null;
      set(KEYS[name], pair ? String(pair[0]) : "");
      set(totals[0], pair && pair[1] !== null ? String(pair[1]) : "");
      set(totals[1], "");
      continue;
    }
    set(KEYS[name], text);
    if (vorbis) for (const k of VORBIS_ALSO[name] ?? []) set(k, "");
  }
  args.push(output);
  return args;
}
