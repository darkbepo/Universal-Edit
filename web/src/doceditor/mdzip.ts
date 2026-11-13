/**
 * The Markdown-ZIP check (a ZIP the website saved for Markdown with pictures). Its own small
 * module: the Edit switch runs it on a lone archive without loading the editor's readers.
 */

import { zipEntryNames } from "./docx-package";

export const hasDotDot = (name: string) => name.split("/").some((part) => part === "..");

/**
 * The Markdown file name inside `bytes` when it is a Markdown ZIP the website saved: exactly
 * one `.md` at the root and every other entry inside `<that stem>_files/` (no `..`, no
 * backslashes, no absolute paths). Reads the central directory only. Otherwise null.
 */
export function markdownZipName(bytes: Uint8Array): string | null {
  let names: string[];
  try {
    names = zipEntryNames(bytes);
  } catch {
    return null;
  }
  const roots = names.filter((n) => !n.includes("/"));
  // A backslash is a folder separator to some unzip tools: never in any name.
  if (roots.length !== 1 || !/^.+\.md$/i.test(roots[0]) || roots[0].includes("\\")) return null;
  const md = roots[0];
  const folder = `${md.slice(0, -3)}_files/`;
  for (const n of names) {
    if (n === md) continue;
    if (!n.startsWith(folder) || n.includes("\\") || hasDotDot(n)) return null;
  }
  return md;
}

/** True when `bytes` is a Markdown ZIP saved by the website (see `markdownZipName`): the
 * Edit switch treats such a dropped `.zip` as one editable document. */
export const isEditableMarkdownZip = (bytes: Uint8Array): boolean => markdownZipName(bytes) !== null;
