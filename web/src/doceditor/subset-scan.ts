/**
 * The pre-parse scan of the HTML normaliser (the desktop's `html_scan.py`): finds tags the way
 * the desktop's libxml2 reads them, so numeric references, NUL / control characters, the paragraph
 * marks and  inside <pre> can be adjusted before the page's HTML5 parser sees the text.
 */

const set = (s: string) => new Set(s.split(" "));

// \r inside <pre> is kept by libxml2 but turned into \n by an HTML5 parser: it travels as this
// noncharacter and is put back whenever text or attributes are read.
const CR = String.fromCharCode(0xfdd0);
const CR_ALL = new RegExp(CR, "g");
const XML_FORBIDDEN = new RegExp(
  `[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f${String.fromCharCode(0xfffe)}${String.fromCharCode(0xffff)}]`,
  "g",
);
export const restore = (s: string) => (s.includes(CR) ? s.replace(CR_ALL, "\r") : s);

// ----- numeric references (the desktop's _html5_text) --------------------------------------

const REPLACEMENT = String.fromCharCode(0xfffd);
const NUM_REF = /&#(?:[xX]([0-9a-fA-F]+)|([0-9]+));?/g;
// Windows-1252 for 0x80-0x9F (0: not defined there, the reference is kept).
const CP1252 = [
  0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021, 0x02c6, 0x2030, 0x0160, 0x2039,
  0x0152, 0, 0x017d, 0, 0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014, 0x02dc, 0x2122,
  0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
];

/** A reference read as U+FFFD: 0, C0 controls other than tab / LF / FF / CR, DEL, surrogates,
 * noncharacters and anything past U+10FFFF (the desktop's libxml2 can't keep them). */
function badCodePoint(n: number): boolean {
  return (
    n === 0 ||
    (n < 0x20 && n !== 0x09 && n !== 0x0a && n !== 0x0c && n !== 0x0d) ||
    n === 0x7f ||
    (n >= 0xd800 && n <= 0xdfff) ||
    (n >= 0xfdd0 && n <= 0xfdef) ||
    (n & 0xfffe) === 0xfffe ||
    n > 0x10ffff
  );
}

function numRef(ref: string, hex: string | undefined, dec: string | undefined): string {
  const digits = (hex ?? dec ?? "").replace(/^0+/, "");
  // Longer numbers are past U+10FFFF anyway.
  const n = digits.length <= 8 ? parseInt(digits || "0", hex !== undefined ? 16 : 10) : 0x110000;
  if (n >= 128 && n <= 159) {
    const c = CP1252[n - 128];
    return c ? String.fromCharCode(c) : ref;
  }
  return badCodePoint(n) ? REPLACEMENT : ref;
}

const numRefs = (s: string) => (s.includes("&#") ? s.replace(NUM_REF, numRef) : s);

// Raw text elements: references in them are text, so they are left alone. Comments, <script>,
// <style> and the RCDATA elements (<title>, <textarea>) are scanned past, so a "<xmp" inside
// them isn't taken for a tag.
const RAW_TEXT = set("xmp noembed noframes plaintext iframe");
const SPECIAL_END = new Map(
  "script style xmp noembed noframes iframe title textarea"
    .split(" ")
    .map((name) => [name, new RegExp(`</${name}(?=[\\t\\n\\f\\r />])`, "gi")]),
);
// A comment, or a start / end tag and its name (HTML5: a letter, then anything up to a space,
// "/" or ">").
const TAG_START = /<(?:(!--)|(\/?)([A-Za-z][^\t\n\f\r />]*))/g;
const isTagSpace = (c: string) => c === " " || c === "\t" || c === "\n" || c === "\f" || c === "\r";

/** The index of the ">" that ends a tag whose attributes start at `i` (-1 when it never ends).
 * Quoted attribute values are skipped, so a ">" or "<xmp>" inside one is not markup. */
function tagEnd(html: string, i: number): number {
  const n = html.length;
  let inName = false; // in (or after) an attribute name, where "=" starts its value
  while (i < n) {
    const c = html[i];
    if (c === ">") return i;
    if (c === "=" && inName) {
      i++;
      while (i < n && isTagSpace(html[i])) i++;
      if (i < n && (html[i] === '"' || html[i] === "'")) {
        const close = html.indexOf(html[i], i + 1);
        if (close < 0) return -1;
        i = close + 1;
      } else {
        // unquoted: up to a space or ">"
        while (i < n && !isTagSpace(html[i]) && html[i] !== ">") i++;
      }
      inName = false;
      continue;
    }
    if (c === "/") inName = false;
    else if (!isTagSpace(c)) inName = true;
    i++;
  }
  return -1;
}

interface Tag {
  name: string; // lower case
  closing: boolean;
  nameEnd: number; // where the attributes start
  end: number; // just past the ">"
  bodyEnd: number; // just past the text content of a raw text / RCDATA / script element
}

/** The next start or end tag from `from` on, read the way the desktop's scan reads it
 * (html_subset._html5_text): comments are skipped, and so is the text of raw text, RCDATA,
 * <script> and <style> elements (up to `bodyEnd`). Null when no further tag ends. */
function nextTag(html: string, from: number): Tag | null {
  let i = from;
  for (;;) {
    TAG_START.lastIndex = i;
    const m = TAG_START.exec(html);
    if (!m) return null;
    const after = m.index + m[0].length;
    if (m[1]) {
      // a comment, up to "-->" (an unclosed one runs to the end)
      const close = html.indexOf("-->", after);
      if (close < 0) return null;
      i = close + 3;
      continue;
    }
    const gt = tagEnd(html, after);
    if (gt < 0) return null;
    const name = m[3].toLowerCase();
    const closing = m[2] === "/";
    let bodyEnd = gt + 1;
    if (!closing && name === "plaintext") bodyEnd = html.length; // never ends
    else if (!closing && SPECIAL_END.has(name)) {
      const closer = SPECIAL_END.get(name) as RegExp;
      closer.lastIndex = gt + 1;
      bodyEnd = closer.exec(html)?.index ?? html.length;
    }
    return { name, closing, nameEnd: after, end: gt + 1, bodyEnd };
  }
}

/** Numeric references the way the desktop reads them (it maps them before libxml2 parses the
 * page): invalid code points become U+FFFD. One pass from left to right. */
export function html5Refs(html: string): string {
  if (!html.includes("&#")) return html;
  const out: string[] = [];
  let pos = 0;
  for (let t = nextTag(html, 0); t; t = nextTag(html, t.bodyEnd)) {
    if (t.bodyEnd > t.end && RAW_TEXT.has(t.name)) {
      out.push(numRefs(html.slice(pos, t.end)), html.slice(t.end, t.bodyEnd));
      pos = t.bodyEnd;
    }
  }
  out.push(numRefs(html.slice(pos)));
  return out.join("");
}

// Marks the <p> start tags of the source, so the empty paragraph HTML5 makes for a stray </p>
// (libxml2 ignores it) can be told apart and removed.
export const P_MARK = "data-umc-p";
// A numeric reference to a line feed at the start of a text.
const LF_REF = /^&#(?:0*10(?![0-9])|[xX]0*[aA](?![0-9a-fA-F]))/;

/** Make an HTML5 parser read `html` the way libxml2 does where they differ. */
export function libxmlLike(html: string): string {
  // libxml2 turns NUL into a space and drops the other characters XML 1.0 forbids.
  html = html.replace(/\x00/g, " ").replace(XML_FORBIDDEN, "");
  const marked: string[] = [];
  let from = 0;
  for (let t = nextTag(html, 0); t; t = nextTag(html, t.bodyEnd)) {
    if (t.name === "p" && !t.closing) {
      // with a value, so a "=" that follows (`<p ="x">`) doesn't become its value
      marked.push(html.slice(from, t.nameEnd), ` ${P_MARK}=""`);
      from = t.nameEnd;
    }
  }
  marked.push(html.slice(from));
  html = marked.join("");
  // Inside <pre>: keep \r (as CR, see above) and the first newline, which HTML5 drops.
  const closeTag = /<\/pre\s*>/gi;
  const parts: string[] = [];
  from = 0;
  for (let t = nextTag(html, 0); t; ) {
    if (t.name !== "pre" || t.closing) {
      t = nextTag(html, t.bodyEnd);
      continue;
    }
    closeTag.lastIndex = t.end;
    const end = closeTag.exec(html)?.index ?? html.length;
    const kept = keepCarriageReturns(html.slice(t.end, end));
    // (also a newline written as a reference: HTML5 drops that one too)
    parts.push(html.slice(from, t.end), kept.startsWith("\n") || LF_REF.test(kept) ? "\n" : "", kept);
    from = end;
    t = nextTag(html, end);
  }
  parts.push(html.slice(from));
  return parts.join("");
}

/** \r becomes CR in text, not inside tags. */
function keepCarriageReturns(body: string): string {
  if (!body.includes("\r")) return body;
  let out = "";
  let i = 0;
  while (i < body.length) {
    const lt = body.indexOf("<", i);
    const gt = lt < 0 ? -1 : body.indexOf(">", lt + 1);
    const textEnd = gt < 0 ? body.length : lt;
    out += body.slice(i, textEnd).replace(/\r/g, CR);
    if (gt < 0) break;
    out += body.slice(lt, gt + 1);
    i = gt + 1;
  }
  return out;
}

