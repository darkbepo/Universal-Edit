// Data formats: JSON ↔ YAML ↔ XML (trees) ↔ CSV / TSV / XLSX (tables) — same rules as the
// desktop app's data.py. Libraries load lazily inside the worker.
import { opt, type Values } from "../core/options";
import { ConversionError, type Converter, type OutputFile } from "../core/types";
import { baseName, uniqueNamer } from "./util";

export const DATA_READ = new Set(["json", "yaml", "xml", "csv", "tsv", "xlsx"]);
const TARGETS: Record<string, string> = { json: "JSON", yaml: "YAML", xml: "XML", csv: "CSV", tsv: "TSV", xlsx: "XLSX" };
const TABLES = ["data:csv", "data:tsv", "data:xlsx"];
const TREES = ["data:json", "data:yaml", "data:xml"];
const MIME: Record<string, string> = {
  json: "application/json", yaml: "application/yaml", xml: "application/xml", csv: "text/csv",
  tsv: "text/tab-separated-values", xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

type Row = Record<string, unknown>;
type Tree = unknown;

// ------------------------------------------------------------------------- shared helpers

export function scalar(text: string): unknown {
  const t = text.trim();
  if (t === "true" || t === "false") return t === "true";
  if (/^-?(0|[1-9]\d*)$/.test(t)) return Number(t);
  if (/^-?((0|[1-9]\d*)?\.\d+([eE][-+]?\d+)?|(0|[1-9]\d*)[eE][-+]?\d+)$/.test(t)) return Number(t);
  return text;
}

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function flatten(row: unknown, prefix = ""): Row {
  const out: Row = {};
  if (isObj(row)) {
    for (const [k, v] of Object.entries(row)) {
      const key = prefix ? `${prefix}.${k}` : k;
      if (isObj(v) && Object.keys(v).length) Object.assign(out, flatten(v, key));
      else if (Array.isArray(v)) out[key] = JSON.stringify(v);
      else out[key] = v;
    }
  } else out[prefix || "value"] = row;
  return out;
}

export function unflatten(row: Row): Row {
  const out: Row = {};
  for (const [key, value] of Object.entries(row)) {
    const parts = key.split(".");
    let node = out;
    for (const p of parts.slice(0, -1)) {
      if (!isObj(node[p])) node[p] = {};
      node = node[p] as Row;
    }
    node[parts[parts.length - 1]] = value;
  }
  return out;
}

/** Find the table in a tree: follow single-key wrappers down to a list of rows. */
export function asTable(value: Tree): Row[] {
  let v = value;
  while (isObj(v)) {
    const lists = Object.values(v).filter(Array.isArray);
    if (lists.length === 1) v = lists[0];
    else if (Object.keys(v).length === 1 && isObj(Object.values(v)[0])) v = Object.values(v)[0];
    else v = [v];
  }
  const list = Array.isArray(v) ? v : [v];
  return list.map((r) => (isObj(r) ? r : { value: r }));
}

const columns = (rows: Row[]) => [...new Set(rows.flatMap((r) => Object.keys(r)))];

function rowsToRecords(rows: unknown[][], o: Values, typed: boolean): Row[] {
  const kept = rows.filter((r) => r.some((c) => c !== null && c !== undefined && c !== ""));
  if (!kept.length) return [];
  const width = Math.max(...kept.map((r) => r.length));
  const name = (i: number) => `column${i + 1}`;
  const head = o.header
    ? [...Array(width).keys()].map((i) => (kept[0][i] === undefined || kept[0][i] === null || kept[0][i] === "" ? name(i) : String(kept[0][i])))
    : [...Array(width).keys()].map(name);
  return (o.header ? kept.slice(1) : kept).map((r) => {
    const rec: Row = {};
    head.forEach((h, i) => {
      const c = r[i] ?? null;
      rec[h] = !typed && o.detect_numbers && typeof c === "string" ? scalar(c) : c;
    });
    return rec;
  });
}

// ------------------------------------------------------------------------- CSV

export function parseCsv(text: string, delimiter?: string): string[][] {
  if (!delimiter) {
    const first = text.split(/\r?\n/, 1)[0] ?? "";
    delimiter = [",", ";", "\t", "|"].sort((a, b) => first.split(b).length - first.split(a).length)[0];
  }
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === delimiter) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  return rows;
}

const csvCell = (v: unknown, d: string) => {
  const s = v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
  return /["\r\n]/.test(s) || s.includes(d) ? `"${s.replaceAll('"', '""')}"` : s;
};

// ------------------------------------------------------------------------- XLSX (own minimal reader/writer)

const colIndex = (ref: string) => [...ref.replace(/\d+$/, "")].reduce((n, c) => n * 26 + c.charCodeAt(0) - 64, 0) - 1;
const colName = (i: number): string => (i < 0 ? "" : colName(Math.floor(i / 26) - 1) + String.fromCharCode(65 + (i % 26)));
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

async function readXlsx(bytes: Uint8Array, o: Values): Promise<Tree> {
  const { unzipSync, strFromU8 } = await import("fflate");
  const { XMLParser } = await import("fast-xml-parser");
  let zip: Record<string, Uint8Array>;
  try {
    zip = unzipSync(bytes);
  } catch {
    throw new ConversionError("This isn't a valid XLSX file.");
  }
  const always = new Set(["sheet", "Relationship", "si", "r", "row", "c"]);
  const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "@", textNodeName: "#text", parseTagValue: false, removeNSPrefix: true, isArray: (name) => always.has(name) });
  const xml = (path: string) => (zip[path] ? parser.parse(strFromU8(zip[path])) : null);
  const text = (node: unknown): string => {
    if (node === undefined || node === null) return "";
    if (typeof node === "string" || typeof node === "number") return String(node);
    const n = node as Record<string, unknown>;
    if ("#text" in n) return String(n["#text"]);
    if (n.t !== undefined) return text(n.t);
    if (Array.isArray(n.r)) return n.r.map((r) => text((r as Record<string, unknown>).t)).join("");
    return "";
  };
  const shared: string[] = (xml("xl/sharedStrings.xml")?.sst?.si ?? []).map(text);
  const rels: Record<string, string> = {};
  for (const r of xml("xl/_rels/workbook.xml.rels")?.Relationships?.Relationship ?? []) rels[r["@Id"]] = r["@Target"];
  const sheets: { name: string; path: string }[] = (xml("xl/workbook.xml")?.workbook?.sheets?.sheet ?? []).map((s: Record<string, string>) => {
    const target = rels[s["@id"]] ?? "";
    return { name: s["@name"], path: target.startsWith("/") ? target.slice(1) : `xl/${target.replace(/^\.\//, "")}` };
  });
  const out: Record<string, Row[]> = {};
  for (const sheet of o.sheets === "all" ? sheets : sheets.slice(0, 1)) {
    const rows: unknown[][] = [];
    for (const row of xml(sheet.path)?.worksheet?.sheetData?.row ?? []) {
      const cells: unknown[] = [];
      for (const c of row.c ?? []) {
        const t = c["@t"];
        const raw = c.v === undefined ? undefined : text(c.v);
        let v: unknown = null;
        if (t === "s") v = shared[Number(raw)] ?? "";
        else if (t === "inlineStr") v = text(c.is);
        else if (t === "b") v = raw === "1";
        else if (t === "str" || t === "e") v = raw ?? "";
        else if (raw !== undefined) v = Number(raw);
        cells[colIndex(c["@r"] ?? colName(cells.length))] = v;
      }
      rows.push(cells);
    }
    out[sheet.name] = rowsToRecords(rows, o, true);
  }
  return o.sheets === "all" ? out : (Object.values(out)[0] ?? []);
}

async function writeXlsx(sheets: Record<string, Row[]>): Promise<Uint8Array> {
  const { zipSync, strToU8 } = await import("fflate");
  const names = Object.keys(sheets).map((n, i) => (n.replace(/[\\/*?:[\]]/g, "_").slice(0, 31) || `Sheet${i + 1}`));
  const cell = (ref: string, v: unknown, style = "") => {
    if (v === null || v === undefined || v === "") return "";
    if (typeof v === "number" && Number.isFinite(v)) return `<c r="${ref}"${style}><v>${v}</v></c>`;
    if (typeof v === "boolean") return `<c r="${ref}" t="b"${style}><v>${v ? 1 : 0}</v></c>`;
    const s = typeof v === "object" ? JSON.stringify(v) : String(v);
    return `<c r="${ref}" t="inlineStr"${style}><is><t xml:space="preserve">${esc(s)}</t></is></c>`;
  };
  const files: Record<string, Uint8Array> = {
    "[Content_Types].xml": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>${names.map((_, i) => `<Override PartName="/xl/worksheets/sheet${i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`).join("")}</Types>`),
    "_rels/.rels": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>`),
    "xl/workbook.xml": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>${names.map((n, i) => `<sheet name="${esc(n)}" sheetId="${i + 1}" r:id="rId${i + 1}"/>`).join("")}</sheets></workbook>`),
    "xl/_rels/workbook.xml.rels": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${names.map((_, i) => `<Relationship Id="rId${i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet${i + 1}.xml"/>`).join("")}<Relationship Id="rId${names.length + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>`),
    "xl/styles.xml": strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>`),
  };
  Object.values(sheets).forEach((rows, i) => {
    const cols = columns(rows);
    const head = `<row r="1">${cols.map((c, j) => cell(`${colName(j)}1`, c, ' s="1"')).join("")}</row>`;
    const body = rows.map((r, k) => `<row r="${k + 2}">${cols.map((c, j) => cell(`${colName(j)}${k + 2}`, r[c])).join("")}</row>`).join("");
    files[`xl/worksheets/sheet${i + 1}.xml`] = strToU8(`<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${head}${body}</sheetData></worksheet>`);
  });
  return zipSync(files, { level: 6 });
}

// ------------------------------------------------------------------------- XML

const xmlName = (key: string) => {
  const n = key.replace(/[^A-Za-z0-9_.-]/g, "_") || "_";
  return /^[A-Za-z_]/.test(n) ? n : `_${n}`;
};

/** Prepare a tree for XMLBuilder: valid names, list items wrapped, booleans as text. */
function forXml(v: unknown, item: string): unknown {
  if (Array.isArray(v)) return { [xmlName(item)]: v.map((x) => (Array.isArray(x) ? forXml(x, item) : forXml(x, item))) };
  if (isObj(v)) {
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v)) {
      const key = k === "#text" ? k : k.startsWith("@") ? `@${xmlName(k.slice(1))}` : xmlName(k);
      out[key] = Array.isArray(x) ? x.map((y) => forXml(y, item)) : forXml(x, item);
    }
    return out;
  }
  return v === null || v === undefined ? "" : String(v);
}

/** Attributes before child elements, like the desktop app (keeps column order identical). */
function attributesFirst(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(attributesFirst);
  if (!isObj(v)) return v;
  const entries = Object.entries(v).map(([k, x]) => [k, attributesFirst(x)] as const);
  return Object.fromEntries([...entries.filter(([k]) => k.startsWith("@")), ...entries.filter(([k]) => !k.startsWith("@"))]);
}

// ------------------------------------------------------------------------- read / write

async function read(bytes: Uint8Array, ext: string, name: string, o: Values): Promise<Tree> {
  const text = () => new TextDecoder("utf-8").decode(bytes).replace(/^﻿/, "");
  try {
    if (ext === "json") return JSON.parse(text());
    if (ext === "yaml") return (await import("yaml")).parse(text());
    if (ext === "xml") {
      const { XMLParser, XMLValidator } = await import("fast-xml-parser");
      const valid = XMLValidator.validate(text());
      if (valid !== true) throw new Error(valid.err.msg);
      return attributesFirst(new XMLParser({ ignoreAttributes: false, attributeNamePrefix: "@", textNodeName: "#text", parseTagValue: false, parseAttributeValue: false, ignoreDeclaration: true, ignorePiTags: true }).parse(text()));
    }
    if (ext === "csv" || ext === "tsv") return rowsToRecords(parseCsv(text(), ext === "tsv" ? "\t" : undefined), o, false);
    if (ext === "xlsx") return await readXlsx(bytes, o);
  } catch (e) {
    if (e instanceof ConversionError) throw e;
    throw new ConversionError(`“${name}” isn't valid ${ext.toUpperCase()}: ${(e as Error).message}`);
  }
  throw new ConversionError(`Can't read ${ext.toUpperCase()} files as data.`);
}

async function write(value: Tree, ext: string, o: Values): Promise<Uint8Array> {
  const enc = (s: string) => new TextEncoder().encode(s);
  const indent = Number(o.indent);
  if (ext === "json") return enc(JSON.stringify(value, null, indent || undefined) + "\n");
  if (ext === "yaml") return enc((await import("yaml")).stringify(value, { indent: indent || 2 }));
  if (ext === "xml") {
    const { XMLBuilder } = await import("fast-xml-parser");
    let root = String(o.xml_root || "root");
    let body: unknown = value;
    if (isObj(value) && Object.keys(value).length === 1 && !Array.isArray(Object.values(value)[0])) {
      [root, body] = Object.entries(value)[0];
    }
    const builder = new XMLBuilder({ ignoreAttributes: false, attributeNamePrefix: "@", textNodeName: "#text", format: indent > 0, indentBy: " ".repeat(indent || 2), suppressEmptyNode: true });
    return enc(`<?xml version='1.0' encoding='utf-8'?>\n${builder.build({ [xmlName(root)]: forXml(body, String(o.xml_item || "item")) }).trim()}\n`);
  }
  let sheets: Record<string, Row[]> =
    ext === "xlsx" && isObj(value) && Object.keys(value).length && Object.values(value).every(Array.isArray)
      ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, asTable(v)]))
      : { Sheet1: asTable(value) };
  if (o.flatten) sheets = Object.fromEntries(Object.entries(sheets).map(([k, rows]) => [k, rows.map((r) => flatten(r))]));
  if (ext === "xlsx") return writeXlsx(sheets);
  const rows = Object.values(sheets)[0];
  const cols = columns(rows);
  const d = ext === "tsv" ? "\t" : String(o.delimiter || ",");
  const lines = [cols.map((c) => csvCell(c, d)).join(d), ...rows.map((r) => cols.map((c) => csvCell(r[c], d)).join(d))];
  return enc((o.excel_bom ? "﻿" : "") + lines.join("\r\n") + "\r\n");
}

export const dataConverter: Converter = {
  id: "data",
  family: "document",
  options: [
    opt.choice("indent", "Indentation", 2, [[2, "2 spaces"], [4, "4 spaces"], [0, "Minified"]], { targets: TREES }),
    opt.choice("delimiter", "Separator", ",", [[",", "Comma ,"], [";", "Semicolon ;"], ["|", "Pipe |"]], { targets: ["data:csv"] }),
    opt.bool("excel_bom", "Excel-friendly (UTF-8 with BOM)", false, { targets: ["data:csv", "data:tsv"], help: "Makes Excel show accents and other special characters correctly." }),
    opt.bool("flatten", "Flatten nested data into columns (a.b)", true, { targets: TABLES }),
    opt.bool("unflatten", "Rebuild nesting from column names like a.b", true, { targets: TREES }),
    opt.text("xml_root", "XML root element", "root", { targets: ["data:xml"] }),
    opt.text("xml_item", "XML element for list items", "item", { targets: ["data:xml"] }),
    opt.bool("header", "Tables I'm converting have a header row", true, { help: "For CSV/TSV/XLSX input. Off = columns are named column1, column2…" }),
    opt.bool("detect_numbers", "Turn numbers and true/false in CSV into real values", true),
    opt.choice("sheets", "Spreadsheet sheets", "first", [["first", "First sheet only"], ["all", "All sheets"]], { help: "For XLSX input." }),
  ],
  targets(files) {
    if (!files.length || !files.every((f) => DATA_READ.has(f.ext))) return [];
    const single = files.length === 1 ? files[0].ext : "";
    return Object.entries(TARGETS).filter(([ext]) => ext !== single).map(([ext, label]) => ({
      id: `data:${ext}`, label, ext, group: "Data", converterId: "data",
    }));
  },
  async run(files, target, o, ctx) {
    const unique = uniqueNamer();
    const out: OutputFile[] = [];
    const ext = target.ext;
    for (const [i, f] of files.entries()) {
      ctx.progress(i / files.length, `Converting ${f.name} (${i + 1}/${files.length})`);
      let value = await read(new Uint8Array(await f.file.arrayBuffer()), f.ext, f.name, o);
      if (["json", "yaml", "xml"].includes(ext) && ["csv", "tsv", "xlsx"].includes(f.ext) && o.unflatten) {
        value = Array.isArray(value)
          ? (value as Row[]).map(unflatten)
          : Object.fromEntries(Object.entries(value as Record<string, Row[]>).map(([k, v]) => [k, v.map(unflatten)]));
      }
      out.push({ name: unique(`${baseName(f.name)}.${ext}`), data: await write(value, ext, o), type: MIME[ext] });
    }
    ctx.progress(1, "Done");
    return out;
  },
};
