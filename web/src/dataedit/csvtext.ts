// CSV / TSV text ↔ rows — a port of src/umc/dataedit/csvtext.py: our own RFC 4180 reader /
// writer, lenient like spreadsheets (a quote inside an unquoted field is kept, text after a
// closing quote is added to the field, an unclosed quote runs to the end).

export const SEPARATORS = [",", ";", "\t", "|"];
export const SNIFF_CHARS = 65536;
export const SNIFF_ROWS = 50;

export interface Parsed {
  rows: string[][];
  quoteAll: boolean; // every field was in quotes (written back the same way)
  finalNewline: boolean;
}

const QUOTED = /"((?:[^"]|"")*)"/y;
const plainCache = new Map<string, RegExp>();

function plain(sep: string): RegExp {
  let re = plainCache.get(sep);
  if (!re) {
    re = new RegExp(`[^${sep.replace(/[\\\]^-]/g, "\\$&")}\\n]*`, "y");
    plainCache.set(sep, re);
  }
  return re;
}

function matchAt(re: RegExp, text: string, pos: number): RegExpExecArray | null {
  re.lastIndex = pos;
  return re.exec(text);
}

/** `text` has "\n" line endings (codeedit/textfile normalises them). */
export function parse(text: string, sep: string): Parsed {
  const rows: string[][] = [];
  const n = text.length;
  const final = text.endsWith("\n");
  const end = final ? n - 1 : n;
  let pos = 0, quoted = 0, fields = 0;
  const p = plain(sep);
  let row: string[] = [];
  while (pos <= end && end > 0) {
    let value: string;
    if (pos < n && text[pos] === '"') {
      const m = matchAt(QUOTED, text, pos);
      if (m === null) {
        value = text.slice(pos + 1, end).replaceAll('""', '"'); // never closed: the rest
        pos = n;
      } else {
        value = m[1].replaceAll('""', '"');
        const tail = matchAt(p, text, pos + m[0].length)!;
        value += tail[0];
        pos = pos + m[0].length + tail[0].length;
      }
      quoted++;
    } else {
      const m = matchAt(p, text, pos)!;
      value = m[0];
      pos += m[0].length;
    }
    fields++;
    row.push(value);
    if (pos < n && text[pos] === sep) {
      pos++;
      continue;
    }
    rows.push(row);
    row = [];
    pos++; // the "\n" (or past the end)
  }
  return { rows, quoteAll: fields > 0 && quoted === fields, finalNewline: final };
}

/** The separator that gives the same field count (> 1) on most of the first rows; "," when
 * none does. */
export function sniff(text: string): string {
  let sample = text;
  if (text.length > SNIFF_CHARS) {
    const cut = text.lastIndexOf("\n", SNIFF_CHARS - 1);
    sample = cut > 0 ? text.slice(0, cut + 1) : text.slice(0, SNIFF_CHARS);
  }
  let best = ",", bestScore = 0;
  for (const sep of SEPARATORS) {
    const counts = new Map<number, number>();
    for (const r of parse(sample, sep).rows.slice(0, SNIFF_ROWS)) counts.set(r.length, (counts.get(r.length) ?? 0) + 1);
    if (!counts.size) continue;
    let width = 0, freq = 0;
    for (const [w, f] of counts) if (f > freq || (f === freq && w > width)) [width, freq] = [w, f];
    const score = width > 1 ? freq : 0;
    if (score > bestScore) [best, bestScore] = [sep, score];
  }
  return best;
}

function field(value: string, sep: string, quoteAll: boolean): string {
  if (quoteAll || value.includes(sep) || value.includes('"') || value.includes("\n") || value.includes("\r")) {
    return '"' + value.replaceAll('"', '""') + '"';
  }
  return value;
}

/** The rows as text with "\n" line endings. */
export function write(rows: string[][], sep: string, quoteAll: boolean, finalNewline: boolean): string {
  const lines = rows.map((r) => r.map((v) => field(v, sep, quoteAll)).join(sep));
  return lines.join("\n") + (finalNewline && lines.length ? "\n" : "");
}
