/** Shared types for the document editor (port of src/umc/doceditor/types.py; no DOM). */

/** Picture bytes by the name the HTML refers to them with ("img/1.png"). */
export type DocImages = Map<string, Uint8Array>;

export const WEB_EDITABLE_DOCUMENT_EXTS = ["docx", "odt", "rtf", "md", "txt", "html"];
export const MAX_FILE_BYTES = 50 * 1024 * 1024;
export const MAX_CHARS = 2_000_000;
// A DOCX whose parts would unpack to more than this is refused (zip bombs).
export const MAX_UNPACKED_BYTES = 500 * 1024 * 1024;
export const TOO_LARGE = "This document is too large to edit here.";

// Things a document can contain that the editor can't keep (same strings as the desktop).
export const W_HEADERS = "headers and footers";
export const W_TEXTBOX = "text boxes and shapes";
export const W_COLUMNS = "multiple columns";
export const W_TRACKED = "tracked changes";
export const W_COMMENTS = "comments";
export const W_NOTES = "footnotes and endnotes";
export const W_FIELDS = "fields";
export const W_OBJECTS = "embedded objects";
export const W_PICTURES = "pictures in a format the editor can't show";
export const W_LISTSTYLE = "numbering with letters or Roman numerals";
export const W_PAGESTYLE = "page styles, classes and scripts";
export const W_WEBPICS = "pictures from the web";
export const W_RAWHTML = "embedded HTML the editor doesn't support";
export const W_MDEXTRA = "footnotes and other Markdown extras";
export const W_MDFORMAT = "inline code, small headings, picture descriptions, comments and table alignment";
export const W_EQUATIONS = "equations and embedded documents";
export const W_RTL_RUBY = "ruby annotations and right-to-left direction";

export const WARNINGS: Record<string, string> = {
  W_HEADERS,
  W_TEXTBOX,
  W_COLUMNS,
  W_TRACKED,
  W_COMMENTS,
  W_NOTES,
  W_FIELDS,
  W_OBJECTS,
  W_PICTURES,
  W_LISTSTYLE,
  W_PAGESTYLE,
  W_WEBPICS,
  W_RAWHTML,
  W_MDEXTRA,
  W_MDFORMAT,
  W_EQUATIONS,
  W_RTL_RUBY,
};

export interface OpenedDocument {
  html: string;
  images: DocImages;
  sourceFormat: string;
  warnings: string[];
  /** Markdown: the "---" block at the top, kept verbatim. */
  frontMatter: string;
  /** HTML: the page's <title>. */
  title: string;
  /** The original DOCX bytes when the source was DOCX (styles, page setup and headers are kept). */
  template: Uint8Array | null;
}

export interface SaveContent {
  html: string;
  images: DocImages;
  plainText: string;
  frontMatter: string;
  title: string;
  template: Uint8Array | null;
}

/** One plain-language sentence for the "parts that will be lost" notice. */
export function warningText(warnings: string[]): string {
  if (warnings.length === 0) return "";
  return (
    `This document has parts the editor can't keep: ${warnings.join(", ")}. ` +
    "They will be lost if you save over the original — use Save as to keep a copy."
  );
}

export function nextImageName(images: DocImages, ext: string): string {
  return `img/${images.size + 1}.${ext}`;
}

/** 32-bit FNV-1a of `data` (a quick key; equal pictures are still compared byte by byte). */
function fnv1a(data: Uint8Array): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < data.length; i++) h = Math.imul(h ^ data[i], 0x01000193);
  return h >>> 0;
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** The names given to pictures so far, so one picture used twice keeps one name (as on desktop,
 * where the key is the bytes and the extension). */
export class PictureNames {
  private readonly byKey = new Map<string, { data: Uint8Array; name: string }[]>();

  find(data: Uint8Array, ext: string): string | null {
    for (const p of this.byKey.get(`${ext}:${data.length}:${fnv1a(data)}`) ?? []) {
      if (sameBytes(p.data, data)) return p.name;
    }
    return null;
  }

  add(name: string, data: Uint8Array, ext: string): void {
    const key = `${ext}:${data.length}:${fnv1a(data)}`;
    this.byKey.set(key, [...(this.byKey.get(key) ?? []), { data, name }]);
  }
}
