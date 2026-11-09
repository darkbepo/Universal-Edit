// Document routing (port of the desktop app's planner): every conversion an engine can do is a
// weighted edge; the cheapest path wins. e.g. md → pdf = md →(pandoc) html →(mupdf) pdf.
// Pure logic here; execution lives in pdf.ts.

export type DocEngine = "pandoc" | "mupdf";

export interface Edge {
  engine: DocEngine;
  src: string;
  dst: string;
  cost: number;
}

// Kept in sync with engines/pandoc.ts (duplicated so this module stays engine-free).
const PANDOC_READ = ["md", "html", "epub", "docx", "odt", "rst", "tex", "org", "txt", "rtf", "csv", "tsv", "xlsx", "pptx", "ipynb", "fb2"];
const PANDOC_WRITE = ["md", "html", "epub", "docx", "odt", "rtf", "txt", "rst", "tex", "org", "pptx"];
export const MUPDF_OPEN = ["pdf", "epub", "xps", "mobi", "fb2", "cbz"];

function buildEdges(): Edge[] {
  const e: Edge[] = [];
  for (const s of PANDOC_READ) {
    for (const d of PANDOC_WRITE) {
      if (s === d) continue;
      let cost = s === "txt" ? 2 : 1; // plain text read as Markdown: fine, not perfect
      if (d === "epub") cost += 0.5; // adds a title page; poor stepping stone
      if (d === "pptx") cost += 0.5;
      e.push({ engine: "pandoc", src: s, dst: d, cost });
    }
  }
  e.push({ engine: "mupdf", src: "html", dst: "pdf", cost: 1.5 }); // HTML layout engine
  e.push({ engine: "mupdf", src: "txt", dst: "pdf", cost: 1 });
  e.push({ engine: "mupdf", src: "pdf", dst: "txt", cost: 1 });
  e.push({ engine: "mupdf", src: "pdf", dst: "html", cost: 2 });
  for (const s of ["epub", "xps", "mobi", "fb2", "cbz"]) e.push({ engine: "mupdf", src: s, dst: "pdf", cost: 1.5 });
  // Plain text loses all formatting: fine as a final target, poor as a stepping stone.
  return e.map((x) => (x.dst === "txt" ? { ...x, cost: x.cost + 2 } : x));
}

export const EDGES = buildEdges();

/** Cheapest list of edges from ``src`` to ``dst`` ([] if equal, null if impossible). */
export function plan(src: string, dst: string): Edge[] | null {
  if (src === dst) return [];
  const best = new Map<string, number>([[src, 0]]);
  const prev = new Map<string, Edge>();
  const queue: [number, string][] = [[0, src]];
  while (queue.length) {
    queue.sort((a, b) => a[0] - b[0]);
    const [cost, node] = queue.shift()!;
    if (node === dst) break;
    if (cost > (best.get(node) ?? Infinity)) continue;
    for (const edge of EDGES) {
      if (edge.src !== node) continue;
      const c = cost + edge.cost;
      if (c < (best.get(edge.dst) ?? Infinity)) {
        best.set(edge.dst, c);
        prev.set(edge.dst, edge);
        queue.push([c, edge.dst]);
      }
    }
  }
  if (!prev.has(dst)) return null;
  const route: Edge[] = [];
  for (let node = dst; node !== src; ) {
    const edge = prev.get(node)!;
    route.unshift(edge);
    node = edge.src;
  }
  return route;
}

export function reachable(src: string): Set<string> {
  const seen = new Set([src]);
  const stack = [src];
  while (stack.length) {
    const node = stack.pop()!;
    for (const e of EDGES) {
      if (e.src === node && !seen.has(e.dst)) {
        seen.add(e.dst);
        stack.push(e.dst);
      }
    }
  }
  return seen;
}
