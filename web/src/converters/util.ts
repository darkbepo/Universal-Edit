// Small helpers shared by converters.

export function baseName(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

/** Returns a function that de-duplicates output names: "a.png", "a (1).png", … */
export function uniqueNamer(): (name: string) => string {
  const used = new Set<string>();
  return (name) => {
    let candidate = name;
    const dot = name.lastIndexOf(".");
    const stem = dot > 0 ? name.slice(0, dot) : name;
    const ext = dot > 0 ? name.slice(dot) : "";
    for (let n = 1; used.has(candidate); n++) candidate = `${stem} (${n})${ext}`;
    used.add(candidate);
    return candidate;
  };
}

/** Keep ``perThirty`` evenly spaced frames out of every 30 (FFmpeg select expression). */
export function selectExpr(perThirty: number): string {
  if (perThirty >= 30) return "";
  const m = "mod(n\\,30)";
  return `select='eq(floor((${m}+1)*${perThirty}/30)-floor(${m}*${perThirty}/30)\\,1)'`;
}

export const MIME: Record<string, string> = {
  mp4: "video/mp4", m4v: "video/mp4", mov: "video/quicktime", webm: "video/webm",
  mkv: "video/x-matroska", avi: "video/x-msvideo", gif: "image/gif", mp3: "audio/mpeg",
  m4a: "audio/mp4", wav: "audio/wav", flac: "audio/flac", ogg: "audio/ogg", opus: "audio/ogg",
  png: "image/png", jpg: "image/jpeg", webp: "image/webp", bmp: "image/bmp", tiff: "image/tiff",
  pdf: "application/pdf", txt: "text/plain",
};
