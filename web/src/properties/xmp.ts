// Just enough XMP for photo details — a line-by-line port of the desktop's properties/xmp.py.
import { wsTrim } from "./exif";
const ENTITIES: Record<string, string> = { "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&amp;": "&" };
const unescape = (text: string) => text.replace(/&(lt|gt|quot|apos|amp);/g, (m) => ENTITIES[m]);
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The text of ``prop`` (e.g. "dc:title"); list items are joined with "; ". */
export function get(xmp: string, prop: string): string | null {
  const n = esc(prop);
  let m = new RegExp(`\\s${n}=(?:"([^"]*)"|'([^']*)')`).exec(xmp);
  if (m) return wsTrim(unescape(m[1] ?? m[2])) || null;
  m = new RegExp(`<${n}\\b[^>]*?>([\\s\\S]*?)</${n}>`).exec(xmp);
  if (!m) return null;
  const items = [...m[1].matchAll(/<rdf:li\b[^>]*?>([\s\S]*?)<\/rdf:li>/g)].map((x) => x[1]);
  const text = items.length
    ? items.filter((i) => wsTrim(i)).map((i) => wsTrim(unescape(i))).join("; ")
    : wsTrim(unescape(m[1].replace(/<[^>]*>/g, "")));
  return text || null;
}

/** ``xmp`` without ``props``; a prop ending in "*" removes every property starting with it. */
export function remove(xmp: string, props: string[]): string {
  for (const prop of props) {
    if (prop.endsWith("*")) {
      const n = `${esc(prop.slice(0, -1))}[\\w.-]*`;
      const found = [
        ...[...xmp.matchAll(new RegExp(`<(${n})\\b`, "g"))].map((m) => m[1]),
        ...[...xmp.matchAll(new RegExp(`\\s(${n})=["']`, "g"))].map((m) => m[1]),
      ];
      const names = [...new Set(found)].sort();
      if (names.length) xmp = remove(xmp, names);
      continue;
    }
    const n = esc(prop);
    xmp = xmp.replace(new RegExp(`[ \\t]*<${n}\\b[^>]*?/>[ \\t]*\\r?\\n?`, "g"), "");
    xmp = xmp.replace(new RegExp(`[ \\t]*<${n}\\b[^>]*?>[\\s\\S]*?</${n}>[ \\t]*\\r?\\n?`, "g"), "");
    xmp = xmp.replace(new RegExp(`\\s${n}=(?:"[^"]*"|'[^']*')`, "g"), "");
  }
  return xmp;
}
