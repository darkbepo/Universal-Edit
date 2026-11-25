// A file's new name, checked like the desktop (a port of clean_name in
// src/umc/properties/fileinfo.py; both suites check tests/fixtures/fileinfo_cases.json): a missing
// extension is added back, another extension is refused (that would only relabel the file).
// The website can't rename the file on disk or change its date: the new name is used in the
// queue (what conversions are called) and for "Download with this name".

export const MAX_NAME = 255;
const BAD_CHARS = '<>:"/\\|?*';
const RESERVED = new Set(["con", "prn", "aux", "nul", ...[1, 2, 3, 4, 5, 6, 7, 8, 9].flatMap((i) => [`com${i}`, `lpt${i}`])]);
const WHITESPACE = /^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g;

export class FileError extends Error {}

/** ".jpg" of "photo.jpg"; "" for "notes" or ".gitignore". */
export function suffixOf(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  const stem = name.slice(0, dot);
  const ext = name.slice(dot + 1);
  return stem && ext && !ext.includes(" ") ? `.${ext}` : "";
}

/** Python's len(): code points. */
const length = (s: string) => [...s].length;

/** The new file name for a file called `old` (unchanged = `old`); throws FileError. */
export function cleanName(text: string, old: string): string {
  let name = text.replace(WHITESPACE, "");
  if (!name) throw new FileError("Type a name for the file.");
  const shown = [...new Set([...name].filter((c) => BAD_CHARS.includes(c)))].sort();
  if ([...name].some((c) => c.codePointAt(0)! < 32)) shown.push("line breaks or tabs");
  if (shown.length) throw new FileError(`A file name can't contain ${shown.join(" ")}.`);
  if (name === "." || name === ".." || name.endsWith(".")) throw new FileError("A file name can't end with a dot.");
  const oldExt = suffixOf(old);
  const newExt = suffixOf(name);
  if (!newExt && oldExt) name += oldExt;
  else if (newExt.toLowerCase() !== oldExt.toLowerCase()) {
    const keep = oldExt || "no extension";
    throw new FileError(`Keep the file's extension (${keep}): changing it doesn't change what's inside. Use Convert to make another type.`);
  }
  if (RESERVED.has(name.split(".")[0].toLowerCase().replace(/\s+$/, ""))) throw new FileError(`Windows doesn't allow a file called “${name}”.`);
  if (length(name) > MAX_NAME) throw new FileError(`The name is too long (at most ${MAX_NAME} characters).`);
  return name;
}
