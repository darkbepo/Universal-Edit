// SVG path data (the `d` attribute): parse, write, transform, bounds — a line-by-line port of
// src/umc/vectoredit/pathdata.py. A segment is an array whose first item is the command:
// ["M", x, y], ["L", x, y], ["C", x1, y1, x2, y2, x, y], ["Q", x1, y1, x, y],
// ["A", rx, ry, rotation, large, sweep, x, y], ["Z"] — all absolute.

import { type Box, Matrix, num, pointsBox } from "./geom";

export type Seg = [string, ...number[]];

const TOKEN = /[ \t\r\n\f]*,?[ \t\r\n\f]*([MmZzLlHhVvCcSsQqTtAa]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)/y;
const FLAG = /[ \t\r\n\f]*,?[ \t\r\n\f]*([01])/y;
const ARGS: Record<string, number> = { M: 2, L: 2, H: 1, V: 1, C: 6, S: 4, Q: 4, T: 2, A: 7, Z: 0 };
const isAlpha = (s: string) => /^[A-Za-z]$/.test(s);

class Reader {
  pos = 0;
  constructor(readonly d: string) {}

  private token(): RegExpExecArray | null {
    TOKEN.lastIndex = this.pos;
    return TOKEN.exec(this.d);
  }

  peekCommand(): string | null {
    const hit = this.token();
    if (!hit || !isAlpha(hit[1])) return null;
    return hit[1];
  }

  command(): string {
    const hit = this.token()!;
    this.pos = TOKEN.lastIndex;
    return hit[1];
  }

  number(): number | null {
    const hit = this.token();
    if (!hit || isAlpha(hit[1])) return null;
    this.pos = TOKEN.lastIndex;
    return Number(hit[1]);
  }

  flag(): number | null {
    FLAG.lastIndex = this.pos;
    const hit = FLAG.exec(this.d);
    if (!hit) return null;
    this.pos = FLAG.lastIndex;
    return Number(hit[1]);
  }

  atEnd(): boolean {
    return /^[ \t\r\n\f]*$/.test(this.d.slice(this.pos));
  }
}

/** Parse `d`. Like browsers, a mistake ends the path there: what came before is kept. */
export function parse(d: string): Seg[] {
  const r = new Reader(d);
  const out: Seg[] = [];
  let x = 0;
  let y = 0;
  let sx = 0;
  let sy = 0;
  let lastC: [number, number] | null = null;
  let lastQ: [number, number] | null = null;
  let cmd: string | null = null;
  while (!r.atEnd()) {
    const nxt = r.peekCommand();
    if (nxt !== null) cmd = r.command();
    else if (cmd === null || cmd === "Z" || cmd === "z") break;
    const upper: string = cmd!.toUpperCase();
    const rel: boolean = cmd !== upper;
    if (upper === "Z") {
      out.push(["Z"]);
      x = sx;
      y = sy;
      lastC = lastQ = null;
      continue;
    }
    const args: number[] = [];
    for (let k = 0; k < ARGS[upper]; k++) {
      const v = upper === "A" && (k === 3 || k === 4) ? r.flag() : r.number();
      if (v === null) return out;
      args.push(v);
    }
    if (!out.length && upper !== "M") return out;
    const ox = rel ? x : 0;
    const oy = rel ? y : 0;
    let newC: [number, number] | null = null;
    let newQ: [number, number] | null = null;
    if (upper === "M") {
      x = args[0] + ox;
      y = args[1] + oy;
      sx = x;
      sy = y;
      out.push(["M", x, y]);
      cmd = rel ? "l" : "L";
    } else if (upper === "L") {
      x = args[0] + ox;
      y = args[1] + oy;
      out.push(["L", x, y]);
    } else if (upper === "H") {
      x = args[0] + ox;
      out.push(["L", x, y]);
    } else if (upper === "V") {
      y = args[0] + (rel ? y : 0);
      out.push(["L", x, y]);
    } else if (upper === "C" || upper === "S") {
      let x1: number;
      let y1: number;
      let rest: number[];
      if (upper === "C") {
        x1 = args[0] + ox;
        y1 = args[1] + oy;
        rest = args.slice(2);
      } else {
        [x1, y1] = lastC ? [2 * x - lastC[0], 2 * y - lastC[1]] : [x, y];
        rest = args;
      }
      const x2 = rest[0] + ox;
      const y2 = rest[1] + oy;
      x = rest[2] + ox;
      y = rest[3] + oy;
      out.push(["C", x1, y1, x2, y2, x, y]);
      newC = [x2, y2];
    } else if (upper === "Q" || upper === "T") {
      let x1: number;
      let y1: number;
      let end: number[];
      if (upper === "Q") {
        x1 = args[0] + ox;
        y1 = args[1] + oy;
        end = args.slice(2);
      } else {
        [x1, y1] = lastQ ? [2 * x - lastQ[0], 2 * y - lastQ[1]] : [x, y];
        end = args;
      }
      x = end[0] + ox;
      y = end[1] + oy;
      out.push(["Q", x1, y1, x, y]);
      newQ = [x1, y1];
    } else {
      x = args[5] + ox;
      y = args[6] + oy;
      out.push(["A", Math.abs(args[0]), Math.abs(args[1]), args[2], args[3], args[4], x, y]);
    }
    lastC = newC;
    lastQ = newQ;
  }
  return out;
}

/** Absolute commands separated by spaces, numbers through `num`. */
export function write(segs: Seg[]): string {
  return segs
    .map((s) => {
      if (s[0] === "Z") return "Z";
      if (s[0] === "A") {
        const [, rx, ry, rot, large, sweep, x, y] = s as number[] & Seg;
        return `A${num(rx as number)} ${num(ry as number)} ${num(rot as number)} ${Math.trunc(large as number)} ${Math.trunc(sweep as number)} ${num(x as number)} ${num(y as number)}`;
      }
      return s[0] + (s.slice(1) as number[]).map(num).join(" ");
    })
    .join(" ");
}

/** An SVG arc as cubic curves (at most 90° each), following the SVG implementation notes. */
export function arcToCubics(
  x0: number,
  y0: number,
  rxIn: number,
  ryIn: number,
  rot: number,
  large: number,
  sweep: number,
  x: number,
  y: number,
): Seg[] {
  if (x0 === x && y0 === y) return [];
  let rx = rxIn;
  let ry = ryIn;
  if (rx === 0 || ry === 0) return [["C", x0, y0, x, y, x, y]];
  const phi = (pyMod(rot, 360) * Math.PI) / 180;
  const cos = Math.cos(phi);
  const sin = Math.sin(phi);
  const dx = (x0 - x) / 2;
  const dy = (y0 - y) / 2;
  const x1p = cos * dx + sin * dy;
  const y1p = -sin * dx + cos * dy;
  const lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
  if (lam > 1) {
    const k = Math.sqrt(lam);
    rx *= k;
    ry *= k;
  }
  const numer = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
  const den = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
  let coef = den ? Math.sqrt(Math.max(0, numer / den)) : 0;
  if (Boolean(large) === Boolean(sweep)) coef = -coef;
  const cxp = (coef * rx * y1p) / ry;
  const cyp = (-coef * ry * x1p) / rx;
  const cx = cos * cxp - sin * cyp + (x0 + x) / 2;
  const cy = sin * cxp + cos * cyp + (y0 + y) / 2;

  const angle = (ux: number, uy: number, vx: number, vy: number) =>
    Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);

  const t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
  let dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry);
  if (!sweep && dt > 0) dt -= 2 * Math.PI;
  else if (sweep && dt < 0) dt += 2 * Math.PI;
  const n = Math.max(1, Math.ceil(Math.abs(dt) / (Math.PI / 2) - 1e-9));
  const step = dt / n;
  const alpha = (4 / 3) * Math.tan(step / 4);
  const out: Seg[] = [];

  const point = (t: number): [number, number, number, number] => {
    const ex = rx * Math.cos(t);
    const ey = ry * Math.sin(t);
    const tx = -rx * Math.sin(t);
    const ty = ry * Math.cos(t);
    return [cos * ex - sin * ey + cx, sin * ex + cos * ey + cy, cos * tx - sin * ty, sin * tx + cos * ty];
  };

  let [, , pdx, pdy] = point(t1);
  let px = x0;
  let py = y0;
  for (let k = 0; k < n; k++) {
    const t = t1 + step * (k + 1);
    let [qx, qy] = point(t);
    const [, , qdx, qdy] = point(t);
    if (k === n - 1) {
      qx = x;
      qy = y;
    }
    out.push(["C", px + alpha * pdx, py + alpha * pdy, qx - alpha * qdx, qy - alpha * qdy, qx, qy]);
    px = qx;
    py = qy;
    pdx = qdx;
    pdy = qdy;
  }
  return out;
}

/** Python's `%` (the result takes the divisor's sign). */
const pyMod = (a: number, b: number) => ((a % b) + b) % b;

/** The same path with every arc replaced by cubics. */
export function withoutArcs(segs: Seg[]): Seg[] {
  const out: Seg[] = [];
  let p: [number, number, number, number] = [0, 0, 0, 0];
  for (const s of segs) {
    if (s[0] === "A") {
      const v = s.slice(1) as number[];
      out.push(...arcToCubics(p[0], p[1], v[0], v[1], v[2], v[3], v[4], v[5], v[6]));
    } else out.push(s);
    p = advance(s, p);
  }
  return out;
}

type Pos = [number, number, number, number]; // x, y, and where the subpath started

function advance(s: Seg, [, , sx, sy]: Pos): Pos {
  if (s[0] === "Z") return [sx, sy, sx, sy];
  if (s[0] === "M") return [s[1] as number, s[2] as number, s[1] as number, s[2] as number];
  return [s[s.length - 2] as number, s[s.length - 1] as number, sx, sy];
}

/** Every point through `m`. Arcs stay arcs under a move; otherwise they become cubics. */
export function transform(segsIn: Seg[], m: Matrix): Seg[] {
  const segs = m.isTranslate() ? segsIn : withoutArcs(segsIn);
  return segs.map((s): Seg => {
    if (s[0] === "Z") return s;
    if (s[0] === "A") {
      const [nx, ny] = m.apply(s[6] as number, s[7] as number);
      return [...(s.slice(0, 6) as Seg), nx, ny] as Seg;
    }
    const out: Seg = [s[0]];
    for (let k = 1; k < s.length; k += 2) out.push(...m.apply(s[k] as number, s[k + 1] as number));
    return out;
  });
}

function cubicExtrema(p0: number, p1: number, p2: number, p3: number): number[] {
  const a = -p0 + 3 * p1 - 3 * p2 + p3;
  const b = 2 * (p0 - 2 * p1 + p2);
  const c = p1 - p0;
  if (Math.abs(a) < 1e-12) {
    return Math.abs(b) > 1e-12 && 0 < -c / b && -c / b < 1 ? [-c / b] : [];
  }
  const disc = b * b - 4 * a * c;
  if (disc < 0) return [];
  const r = Math.sqrt(disc);
  return [(-b + r) / (2 * a), (-b - r) / (2 * a)].filter((t) => 0 < t && t < 1);
}

function cubicAt(p0: number, p1: number, p2: number, p3: number, t: number): number {
  const u = 1 - t;
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
}

function cubicPoints(
  x0: number, y0: number, x1: number, y1: number, x2: number, y2: number, x3: number, y3: number,
): [number, number][] {
  const ts = [1, ...cubicExtrema(x0, x1, x2, x3), ...cubicExtrema(y0, y1, y2, y3)];
  return ts.map((t) => [cubicAt(x0, x1, x2, x3, t), cubicAt(y0, y1, y2, y3, t)]);
}

/** The path's tight bounding box (curve extrema, not control points); null when empty. */
export function bounds(segs: Seg[]): Box | null {
  const pts: [number, number][] = [];
  let p: [number, number, number, number] = [0, 0, 0, 0];
  for (const s of withoutArcs(segs)) {
    const [x, y] = p;
    const v = s.slice(1) as number[];
    if (s[0] === "M" || s[0] === "L") pts.push([v[0], v[1]]);
    else if (s[0] === "Q") {
      const c1x = x + (2 / 3) * (v[0] - x);
      const c1y = y + (2 / 3) * (v[1] - y);
      const c2x = v[2] + (2 / 3) * (v[0] - v[2]);
      const c2y = v[3] + (2 / 3) * (v[1] - v[3]);
      pts.push(...cubicPoints(x, y, c1x, c1y, c2x, c2y, v[2], v[3]));
    } else if (s[0] === "C") pts.push(...cubicPoints(x, y, v[0], v[1], v[2], v[3], v[4], v[5]));
    p = advance(s, p);
  }
  return pts.length ? pointsBox(pts) : null;
}

export { Matrix };
