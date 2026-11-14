// Reading and writing text files byte for byte — a port of src/umc/codeedit/textfile.py.
// The text is handed to the editor with "\n" line endings; encode() puts the file's own encoding,
// BOM and line endings back. Saving untouched text returns the bytes as read.

export type Encoding = "utf-8" | "utf-16-le" | "utf-16-be" | "windows-1252";
export type Newline = "\r\n" | "\n" | "\r";

export const SNIFF_BYTES = 8192;

export interface TextFile {
  text: string; // line endings normalised to "\n"
  encoding: Encoding;
  bom: boolean;
  newline: Newline; // the most common one ("\n" when there is none; ties → "\r\n")
  mixed: boolean; // more than one kind in the file
  raw: Uint8Array; // the bytes as read
}

const BOMS: [number[], Encoding][] = [
  [[0xef, 0xbb, 0xbf], "utf-8"],
  [[0xff, 0xfe], "utf-16-le"],
  [[0xfe, 0xff], "utf-16-be"],
];
const TEXT_CONTROLS = new Set([0x09, 0x0a, 0x0c, 0x0d, 0x1b]); // tab, LF, form feed, CR, ESC

// Windows-1252 bytes 0x80–0x9F as browsers read them (undefined ones stand for U+0081 …).
const CP1252_DECODE: string[] = (() => {
  const dec = new TextDecoder("windows-1252");
  const out: string[] = [];
  for (let b = 0x80; b < 0xa0; b++) out.push(dec.decode(new Uint8Array([b])));
  return out;
})();
const CP1252_ENCODE = new Map(CP1252_DECODE.map((ch, i) => [ch.codePointAt(0)!, 0x80 + i]));

function bomOf(data: Uint8Array): [number[], Encoding] | null {
  return BOMS.find(([bom]) => bom.every((b, i) => data[i] === b)) ?? null;
}

export function isBinary(head: Uint8Array): boolean {
  const bom = bomOf(head);
  if (bom && bom[1] !== "utf-8") return false;
  return head.subarray(0, SNIFF_BYTES).includes(0);
}

export function looksLikeText(head: Uint8Array): boolean {
  head = head.subarray(0, SNIFF_BYTES);
  if (isBinary(head)) return false;
  if (bomOf(head)) return true;
  try {
    new TextDecoder("utf-8", { fatal: true }).decode(head, { stream: true }); // a cut-off last character is fine
    return true;
  } catch {
    /* not UTF-8 */
  }
  let controls = 0;
  for (const b of head) if ((b < 0x20 && !TEXT_CONTROLS.has(b)) || b === 0x7f) controls++;
  return controls <= head.length * 0.05;
}

function decodeText(body: Uint8Array, encoding: Encoding): string {
  return new TextDecoder(encoding === "utf-16-le" ? "utf-16le" : encoding === "utf-16-be" ? "utf-16be" : encoding, {
    ignoreBOM: true,
    fatal: encoding !== "windows-1252",
  }).decode(body);
}

function newlines(text: string): [Newline, boolean] {
  const count = (s: string) => text.split(s).length - 1;
  const crlf = count("\r\n");
  const counts: [number, Newline][] = [[crlf, "\r\n"], [count("\n") - crlf, "\n"], [count("\r") - crlf, "\r"]];
  const kinds = counts.filter(([n]) => n).length;
  if (!kinds) return ["\n", false];
  let best = counts[0];
  for (const c of counts) if (c[0] > best[0]) best = c; // the first wins ties: CRLF, then LF
  return [best[1], kinds > 1];
}

/** Throws Error("binary") for files that aren't text. */
export function decode(data: Uint8Array): TextFile {
  if (isBinary(data)) throw new Error("binary");
  const found = bomOf(data);
  let text: string;
  let encoding: Encoding;
  if (found) {
    encoding = found[1];
    text = decodeText(data.subarray(found[0].length), encoding);
  } else {
    try {
      text = decodeText(data, "utf-8");
      encoding = "utf-8";
    } catch {
      text = decodeText(data, "windows-1252");
      encoding = "windows-1252";
    }
  }
  const [newline, mixed] = newlines(text);
  const normal = text.replaceAll("\r\n", "\n").replaceAll("\r", "\n");
  return { text: normal, encoding, bom: !!found, newline, mixed, raw: data };
}

function encodeBody(body: string, enc: Encoding): Uint8Array {
  if (enc === "utf-8") return new TextEncoder().encode(body);
  if (enc === "windows-1252") {
    const out = new Uint8Array(body.length);
    for (let i = 0; i < body.length; i++) {
      const c = body.charCodeAt(i);
      const mapped = CP1252_ENCODE.get(c);
      if (mapped !== undefined) out[i] = mapped;
      else if (c < 0x80 || (c >= 0xa0 && c <= 0xff)) out[i] = c;
      else throw new Error("encoding");
    }
    return out;
  }
  const out = new Uint8Array(body.length * 2); // UTF-16 code units, surrogates included
  const view = new DataView(out.buffer);
  for (let i = 0; i < body.length; i++) view.setUint16(i * 2, body.charCodeAt(i), enc === "utf-16-le");
  return out;
}

/** The bytes to save. Throws Error("encoding") if the text doesn't fit the encoding. */
export function encode(
  f: TextFile,
  text: string,
  opts: { encoding?: Encoding; newline?: Newline; bom?: boolean } = {},
): Uint8Array {
  const enc = opts.encoding ?? f.encoding;
  const nl = opts.newline ?? f.newline;
  const withBom = opts.bom ?? f.bom;
  if (text === f.text && enc === f.encoding && nl === f.newline && withBom === f.bom && !(opts.newline && f.mixed)) {
    return f.raw;
  }
  const body = nl === "\n" ? text : text.replaceAll("\n", nl);
  const out = encodeBody(body, enc);
  if (!withBom || enc === "windows-1252") return out;
  const bom = BOMS.find(([, e]) => e === enc)![0];
  const result = new Uint8Array(bom.length + out.length);
  result.set(bom);
  result.set(out, bom.length);
  return result;
}
