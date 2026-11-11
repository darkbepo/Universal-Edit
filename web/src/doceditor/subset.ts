/**
 * Normalise arbitrary HTML into the document editor's small, canonical HTML subset.
 *
 * A port of the desktop's `src/umc/doceditor/html_subset.py` (read its docstring for the
 * subset); the output must be byte-identical, so the helpers below copy Python's behaviour
 * (str.strip, round, "%.2f", urlparse, PIL.ImageColor). Parsing uses the page's DOMParser; the
 * input is first adjusted where an HTML5 parser differs from libxml2 (control characters, CR
 * in <pre>, the newline after <pre>). The HTML is serialised from our own tokens.
 */

import {
  MAX_FILE_BYTES,
  W_PAGESTYLE,
  W_RAWHTML,
  W_WEBPICS,
  type DocImages,
  nextImageName,
} from "./types";

export interface NormaliseOptions {
  /** Bytes of a relative picture path ("sub/p.png"), or null. Never called for unsafe paths. */
  resolveImage?: (src: string) => Uint8Array | null;
}

const set = (s: string) => new Set(s.split(" "));
// Removed together with everything inside them.
const DROP = set(
  "script style head title meta link iframe object embed noscript template svg canvas audio " +
    "video applet textarea select option button frame frameset",
);
const BLOCK = set(
  "p div h1 h2 h3 h4 h5 h6 blockquote pre ul ol li table thead tbody tfoot tr td th caption hr " +
    "section article header footer nav aside main form figure figcaption address center dl dt " +
    "dd fieldset body html",
);
const LISTS = set("ul ol");
// Embedded content that is dropped (W_RAWHTML); forms keep their text, not their fields.
const RAW_DROPPED = set(
  "iframe object embed applet audio video canvas svg frame frameset form input textarea select button",
);
const INLINE_MAP: Record<string, string> = {
  b: "b",
  strong: "b",
  i: "i",
  em: "i",
  cite: "i",
  u: "u",
  ins: "u",
  s: "s",
  strike: "s",
  del: "s",
};
const HEADINGS: Record<string, string> = {
  h1: "h1",
  h2: "h2",
  h3: "h3",
  h4: "h3",
  h5: "h3",
  h6: "h3",
};
const IMAGE_EXTS: Record<string, string> = {
  png: "png",
  jpg: "jpg",
  jpeg: "jpg",
  gif: "gif",
  bmp: "bmp",
  webp: "webp",
};
const FONT_SIZES_PT: Record<string, number> = { 1: 8, 2: 10, 3: 12, 4: 14, 5: 18, 6: 24, 7: 36 };
const SAFE_SCHEMES = new Set(["", "http", "https", "mailto", "tel"]);

// Python's str.isspace() characters (used by strip(), split() and the Unicode \s of re).
const PY_WS =
  "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const S = `[${PY_WS}]`;
const PY_SPACE = new RegExp(S);

// Index loops, not regexes: `\s+$` is quadratic on long runs of white space.
function trimWith(s: string, isSpace: (c: string) => boolean): string {
  let start = 0;
  let end = s.length;
  while (start < end && isSpace(s[start])) start++;
  while (end > start && isSpace(s[end - 1])) end--;
  return start === 0 && end === s.length ? s : s.slice(start, end);
}
const isPySpace = (c: string) => PY_SPACE.test(c);
/** Python's str.strip(). */
const pyStrip = (s: string) => trimWith(s, isPySpace);
const isHtmlSpace = (c: string) =>
  c === " " || c === "\t" || c === "\r" || c === "\n" || c === "\f";
/** Python's `text.strip(" \t\r\n\f")` is not empty. */
const hasContent = (s: string) => {
  for (let i = 0; i < s.length; i++) if (!isHtmlSpace(s[i])) return true;
  return false;
};

/** `WS.sub(" ", text)` in linear time: runs of spaces, and line breaks with the indentation
 * around them, become one space; other tabs stay. */
function collapseWs(s: string): string {
  const parts: string[] = [];
  const n = s.length;
  let i = 0;
  let from = 0; // start of the text not copied yet
  while (i < n) {
    const c = s[i];
    if (c !== " " && c !== "\t" && c !== "\r" && c !== "\n" && c !== "\f") {
      i++;
      continue;
    }
    let j = i;
    while (j < n && (s[j] === " " || s[j] === "\t")) j++;
    if (j < n && (s[j] === "\r" || s[j] === "\n" || s[j] === "\f")) {
      while (j < n && isHtmlSpace(s[j])) j++;
      parts.push(s.slice(from, i), " ");
      i = from = j;
      continue;
    }
    // Spaces and tabs without a line break after them (true at every position of the run):
    // only runs of 2+ spaces collapse.
    for (let k = i; k < j;) {
      if (s[k] !== " ") {
        k++;
        continue;
      }
      let e = k;
      while (e < j && s[e] === " ") e++;
      if (e - k >= 2) {
        parts.push(s.slice(from, k), " ");
        from = e;
      }
      k = e;
    }
    i = j === i ? i + 1 : j;
  }
  parts.push(s.slice(from));
  return parts.join("");
}

const DATA_URI = new RegExp(`^data:image/(png|jpe?g|gif|bmp|webp)(?:;[^,;]*)*;base64,(.*)$`, "is");
const CANON_IMG = /^img\/\d+\.(?:png|jpg|gif|bmp|webp)$/;
const XML_DECL = new RegExp(`^${S}*<\\?xml[^>]*\\?>`, "i");
// Deepest paragraph indent, 720 pt (huge values used to print as "1e+27pt").
const MAX_INDENT = 20;
const UNREADABLE = "This file couldn't be read as HTML.";
// Python's recursion limit while normalising (the desktop raises past it); JS stack overflows
// are reported the same way.
const MAX_DEPTH = 20000;

type Kind = "T" | "O" | "C" | "V" | "S";
// T = text, O = opening tag, C = closing tag, V = void tag,
// S = a line break the normaliser added between blocks (dropped at the ends).
type Token = [Kind, string];
const isBr = (t: Token | undefined) => t !== undefined && t[0] === "V" && t[1] === "<br>";
const BR = (): Token => ["V", "<br>"];

// ----- Python helpers -------------------------------------------------------------------------

function parseStyle(value: string | null): Map<string, string> {
  const result = new Map<string, string>();
  for (const decl of (value ?? "").split(";")) {
    const i = decl.indexOf(":");
    if (i >= 0) {
      result.set(
        pyStrip(decl.slice(0, i)).toLowerCase(),
        pyStrip(decl.slice(i + 1).replace(new RegExp(`!${S}*important`, "gi"), "")),
      );
    }
  }
  return result;
}

const COLOURS = new Map(
  (
    "aliceblue:f0f8ff antiquewhite:faebd7 aqua:00ffff aquamarine:7fffd4 azure:f0ffff beige:f5f5dc " +
    "bisque:ffe4c4 black:000000 blanchedalmond:ffebcd blue:0000ff blueviolet:8a2be2 brown:a52a2a " +
    "burlywood:deb887 cadetblue:5f9ea0 chartreuse:7fff00 chocolate:d2691e coral:ff7f50 " +
    "cornflowerblue:6495ed cornsilk:fff8dc crimson:dc143c cyan:00ffff darkblue:00008b " +
    "darkcyan:008b8b darkgoldenrod:b8860b darkgray:a9a9a9 darkgrey:a9a9a9 darkgreen:006400 " +
    "darkkhaki:bdb76b darkmagenta:8b008b darkolivegreen:556b2f darkorange:ff8c00 " +
    "darkorchid:9932cc darkred:8b0000 darksalmon:e9967a darkseagreen:8fbc8f darkslateblue:483d8b " +
    "darkslategray:2f4f4f darkslategrey:2f4f4f darkturquoise:00ced1 darkviolet:9400d3 " +
    "deeppink:ff1493 deepskyblue:00bfff dimgray:696969 dimgrey:696969 dodgerblue:1e90ff " +
    "firebrick:b22222 floralwhite:fffaf0 forestgreen:228b22 fuchsia:ff00ff gainsboro:dcdcdc " +
    "ghostwhite:f8f8ff gold:ffd700 goldenrod:daa520 gray:808080 grey:808080 green:008000 " +
    "greenyellow:adff2f honeydew:f0fff0 hotpink:ff69b4 indianred:cd5c5c indigo:4b0082 " +
    "ivory:fffff0 khaki:f0e68c lavender:e6e6fa lavenderblush:fff0f5 lawngreen:7cfc00 " +
    "lemonchiffon:fffacd lightblue:add8e6 lightcoral:f08080 lightcyan:e0ffff " +
    "lightgoldenrodyellow:fafad2 lightgreen:90ee90 lightgray:d3d3d3 lightgrey:d3d3d3 " +
    "lightpink:ffb6c1 lightsalmon:ffa07a lightseagreen:20b2aa lightskyblue:87cefa " +
    "lightslategray:778899 lightslategrey:778899 lightsteelblue:b0c4de lightyellow:ffffe0 " +
    "lime:00ff00 limegreen:32cd32 linen:faf0e6 magenta:ff00ff maroon:800000 " +
    "mediumaquamarine:66cdaa mediumblue:0000cd mediumorchid:ba55d3 mediumpurple:9370db " +
    "mediumseagreen:3cb371 mediumslateblue:7b68ee mediumspringgreen:00fa9a " +
    "mediumturquoise:48d1cc mediumvioletred:c71585 midnightblue:191970 mintcream:f5fffa " +
    "mistyrose:ffe4e1 moccasin:ffe4b5 navajowhite:ffdead navy:000080 oldlace:fdf5e6 " +
    "olive:808000 olivedrab:6b8e23 orange:ffa500 orangered:ff4500 orchid:da70d6 " +
    "palegoldenrod:eee8aa palegreen:98fb98 paleturquoise:afeeee palevioletred:db7093 " +
    "papayawhip:ffefd5 peachpuff:ffdab9 peru:cd853f pink:ffc0cb plum:dda0dd powderblue:b0e0e6 " +
    "purple:800080 rebeccapurple:663399 red:ff0000 rosybrown:bc8f8f royalblue:4169e1 " +
    "saddlebrown:8b4513 salmon:fa8072 sandybrown:f4a460 seagreen:2e8b57 seashell:fff5ee " +
    "sienna:a0522d silver:c0c0c0 skyblue:87ceeb slateblue:6a5acd slategray:708090 " +
    "slategrey:708090 snow:fffafa springgreen:00ff7f steelblue:4682b4 tan:d2b48c teal:008080 " +
    "thistle:d8bfd8 tomato:ff6347 turquoise:40e0d0 violet:ee82ee wheat:f5deb3 white:ffffff " +
    "whitesmoke:f5f5f5 yellow:ffff00 yellowgreen:9acd32"
  )
    .split(" ")
    .map((e) => e.split(":") as [string, string]),
);

const ONE_THIRD = 1 / 3;
const ONE_SIXTH = 1 / 6;
const TWO_THIRD = 2 / 3;
function pyMod1(x: number): number {
  let r = x % 1;
  if (r !== 0 && r < 0) r += 1;
  return r;
}
function hlsV(m1: number, m2: number, hue: number): number {
  hue = pyMod1(hue);
  if (hue < ONE_SIXTH) return m1 + (m2 - m1) * hue * 6;
  if (hue < 0.5) return m2;
  if (hue < TWO_THIRD) return m1 + (m2 - m1) * (TWO_THIRD - hue) * 6;
  return m1;
}
function hlsToRgb(h: number, l: number, s: number): number[] {
  if (s === 0) return [l, l, l];
  const m2 = l <= 0.5 ? l * (1 + s) : l + s - l * s;
  const m1 = 2 * l - m2;
  return [hlsV(m1, m2, h + ONE_THIRD), hlsV(m1, m2, h), hlsV(m1, m2, h - ONE_THIRD)];
}
function hsvToRgb(h: number, s: number, v: number): number[] {
  if (s === 0) return [v, v, v];
  let i = Math.trunc(h * 6);
  const f = h * 6 - i;
  const p = v * (1 - s);
  const q = v * (1 - s * f);
  const t = v * (1 - s * (1 - f));
  i = ((i % 6) + 6) % 6;
  return [
    [v, t, p],
    [q, v, p],
    [p, v, t],
    [p, q, v],
    [t, p, v],
    [v, p, q],
  ][i];
}

/** PIL.ImageColor.getrgb (first three channels), or null. */
function getRgb(color: string): number[] | null {
  if (color.length > 100) return null;
  color = color.toLowerCase();
  const named = COLOURS.get(color);
  if (named) color = `#${named}`;
  const hex = (s: string) => parseInt(s, 16);
  let m: RegExpMatchArray | null;
  if ((m = color.match(/^#([a-f0-9])([a-f0-9])([a-f0-9])[a-f0-9]?\n?$/))) {
    return [hex(m[1] + m[1]), hex(m[2] + m[2]), hex(m[3] + m[3])];
  }
  if ((m = color.match(/^#([a-f0-9]{2})([a-f0-9]{2})([a-f0-9]{2})(?:[a-f0-9]{2})?\n?$/))) {
    return [hex(m[1]), hex(m[2]), hex(m[3])];
  }
  const sp = `${S}*`;
  const int3 = (pct: string) =>
    new RegExp(
      `^rgb\\(${sp}(\\d+)${pct}${sp},${sp}(\\d+)${pct}${sp},${sp}(\\d+)${pct}${sp}\\)\\n?$`,
    );
  if ((m = color.match(int3("")))) return [+m[1], +m[2], +m[3]];
  if ((m = color.match(int3("%"))))
    return [m[1], m[2], m[3]].map((c) => Math.trunc((+c * 255) / 100 + 0.5));
  const num = "(\\d+\\.?\\d*)";
  const hs = (name: string) =>
    new RegExp(`^${name}\\(${sp}${num}${sp},${sp}${num}%${sp},${sp}${num}%${sp}\\)\\n?$`);
  if ((m = color.match(hs("hsl")))) {
    return hlsToRgb(+m[1] / 360, +m[3] / 100, +m[2] / 100).map((c) => Math.trunc(c * 255 + 0.5));
  }
  if ((m = color.match(hs("hs[bv]")))) {
    return hsvToRgb(+m[1] / 360, +m[2] / 100, +m[3] / 100).map((c) => Math.trunc(c * 255 + 0.5));
  }
  if (
    (m = color.match(
      new RegExp(
        `^rgba\\(${sp}(\\d+)${sp},${sp}(\\d+)${sp},${sp}(\\d+)${sp},${sp}\\d+${sp}\\)\\n?$`,
      ),
    ))
  ) {
    return [+m[1], +m[2], +m[3]];
  }
  return null;
}

/** Python's f"{n:02x}". */
function hex2(n: number): string {
  return n < 0 ? `-${(-n).toString(16)}` : n.toString(16).padStart(2, "0");
}

function colour(value: string): string | null {
  const rgb = getRgb(pyStrip(value).toLowerCase());
  return rgb ? `#${rgb.map(hex2).join("")}` : null;
}

/** Python's f"{value:.2f}" (exact ties round half to even), without trailing zeros. */
function formatNumber(value: number): string {
  let s: string;
  if (Number.isInteger(value * 8) && !Number.isInteger(value * 4) && Math.abs(value) < 1e9) {
    let n = Math.floor(value * 100); // value * 100 is exact: x.5
    if (n % 2 !== 0) n += 1;
    s = (n / 100).toFixed(2);
  } else {
    s = value.toFixed(2);
  }
  return s.replace(/0+$/, "").replace(/\.$/, "");
}

/** Python's round() (half to even). */
function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** A CSS length in pt or px, as points. */
function points(value: string): number | null {
  const m = pyStrip(value)
    .toLowerCase()
    .match(new RegExp(`^(-?\\d+(?:\\.\\d+)?)${S}*(pt|px)$`));
  if (!m) return null;
  const n = parseFloat(m[1]);
  return m[2] === "px" ? n * 0.75 : n;
}

function fontFamily(value: string): string | null {
  let first = pyStrip(value.split(",")[0]);
  first = pyStrip(trimWith(first, (c) => c === "'" || c === '"'));
  first = first.replace(/[<>"';{}]/g, "");
  return first || null;
}

function spanStyleText(
  fam: string | null,
  size: number | null,
  fg: string | null,
  bg: string | null,
): string {
  const parts: string[] = [];
  if (fam) parts.push(`font-family:${fam}`);
  if (size !== null && size > 0 && size <= 1000) parts.push(`font-size:${formatNumber(size)}pt`);
  if (fg) parts.push(`color:${fg}`);
  if (bg) parts.push(`background-color:${bg}`);
  return parts.join(";");
}

const isDigits = (s: string) => /^[0-9]+$/.test(s);

/** Bold / italic / underline / strike set through CSS, as the tags b, i, u, s. */
function cssEmphasis(css: Map<string, string>): string[] {
  const tags: string[] = [];
  const weight = (css.get("font-weight") ?? "").toLowerCase();
  if (weight === "bold" || weight === "bolder" || (isDigits(weight) && Number(weight) >= 600))
    tags.push("b");
  const style = (css.get("font-style") ?? "").toLowerCase();
  if (style === "italic" || style === "oblique") tags.push("i");
  const deco =
    `${css.get("text-decoration") ?? ""} ${css.get("text-decoration-line") ?? ""}`.toLowerCase();
  if (deco.includes("underline")) tags.push("u");
  if (deco.includes("line-through")) tags.push("s");
  return tags;
}

/** <b style="font-weight:normal"> (pasted from Google Docs) isn't bold. */
function notBold(tag: string, node: Element): boolean {
  const weight = (parseStyle(attr(node, "style")).get("font-weight") ?? "").toLowerCase();
  return (
    (tag === "b" || tag === "strong") &&
    (weight === "normal" || weight === "lighter" || (isDigits(weight) && Number(weight) < 600))
  );
}

/** html.escape(value, quote=True). */
function escAttr(value: string): string {
  return escText(value).replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
}
/** html.escape(value, quote=False). */
function escText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// \r inside <pre> is kept by libxml2 but turned into \n by an HTML5 parser: it travels as this
// noncharacter and is put back whenever text or attributes are read.
const CR = String.fromCharCode(0xfdd0);
const CR_ALL = new RegExp(CR, "g");
const XML_FORBIDDEN = new RegExp(
  `[\\x01-\\x08\\x0b\\x0c\\x0e-\\x1f${String.fromCharCode(0xfffe)}${String.fromCharCode(0xffff)}]`,
  "g",
);
const restore = (s: string) => (s.includes(CR) ? s.replace(CR_ALL, "\r") : s);
const attr = (node: Element, name: string): string | null => {
  const v = node.getAttribute(name);
  return v === null ? null : restore(v);
};

function tagOf(node: Node): string | null {
  return node.nodeType === 1 ? (node as Element).localName.toLowerCase() : null;
}
const elements = (node: Element): Element[] => Array.from(node.children);

function finalise(tokens: Token[], pre = false, dropTrailingBr = false): string {
  if (!pre) {
    const out: Token[] = [];
    let prevSpace = true;
    for (const [kind, raw] of tokens) {
      let text = raw;
      if (kind === "T") {
        text = collapseWs(text);
        if (prevSpace && text.startsWith(" ")) text = text.slice(1);
        if (!text) continue;
        prevSpace = text.endsWith(" ");
      } else if (kind === "V" || kind === "S") {
        prevSpace = text === "<br>";
      }
      out.push([kind, text]);
    }
    tokens = out;
    if (dropTrailingBr) while (tokens.length && isBr(tokens[tokens.length - 1])) tokens.pop();
    let i = tokens.length;
    while (i > 0) {
      const [kind, text] = tokens[i - 1];
      if (kind === "C") i -= 1;
      else if (kind === "S") {
        tokens.splice(i - 1, 1);
        i -= 1;
      } else if (kind === "T" && text.endsWith(" ")) {
        let end = text.length;
        while (end > 0 && text[end - 1] === " ") end--;
        tokens[i - 1] = [kind, text.slice(0, end)];
        break;
      } else break;
    }
  }
  return tokens.map(([k, t]) => (k === "T" ? escText(t) : t)).join("");
}

// ----- URLs and paths (Python's urllib.parse / pathlib) ------------------------------------

interface SplitUrl {
  scheme: string;
  netloc: string;
  path: string;
}

/** urllib.parse.urlparse: scheme, netloc and path (params cut off); null where Python raises. */
function urlparse(url: string): SplitUrl | null {
  url = url.replace(/^[\x00-\x20]+/, "").replace(/[\t\r\n]/g, "");
  let scheme = "";
  let netloc = "";
  const i = url.indexOf(":");
  if (i > 0 && /^[A-Za-z]/.test(url) && /^[A-Za-z0-9+\-.]+$/.test(url.slice(0, i))) {
    scheme = url.slice(0, i).toLowerCase();
    url = url.slice(i + 1);
  }
  if (url.startsWith("//")) {
    let end = url.length;
    for (const c of "/?#") {
      const j = url.indexOf(c, 2);
      if (j >= 0) end = Math.min(end, j);
    }
    netloc = url.slice(2, end);
    url = url.slice(end);
    if (netloc.includes("[") !== netloc.includes("]")) return null;
  }
  const hash = url.indexOf("#");
  if (hash >= 0) url = url.slice(0, hash);
  const q = url.indexOf("?");
  if (q >= 0) url = url.slice(0, q);
  if (scheme === "" || USES_PARAMS.has(scheme)) {
    if (url.includes(";")) {
      const slash = url.lastIndexOf("/");
      const j = slash >= 0 ? url.indexOf(";", slash) : url.indexOf(";");
      if (j >= 0) url = url.slice(0, j);
    }
  }
  return { scheme, netloc, path: url };
}
const USES_PARAMS = set(
  "ftp hdl prospero http imap https shttp rtsp rtsps rtspu sip sips mms sftp tel",
);

/** urllib.parse.unquote (UTF-8, invalid bytes replaced). */
function unquote(s: string): string {
  if (!s.includes("%")) return s;
  return s.replace(/(?:%[0-9a-fA-F]{2})+/g, (run) => {
    const bytes = new Uint8Array(run.length / 3);
    for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(run.slice(i * 3 + 1, i * 3 + 3), 16);
    return new TextDecoder("utf-8").decode(bytes);
  });
}

/** `rel` is a relative path that stays inside its folder (Windows and POSIX rules, no I/O). */
function isPlainRelative(rel: string): boolean {
  if (rel.startsWith("\\\\") || rel.startsWith("//")) return false;
  if (rel.length >= 2 && rel[1] === ":") return false; // a drive ("C:")
  if (rel.startsWith("/") || rel.startsWith("\\")) return false;
  let depth = 0;
  for (const part of rel.split(/[\\/]/)) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      depth -= 1;
      if (depth < 0) return false;
    } else depth += 1;
  }
  return depth > 0;
}

function suffix(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  const i = name.lastIndexOf(".");
  return i > 0 && i < name.length - 1 ? name.slice(i) : "";
}

/** base64.b64decode(s, validate=True), or null where Python raises. */
function b64decode(s: string): Uint8Array | null {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) return null;
  const data = s.replace(/=+$/, "");
  const pads = s.length - data.length;
  const rest = data.length % 4;
  if (rest === 1 || (rest === 2 && pads < 2) || (rest === 3 && pads < 1)) return null;
  const bin = atob(data + "=".repeat((4 - rest) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

// ----- the normaliser --------------------------------------------------------------------

type Group = [Token[], string[]];

class TooDeep extends Error {}

/** The engine's own "too much recursion" error (V8, SpiderMonkey, JavaScriptCore). */
function isStackOverflow(err: unknown): boolean {
  return err instanceof Error && /call stack|too much recursion/i.test(err.message);
}

class Normaliser {
  images: DocImages = new Map();
  private depth = 0;

  constructor(
    private readonly reserved: Set<string>,
    private readonly resolveImage?: (src: string) => Uint8Array | null,
  ) {}

  private enter(): void {
    if (++this.depth > MAX_DEPTH) throw new TooDeep();
  }

  // ----- pictures -----
  private addImage(data: Uint8Array, ext: string): string {
    let name = nextImageName(this.images, ext);
    let n = this.images.size + 1;
    while (this.images.has(name) || this.reserved.has(name)) {
      n += 1;
      name = `img/${n}.${ext}`;
    }
    this.images.set(name, data);
    return name;
  }

  private imageSrc(raw: string): string | null {
    const src = pyStrip(raw);
    const m = src.match(DATA_URI);
    if (m) {
      const data = b64decode(m[2].replace(new RegExp(`${S}+`, "g"), ""));
      if (data === null || data.length === 0) return null;
      return this.addImage(data, IMAGE_EXTS[m[1].toLowerCase()]);
    }
    const parsed = urlparse(src);
    if (parsed === null || parsed.scheme || parsed.netloc || !parsed.path) return null;
    const found = this.readRelative(unquote(parsed.path));
    if (found !== null) return this.addImage(found[0], found[1]);
    // Already in canonical form (output of an earlier pass): keep the reference.
    return CANON_IMG.test(src) ? src : null;
  }

  private readRelative(rel: string): [Uint8Array, string] | null {
    // Checked before asking for the file: a UNC path would contact the host.
    if (!this.resolveImage || !isPlainRelative(rel)) return null;
    const ext = IMAGE_EXTS[suffix(rel).toLowerCase().replace(/^\./, "")];
    if (ext === undefined) return null;
    try {
      const data = this.resolveImage(rel);
      return data && data.length <= MAX_FILE_BYTES ? [data, ext] : null;
    } catch {
      return null;
    }
  }

  // ----- inline -----
  private inlineChildren(node: Node, toks: Token[]): void {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (text) toks.push(["T", text]);
      } else if (child.nodeType === 1) this.inline(child as Element, toks);
    }
  }

  private inline(node: Element, toks: Token[]): void {
    const tag = tagOf(node);
    if (tag === null || DROP.has(tag)) return;
    this.enter();
    if (tag === "br") toks.push(BR());
    else if (tag === "img") this.img(node, toks);
    else if (Object.hasOwn(INLINE_MAP, tag) && !notBold(tag, node)) {
      const out = INLINE_MAP[tag];
      toks.push(["O", `<${out}>`]);
      this.inlineChildren(node, toks);
      toks.push(["C", `</${out}>`]);
    } else if (tag === "a") {
      const href = pyStrip((attr(node, "href") ?? "").replace(/[\x00-\x1f\x7f]/g, ""));
      const parsed = href ? urlparse(href) : null;
      if (parsed && SAFE_SCHEMES.has(parsed.scheme)) {
        toks.push(["O", `<a href="${escAttr(href)}">`]);
        this.inlineChildren(node, toks);
        toks.push(["C", "</a>"]);
      } else this.inlineChildren(node, toks);
    } else if (tag === "span" || tag === "font") {
      const style = spanStyle(node, tag === "font");
      const wraps = cssEmphasis(parseStyle(attr(node, "style")));
      if (style) toks.push(["O", `<span style="${escAttr(style)}">`]);
      for (const t of wraps) toks.push(["O", `<${t}>`]);
      this.inlineChildren(node, toks);
      for (const t of [...wraps].reverse()) toks.push(["C", `</${t}>`]);
      if (style) toks.push(["C", "</span>"]);
    } else if (BLOCK.has(tag)) {
      // a block inside an inline tag: its content becomes a line
      separate(toks);
      this.inlineChildren(node, toks);
      separate(toks);
    } else this.inlineChildren(node, toks); // unknown inline: keep the content
    this.depth--;
  }

  private img(node: Element, toks: Token[]): void {
    const name = this.imageSrc(attr(node, "src") ?? "");
    if (name === null) return;
    const css = parseStyle(attr(node, "style"));
    let attrs = `src="${escAttr(name)}"`;
    for (const dim of ["width", "height"]) {
      const raw = attr(node, dim) || css.get(dim) || "";
      const m = raw.match(new RegExp(`^${S}*(\\d+)(?:px)?${S}*$`));
      if (m && Number(m[1]) > 0 && Number(m[1]) <= 100000) attrs += ` ${dim}="${Number(m[1])}"`;
    }
    toks.push(["V", `<img ${attrs}>`]);
  }

  // ----- blocks -----
  /** Emit the content of a block container; loose inline content becomes paragraphs. */
  blocks(node: Node, out: string[]): void {
    this.enter();
    const run: Token[] = [];
    const flush = () => {
      if (run.some(([k, t]) => k === "V" || (k === "T" && hasContent(t)))) {
        out.push(`<p>${finalise(run)}</p>`);
      }
      run.length = 0;
    };
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (text) run.push(["T", text]);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = tagOf(child) as string;
      if (BLOCK.has(tag)) {
        flush();
        this.block(child as Element, tag, out);
      } else this.inline(child as Element, run);
    }
    flush();
    this.depth--;
  }

  private leaf(tag: string, node: Element): string {
    const toks: Token[] = [];
    this.inlineChildren(node, toks);
    return `<${tag}${blockStyle(node)}>${finalise(toks)}</${tag}>`;
  }

  private block(node: Element, tag: string, out: string[]): void {
    this.enter();
    if (tag === "p") out.push(this.leaf("p", node));
    else if (Object.hasOwn(HEADINGS, tag)) out.push(this.leaf(HEADINGS[tag], node));
    else if (tag === "pre") {
      const toks: Token[] = [];
      this.inlineChildren(node, toks);
      out.push(`<pre>${finalise(toks, true)}</pre>`);
    } else if (tag === "blockquote") {
      const inner: string[] = [];
      this.blocks(node, inner);
      out.push(`<blockquote>${inner.join("")}</blockquote>`);
    } else if (LISTS.has(tag)) out.push(this.list(node, tag));
    else if (tag === "table") this.table(node, out);
    else if (tag === "hr") out.push("<hr>");
    else if (tag === "caption") out.push(this.leaf("p", node));
    else if (
      tag === "body" ||
      tag === "html" ||
      elements(node).some((c) => BLOCK.has(tagOf(c) as string))
    ) {
      this.blocks(node, out);
    } else out.push(this.leaf("p", node)); // div, section, ...: a paragraph
    this.depth--;
  }

  private list(node: Element, tag: string): string {
    this.enter();
    const items: string[] = [];
    for (const child of elements(node)) {
      const ctag = tagOf(child) as string;
      if (DROP.has(ctag)) continue;
      if (LISTS.has(ctag) && items.length) {
        // a list nested directly in a list belongs to the item above
        const last = items[items.length - 1];
        items[items.length - 1] = last.slice(0, -"</li>".length) + this.list(child, ctag) + "</li>";
      } else for (const item of this.li(child)) items.push(item);
    }
    this.depth--;
    return `<${tag}>${items.join("")}</${tag}>`;
  }

  /** One item, or more when text follows a nested list (that text becomes the next item). */
  private li(node: Element): string[] {
    const groups: Group[] = [[[], []]];
    this.liWalk(node, groups);
    const style = blockStyle(node);
    const out: string[] = [];
    groups.forEach(([toks, nested], n) => {
      const text = finalise(toks, false, true);
      if (n === 0 || text || nested.length) out.push(`<li${style}>${text}${nested.join("")}</li>`);
    });
    return out;
  }

  /** Flatten a list item: its paragraphs become lines, nested lists stay lists. */
  private liWalk(node: Element, groups: Group[]): void {
    this.enter();
    const breakLine = () => {
      const [toks, nested] = groups[groups.length - 1];
      if (toks.length && !isBr(toks[toks.length - 1]) && !nested.length) toks.push(BR());
    };
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === 3) {
        const text = restore((child as Text).data);
        if (text) add(groups, [["T", text]]);
        continue;
      }
      if (child.nodeType !== 1) continue;
      const tag = tagOf(child) as string;
      if (LISTS.has(tag)) groups[groups.length - 1][1].push(this.list(child as Element, tag));
      else if (BLOCK.has(tag)) {
        breakLine();
        this.liWalk(child as Element, groups);
        breakLine();
      } else {
        const toks: Token[] = [];
        this.inline(child as Element, toks);
        if (toks.length) add(groups, toks);
      }
    }
    this.depth--;
  }

  private table(node: Element, out: string[]): void {
    const rows: Element[] = [];
    const captions: Element[] = [];
    const collect = (parent: Element) => {
      for (const c of elements(parent)) {
        const t = tagOf(c);
        if (t === "tr") rows.push(c);
        else if (t === "thead" || t === "tbody" || t === "tfoot") collect(c);
        else if (t === "caption") captions.push(c);
      }
    };
    collect(node);
    for (const cap of captions) out.push(this.leaf("p", cap));
    const htmlRows: string[] = [];
    for (const tr of rows) {
      const cells: string[] = [];
      for (const td of elements(tr)) {
        const t = tagOf(td);
        if (t !== "td" && t !== "th") continue;
        let span = "";
        for (const name of ["colspan", "rowspan"]) {
          const m = (attr(td, name) ?? "").match(new RegExp(`^${S}*(\\d+)${S}*$`));
          if (m && Number(m[1]) > 1 && Number(m[1]) <= 1000) span += ` ${name}="${Number(m[1])}"`;
        }
        const inner: string[] = [];
        this.blocks(td, inner);
        cells.push(`<td${span}>${inner.join("") || "<p></p>"}</td>`);
      }
      if (cells.length) htmlRows.push(`<tr>${cells.join("")}</tr>`);
    }
    if (htmlRows.length) {
      const css = parseStyle(attr(node, "style"));
      const border = (css.get("border") || css.get("border-style") || "").toLowerCase();
      const plain =
        ["none", "0", "0px", "hidden"].includes(border) || (attr(node, "border") ?? "") === "0";
      out.push(`${plain ? '<table style="border:none">' : "<table>"}${htmlRows.join("")}</table>`);
    }
  }
}

function separate(toks: Token[]): void {
  for (let i = toks.length - 1; i >= 0; i--) {
    const [kind] = toks[i];
    if (kind === "O") continue;
    if (kind === "S" || isBr(toks[i])) return;
    toks.push(["S", "<br>"]);
    return;
  }
}

/** Add inline tokens to the item being built (a new item after a nested list). */
function add(groups: Group[], tokens: Token[]): void {
  if (groups[groups.length - 1][1].length) {
    if (tokens.every(([k, t]) => k === "T" && !hasContent(t))) return;
    groups.push([[], []]);
  }
  const toks = groups[groups.length - 1][0];
  for (const t of tokens) toks.push(t); // no push(...): it overflows on huge arrays
}

function spanStyle(node: Element, font: boolean): string {
  const css = parseStyle(attr(node, "style"));
  let fam: string | null = css.get("font-family") ?? null;
  let size = css.has("font-size") ? points(css.get("font-size") as string) : null;
  let fg = css.has("color") ? colour(css.get("color") as string) : null;
  const bg = css.has("background-color") ? colour(css.get("background-color") as string) : null;
  if (font) {
    fam = fam || attr(node, "face");
    const digits = (attr(node, "size") ?? "").match(new RegExp(`^${S}*([1-7])${S}*$`));
    if (size === null && digits) size = FONT_SIZES_PT[digits[1]];
    const c = attr(node, "color");
    if (fg === null && c) fg = colour(c);
  }
  return spanStyleText(fam ? fontFamily(fam) : null, size, fg, bg);
}

function blockStyle(node: Element): string {
  const css = parseStyle(attr(node, "style"));
  const parts: string[] = [];
  const align = pyStrip(css.get("text-align") || attr(node, "align") || "").toLowerCase();
  if (["left", "center", "right", "justify"].includes(align)) parts.push(`text-align:${align}`);
  if (css.has("margin-left")) {
    const pts = points(css.get("margin-left") as string);
    const level = pts === null ? 0 : pyRound(pts / 36);
    if (level >= 1) parts.push(`margin-left:${36 * Math.min(level, MAX_INDENT)}pt`);
  }
  const brk = (css.get("page-break-before") || css.get("break-before") || "").toLowerCase();
  if (brk === "always" || brk === "page") parts.push("page-break-before:always");
  return parts.length ? ` style="${parts.join(";")}"` : "";
}

// ----- parsing -----------------------------------------------------------------------------

/** Make an HTML5 parser read `html` the way libxml2 does where they differ. */
function libxmlLike(html: string): string {
  // libxml2 turns NUL into a space and drops the other characters XML 1.0 forbids.
  html = html.replace(/\x00/g, " ").replace(XML_FORBIDDEN, "");
  // Inside <pre>: keep \r (as CR, see above) and the first newline, which HTML5 drops.
  // Scanned with indexOf, not regexes: many unterminated "<pre" or "<" must stay linear.
  const openTag = /<pre/gi;
  const closeTag = /<\/pre\s*>/gi;
  const find = (re: RegExp, from: number) => {
    re.lastIndex = from;
    return re.exec(html)?.index ?? -1;
  };
  const parts: string[] = [];
  let from = 0;
  let at = find(openTag, 0);
  while (at >= 0) {
    if (/\w/.test(html[at + 4] ?? "")) {
      at = find(openTag, at + 4); // <prefix>, not <pre>
      continue;
    }
    const gt = html.indexOf(">", at + 4);
    if (gt < 0) break; // no tag can end after this
    let end = find(closeTag, gt + 1);
    if (end < 0) end = html.length;
    const kept = keepCarriageReturns(html.slice(gt + 1, end));
    parts.push(html.slice(from, gt + 1), kept.startsWith("\n") ? "\n" : "", kept);
    from = end;
    at = find(openTag, end);
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

/** A whole page with nothing but a doctype and comments in it (linear: no regex backtracking). */
function onlyDoctypeAndComments(html: string): boolean {
  let i = 0;
  for (;;) {
    while (i < html.length && /\s/.test(html[i])) i++;
    if (i >= html.length) return true;
    let end = -1;
    if (html.slice(i, i + 9).toLowerCase() === "<!doctype") end = html.indexOf(">", i + 9) + 1;
    else if (html.startsWith("<!--", i)) {
      end = html.indexOf("-->", i + 4);
      if (end >= 0) end += 3;
    }
    if (end <= 0) return false;
    i = end;
  }
}

function parseDocument(html: string): Document {
  return new DOMParser().parseFromString(libxmlLike(html), "text/html");
}

const FULL_HTML = new RegExp(`^${S}*<(?:html|!doctype)`, "i");

/** The parsed body, or null for empty input (lxml's fragment_fromstring). */
function parse(html: string): HTMLElement | null {
  html = html.replace(XML_DECL, "");
  if (!pyStrip(html)) return null;
  // Like lxml's fragment_fromstring: a page that starts with <html> or <!doctype> is read as a
  // whole document (a lone doctype has nothing in it); anything else is put inside a <body>, so
  // a leading <noscript> or <title> stays in the content instead of moving into <head>.
  if (FULL_HTML.test(html)) {
    if (onlyDoctypeAndComments(html)) {
      throw new Error(UNREADABLE);
    }
  } else {
    // No closing tags: after an unclosed <plaintext> or <xmp> they would show up as text.
    html = `<html><body>${html}`;
  }
  const body = parseDocument(html).body;
  if (!body || body.localName !== "body") throw new Error(UNREADABLE);
  return body;
}

/** Every element, <template> contents included (lxml keeps them in the tree). */
function* allElements(root: Node): Generator<Element> {
  const stack: Node[] = [root];
  while (stack.length) {
    const node = stack.pop() as Node;
    if (node.nodeType === 1) yield node as Element;
    const kids = Array.from(
      node.nodeType === 1 && (node as Element).localName === "template"
        ? (node as HTMLTemplateElement).content.childNodes
        : node.childNodes,
    );
    for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
  }
}

/** Python-Markdown marks fenced code with class="language-x": not the page's styling. */
function isLanguageClass(value: string): boolean {
  return value
    .split(new RegExp(`${S}+`))
    .filter((c) => c)
    .every((c) => c.startsWith("language-"));
}

/** What normalising `html` drops that a reader would miss (warnings, in a fixed order). */
function lostParts(page: Document): string[] {
  const found = new Set<string>();
  for (const el of allElements(page)) {
    const tag = tagOf(el) as string;
    const rel = (attr(el, "rel") ?? "").toLowerCase();
    if (tag === "style" || tag === "script" || (tag === "link" && rel.includes("stylesheet")))
      found.add(W_PAGESTYLE);
    else if (RAW_DROPPED.has(tag)) found.add(W_RAWHTML);
    else if (tag === "img") {
      const parsed = urlparse(pyStrip(attr(el, "src") ?? ""));
      if (parsed && (parsed.scheme === "http" || parsed.scheme === "https" || parsed.netloc))
        found.add(W_WEBPICS);
    }
    const cls = attr(el, "class");
    if (
      (cls && !isLanguageClass(cls)) ||
      el.getAttributeNames().some((k) => k.toLowerCase().startsWith("on"))
    ) {
      found.add(W_PAGESTYLE);
    }
  }
  return [W_PAGESTYLE, W_WEBPICS, W_RAWHTML].filter((w) => found.has(w));
}

/** The page's <title>, with its white space collapsed ("" if there is none). */
export function htmlTitle(html: string): string {
  for (const el of allElements(parseDocument(html.replace(XML_DECL, "")))) {
    if (tagOf(el) === "title") {
      return restore(el.textContent ?? "")
        .split(new RegExp(`${S}+`))
        .filter((w) => w)
        .join(" ");
    }
  }
  return "";
}

/** Rewrite `html` into the editor's subset, plus the warnings for what was dropped. */
export function normaliseHtmlReport(
  html: string,
  opts: NormaliseOptions = {},
): { html: string; images: DocImages; warnings: string[] } {
  const root = parse(html);
  if (root === null) return { html: "", images: new Map(), warnings: [] };
  const warnings = lostParts(root.ownerDocument); // the whole page, <head> included
  const reserved = new Set<string>();
  for (const el of allElements(root)) {
    const src = tagOf(el) === "img" ? pyStrip(attr(el, "src") ?? "") : "";
    if (CANON_IMG.test(src)) reserved.add(src);
  }
  const norm = new Normaliser(reserved, opts.resolveImage);
  const out: string[] = [];
  try {
    norm.blocks(root, out);
  } catch (err) {
    if (err instanceof TooDeep || isStackOverflow(err)) throw new Error(UNREADABLE);
    throw err;
  }
  return { html: out.join(""), images: norm.images, warnings };
}

/** Rewrite `html` into the editor's subset; returns the fragment and its pictures. */
export function normaliseHtml(
  html: string,
  opts: NormaliseOptions = {},
): { html: string; images: DocImages } {
  const { html: out, images } = normaliseHtmlReport(html, opts);
  return { html: out, images };
}

/** Number of characters (code points) of visible text in `html`. */
export function textLength(html: string): number {
  const root = parse(html);
  if (root === null) return 0;
  let n = 0;
  const stack: Node[] = [root];
  while (stack.length) {
    const node = stack.pop() as Node;
    if (node.nodeType === 3) {
      // Code points, like Python's len (a U+FDD0 standing for \r is one character too).
      const text = (node as Text).data;
      for (let i = 0; i < text.length; i++) {
        const c = text.charCodeAt(i);
        const low = c >= 0xdc00 && c <= 0xdfff;
        if (!low || i === 0 || (text.charCodeAt(i - 1) & 0xfc00) !== 0xd800) n++;
      }
    } else if (node === root || (node.nodeType === 1 && !DROP.has(tagOf(node) as string))) {
      for (const child of Array.from(node.childNodes)) stack.push(child);
    }
  }
  return n;
}
