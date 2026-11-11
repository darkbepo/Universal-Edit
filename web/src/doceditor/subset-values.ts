/**
 * Value helpers of the HTML normaliser (the desktop's `html_subset.py` leans on Python for these,
 * so they copy its behaviour): CSS colours, lengths and font names, and URLs and paths
 * (urllib.parse / pathlib).
 */

import { PY_WS, pyStrip, trimWith } from "./pycompat";
import { restore } from "./subset-scan";

export const set = (s: string) => new Set(s.split(" "));
export const S = `[${PY_WS}]`;
export const attr = (node: Element, name: string): string | null => {
  const v = node.getAttribute(name);
  return v === null ? null : restore(v);
};

// ----- Python helpers -------------------------------------------------------------------------

export function parseStyle(value: string | null): Map<string, string> {
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

export function colour(value: string): string | null {
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


/** A CSS length in pt or px, as points. */
export function points(value: string): number | null {
  const m = pyStrip(value)
    .toLowerCase()
    .match(new RegExp(`^(-?\\d+(?:\\.\\d+)?)${S}*(pt|px)$`));
  if (!m) return null;
  const n = parseFloat(m[1]);
  return m[2] === "px" ? n * 0.75 : n;
}

export function fontFamily(value: string): string | null {
  let first = pyStrip(value.split(",")[0]);
  first = pyStrip(trimWith(first, (c) => c === "'" || c === '"'));
  first = first.replace(/[<>"';{}]/g, "");
  return first || null;
}

export function spanStyleText(
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
export function cssEmphasis(css: Map<string, string>): string[] {
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
export function notBold(tag: string, node: Element): boolean {
  const weight = (parseStyle(attr(node, "style")).get("font-weight") ?? "").toLowerCase();
  return (
    (tag === "b" || tag === "strong") &&
    (weight === "normal" || weight === "lighter" || (isDigits(weight) && Number(weight) < 600))
  );
}

// ----- URLs and paths (Python's urllib.parse / pathlib) ------------------------------------

interface SplitUrl {
  scheme: string;
  netloc: string;
  path: string;
}

/** urllib.parse.urlparse: scheme, netloc and path (params cut off); null where Python raises. */
export function urlparse(url: string): SplitUrl | null {
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
export function unquote(s: string): string {
  if (!s.includes("%")) return s;
  return s.replace(/(?:%[0-9a-fA-F]{2})+/g, (run) => {
    const bytes = new Uint8Array(run.length / 3);
    for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(run.slice(i * 3 + 1, i * 3 + 3), 16);
    return new TextDecoder("utf-8").decode(bytes);
  });
}

/** `rel` is a relative path that stays inside its folder (Windows and POSIX rules, no I/O). */
export function isPlainRelative(rel: string): boolean {
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

export function suffix(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  const i = name.lastIndexOf(".");
  return i > 0 && i < name.length - 1 ? name.slice(i) : "";
}

/** base64.b64decode(s, validate=True), or null where Python raises. */
export function b64decode(s: string): Uint8Array | null {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(s)) return null;
  const data = s.replace(/=+$/, "");
  const pads = s.length - data.length;
  const rest = data.length % 4;
  if (rest === 1 || (rest === 2 && pads < 2) || (rest === 3 && pads < 1)) return null;
  const bin = atob(data + "=".repeat((4 - rest) % 4));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
