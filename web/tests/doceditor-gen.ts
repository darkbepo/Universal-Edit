// Seeded random HTML for the document editor's property tests (the same generator as the
// desktop's tests/test_doceditor_canonical.py): subset tags, tags the normaliser maps or drops,
// and awkward white space.

export class Rand {
  constructor(private seed: number) {}
  next(): number {
    this.seed = (Math.imul(this.seed, 1103515245) + 12345) & 0x7fffffff;
    return this.seed / 0x7fffffff;
  }
  int(n: number): number {
    return Math.floor(this.next() * n);
  }
  pick<T>(items: T[]): T {
    return items[this.int(items.length)];
  }
}

export const TEXTS = ["a", "b c", " ", "  ", "\t", " ", "x  y", "\n", "&amp;", "z ", " q"];
const SPANS = ["color:red", "font-size:14pt", "background-color:#00ff00;color:#000", "font-family:Arial"];
const STYLES = [
  "",
  ' style="text-align:center"',
  ' style="margin-left:36pt"',
  ' style="page-break-before:always;text-align:right"',
];
const TAGS = ["b", "i", "u", "s", "", "", "", "", "strong", "code", "sub"];

function inline(r: Rand, d: number): string {
  if (d > 3 || r.next() < 0.35) return r.pick(TEXTS);
  const k = r.int(12);
  const kids = () => Array.from({ length: r.int(3) }, () => inline(r, d + 1)).join("");
  if ([0, 1, 2, 3, 8, 9, 10].includes(k)) return `<${TAGS[k]}>${kids()}</${TAGS[k]}>`;
  if (k === 4) return `<a href="${r.pick(["u", "v"])}">${kids()}</a>`;
  if (k === 5) return `<span style="${r.pick(SPANS)}">${kids()}</span>`;
  if (k === 6) return `<img src="img/${r.pick([1, 2])}.png"${r.pick(["", ' width="5"'])}>`;
  if (k === 7) return "<br>";
  return r.pick(TEXTS);
}

const inl = (r: Rand) => Array.from({ length: 1 + r.int(4) }, () => inline(r, 0)).join("");

export function block(r: Rand, d: number): string {
  const kids = (f: () => string) => Array.from({ length: r.int(3) }, f).join("");
  const st = r.pick(STYLES);
  const k = r.int(d > 3 ? 4 : 11);
  if (k === 0) return `<p${st}>${inl(r)}</p>`;
  if (k === 1) return `<h${1 + r.int(5)}${st}>${inl(r)}</h1>`;
  if (k === 2) return `<pre>${r.pick(["", "\n", "\n\n"])}${inl(r)}${r.pick(["", "\n"])}</pre>`;
  if (k === 3) return r.pick(["<hr>", inl(r)]);
  if (k === 4) return `<blockquote>${kids(() => block(r, d + 1))}</blockquote>`;
  if (k === 5 || k === 6) {
    const tag = r.pick(["ul", "ol"]);
    const item = () => {
      const inner = inl(r) + (r.next() < 0.4 ? block(r, d + 1) : "");
      return `<li${st}>${inner}${r.next() < 0.3 ? inl(r) : ""}</li>`;
    };
    return `<${tag}>${kids(item)}</ul>`;
  }
  if (k === 7 || k === 8) {
    const border = r.pick(["", ' style="border:none"']);
    const cell = () => {
      const span = r.pick(["", ' colspan="2"', ' rowspan="2"']);
      const body = r.next() < 0.5 ? inl(r) : kids(() => block(r, d + 1));
      return `<td${span}>${body}</td>`;
    };
    return `<table${border}>${kids(() => `<tr>${kids(cell)}</tr>`)}</table>`;
  }
  if (k === 9) return `<div>${kids(() => block(r, d + 1))}</div>`;
  return `<p>${inl(r)}</p>`;
}

/** One to four random blocks. */
export const randomHtml = (r: Rand) => Array.from({ length: 1 + r.int(4) }, () => block(r, 0)).join("");
