// Song details (title, artist, album, …, cover) kept when the audio editor saves — a line-by-line
// port of src/umc/audioedit/tags.py (`carried`). Several joined files keep only what they all
// share (a cover only when every file has the same one). Written by the hidden "music-tags"
// converter after the sound is encoded. Both suites check tests/fixtures/audioedit_tags_cases.json.
import { canChange, canChangeCover, cleanValues, common, coverMime, FIELDS, type Field, type MusicDetails } from "../properties/music";

export interface CarriedTags {
  /** Only fields with text. */
  values: Partial<Record<Field, string>>;
  cover: Uint8Array | null;
}

export const EMPTY_TAGS: CarriedTags = { values: {}, cover: null };

const EMPTY: MusicDetails = { title: "", artist: "", album: "", album_artist: "", track: "", disc: "", year: "", genre: "", has_cover: false };

const sameBytes = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((v, i) => v === b[i]);

/** The details every file shares. `null` = a file without readable tags. */
export function carriedTags(details: (MusicDetails | null)[], covers: (Uint8Array | null)[]): CarriedTags {
  if (!details.length) return { values: {}, cover: null };
  const shared = common(details.map((d) => d ?? EMPTY));
  const values: Partial<Record<Field, string>> = {};
  for (const name of FIELDS) if (shared[name]) values[name] = shared[name] as string;
  const first = covers[0] ?? null;
  const cover = first && covers.every((c) => c !== null && sameBytes(c, first)) ? first : null;
  return { values, cover };
}

/** What to write into a file saved as `fmt`; null when nothing (or it can't hold tags).
 *  The browser can't write OGG / Opus covers, so those keep the text only. */
export function editFor(tags: CarriedTags, fmt: string): { values: Partial<Record<Field, string>>; cover: Uint8Array | null } | null {
  if (!canChange(fmt)) return null;
  let cover = canChangeCover(fmt) ? tags.cover : null;
  if (cover) {
    try {
      coverMime(cover);
    } catch {
      cover = null; // not a JPG / PNG: keep the text details anyway
    }
  }
  const values: Partial<Record<Field, string>> = {};
  for (const name of Object.keys(tags.values) as Field[]) {
    try {
      // one odd field (a vinyl track "A1") mustn't cost the others
      Object.assign(values, cleanValues({ [name]: tags.values[name] }));
    } catch {
      /* not a value the tags can hold: leave it out */
    }
  }
  if (!Object.keys(values).length && !cover) return null;
  return { values, cover };
}
