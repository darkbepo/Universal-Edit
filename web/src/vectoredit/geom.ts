// Numbers, 2D affine matrices and boxes — a line-by-line port of src/umc/vectoredit/geom.py
// (both test suites check tests/fixtures/vectoredit_cases.json).

/** At most 3 decimals, no trailing zeros, no "-0": the only way the model writes numbers.
 * Rounds half up on the thousandths and builds the text from integers, like the desktop. */
export function num(x: number): string {
  if (!Number.isFinite(x)) throw new RangeError(`not a finite number: ${x}`);
  let k = Math.floor(x * 1000 + 0.5);
  const sign = k < 0 ? "-" : "";
  k = Math.abs(k);
  const whole = Math.floor(k / 1000);
  const frac = k % 1000;
  if (frac === 0) return whole === 0 ? "0" : `${sign}${whole}`;
  return `${sign}${whole}.` + String(frac).padStart(3, "0").replace(/0+$/, "");
}

/** `x' = a x + c y + e`, `y' = b x + d y + f` (SVG's `matrix(a b c d e f)`). */
export class Matrix {
  constructor(
    readonly a = 1,
    readonly b = 0,
    readonly c = 0,
    readonly d = 1,
    readonly e = 0,
    readonly f = 0,
  ) {}

  /** `this.times(o)` applies `o` first, then `this` (SVG lists read left to right). */
  times(o: Matrix): Matrix {
    return new Matrix(
      this.a * o.a + this.c * o.b,
      this.b * o.a + this.d * o.b,
      this.a * o.c + this.c * o.d,
      this.b * o.c + this.d * o.d,
      this.a * o.e + this.c * o.f + this.e,
      this.b * o.e + this.d * o.f + this.f,
    );
  }

  apply(x: number, y: number): [number, number] {
    return [this.a * x + this.c * y + this.e, this.b * x + this.d * y + this.f];
  }

  inverse(): Matrix {
    const det = this.a * this.d - this.b * this.c;
    if (det === 0) throw new RangeError("the transform squashes everything flat");
    return new Matrix(
      this.d / det,
      -this.b / det,
      -this.c / det,
      this.a / det,
      (this.c * this.f - this.d * this.e) / det,
      (this.b * this.e - this.a * this.f) / det,
    );
  }

  isIdentity(): boolean {
    return this.isTranslate() && this.e === 0 && this.f === 0;
  }

  isTranslate(): boolean {
    return this.a === 1 && this.b === 0 && this.c === 0 && this.d === 1;
  }
}

export const IDENTITY = new Matrix();

export const translate = (tx: number, ty = 0) => new Matrix(1, 0, 0, 1, tx, ty);

export const scale = (sx: number, sy?: number) => new Matrix(sx, 0, 0, sy ?? sx, 0, 0);

export function rotate(deg: number, cx = 0, cy = 0): Matrix {
  const r = (deg * Math.PI) / 180;
  const cos = Math.cos(r);
  const sin = Math.sin(r);
  const turn = new Matrix(cos, sin, -sin, cos, 0, 0);
  if (cx === 0 && cy === 0) return turn;
  return translate(cx, cy).times(turn).times(translate(-cx, -cy));
}

const NUMBER = "[+-]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?";
const FUNC = /[ \t\r\n\f]*(matrix|translate|scale|rotate|skewX|skewY)[ \t\r\n\f]*\(([^)]*)\)[ \t\r\n\f]*,?/y;
const ARITY: Record<string, number[]> = {
  matrix: [6],
  translate: [1, 2],
  scale: [1, 2],
  rotate: [1, 3],
  skewX: [1],
  skewY: [1],
};

export const stripWs = (s: string) => s.replace(/^[ \t\r\n\f]+|[ \t\r\n\f]+$/g, "");

/** An SVG `transform` list → one matrix. Throws on anything malformed. */
export function parseTransform(input: string): Matrix {
  let m = IDENTITY;
  const text = stripWs(input);
  let pos = 0;
  while (pos < text.length) {
    FUNC.lastIndex = pos;
    const hit = FUNC.exec(text);
    if (!hit) throw new SyntaxError(`bad transform: ${input}`);
    const [, name, inner] = hit;
    const args = (inner.match(new RegExp(NUMBER, "g")) ?? []).map(Number);
    const leftover = stripWs(inner.replace(new RegExp(NUMBER, "g"), "").replace(/,/g, ""));
    if (leftover || !ARITY[name].includes(args.length)) {
      throw new SyntaxError(`bad transform: ${input}`);
    }
    m = m.times(one(name, args));
    pos = FUNC.lastIndex;
  }
  return m;
}

function one(name: string, v: number[]): Matrix {
  if (name === "matrix") return new Matrix(v[0], v[1], v[2], v[3], v[4], v[5]);
  if (name === "translate") return translate(v[0], v.length > 1 ? v[1] : 0);
  if (name === "scale") return scale(v[0], v.length > 1 ? v[1] : undefined);
  if (name === "rotate") return v.length === 3 ? rotate(v[0], v[1], v[2]) : rotate(v[0]);
  const t = Math.tan((v[0] * Math.PI) / 180);
  return name === "skewX" ? new Matrix(1, 0, t, 1, 0, 0) : new Matrix(1, t, 0, 1, 0, 0);
}

/** The shortest of "", `translate(…)`, `scale(…)` or `matrix(…)` that writes `m`. */
export function formatTransform(m: Matrix): string {
  const parts = [m.a, m.b, m.c, m.d, m.e, m.f].map(num);
  const [a, b, c, d, e, f] = parts;
  if (a === "1" && b === "0" && c === "0" && d === "1") {
    if (e === "0" && f === "0") return "";
    return f === "0" ? `translate(${e})` : `translate(${e} ${f})`;
  }
  if (b === "0" && c === "0" && e === "0" && f === "0") {
    return a === d ? `scale(${a})` : `scale(${a} ${d})`;
  }
  return `matrix(${parts.join(" ")})`;
}

export class Box {
  constructor(
    readonly x0: number,
    readonly y0: number,
    readonly x1: number,
    readonly y1: number,
  ) {}

  get width(): number {
    return this.x1 - this.x0;
  }

  get height(): number {
    return this.y1 - this.y0;
  }

  union(o: Box | null): Box {
    if (o === null) return this;
    return new Box(
      Math.min(this.x0, o.x0),
      Math.min(this.y0, o.y0),
      Math.max(this.x1, o.x1),
      Math.max(this.y1, o.y1),
    );
  }

  transformed(m: Matrix): Box {
    const corners: [number, number][] = [
      [this.x0, this.y0],
      [this.x1, this.y0],
      [this.x0, this.y1],
      [this.x1, this.y1],
    ];
    return pointsBox(corners.map(([x, y]) => m.apply(x, y)));
  }
}

export function pointsBox(pts: [number, number][]): Box {
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  return new Box(Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys));
}
