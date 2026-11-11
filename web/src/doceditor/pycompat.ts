/**
 * Python behaviour the document editor's ports copy exactly, so the website gives the same
 * bytes as the desktop: str.isspace / strip / split, int(), round(), f"{x:g}", html.escape.
 * No DOM; index loops instead of regexes over document text (those can be quadratic).
 */

/** Python's str.isspace() characters, as a regex character-class body (also re's Unicode \s). */
export const PY_WS =
  "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_SPACE = new RegExp(`[${PY_WS}]`);

export const isPySpace = (c: string): boolean => PY_SPACE.test(c);

/** `s` without the leading and trailing characters `isSpace` accepts. */
export function trimWith(s: string, isSpace: (c: string) => boolean): string {
  let start = 0;
  let end = s.length;
  while (start < end && isSpace(s[start])) start++;
  while (end > start && isSpace(s[end - 1])) end--;
  return start === 0 && end === s.length ? s : s.slice(start, end);
}

/** Python's str.strip(). */
export const pyStrip = (s: string): string => trimWith(s, isPySpace);

/** The first word of `text`, as `text.split()[0]` would give it ("" when there is none). */
export function firstWord(text: string): string {
  let i = 0;
  while (i < text.length && isPySpace(text[i])) i++;
  let j = i;
  while (j < text.length && !isPySpace(text[j])) j++;
  return text.slice(i, j);
}

/** Python's int() of a string, or null where it would raise ValueError. */
export function pyInt(value: string | null | undefined): number | null {
  const s = pyStrip(value ?? "");
  return /^[+-]?\d+(?:_\d+)*$/.test(s) ? Number.parseInt(s.replaceAll("_", ""), 10) : null;
}

/** Python's round(x): halves go to the even neighbour. */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** Python's round(x, digits): by the exact value of `x`, exact halves to even. */
export function pyRoundTo(x: number, digits: number): number {
  // toFixed rounds the exact binary value like Python, but sends exact halves away from zero.
  // An exact half: the exact expansion is "…5" followed only by zeros (x * 10 ** digits
  // can't tell, as 2.675 * 100 === 267.5 although 2.675 is 2.67499999…).
  const exact = Math.abs(x).toFixed(100);
  const tail = exact.slice(exact.indexOf(".") + 1 + digits);
  if (/^50*$/.test(tail)) return pyRound(x * 10 ** digits) / 10 ** digits;
  return Number(x.toFixed(digits));
}

/** Python's `f"{x:g}"`. */
export function pyG(x: number): string {
  if (x === 0) return "0";
  const [mant, e] = x.toExponential(5).split("e");
  const exp = Number(e);
  const strip = (s: string) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
  if (exp < -4 || exp >= 6) {
    return `${strip(mant)}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  }
  return strip(x.toFixed(5 - exp));
}

/** html.escape(value, quote=False). */
export const escText = (value: string): string =>
  value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

/** html.escape(value, quote=True). */
export const escAttr = (value: string): string =>
  escText(value).replaceAll('"', "&quot;").replaceAll("'", "&#x27;");
