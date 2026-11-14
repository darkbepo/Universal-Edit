// Tidy for the code editor — a port of src/umc/codeedit/tidy.py (same output; shared cases:
// tests/fixtures/codeedit_tidy_cases.json). JSON is re-emitted from the scanner's tokens, so
// numbers and strings keep their spelling; XML elements with only element children are indented,
// anything with text in it stays exactly as written.
import { check, scanJson } from "./check";

export const TIDY: ReadonlySet<string> = new Set(["json", "xml", "svg"]);
const INDENT = "  ";

export class TidyError extends Error {}

function tidyJson(text: string): string {
  const tokens = scanJson(text);
  if (!tokens.length) return text;
  const out: string[] = [];
  let depth = 0;
  tokens.forEach((tok, i) => {
    const next = tokens[i + 1]?.kind ?? "";
    if (tok.kind === "{" || tok.kind === "[") {
      if (next === "}" || next === "]") {
        out.push(tok.text);
        return;
      }
      depth++;
      out.push(tok.text + "\n" + INDENT.repeat(depth));
    } else if (tok.kind === "}" || tok.kind === "]") {
      const prev = tokens[i - 1].kind;
      if (prev === "{" || prev === "[") out.push(tok.text);
      else {
        depth--;
        out.push("\n" + INDENT.repeat(depth) + tok.text);
      }
    } else if (tok.kind === ",") out.push(",\n" + INDENT.repeat(depth));
    else if (tok.kind === ":") out.push(": ");
    else out.push(tok.text);
  });
  return out.join("");
}

// ---------------------------------------------------------------------------- XML
const XML_TOKEN =
  /<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|<!DOCTYPE(?:"[^"]*"|'[^']*'|\[(?:"[^"]*"|'[^']*'|[^\]"'])*\]|[^>])*>|<![^>]*>|<(?:"[^"]*"|'[^']*'|[^'">])*>|[^<]+/g;

// Where a line break between child elements would show as a space (HTML / SVG text flow): such
// elements, and any element holding one of the inline ones, are kept exactly as written.
export const TEXT_FLOW = new Set(
  "p h1 h2 h3 h4 h5 h6 li dt dd td th caption label button legend option summary figcaption pre title text tspan textPath desc".split(" "),
);
export const INLINE = new Set(
  "a abbr b bdi bdo br cite code data dfn em i img kbd mark q s samp small span strong sub sup time u var tspan textPath".split(" "),
);
const PRESERVE = /\sxml:space\s*=\s*["']preserve["']/;

/** The local name of an opening tag ("<svg:text x=1>" → "text"). */
function tagName(tag: string): string {
  const m = /^<\s*([^\s/>]+)/.exec(tag);
  return m ? m[1].split(":").pop()! : "";
}

interface XmlNode {
  kind: "element" | "text" | "other" | "cdata";
  start: number;
  end: number;
  openTag: string;
  closeTag: string;
  children: XmlNode[];
}

const node = (kind: XmlNode["kind"], start: number, end = 0, openTag = ""): XmlNode => ({ kind, start, end, openTag, closeTag: "", children: [] });

function xmlTree(text: string): XmlNode[] {
  const root = node("element", 0);
  const stack = [root];
  for (const m of text.matchAll(XML_TOKEN)) {
    const tok = m[0];
    const start = m.index;
    const end = start + tok.length;
    const top = stack[stack.length - 1];
    if (tok.startsWith("<!--") || tok.startsWith("<?") || (tok.startsWith("<!") && !tok.startsWith("<![CDATA["))) top.children.push(node("other", start, end));
    else if (tok.startsWith("<![CDATA[")) top.children.push(node("cdata", start, end));
    else if (tok.startsWith("</")) {
      if (stack.length === 1) continue; // a stray closing tag (the check normally refuses these first)
      const n = stack.pop()!;
      n.closeTag = tok;
      n.end = end;
    } else if (tok.startsWith("<")) {
      const n = node("element", start, 0, tok);
      top.children.push(n);
      if (tok.endsWith("/>")) n.end = end;
      else stack.push(n);
    } else top.children.push(node("text", start, end));
  }
  return root.children;
}

const blank = (text: string, n: XmlNode) => n.kind === "text" && !text.slice(n.start, n.end).trim();

function xmlEmit(n: XmlNode, text: string, depth: number): string {
  if (n.kind !== "element" || !n.closeTag) return text.slice(n.start, n.end);
  const kids = n.children.filter((c) => !blank(text, c));
  const keep =
    !kids.length ||
    kids.some((c) => c.kind === "text" || c.kind === "cdata") || // text inside
    PRESERVE.test(n.openTag) || // xml:space="preserve"
    TEXT_FLOW.has(tagName(n.openTag)) || // a paragraph, a label, SVG <text>…
    kids.some((c) => c.kind === "element" && INLINE.has(tagName(c.openTag)));
  if (keep) return text.slice(n.start, n.end); // a line break here would show: exactly as written
  const inner = kids.map((c) => "\n" + INDENT.repeat(depth + 1) + xmlEmit(c, text, depth + 1)).join("");
  return n.openTag + inner + "\n" + INDENT.repeat(depth) + n.closeTag;
}

function tidyXml(text: string): string {
  return xmlTree(text)
    .filter((n) => !blank(text, n))
    .map((n) => xmlEmit(n, text, 0))
    .join("\n");
}

/** The tidied text. Throws TidyError while the check finds a mistake. */
export function tidy(languageId: string, text: string): string {
  if (!TIDY.has(languageId)) throw new TidyError("Tidy works on JSON and XML files.");
  const problems = check(languageId, text);
  if (problems.length) {
    const p = problems[0];
    throw new TidyError(`Fix the mistake on line ${p.line} first: ${p.message}.`);
  }
  const body = languageId === "json" ? tidyJson(text) : tidyXml(text);
  return body + (text.endsWith("\n") && !body.endsWith("\n") ? "\n" : "");
}
