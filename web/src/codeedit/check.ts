// Checks for JSON / YAML / XML in the code editor — a port of src/umc/codeedit/check.py.
// JSON uses our own scanner (same line / column as the desktop); YAML and XML use this app's
// libraries (yaml, fast-xml-parser). Shared cases: tests/fixtures/codeedit_check_cases.json.
import { XMLValidator } from "fast-xml-parser";
import { parseAllDocuments } from "yaml";

export const CHECKED: ReadonlySet<string> = new Set(["json", "yaml", "xml", "svg"]);

export interface Problem {
  line: number; // 1-based
  column: number; // 1-based; 0 = the whole line
  message: string;
}

export class JsonError extends Error {
  constructor(readonly pos: number, message: string) {
    super(message);
  }
}

export interface Token {
  kind: "{" | "}" | "[" | "]" | ":" | "," | "string" | "number" | "literal";
  text: string; // exactly as written
}

const NUMBER = /-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
const WS = " \t\r\n";
const isDigit = (c: string | undefined) => !!c && c >= "0" && c <= "9";

function lineCol(text: string, pos: number): [number, number] {
  let line = 1;
  let lineStart = 0;
  for (let i = 0; i < pos; i++) {
    if (text[i] === "\n") {
      line++;
      lineStart = i + 1;
    }
  }
  return [line, pos - lineStart + 1];
}

/** The file's tokens, or JsonError at the first mistake. An empty file is fine ([]). */
export function scanJson(text: string): Token[] {
  const tokens: Token[] = [];
  const n = text.length;
  let pos = 0;
  const ws = () => {
    while (pos < n && WS.includes(text[pos])) pos++;
  };
  const string = () => {
    const start = pos;
    pos++;
    while (pos < n) {
      const ch = text[pos];
      if (ch === '"') {
        pos++;
        tokens.push({ kind: "string", text: text.slice(start, pos) });
        return;
      }
      if (ch === "\\") {
        const next = text[pos + 1];
        if (next === "u") {
          if (!/^[0-9a-fA-F]{4}$/.test(text.slice(pos + 2, pos + 6))) throw new JsonError(pos, "A \\u code needs four hex digits");
          pos += 6;
          continue;
        }
        if (next !== undefined && '"\\/bfnrt'.includes(next)) {
          pos += 2;
          continue;
        }
        throw new JsonError(pos, "Unknown \\ code in a string");
      }
      if (ch === "\n" || ch.charCodeAt(0) < 0x20) break;
      pos++;
    }
    throw new JsonError(start, "Missing closing quote");
  };
  const value = () => {
    ws();
    if (pos >= n) throw new JsonError(pos, "Missing value");
    const ch = text[pos];
    if (ch === "{") obj();
    else if (ch === "[") arr();
    else if (ch === '"') string();
    else if (ch === "-" || isDigit(ch)) {
      NUMBER.lastIndex = pos;
      const m = NUMBER.exec(text);
      const end = m ? pos + m[0].length : pos;
      if (!m || (end < n && (isDigit(text[end]) || ".eE".includes(text[end])))) throw new JsonError(m ? end : pos, "That number isn't valid");
      tokens.push({ kind: "number", text: text.slice(pos, end) });
      pos = end;
    } else {
      for (const word of ["true", "false", "null"]) {
        if (text.startsWith(word, pos)) {
          tokens.push({ kind: "literal", text: word });
          pos += word.length;
          return;
        }
      }
      throw new JsonError(pos, "Missing value");
    }
  };
  const obj = () => {
    tokens.push({ kind: "{", text: "{" });
    pos++;
    ws();
    if (pos < n && text[pos] === "}") {
      tokens.push({ kind: "}", text: "}" });
      pos++;
      return;
    }
    for (;;) {
      ws();
      if (pos < n && text[pos] === "}" && tokens[tokens.length - 1].kind === ",") throw new JsonError(pos, "Trailing comma");
      if (pos >= n || text[pos] !== '"') throw new JsonError(pos, "A key must be in double quotes");
      string();
      ws();
      if (pos >= n || text[pos] !== ":") throw new JsonError(pos, "Missing colon after the key");
      tokens.push({ kind: ":", text: ":" });
      pos++;
      value();
      ws();
      if (pos < n && text[pos] === ",") {
        tokens.push({ kind: ",", text: "," });
        pos++;
        continue;
      }
      if (pos < n && text[pos] === "}") {
        tokens.push({ kind: "}", text: "}" });
        pos++;
        return;
      }
      throw new JsonError(pos, "Missing comma or closing bracket");
    }
  };
  const arr = () => {
    tokens.push({ kind: "[", text: "[" });
    pos++;
    ws();
    if (pos < n && text[pos] === "]") {
      tokens.push({ kind: "]", text: "]" });
      pos++;
      return;
    }
    for (;;) {
      ws();
      if (pos < n && text[pos] === "]" && tokens[tokens.length - 1].kind === ",") throw new JsonError(pos, "Trailing comma");
      value();
      ws();
      if (pos < n && text[pos] === ",") {
        tokens.push({ kind: ",", text: "," });
        pos++;
        continue;
      }
      if (pos < n && text[pos] === "]") {
        tokens.push({ kind: "]", text: "]" });
        pos++;
        return;
      }
      throw new JsonError(pos, "Missing comma or closing bracket");
    }
  };
  ws();
  if (pos >= n) return [];
  value();
  ws();
  if (pos < n) throw new JsonError(pos, "Unexpected text after the end");
  return tokens;
}

function checkJson(text: string): Problem[] {
  try {
    scanJson(text);
  } catch (e) {
    if (!(e instanceof JsonError)) throw e;
    const [line, column] = lineCol(text, e.pos);
    return [{ line, column, message: e.message }];
  }
  return [];
}

function yamlMessage(code: string): string {
  if (code === "TAB_AS_INDENT") return "Tabs can't be used for indenting in YAML";
  if (code === "MISSING_CHAR") return "Missing closing quote or bracket";
  if (code === "BAD_INDENT" || code === "UNEXPECTED_TOKEN") return "This line doesn't fit the indenting around it";
  return "This isn't valid YAML";
}

function checkYaml(text: string): Problem[] {
  for (const doc of parseAllDocuments(text)) {
    const e = (doc as { errors?: { code: string; linePos?: { line: number }[] }[] }).errors?.[0];
    if (e) return [{ line: e.linePos?.[0]?.line ?? 1, column: 0, message: yamlMessage(e.code) }];
  }
  return [];
}

function xmlMessage(msg: string): string {
  if (/closing tag/i.test(msg)) return "The closing tag doesn't match the open one";
  if (msg.includes("'&'")) return "A & must be written as &amp;";
  if (/Unclosed tag/i.test(msg)) return "A tag isn't closed";
  if (/attribute/i.test(msg)) return "An attribute isn't written right";
  return "This isn't valid XML";
}

function checkXml(text: string): Problem[] {
  if (!text.trim()) return [];
  const result = XMLValidator.validate(text);
  if (result === true) return [];
  const { line, msg } = result.err;
  // an unclosed tag shows at the end of the file, like the desktop (lxml)
  const at = /Unclosed tag/i.test(msg) ? (text.match(/\n/g)?.length ?? 0) + 1 : line;
  return [{ line: at, column: 0, message: xmlMessage(msg) }];
}

/** The mistakes in `text` (only the first one); [] when fine or not a checked language. */
export function check(languageId: string, text: string): Problem[] {
  if (languageId === "json") return checkJson(text);
  if (languageId === "yaml") return checkYaml(text);
  if (languageId === "xml" || languageId === "svg") return checkXml(text);
  return [];
}
