// What a video file contains — just enough to hide targets that can't work ("Audio only" for a
// silent video, "Extract subtitles" for a video without text subtitles). Runs on the page when
// files are added, so it only reads the container's track list, never decodes anything.
// Every answer is true / false / null; null = couldn't tell, and the target stays offered.

export interface TrackInfo {
  audio: boolean | null;
  textSubs: boolean | null;
}

const MKV_TEXT_CODECS = ["S_TEXT/", "S_SSA", "S_ASS"]; // S_TEXT/UTF8, S_TEXT/ASS, S_TEXT/WEBVTT…
const MP4_TEXT_ENTRIES = ["tx3g", "wvtt", "stpp", "c608"];

const ascii = (bytes: Uint8Array) => {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return s;
};

/** MP4/MOV/M4V: find the 'moov' box (start or end of file) and look at its track handlers. */
async function mp4TextSubs(file: File): Promise<boolean | null> {
  let pos = 0;
  for (let guard = 0; guard < 64 && pos + 8 <= file.size; guard++) {
    const head = new DataView(await file.slice(pos, pos + 16).arrayBuffer());
    let size = head.getUint32(0);
    const type = ascii(new Uint8Array(head.buffer, 4, 4));
    if (size === 1) size = Number(head.getBigUint64(8));
    else if (size === 0) size = file.size - pos;
    if (size < 8) return null;
    if (type === "moov") {
      if (size > 64 * 1024 * 1024) return null; // absurd: don't read it all
      const moov = ascii(new Uint8Array(await file.slice(pos, pos + size).arrayBuffer()));
      const handlers = [...moov.matchAll(/hdlr[\s\S]{8}(vide|soun|sbtl|subt|text|clcp)/g)].map((m) => m[1]);
      if (handlers.some((h) => h === "sbtl" || h === "subt" || h === "text")) return true;
      if (MP4_TEXT_ENTRIES.some((e) => moov.includes(e))) return true;
      return handlers.length ? false : null;
    }
    pos += size;
  }
  return null;
}

/** MKV/WebM: the Tracks element sits near the start; text tracks have S_TEXT/… codec ids. */
async function mkvTextSubs(file: File): Promise<boolean | null> {
  const head = ascii(new Uint8Array(await file.slice(0, 4 * 1024 * 1024).arrayBuffer()));
  if (MKV_TEXT_CODECS.some((c) => head.includes(c))) return true;
  // Tracks element id 0x1654AE6B: seen (and no text codec in it) → confident there are none.
  return head.includes("\x16\x54\xae\x6b") ? false : null;
}

async function hasAudio(file: File): Promise<boolean | null> {
  try {
    const mb = await import("mediabunny");
    const input = new mb.Input({ source: new mb.BlobSource(file), formats: mb.ALL_FORMATS });
    try {
      return (await input.getAudioTracks()).length > 0;
    } finally {
      input.dispose?.();
    }
  } catch {
    return null; // a container Mediabunny can't read (AVI, FLV, WMV…)
  }
}

export async function probeTracks(file: File, ext: string): Promise<TrackInfo> {
  const subs = ["mp4", "m4v", "mov"].includes(ext) ? mp4TextSubs(file)
    : ["mkv", "webm"].includes(ext) ? mkvTextSubs(file)
    : Promise.resolve(null);
  const [audio, textSubs] = await Promise.all([hasAudio(file), subs.catch(() => null)]);
  return { audio, textSubs };
}
